#!/usr/bin/env bash
set -euo pipefail

xvfb-run -a -s "-screen 0 1280x800x24" env QT_QPA_PLATFORM=xcb python -m tools.release verify

XDG_RUNTIME_DIR=$(mktemp -d "$RUNNER_TEMP/wayland-XXXXXX")
export XDG_RUNTIME_DIR
export WAYLAND_DISPLAY=edopal-test
# Jammy accepts the module filename. Newer Weston releases use its short name.
weston --backend="$1" --width=1280 --height=800 --socket="$WAYLAND_DISPLAY" --idle-time=0 \
  --no-config --log="$RUNNER_TEMP/weston.log" &
compositor=$!
trap 'kill "$compositor" 2>/dev/null || true' EXIT
for _ in {1..100}; do
  [[ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]] && break
  kill -0 "$compositor" 2>/dev/null || break
  sleep 0.1
done
if [[ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]]; then
  cat "$RUNNER_TEMP/weston.log"
  exit 1
fi
env -u DISPLAY QT_QPA_PLATFORM=wayland python -m tools.release verify
