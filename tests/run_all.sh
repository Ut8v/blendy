#!/usr/bin/env bash
# Everything that runs without Blender, then (optionally) the Blender suite.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== validator / server / director / learning (no Blender) =="
./.venv/bin/python -m unittest discover -s tests -t .
echo "== specs =="
./.venv/bin/python compiler/validate.py spec/scenes/*.json
if [ "${1:-}" = "--blender" ]; then
  echo "== compiler, headless against real Blender =="
  tests/blender/run.sh
fi
