"""Build one release zip, or exercise its extracted contents on the current host."""

import argparse
import os
import plistlib
import statistics
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from zipfile import ZIP_DEFLATED, ZipFile

from config import APP_DATA_NAME, APP_NAME, app_version
from tools.qt_payload import QT_MODULES, plugin_paths

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PLATFORM = {"win32": "Windows", "linux": "Linux", "darwin": "macOS"}[sys.platform]
NAME = os.environ.get("APP_FILE", APP_NAME)
EXE = NAME + (".exe" if sys.platform == "win32" else "")
ARCHIVE = DIST / f"{NAME}-{PLATFORM}-v{app_version()}.zip"
LIMIT_MB = {"Windows": 80, "macOS": 105, "Linux": 120}
SKINS = {f"{skin}.zip" for skin in ("abyss", "moon", "nebula", "ristretto")}
BUNDLE_ID = "com.cntrl-alt-lenny.edopro-hd-sync"
FORBIDDEN = (
    "webengine",
    "qt6qml",
    "qtqml",
    "qt6multimedia",
    "qtmultimedia",
    "qt6quick",
    "qtquick",
    "/qml/",
    "qmltooling",
    "tkinter",
    "_tk_data",
    "_tcl_data",
    "/tk.framework/",
    "/tcl.framework/",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def check_exclusions(paths: Iterable[str]) -> None:
    for path in paths:
        lower = path.replace("\\", "/").lower()
        check(
            not any(word in "/" + lower for word in FORBIDDEN)
            and not lower.endswith((".qml", ".qmlc")),
            f"Unwanted runtime file: {path}",
        )
        check(lower.rsplit("/", 1)[-1] not in SKINS, f"Bundled on-demand skin: {path}")


def inspect_windows_resources(exe: Path) -> None:
    import pefile

    with pefile.PE(str(exe), fast_load=True) as pe:
        check(pe.OPTIONAL_HEADER.Subsystem == 2, "Windows executable must use GUI subsystem 2")
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        types = {entry.id for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        for resource in ("RT_ICON", "RT_GROUP_ICON", "RT_VERSION"):
            check(pefile.RESOURCE_TYPE[resource] in types, f"Missing Windows resource {resource}")
        entries = {
            key.decode(): value.decode()
            for group in pe.FileInfo
            for info in group
            if info.Key == b"StringFileInfo"
            for table in info.StringTable
            for key, value in table.entries.items()
        }
        for key, value in {
            "ProductName": APP_NAME,
            "FileDescription": APP_NAME,
            "OriginalFilename": EXE,
            "InternalName": NAME,
            "ProductVersion": app_version(),
            "FileVersion": app_version(),
        }.items():
            check(entries.get(key) == value, f"Incorrect Windows {key}: {entries.get(key)!r}")
        print(
            f"Verified Windows GUI subsystem, icon group and {APP_NAME} {app_version()} version resource"
        )


def inspect_payload(exe: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    size = exe.stat().st_size
    print(f"{exe.name}: {size:,} bytes")
    check(size < LIMIT_MB[PLATFORM] * 1_000_000, f"Executable exceeds {LIMIT_MB[PLATFORM]} MB")
    archive = CArchiveReader(str(exe))
    names = {n.replace("\\", "/").lower() for n in archive.toc}
    modules = archive.open_embedded_archive("PYZ.pyz").toc
    check_exclusions(names | set(modules))
    check("PySide6.support.deprecated" in modules, "Missing PySide compatibility module")
    bindings = {
        n.split("/")[1].split(".")[0]
        for n in names
        if n.startswith("pyside6/qt") and n.count("/") == 1 and n.endswith((".pyd", ".so"))
    }
    check(bindings == {m.lower() for m in QT_MODULES}, f"Unexpected Qt bindings: {bindings}")
    expected = {p.as_posix().lower() for p in plugin_paths(sys.platform)}
    actual = {n.split("/plugins/", 1)[1] for n in names if "/plugins/" in n}
    check(
        expected == actual,
        f"Qt plugin mismatch: missing {expected - actual}, extra {actual - expected}",
    )
    for path in (
        "certifi/cacert.pem",
        "assets/appearance/shared.zip",
        "assets/appearance/inter-ofl.txt",
    ):
        check(path in names, f"Missing runtime data: {path}")
    check(any(n.startswith("_cffi_backend.") for n in names), "Missing miniaudio CFFI backend")
    if sys.platform == "win32":
        inspect_windows_resources(exe)
    print("Largest embedded entries (compressed bytes):")
    for name, entry in sorted(archive.toc.items(), key=lambda pair: pair[1][1], reverse=True)[:10]:
        print(f"  {entry[1]:>10,}  {name}")


def readme() -> str:
    platform = "MacOS" if PLATFORM == "macOS" else PLATFORM
    text = (ROOT / "release_assets" / f"ReadMe-{platform}.txt").read_text(encoding="utf-8")
    return (
        text.replace("@APP_NAME@", APP_NAME)
        .replace("@APP_FILE@", NAME)
        .replace("@APP_DATA_NAME@", APP_DATA_NAME)
    )


def build() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "tools/release.spec"],
        cwd=ROOT,
        check=True,
    )
    if sys.platform == "darwin":
        bundle = DIST / (NAME + ".app")
        inspect_payload(bundle / "Contents/MacOS" / EXE)
        (bundle / "Contents/Resources/ReadMe.txt").write_text(readme(), encoding="utf-8")
        subprocess.run(["codesign", "--force", "--sign", "-", bundle], check=True)
        ARCHIVE.unlink(missing_ok=True)
        subprocess.run(["ditto", "-c", "-k", "--keepParent", bundle, ARCHIVE], check=True)
    else:
        inspect_payload(DIST / EXE)
        with ZipFile(ARCHIVE, "w", ZIP_DEFLATED) as archive:
            archive.write(DIST / EXE, NAME + "/" + EXE)
            archive.writestr(NAME + "/ReadMe.txt", readme().replace("\n", os.linesep))
    print(f"{ARCHIVE.name}: {ARCHIVE.stat().st_size:,} bytes")


def extract(archive: Path, directory: Path) -> Path:
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", archive, directory], check=True)
        bundle = directory / (NAME + ".app")
        check(set(directory.iterdir()) == {bundle}, "Expected exactly one Finder app")
        check_exclusions(p.relative_to(bundle).as_posix() for p in bundle.rglob("*"))
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            plist = plistlib.load(stream)
        for key, value in {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": EXE,
            "CFBundleIconFile": "app-icon.icns",
            "CFBundlePackageType": "APPL",
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": app_version(),
            "LSMinimumSystemVersion": "14.4",
        }.items():
            check(plist.get(key) == value, f"Wrong {key}: {plist.get(key)!r}")
        exe = bundle / "Contents/MacOS" / EXE
        check((bundle / "Contents/Resources/app-icon.icns").is_file(), "Missing Finder icon")
        instructions = bundle / "Contents/Resources/ReadMe.txt"
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--all-architectures", bundle],
            check=True,
        )
        subprocess.run(["lipo", exe, "-verify_arch", "arm64", "x86_64"], check=True)
    else:
        with ZipFile(archive) as zipped:
            check(
                sorted(zipped.namelist()) == sorted([f"{NAME}/{EXE}", f"{NAME}/ReadMe.txt"]),
                f"Unexpected zip entries: {zipped.namelist()}",
            )
            if sys.platform == "win32":
                zipped.extractall(directory)
        if sys.platform == "linux":
            # Exercise the permissions the user's unzip will restore.
            subprocess.run(["unzip", "-q", archive, "-d", directory], check=True)
        exe = directory / NAME / EXE
        instructions = exe.parent / "ReadMe.txt"
    check(instructions.read_text(encoding="utf-8") == readme(), "Incorrect platform ReadMe")
    check(exe.is_file() and os.access(exe, os.X_OK), f"Missing or non-executable app: {exe}")
    return exe


