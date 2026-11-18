#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render0 libxcb-render-util0 libxcb-shape0 \
  libxcb-shm0 libxcb-sync1 libxcb-util1 libxcb-xfixes0 libxcb-xkb1 libxcb-glx0 \
  libx11-6 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 \
  libegl1 libgl1 libgl1-mesa-dri libwayland-client0 libwayland-cursor0 libwayland-egl1 \
  libdbus-1-3 libfontconfig1 libfreetype6 libglib2.0-bin libzstd1 libstdc++6 \
  fontconfig fonts-dejavu-core fonts-liberation "$@"
