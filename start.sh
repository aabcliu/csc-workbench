#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"
usage() {
  cat <<'EOF'
Usage: ./start.sh [start|build|stop|status|logs|help]
  start   Load the bundled prebuilt image (or reuse an installed image) and start.
  build   Build the image from source and start. Internet access is required.
  stop    Stop the service, keeping saved workspaces and analysis results.
  status  Show the container status.
  logs    Follow the service logs (Ctrl-C exits log viewing).

Environment:
  CSC_PORT           Local port (default: 8765).
  CSC_IMAGE          Image tag (default: csc-workbench:0.1.0).
  CSC_IMAGE_ARCHIVE  Optional prebuilt Docker image archive to load.
  CSC_PLATFORM       Target platform (default: Docker's native Linux architecture).
  CSC_PROJECT_NAME   Compose project name (default: csc-workbench).
EOF
}
case "$ACTION" in
  help|-h|--help) usage; exit 0 ;;
  start|build|stop|status|logs) ;;
  *) usage >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then usage >&2; exit 2; fi
if ! command -v docker >/dev/null 2>&1; then
  echo 'Install and start Docker Desktop (or Docker Engine with Compose v2), then retry.' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'Docker Compose v2 is required.' >&2
  exit 1
fi
if ! ENGINE_INFO=$(docker info --format '{{.OSType}} {{.Architecture}}' 2>/dev/null); then
  echo 'Docker is unavailable. Start Docker Desktop or your Docker daemon, then retry.' >&2
  exit 1
fi
case "$ENGINE_INFO" in
  'linux aarch64'|'linux arm64') NATIVE_PLATFORM=linux/arm64 ;;
  'linux x86_64'|'linux amd64') NATIVE_PLATFORM=linux/amd64 ;;
  *) echo "Unsupported Docker platform: $ENGINE_INFO. Use Linux containers on AMD64 or ARM64." >&2; exit 1 ;;
esac
export CSC_PORT="${CSC_PORT:-8765}"
export CSC_IMAGE="${CSC_IMAGE:-csc-workbench:0.1.0}"
export CSC_PLATFORM="${CSC_PLATFORM:-$NATIVE_PLATFORM}"
case "$CSC_PLATFORM" in
  linux/amd64|linux/arm64) ;;
  *) echo 'CSC_PLATFORM must be linux/amd64 or linux/arm64.' >&2; exit 2 ;;
esac
if ! [[ "$CSC_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || (( CSC_PORT > 65535 )); then
  echo 'CSC_PORT must be an integer from 1 to 65535.' >&2
  exit 2
fi
COMPOSE=(docker compose --project-name "${CSC_PROJECT_NAME:-csc-workbench}" -f "$ROOT/deploy/compose.yaml")
case "$ACTION" in
  stop) "${COMPOSE[@]}" down ;;
  status) "${COMPOSE[@]}" ps ;;
  logs) "${COMPOSE[@]}" logs --follow --tail=100 ;;
  start|build)
    ARCHIVE="${CSC_IMAGE_ARCHIVE:-$ROOT/images/csc-workbench.tar}"
    if [[ "$ACTION" == start ]]; then
      if [[ -n "${CSC_IMAGE_ARCHIVE:-}" || -f "$ARCHIVE" ]]; then
        if [[ ! -f "$ARCHIVE" ]]; then echo "Image archive not found: $ARCHIVE" >&2; exit 1; fi
        docker load --input "$ARCHIVE"
      fi
      if ! IMAGE_PLATFORM=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$CSC_IMAGE" 2>/dev/null); then
        echo "Prebuilt image $CSC_IMAGE is not installed." >&2
        echo 'Download and extract the prebuilt package for your CPU, then run ./start.sh.' >&2
        echo 'To build the source package instead, run ./start.sh build.' >&2
        exit 1
      fi
      if [[ "$IMAGE_PLATFORM" != "$CSC_PLATFORM" ]]; then
        echo "Image targets $IMAGE_PLATFORM; Docker startup targets $CSC_PLATFORM." >&2
        echo 'Use the matching prebuilt package, or set CSC_PLATFORM explicitly if Docker supports emulation.' >&2
        exit 1
      fi
      UP_ARGS=(up --detach --no-build --pull never --wait --wait-timeout 120)
    else
      export BUILDX_GIT_INFO=0 BUILDX_GIT_LABELS=0 BUILDX_NO_DEFAULT_ATTESTATIONS=1
      UP_ARGS=(up --detach --build --pull never --wait --wait-timeout 120)
    fi
    mkdir -p "$ROOT/runtime"
    if ! "${COMPOSE[@]}" "${UP_ARGS[@]}"; then
      "${COMPOSE[@]}" logs --tail=80 >&2 || true
      exit 1
    fi
    echo "CSC Workbench is ready: http://127.0.0.1:$CSC_PORT"
    echo 'Try Add program → Load example → Use this program → Generate Tests → Run Verification → Locate Faults.'
    ;;
esac
