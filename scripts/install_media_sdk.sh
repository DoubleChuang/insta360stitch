#!/usr/bin/env bash

set -euo pipefail

sdk_hint="${1:-}"
sdk_url="${2:-}"

find_deb() {
    if [[ -n "${sdk_hint}" ]]; then
        if [[ -f "${sdk_hint}" ]]; then
            printf '%s\n' "${sdk_hint}"
            return 0
        fi

        if [[ -f "/tmp/sdk/${sdk_hint}" ]]; then
            printf '%s\n' "/tmp/sdk/${sdk_hint}"
            return 0
        fi

        echo "MEDIA_SDK_DEB points to '${sdk_hint}', but that file is not available inside the build context." >&2
        return 1
    fi

    local detected
    detected="$(find /tmp/sdk -maxdepth 1 -type f -name '*.deb' | sort | head -n 1 || true)"
    if [[ -n "${detected}" ]]; then
        printf '%s\n' "${detected}"
        return 0
    fi

    if [[ -n "${sdk_url}" ]]; then
        local downloaded="/tmp/insta360-mediasdk.deb"
        curl -fsSL "${sdk_url}" -o "${downloaded}"
        printf '%s\n' "${downloaded}"
        return 0
    fi

    echo "No Insta360 MediaSDK package was provided, and no download URL is configured." >&2
    echo "Put a MediaSDK .deb into ./sdk/ or pass --build-arg MEDIA_SDK_DEB_URL=<url>." >&2
    return 1
}

sdk_deb="$(find_deb)"

apt-get update
if ! apt-get install -y --no-install-recommends "${sdk_deb}"; then
    dpkg -i "${sdk_deb}" || true
    apt-get install -fy --no-install-recommends
fi
rm -rf /var/lib/apt/lists/*

find_one() {
    find /opt /usr /usr/local -type f "$@" 2>/dev/null | sort | head -n 1 || true
}

include_file="$(find_one -name 'ins_stitcher.h')"
library_file="$(find_one -name 'libMediaSDK.so*')"
model_file="$(find /opt /usr /usr/local -type f \( -name 'ai_stitch_model_v1.ins' -o -name 'ai_stitch_model_v2.ins' \) 2>/dev/null | sort | head -n 1 || true)"

if [[ -z "${include_file}" ]]; then
    echo "Could not find ins_stitcher.h after installing the MediaSDK package." >&2
    exit 1
fi

if [[ -z "${library_file}" ]]; then
    echo "Could not find libMediaSDK.so after installing the MediaSDK package." >&2
    exit 1
fi

mkdir -p /opt/insta360
ln -sfn "$(dirname "${include_file}")" /opt/insta360/include
ln -sfn "$(dirname "${library_file}")" /opt/insta360/lib

if [[ -n "${model_file}" ]]; then
    ln -sfn "$(dirname "${model_file}")" /opt/insta360/models
fi

printf '%s\n' "$(dirname "${library_file}")" > /etc/ld.so.conf.d/insta360-mediasdk.conf
ldconfig
