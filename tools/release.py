"""Package a native PyInstaller onedir, verify extracted downloads, or hash all four release assets."""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RELEASE = DIST / "release"
APPIMAGE = "EDOPal-linux-x86_64.AppImage"
PORTABLE = "EDOPal-linux-x86_64.tar.gz"
WINDOWS = "EDOPal-windows-x86_64.zip"
MACOS = "EDOPal-macos-universal2.zip"
ASSETS = {"linux": (APPIMAGE, PORTABLE), "win32": (WINDOWS,), "darwin": (MACOS,)}
RUNTIME_URL = "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256 = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
DOWNLOAD_TARGET_MB = {"linux": 95, "win32": 60, "darwin": 80}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def linux_appimage(onedir: Path, stage: Path) -> None:
    appdir = stage / "EDOPal.AppDir"
    shutil.copytree(onedir, appdir, symlinks=True)
    (appdir / "AppRun").symlink_to("EDOPal")
    shutil.copy2(ROOT / "packaging/EDOPal.desktop", appdir)
    shutil.copy2(ROOT / "packaging/appimage-runtime-LICENSE.txt", appdir)
    shutil.copy2(ROOT / "src/edopal/assets/app-icon.png", appdir / "edopal.png")
    (appdir / ".DirIcon").symlink_to("edopal.png")

    squash = stage / "payload.squashfs"
    # This runtime includes zstd and zlib decoders.
    subprocess.run(
        ["mksquashfs", appdir, squash, "-comp", "zstd", "-b", "1M", "-noappend", "-all-root", "-no-xattrs"], check=True
    )
    # The upstream static ELF computes the SquashFS offset from its own length.
    image = stage / APPIMAGE
    with urllib.request.urlopen(RUNTIME_URL, timeout=60) as response, image.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(image) != RUNTIME_SHA256:
        raise ValueError("AppImage runtime SHA-256 does not match the pinned 20251108 release")
    with squash.open("rb") as data, image.open("ab") as output:
        shutil.copyfileobj(data, output)
    image.chmod(0o755)


def package() -> None:
    if sys.platform != "darwin" and platform.machine().lower() not in {"x86_64", "amd64"}:
        raise SystemExit("This release targets x86-64 on Linux and Windows")
    onedir = DIST / "EDOPal"
    source = DIST / "EDOPal.app" if sys.platform == "darwin" else onedir
    exe = {"linux": "EDOPal", "win32": "EDOPal.exe", "darwin": "Contents/MacOS/EDOPal"}[sys.platform]
    if not (source / exe).is_file():
        raise SystemExit("Build packaging/edopal.spec with PyInstaller first")
    unpacked = sum(path.stat().st_size for path in source.rglob("*") if not path.is_symlink() and path.is_file())
    print(f"{source.name}  {unpacked:,} unpacked bytes", flush=True)
    RELEASE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DIST) as tmp:
        stage = Path(tmp)
        if sys.platform == "linux":
            with tarfile.open(stage / PORTABLE, "w:gz") as archive:
                archive.add(onedir, arcname="EDOPal")
            linux_appimage(onedir, stage)
        elif sys.platform == "win32":
            with ZipFile(stage / WINDOWS, "w", ZIP_DEFLATED) as archive:
                for path in sorted(onedir.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(DIST))
        elif sys.platform == "darwin":
            subprocess.run(["ditto", "-c", "-k", "--keepParent", source, stage / MACOS], check=True)
        for name in ASSETS[sys.platform]:
            path = (stage / name).replace(RELEASE / name)
            size = path.stat().st_size
            target = DOWNLOAD_TARGET_MB[sys.platform]
            print(f"{name}  {size:,} bytes ({size / 1_000_000:.1f} MB, target <= {target} MB)")
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(summary, "a", encoding="utf-8") as stream:
                    print(f"- `{name}` · {size:,} bytes · {unpacked / 1_000_000:.1f} MB unpacked", file=stream)


def smoke(command: list[str | Path], cwd: Path) -> None:
    env = {**os.environ, "EDOPAL_HOME": str(cwd / "profile"), "EDOPAL_PACKS_DIR": str(cwd / "packs")}
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Start away from the source tree so only the shipped runtime and data are visible.
    for name in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(name, None)
    subprocess.run([*command, "--smoke"], check=True, timeout=45, cwd=cwd, env=env)


def verify_macos(bundle: Path, cwd: Path) -> None:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", "--all-architectures", bundle], check=True)
    # PyInstaller checks extension modules. Check bundled frameworks and other Mach-O libraries too.
    macho = {bytes.fromhex(n) for n in ("feedface", "feedfacf", "cafebabe", "cafebabf")}
    macho.update({magic[::-1] for magic in macho})
    for binary in bundle.rglob("*"):
        if binary.is_symlink() or not binary.is_file():
            continue
        with binary.open("rb") as stream:
            is_macho = stream.read(4) in macho
        if is_macho:
            subprocess.run(["lipo", binary, "-verify_arch", "arm64", "x86_64"], check=True)
    for arch in ("arm64", "x86_64"):
        smoke(["arch", f"-{arch}", bundle / "Contents/MacOS/EDOPal"], cwd)


def verify() -> None:
    with tempfile.TemporaryDirectory(prefix="edopal-download-") as tmp:
        cwd = Path(tmp)
        if sys.platform == "linux":
            with tarfile.open(RELEASE / PORTABLE) as archive:
                archive.extractall(cwd, filter="data")
            smoke([cwd / "EDOPal/EDOPal"], cwd)
            # This runs the actual type-2 loader and works with /dev/fuse unavailable.
            smoke([RELEASE / APPIMAGE, "--appimage-extract-and-run"], cwd)
        elif sys.platform == "win32":
            with ZipFile(RELEASE / WINDOWS) as archive:
                archive.extractall(cwd)
            smoke([cwd / "EDOPal/EDOPal.exe"], cwd)
        elif sys.platform == "darwin":
            subprocess.run(["ditto", "-x", "-k", RELEASE / MACOS, cwd], check=True)
            verify_macos(cwd / "EDOPal.app", cwd)
    for name in ASSETS[sys.platform]:
        print(f"Verified {name}  {(RELEASE / name).stat().st_size:,} bytes")


def checksums(directory: Path) -> None:
    expected = {name for group in ASSETS.values() for name in group}
    found = {path.name for path in directory.iterdir() if path.name != "SHA256SUMS"}
    if found != expected:
        raise ValueError(f"Release files differ: missing {sorted(expected - found)}, unexpected {sorted(found - expected)}")
    lines = [f"{sha256(directory / name)}  {name}\n" for name in sorted(expected)]
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="ascii", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("package", "verify", "checksums"))
    action = parser.parse_args().action
    if action == "package":
        package()
    elif action == "verify":
        verify()
    else:
        checksums(RELEASE)
