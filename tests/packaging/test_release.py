import hashlib
import io
import json
import os
import platform
import subprocess
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools import release

LINUX_X64 = pytest.mark.skipif(
    platform.system() != "Linux" or platform.machine() != "x86_64", reason="Linux download launcher"
)


def test_checksums_require_exact_assets_and_use_sha256sum_format(tmp_path):
    names = [release.APPIMAGE, release.PORTABLE, release.WINDOWS, release.MACOS]
    for name in names:
        (tmp_path / name).write_bytes(b"EDOPal")
    release.checksums(tmp_path)
    digest = hashlib.sha256(b"EDOPal").hexdigest()
    assert (tmp_path / "SHA256SUMS").read_bytes() == "".join(f"{digest}  {name}\n" for name in sorted(names)).encode()
    (tmp_path / release.MACOS).rename(tmp_path / "unverified.zip")
    with pytest.raises(ValueError, match="missing .*macos-universal2.*unexpected .*unverified"):
        release.checksums(tmp_path)


def test_windows_zip_reopens_with_its_sibling_runtime(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    onedir = dist / "EDOPal"
    (onedir / "_internal").mkdir(parents=True)
    (onedir / "EDOPal.exe").write_bytes(b"exe")
    (onedir / "_internal/runtime.dll").write_bytes(b"runtime")
    monkeypatch.setattr(release, "DIST", dist)
    monkeypatch.setattr(release, "RELEASE", dist / "release")
    monkeypatch.setattr(release.sys, "platform", "win32")
    monkeypatch.setattr(release.platform, "machine", lambda: "AMD64")
    launched = []

    def check_extraction(command, cwd):
        (exe,) = command
        assert exe == cwd / "EDOPal/EDOPal.exe"
        assert exe.read_bytes() == b"exe"
        assert (exe.parent / "_internal/runtime.dll").read_bytes() == b"runtime"
        launched.append(exe)

    monkeypatch.setattr(release, "smoke", check_extraction)
    release.package()
    release.verify()
    assert len(launched) == 1


def test_macos_verifier_checks_frameworks_and_launches_both_slices(tmp_path, monkeypatch):
    bundle = tmp_path / "EDOPal.app"
    exe = bundle / "Contents/MacOS/EDOPal"
    framework = bundle / "Contents/Frameworks/QtGui.framework/Versions/A/QtGui"
    for binary in (exe, framework):
        binary.parent.mkdir(parents=True)
        binary.write_bytes(bytes.fromhex("cafebabe"))
    (bundle / "Contents/Info.plist").write_bytes(b"<?xml")
    commands = []
    monkeypatch.setattr(release.subprocess, "run", lambda args, *, check: commands.append(args))
    launches = []
    monkeypatch.setattr(release, "smoke", lambda args, cwd: launches.append(args))
    release.verify_macos(bundle, tmp_path)
    assert commands[0] == ["codesign", "--verify", "--deep", "--strict", "--all-architectures", bundle]
    assert {tuple(cmd) for cmd in commands[1:]} == {
        ("lipo", binary, "-verify_arch", "arm64", "x86_64") for binary in (exe, framework)
    }
    assert launches == [["arch", "-arm64", exe], ["arch", "-x86_64", exe]]


@LINUX_X64
@pytest.mark.parametrize("kind", ["tar", "legacy", "corrupt"])
def test_download_launcher_resolves_verifies_runs_and_cleans_up(tmp_path, kind):
    marker = tmp_path / "launched"
    program = b'#!/bin/sh\nprintf "%s\\n" "$PWD" "$#" "$1" > "$EDOPAL_TEST_LAUNCH"\n'
    tag = "v5.5.0" if kind == "legacy" else "v6.0.0"
    if kind == "legacy":
        asset = f"EDOPro-HD-Sync-Linux-{tag}.zip"
        with ZipFile(tmp_path / asset, "w") as archive:
            archive.writestr("EDOPro HD Sync/EDOPro-HD-Sync", program)
    else:
        asset = release.PORTABLE
        with tarfile.open(tmp_path / asset, "w:gz") as archive:
            exe = tarfile.TarInfo("EDOPal/EDOPal")
            exe.size, exe.mode = len(program), 0o755
            archive.addfile(exe, io.BytesIO(program))
    digest = "0" * 64 if kind == "corrupt" else release.sha256(tmp_path / asset)
    metadata = {"tag_name": tag, "assets": [{"name": asset, "digest": f"sha256:{digest}"}]}
    (tmp_path / "metadata").write_text(json.dumps(metadata))
    api = "https://api.github.com/repos/cntrl-alt-lenny/EDOPal/releases/latest"
    url = f"https://github.com/cntrl-alt-lenny/EDOPal/releases/download/{tag}/{asset}"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        "#!/bin/bash\nset -eu\nurl=${@: -3:1}\nout=${@: -1}\n"
        f'case "$url" in\n  "{api}") cp "$DATA/metadata" "$out";;\n'
        f'  "{url}") cp "$DATA/{asset}" "$out";;\n  *) exit 2;;\nesac\n'
    )
    fake_curl.chmod(0o755)
    (tmp_path / "temp").mkdir()
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "DATA": str(tmp_path),
        "TMPDIR": str(tmp_path / "temp"),
        "EDOPAL_TEST_LAUNCH": str(marker),
    }
    result = subprocess.run([release.ROOT / "tools/try-release.sh", "two words"], cwd=tmp_path, env=env, timeout=10)
    assert not list((tmp_path / "temp").iterdir())
    if kind == "corrupt":
        assert result.returncode != 0
        assert not marker.exists()
    else:
        assert result.returncode == 0
        directory, count, arg = marker.read_text().splitlines()
        assert Path(directory).parent == tmp_path / "temp"
        assert (count, arg) == ("1", "two words")
