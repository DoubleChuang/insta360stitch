#!/usr/bin/env bash

set -euo pipefail

if [[ $# -eq 0 ]]; then
    exec /usr/local/bin/insta360-stitch-batch --help
fi

case "$1" in
    bash|sh|insta360_media_stitcher|insta360-stitch-batch)
        exec "$@"
        ;;
    *)
        exec /usr/local/bin/insta360-stitch-batch "$@"
        ;;
esac