def probe(exe: Path, state: Path, flag: str, arch: str | None, platform: str) -> float:
    cmd = ["arch", f"-{arch}"] if arch else []
    cmd += [
        str(exe),
        flag,
        "--quiet",
        "--no-pause",
        "--config",
        str(state / "config.json"),
        "--edopro-path",
        str(state / "ProjectIgnis"),
    ]
    env = {**os.environ, "QT_QPA_PLATFORM": platform, "PYTHONIOENCODING": "utf-8"}
    start = perf_counter_ns()
    try:
        result = subprocess.run(cmd, cwd=state, env=env, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{flag} timed out after 60 s ({platform}, {arch or PLATFORM})") from exc
    elapsed = (perf_counter_ns() - start) / 1e9
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    print(
        f"{flag} ({platform}, {arch or PLATFORM}): {elapsed:.3f} s, exit {result.returncode}\n{output}",
        flush=True,
    )
    check(result.returncode == 0, f"{flag} failed on {platform} ({arch or PLATFORM})")
    if flag == "--gui-smoke-test":
        if arch:
            check(f"Process architecture: {arch}" in output, f"Window did not run under {arch}")
        check(
            f"Qt platform: {platform}" in output and "GUI smoke test passed." in output,
            f"Missing window verification for {platform}",
        )
        prefix = "First visible window perf_counter_ns="
        stamps = [
            int(line.removeprefix(prefix))
            for line in output.splitlines()
            if line.startswith(prefix)
        ]
        check(len(stamps) == 1, "Missing first-window timestamp")
        shown = (stamps[0] - start) / 1e9
        check(0 < shown <= elapsed, "First-window timestamp outside process lifetime")
        print(f"Launch to first visible window: {shown:.3f} s")
        return shown
    check("All checks passed." in output, "Missing health-check results")
    return elapsed


def verify(repeat: int) -> None:
    check(repeat >= 1, "--repeat must be at least 1")
    platform = (
        os.environ.get("QT_QPA_PLATFORM")
        or {"Windows": "windows", "macOS": "cocoa", "Linux": "xcb"}[PLATFORM]
    )
    (ROOT / "build").mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT / "build") as temp:
        root = Path(temp)
        extracted = root / "extracted"
        extracted.mkdir()
        state = root / "state"
        (state / "ProjectIgnis/pics").mkdir(parents=True)
        exe = extract(ARCHIVE, extracted)
        for arch in ("arm64", "x86_64") if sys.platform == "darwin" else (None,):
            times = [probe(exe, state, "--gui-smoke-test", arch, platform) for _ in range(repeat)]
            if len(times) > 1:
                print(
                    f"First window: {times[0]:.3f} s on first launch; warm median {statistics.median(times[1:]):.3f} s"
                )
            probe(exe, state, "--health-check", arch, platform)
        inspect_payload(exe)
        print(f"Verified {ARCHIVE.name}: {ARCHIVE.stat().st_size:,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--repeat", type=int, default=1, help="GUI launches per architecture")
    args = parser.parse_args()
    if args.action == "build":
        build()
    else:
        verify(args.repeat)


if __name__ == "__main__":
    main()
