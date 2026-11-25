#!/usr/bin/env bash
# Run EDOPal straight from this checkout (always the latest local code).
# On SteamOS the dev venv lives in the "ai-apps" distrobox, so hop into it first.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
if ! .venv/bin/python -c "import PySide6" 2>/dev/null; then
    exec distrobox enter "${EDOPAL_BOX:-ai-apps}" -- "$PWD/tools/run-dev.sh" "$@"
fi
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -c "import sys; from edopal.app import main; sys.exit(main())" "$@"
