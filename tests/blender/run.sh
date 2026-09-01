#!/usr/bin/env bash
# Headless compiler tests against real Blender. Pass test class/method names to filter.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
exec "$BLENDER" -b --factory-startup --python "$HERE/test_compiler.py" -- "$@"
