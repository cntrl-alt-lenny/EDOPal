#!/usr/bin/env bash
# Rebuild in the checkout and launch with the same directory layout as the portable release.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python="${PYTHON:-.venv/bin/python}"
[[ -x "$python" ]] || python="${PYTHON:-python3}"
"$python" -m PyInstaller --clean --noconfirm packaging/edopal.spec
exec ./dist/EDOPal/EDOPal "$@"
