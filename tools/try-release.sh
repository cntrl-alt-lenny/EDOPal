#!/usr/bin/env bash
# Download and run the latest Linux release; discard the extracted program when it closes.
set -euo pipefail
[[ $(uname -sm) == 'Linux x86_64' ]] || { echo 'Requires x86-64 Linux.' >&2; exit 1; }
stage=$(realpath -- "$(mktemp -d)")
trap 'rm -rf -- "$stage"' EXIT
cd -- "$stage"
get=(curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --retry 2)
"${get[@]}" https://api.github.com/repos/cntrl-alt-lenny/EDOPal/releases/latest -o release.json
python3 - <<'PY' > download
import json, re
from pathlib import Path

release = json.loads(Path("release.json").read_bytes())
tag = release["tag_name"]
if not re.fullmatch(r"v[0-9A-Za-z.+-]+", tag):
    raise SystemExit("Invalid release tag")
assets = {a["name"]: a for a in release["assets"]}
for name in ("EDOPal-linux-x86_64.tar.gz", f"EDOPro-HD-Sync-Linux-{tag}.zip"):
    if name in assets:
        digest = assets[name].get("digest") or ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit("GitHub supplied no SHA-256 for " + name)
        print(tag, name, digest[7:])
        break
else:
    raise SystemExit("The latest release has no supported Linux archive")
PY
read -r tag asset digest < download
echo "Downloading $asset" >&2
"${get[@]}" "https://github.com/cntrl-alt-lenny/EDOPal/releases/download/$tag/$asset" -o "$asset"
printf '%s  %s\n' "$digest" "$asset" | sha256sum --check
if [[ $asset == *.tar.gz ]]; then
    tar --no-same-owner -xzf "$asset"
    ./EDOPal/EDOPal "$@"
else
    unzip -p "$asset" 'EDOPro HD Sync/EDOPro-HD-Sync' > EDOPal
    chmod 700 EDOPal
    ./EDOPal "$@"
fi
