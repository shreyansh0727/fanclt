#!/usr/bin/env bash
# One command for the firmware build. Anything after the script name goes to
# `pio run`, so flashing needs no extra tooling.
#
#   scripts/build.sh                          build only
#   scripts/build.sh -t upload                flash on the default port
#   scripts/build.sh -t upload --upload-port COM12
#   scripts/build.sh -t clean                 clean the build tree
#
# Set PIO=/path/to/pio to use a different PlatformIO install.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${PIO:-}" ]]; then
  if [[ -x "$ROOT/.venv/Scripts/pio.exe" ]]; then
    PIO="$ROOT/.venv/Scripts/pio.exe"
  elif [[ -x "$ROOT/.venv/bin/pio" ]]; then
    PIO="$ROOT/.venv/bin/pio"
  elif command -v pio >/dev/null 2>&1; then
    PIO="pio"
  else
    echo "PlatformIO not found - pip install platformio or set PIO=/path/to/pio" >&2
    exit 1
  fi
fi

cd "$ROOT/firmware"
exec "$PIO" run "$@"
