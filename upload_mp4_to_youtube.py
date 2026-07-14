#!/usr/bin/env python3
"""Upload a local MP4 to YouTube using the vendored `youtube-upload` project.

This is a thin wrapper around `youtube_upload.main.main()` so you can run it
without installing the package globally.

First run will prompt OAuth in browser/console and create a credentials file.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
from contextlib import redirect_stdout
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
YOUTUBE_UPLOAD_SRC = REPO_ROOT / "youtube-upload"
DEFAULT_LOG_FILE = REPO_ROOT / "upload_mp4_to_youtube.log"


def _ensure_vendored_youtube_upload(
    parser: argparse.ArgumentParser,
) -> None:
    if YOUTUBE_UPLOAD_SRC.exists():
        sys.path.insert(0, YOUTUBE_UPLOAD_SRC.as_posix())
    else:
        parser.error(
            f"Missing vendored folder: {YOUTUBE_UPLOAD_SRC}"
        )


def _resolve_paths(
    parser: argparse.ArgumentParser,
    ns: argparse.Namespace,
) -> argparse.Namespace:
    if ns.client_secrets is None:
        parser.error(
            "No client secrets JSON found. Provide --client-secrets, "
            "or place a client_secret*.json at repo root (or ~/.client_secrets.json)."
        )

    if ns.description_file is not None:
        desc_path = (
            Path(ns.description_file).expanduser().resolve()
        )
        if not desc_path.exists():
            parser.error(
                f"Description file not found: {desc_path}"
            )
        ns.description_file = desc_path.as_posix()

    if ns.thumbnail is not None:
        thumb_path = (
            Path(ns.thumbnail).expanduser().resolve()
        )
        if not thumb_path.exists():
            parser.error(
                f"Thumbnail not found: {thumb_path}"
            )
        ns.thumbnail = thumb_path.as_posix()

    return ns


def _configure_logging(log_file: str) -> None:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.setLevel(logging.INFO)

    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)


def _matches_exclude_pattern(
    video_path: Path,
    pattern: str,
) -> bool:
    normalized_pattern = pattern.lower()
    candidates = [
        video_path.name.lower(),
        video_path.as_posix().lower(),
    ]
    if any(char in pattern for char in "*?[]"):
        return any(
            fnmatch(candidate, normalized_pattern)
            for candidate in candidates
        )
    return any(
        normalized_pattern in candidate
        for candidate in candidates
    )


def _resolve_video_targets(
    parser: argparse.ArgumentParser,
    ns: argparse.Namespace,
) -> tuple[list[Path], list[dict[str, str]]]:
    if ns.video is None and ns.video_dir is None:
        parser.error(
            "Provide a video path or --video-dir."
        )

    if ns.video is not None and ns.video_dir is not None:
        parser.error(
            "Use either a video path or --video-dir, not both."
        )

    if ns.video_dir is not None:
        video_dir = Path(ns.video_dir).expanduser().resolve()
        if not video_dir.exists():
            parser.error(
                f"Video directory not found: {video_dir}"
            )
        if not video_dir.is_dir():
            parser.error(
                f"Not a directory: {video_dir}"
            )
        candidates = sorted(
            path
            for path in video_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".mp4"
        )
    else:
        video_path = Path(ns.video).expanduser().resolve()
        if not video_path.exists():
            parser.error(f"Video not found: {video_path}")
        if video_path.suffix.lower() != ".mp4":
            parser.error(
                "Only .mp4 is supported by this wrapper (got: %s)"
                % video_path.suffix
            )
        candidates = [video_path]

    selected: list[Path] = []
    skipped: list[dict[str, str]] = []
    for video_path in candidates:
        matched_pattern = next(
            (
                pattern
                for pattern in ns.exclude
                if _matches_exclude_pattern(
                    video_path, pattern
                )
            ),
            None,
        )
        if matched_pattern is not None:
            skipped.append(
                {
                    "file": video_path.as_posix(),
                    "reason": (
                        "excluded by pattern: "
                        f"{matched_pattern}"
                    ),
                }
            )
            continue
        selected.append(video_path)

    return selected, skipped


def _build_upload_result(
    video_path: Path,
    exit_code: int,
    raw_output: str,
    error_message: str | None = None,
) -> dict[str, str | int | None]:
    lines = [
        line.strip()
        for line in raw_output.splitlines()
        if line.strip()
    ]
    video_id = lines[-1] if exit_code == 0 and lines else None
    result: dict[str, str | int | None] = {
        "file": video_path.as_posix(),
        "name": video_path.name,
        "exit_code": exit_code,
        "video_id": video_id,
    }
    if error_message is not None:
        result["error"] = error_message
    return result


def _upload_one_video(
    ns: argparse.Namespace,
    video_path: Path,
    youtube_upload_main,
) -> dict[str, str | int | None]:
    upload_ns = argparse.Namespace(**vars(ns))
    upload_ns.video = video_path.as_posix()
    if upload_ns.title is None:
        upload_ns.title = video_path.stem

    youtube_args = _build_youtube_upload_args(upload_ns)
    stdout_buffer = io.StringIO()

    try:
        with redirect_stdout(stdout_buffer):
            youtube_upload_main.main(youtube_args)
    except SystemExit as e:
        exit_code = (
            int(e.code)
            if isinstance(e.code, int)
            else 1
        )
        return _build_upload_result(
            video_path,
            exit_code,
            stdout_buffer.getvalue(),
            error_message=(
                "youtube-upload exited with code "
                f"{exit_code}"
            ),
        )
    except Exception as exc:  # pragma: no cover - external API failures
        return _build_upload_result(
            video_path,
            1,
            stdout_buffer.getvalue(),
            error_message=str(exc),
        )

    return _build_upload_result(
        video_path,
        0,
        stdout_buffer.getvalue(),
    )


def _print_result_summary(
    result: dict[str, object],
) -> None:
    print(
        "RESULT_JSON="
        + json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload an MP4 to YouTube (wrapper for vendored youtube-upload)."
    )
    parser.add_argument(
        "video",
        nargs="?",
        help="Path to the .mp4 file",
    )
    parser.add_argument(
        "--video-dir",
        default=None,
        help="Upload all .mp4 files directly under this folder",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude filenames matching this substring or glob pattern; can be used multiple times",
    )
    parser.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILE.as_posix(),
        help="Log file path",
    )
    return parser


def check_youtube_upload_available(
    client_secrets: str | None = None,
    credentials_file: str | None = None,
    auth_browser: bool = False,
) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    _ensure_vendored_youtube_upload(parser)

    from youtube_upload import auth  # type: ignore
    from youtube_upload.auth import browser  # type: ignore
    from youtube_upload.auth import console  # type: ignore

    resolved_client_secrets = (
        client_secrets or _default_client_secrets_path()
    )
    if resolved_client_secrets is None:
        parser.error(
            "No client secrets JSON found. Provide --client-secrets, "
            "or place a client_secret*.json at repo root (or ~/.client_secrets.json)."
        )

    resolved_credentials = credentials_file
    if resolved_credentials is None:
        resolved_credentials = str(
            Path.home() / ".youtube-upload-credentials.json"
        )

    get_code_callback = (
        browser.get_code
        if auth_browser
        else console.get_code
    )
    youtube = auth.get_resource(
        resolved_client_secrets,
        resolved_credentials,
        get_code_callback=get_code_callback,
    )
    if youtube is None:
        raise RuntimeError(
            "Cannot authenticate with YouTube"
        )

    youtube.channels().list(
        mine=True,
        part="id",
        maxResults=1,
    ).execute()


def _default_client_secrets_path() -> str | None:
    # Prefer any client_secret*.json sitting at repo root.
    candidates = sorted(
        REPO_ROOT.glob("client_secret*.json")
    )
    if candidates:
        return candidates[0].as_posix()
    # Fallback to the legacy name used by youtube-upload.
    legacy = Path.home() / ".client_secrets.json"
    if legacy.exists():
        return legacy.as_posix()
    return None


def _build_youtube_upload_args(
    ns: argparse.Namespace,
) -> list[str]:
    args: list[str] = []

    args += ["--title", ns.title]

    if ns.description is not None:
        args += ["--description", ns.description]
    if ns.description_file is not None:
        args += ["--description-file", ns.description_file]
    if ns.category is not None:
        args += ["--category", ns.category]
    if ns.tags is not None:
        args += ["--tags", ns.tags]

    args += ["--privacy", ns.privacy]

    if ns.publish_at is not None:
        args += ["--publish-at", ns.publish_at]
    if ns.recording_date is not None:
        args += ["--recording-date", ns.recording_date]
    if ns.default_language is not None:
        args += ["--default-language", ns.default_language]
    if ns.default_audio_language is not None:
        args += [
            "--default-audio-language",
            ns.default_audio_language,
        ]

    if ns.thumbnail is not None:
        args += ["--thumbnail", ns.thumbnail]
    if ns.playlist is not None:
        args += ["--playlist", ns.playlist]

    if ns.client_secrets is not None:
        args += ["--client-secrets", ns.client_secrets]
    if ns.credentials_file is not None:
        args += ["--credentials-file", ns.credentials_file]

    if ns.auth_browser:
        args += ["--auth-browser"]
    if ns.open_link:
        args += ["--open-link"]

    args.append(ns.video)
    return args


def main() -> int:
    parser = _build_parser()
    parser.add_argument(
        "--title",
        default=None,
        help="YouTube title (default: filename without extension)",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Video description text",
    )
    parser.add_argument(
        "--description-file",
        default=None,
        help="UTF-8 text file for description",
    )
    parser.add_argument(
        "--category",
        default=None,
        help='Category name (e.g. "People")',
    )
    parser.add_argument(
        "--tags",
        default=None,
        help='Comma-separated tags (e.g. "tag1, tag2")',
    )
    parser.add_argument(
        "--privacy",
        default="private",
        choices=("public", "unlisted", "private"),
        help="Privacy status",
    )
    parser.add_argument(
        "--publish-at",
        default=None,
        help='Schedule publish time (ISO 8601, e.g. "2026-01-28T10:00:00.0Z")',
    )
    parser.add_argument(
        "--recording-date",
        default=None,
        help='Recording date (ISO 8601, e.g. "2026-01-27T11:22:33.0Z")',
    )
    parser.add_argument(
        "--default-language",
        default=None,
        help='ISO 639-1 (e.g. "zh")',
    )
    parser.add_argument(
        "--default-audio-language",
        default=None,
        help='ISO 639-1 (e.g. "zh")',
    )
    parser.add_argument(
        "--thumbnail",
        default=None,
        help="Path to thumbnail PNG/JPEG",
    )
    parser.add_argument(
        "--playlist", default=None, help="Playlist title"
    )
    parser.add_argument(
        "--client-secrets",
        default=_default_client_secrets_path(),
        help="OAuth client secrets JSON (default: repo client_secret*.json if present)",
    )
    parser.add_argument(
        "--credentials-file",
        default=None,
        help="Credentials JSON (default: ~/.youtube-upload-credentials.json)",
    )
    parser.add_argument(
        "--auth-browser",
        action="store_true",
        help="Use a browser GUI flow when authenticating",
    )
    parser.add_argument(
        "--open-link",
        action="store_true",
        help="Open the uploaded video URL after upload",
    )

    ns = parser.parse_args()
    _configure_logging(ns.log_file)

    ns = _resolve_paths(parser, ns)

    selected_videos, skipped_videos = _resolve_video_targets(
        parser, ns
    )

    _ensure_vendored_youtube_upload(parser)

    from youtube_upload import main as youtube_upload_main  # type: ignore

    result: dict[str, object] = {
        "selected_count": len(selected_videos),
        "uploaded_count": 0,
        "failed_count": 0,
        "skipped_count": len(skipped_videos),
        "uploaded": [],
        "failed": [],
        "skipped": skipped_videos,
        "log_file": Path(ns.log_file).expanduser().resolve().as_posix(),
    }

    for skipped in skipped_videos:
        logging.info(
            "skip upload for %s (%s)",
            skipped["file"],
            skipped["reason"],
        )

    if not selected_videos:
        logging.warning("no mp4 files selected for upload")
        _print_result_summary(result)
        return 0

    check_youtube_upload_available(
        client_secrets=ns.client_secrets,
        credentials_file=ns.credentials_file,
        auth_browser=ns.auth_browser,
    )

    for video_path in selected_videos:
        logging.info(
            "start upload: %s", video_path.as_posix()
        )
        upload_result = _upload_one_video(
            ns,
            video_path,
            youtube_upload_main,
        )
        if upload_result["exit_code"] == 0:
            logging.info(
                "upload success: %s -> %s",
                upload_result["file"],
                upload_result["video_id"],
            )
            result["uploaded"].append(upload_result)
        else:
            logging.error(
                "upload failed: %s (%s)",
                upload_result["file"],
                upload_result.get("error"),
            )
            result["failed"].append(upload_result)

    result["uploaded_count"] = len(result["uploaded"])
    result["failed_count"] = len(result["failed"])
    logging.info(
        "upload finished: %s success, %s failed, %s skipped",
        result["uploaded_count"],
        result["failed_count"],
        result["skipped_count"],
    )
    _print_result_summary(result)

    if result["failed_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
