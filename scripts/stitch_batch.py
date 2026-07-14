#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PAIR_RE = re.compile(r"^(?P<prefix>.+)_(?P<track>00|10)_(?P<clip>\d+)$")


@dataclass(frozen=True)
class StitchJob:
    name: str
    inputs: tuple[Path, ...]
    output: Path
    camera_model: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch stitch Insta360 raw .insv clips into equirectangular MP4 files."
    )
    parser.add_argument("input_path", help="Path to one .insv file or a directory containing raw clips.")
    parser.add_argument("output_dir", help="Directory where stitched MP4 files will be written.")
    parser.add_argument(
        "--model-root",
        default=os.environ.get("INSTA360_MODEL_ROOT", "/opt/insta360/models"),
        help="Directory containing ai_stitch_model_v1.ins and/or ai_stitch_model_v2.ins.",
    )
    parser.add_argument(
        "--model-version",
        choices=("auto", "v1", "v2"),
        default="auto",
        help="Force a specific AI stitch model version when using --stitch-type aistitch.",
    )
    parser.add_argument(
        "--stitch-type",
        choices=("template", "optflow", "dynamicstitch", "aistitch"),
        default="optflow",
        help="MediaSDK stitch mode. Default is optflow so AI models are optional.",
    )
    parser.add_argument("--output-size", default="7680x3840", help="Output size in WIDTHxHEIGHT format.")
    parser.add_argument("--bitrate", type=int, default=80_000_000, help="Target bitrate in bps.")
    parser.add_argument("--camera-accessory-type", type=int, default=0, help="Camera accessory type enum.")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan input directories.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--disable-flowstate", action="store_true", help="Disable FlowState stabilization.")
    parser.add_argument("--disable-directionlock", action="store_true", help="Disable direction lock.")
    parser.add_argument("--disable-stitchfusion", action="store_true", help="Disable stitch fusion.")
    parser.add_argument("--disable-h265", action="store_true", help="Use H.264 instead of H.265.")
    parser.add_argument("--disable-gpu", action="store_true", help="Disable CUDA usage in the SDK wrapper.")
    parser.add_argument("--enable-soft-encode", action="store_true", help="Use software encoding.")
    parser.add_argument("--enable-soft-decode", action="store_true", help="Use software decoding.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_output_size(args.output_size)

    input_path = Path(args.input_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_path.exists():
        raise SystemExit(f"Input path does not exist: {input_path}")

    if not args.disable_gpu:
        ensure_gpu_available()

    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = collect_jobs(input_path, output_dir, recursive=args.recursive)

    if not jobs:
        raise SystemExit(f"No .insv files found in {input_path}")

    for job in jobs:
        run_job(job, args)

    print(f"Completed {len(jobs)} stitched clip(s).")
    return 0


def ensure_gpu_available() -> None:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        raise SystemExit(
            "nvidia-smi is not available. Run the container with --gpus all or pass --disable-gpu."
        )

    result = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise SystemExit(
            "No visible NVIDIA GPU was detected. Run the container with --gpus all or pass --disable-gpu."
        )


def validate_output_size(value: str) -> None:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise SystemExit(f"Invalid output size '{value}'. Expected WIDTHxHEIGHT.") from exc

    if width <= 0 or height <= 0:
        raise SystemExit("Output size must be positive.")

    if width != height * 2:
        raise SystemExit("Output size must keep a 2:1 aspect ratio.")

    if width % 2 != 0 or height % 2 != 0:
        raise SystemExit("Output dimensions must be even numbers.")


def collect_jobs(input_path: Path, output_dir: Path, recursive: bool) -> list[StitchJob]:
    if input_path.is_file():
        files = tuple(resolve_inputs_for_file(input_path))
        name = output_name_from_paths(files)
        return [StitchJob(name=name, inputs=files, output=output_dir / f"{name}_stitched.mp4", camera_model=detect_camera_model(files[0]))]

    iterator: Iterable[Path]
    iterator = input_path.rglob("*.insv") if recursive else input_path.glob("*.insv")
    groups: dict[str, dict[str, Path]] = {}

    for candidate in sorted(iterator):
        if candidate.suffix.lower() != ".insv":
            continue

        group_name, track = split_name(candidate)
        slot = track or candidate.name
        groups.setdefault(group_name, {})[slot] = candidate.resolve()

    jobs: list[StitchJob] = []
    for name in sorted(groups):
        members = groups[name]
        ordered = order_group_members(members)
        jobs.append(
            StitchJob(
                name=sanitize_name(name),
                inputs=tuple(ordered),
                output=output_dir / f"{sanitize_name(name)}_stitched.mp4",
                camera_model=detect_camera_model(ordered[0]),
            )
        )

    return jobs


def resolve_inputs_for_file(path: Path) -> list[Path]:
    if path.suffix.lower() != ".insv":
        raise SystemExit(f"Expected an .insv file, got: {path}")

    group_name, track = split_name(path)
    if track is None:
        return [path.resolve()]

    directory = path.parent
    members: dict[str, Path] = {track: path.resolve()}
    for candidate in directory.glob("*.insv"):
        candidate_group, candidate_track = split_name(candidate)
        if candidate_group == group_name and candidate_track is not None:
            members[candidate_track] = candidate.resolve()

    return order_group_members(members)


def split_name(path: Path) -> tuple[str, str | None]:
    match = PAIR_RE.match(path.stem)
    if not match:
        return path.stem, None
    return f"{match.group('prefix')}_{match.group('clip')}", match.group("track")


def order_group_members(members: dict[str, Path]) -> list[Path]:
    if "00" in members or "10" in members:
        ordered = [members[track] for track in ("00", "10") if track in members]
        if ordered:
            return ordered
    return [members[key] for key in sorted(members)]


def output_name_from_paths(paths: tuple[Path, ...]) -> str:
    name, _ = split_name(paths[0])
    return name


def sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "clip"


def detect_camera_model(path: Path) -> str | None:
    exiftool = shutil.which("exiftool")
    if exiftool is None:
        return None

    result = subprocess.run(
        [exiftool, "-s3", "-Model", "-CameraModelName", "-DeviceModelName", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return " ".join(lines).upper() if lines else None


def resolve_ai_model(model_root: Path, model_version: str, camera_model: str | None) -> Path:
    v1 = model_root / "ai_stitch_model_v1.ins"
    v2 = model_root / "ai_stitch_model_v2.ins"

    if model_version == "v1":
        if not v1.is_file():
            raise SystemExit(f"Requested v1 AI model, but {v1} does not exist.")
        return v1

    if model_version == "v2":
        if not v2.is_file():
            raise SystemExit(f"Requested v2 AI model, but {v2} does not exist.")
        return v2

    if camera_model and "X5" in camera_model and v2.is_file():
        return v2

    if v1.is_file():
        return v1

    if v2.is_file():
        return v2

    raise SystemExit(
        f"Could not find ai_stitch_model_v1.ins or ai_stitch_model_v2.ins under {model_root}."
    )


@contextmanager
def staged_model_root(ai_model: Path | None):
    if ai_model is None:
        yield None
        return

    with tempfile.TemporaryDirectory(prefix="insta360-model-") as temp_dir:
        staged_root = Path(temp_dir)
        staged_model = staged_root / ai_model.name
        try:
            staged_model.symlink_to(ai_model)
        except OSError:
            shutil.copy2(ai_model, staged_model)
        yield staged_root


def run_job(job: StitchJob, args: argparse.Namespace) -> None:
    if job.output.exists() and not args.overwrite:
        raise SystemExit(f"Output already exists: {job.output}. Use --overwrite to replace it.")

    model_root = Path(args.model_root).expanduser().resolve()
    if args.stitch_type == "aistitch":
        ai_model = resolve_ai_model(model_root, args.model_version, job.camera_model)
    else:
        ai_model = None

    command = [
        "insta360_media_stitcher",
        "-inputs",
        *[str(path) for path in job.inputs],
        "-output",
        str(job.output),
        "-stitch_type",
        args.stitch_type,
        "-output_size",
        args.output_size,
        "-bitrate",
        str(args.bitrate),
        "-camera_accessory_type",
        str(args.camera_accessory_type),
    ]

    with staged_model_root(ai_model) as effective_model_root:
        if effective_model_root is not None:
            command.extend(["-model_root_dir", str(effective_model_root)])

        if not args.disable_flowstate:
            command.append("-enable_flowstate")
        if not args.disable_directionlock:
            command.append("-enable_directionlock")
        if not args.disable_stitchfusion:
            command.append("-enable_stitchfusion")
        if not args.disable_h265:
            command.append("-enable_h265_encoder")
        if args.disable_gpu:
            command.append("-disable_cuda")
        if args.enable_soft_encode:
            command.append("-enable_soft_encode")
        if args.enable_soft_decode:
            command.append("-enable_soft_decode")

        print(f"[{job.name}] {' '.join(command)}")
        if args.dry_run:
            return

        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise SystemExit(f"Stitching failed for {job.name} with exit code {result.returncode}.")

    verify_output(job.output)


def verify_output(output_file: Path) -> None:
    if not output_file.is_file():
        raise SystemExit(f"Expected stitched file was not created: {output_file}")

    if output_file.stat().st_size <= 0:
        raise SystemExit(f"Stitched file is empty: {output_file}")

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name",
            "-of",
            "json",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe could not read the stitched output: {output_file}")

    metadata = json.loads(result.stdout)
    duration_text = metadata.get("format", {}).get("duration")
    if duration_text is None or float(duration_text) <= 0:
        raise SystemExit(f"Stitched output has no readable duration: {output_file}")


if __name__ == "__main__":
    sys.exit(main())
