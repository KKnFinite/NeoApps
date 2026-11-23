#!/usr/bin/env bash
set -euo pipefail
venv="$RUNNER_TEMP/neoapps-release"
python3.12 -m venv "$venv"
"$venv/bin/python" -m pip --isolated install --no-cache-dir --only-binary=:all: --require-hashes \
  -r scripts/release/requirements-linux-py312.lock -c requirements.txt
"$venv/bin/python" -m pip check
"$venv/bin/python" -m pip --isolated install --no-index --dry-run -r requirements.txt
