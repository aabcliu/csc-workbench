#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PLATFORM="${1:-linux/amd64}"
case "$PLATFORM" in
  linux/amd64|linux/arm64) ;;
  *) echo 'Usage: ./deploy/export-image.sh [linux/amd64|linux/arm64] [output.tar]' >&2; exit 2 ;;
esac
if [[ $# -gt 2 ]]; then echo 'Too many arguments.' >&2; exit 2; fi
OUTPUT="${2:-$ROOT/../csc-workbench-0.1.0-${PLATFORM//\//-}.tar}"
IMAGE="${CSC_IMAGE:-csc-workbench:0.1.0}"
if [[ -e "$OUTPUT" ]]; then echo "Output already exists: $OUTPUT" >&2; exit 1; fi
mkdir -p "$(dirname -- "$OUTPUT")"
BUILDX_GIT_INFO=0 BUILDX_GIT_LABELS=0 docker buildx build --provenance=false --platform "$PLATFORM" --load --tag "$IMAGE" \
  --file "$ROOT/deploy/Dockerfile" "$ROOT"
docker save --output "$OUTPUT" "$IMAGE"
echo "Prebuilt $PLATFORM image: $OUTPUT"
