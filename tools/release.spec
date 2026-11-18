# PyInstaller executes this file with its build objects and SPECPATH in scope.
import os
import pkgutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import PySide6
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).parent
qt_pin = "PySide6-Essentials==" + version("PySide6-Essentials")
if qt_pin not in (root / "requirements.txt").read_text().splitlines():
    raise SystemExit(f"Installed {qt_pin} does not match requirements.txt")
sys.path.insert(0, str(root))
from config import APP_NAME, app_version
from tools.build_icon import build_icons
from tools.qt_payload import QT_MODULES, plugin_paths

name = os.environ.get("APP_FILE", APP_NAME)
stage = root / "build"
build_icons(stage)
qt = Path(PySide6.__file__).parent
plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
binaries = []
for relative in plugin_paths(sys.platform):
    source = plugins / relative
    if not source.is_file():
        raise SystemExit(f"Missing Qt plugin: {source}")
    if sys.platform.startswith("linux"):
        deps = subprocess.check_output(["ldd", source], text=True)
        if "not found" in deps:
            raise SystemExit(f"Unresolved Qt dependency for {source}:\n{deps}")
    binaries.append((str(source), str(source.parent.relative_to(qt.parent))))

# Replace the broad Qt module hooks before native dependency analysis. The
# stock base hook still collects shiboken and the Windows OpenGL fallback.
hooks = stage / "qt-hooks"
hooks.mkdir(exist_ok=True)
for module in QT_MODULES:
    (hooks / f"hook-PySide6.{module}.py").write_text("# Plugins supplied by release.spec.\n")
exclude = ["tkinter", "_tkinter", "gui", "PIL", "PyQt5", "PyQt6", "PySide2", "pytest"]
exclude += [
    "PySide6." + mod.name
    for mod in pkgutil.iter_modules([str(qt)])
    if mod.name.startswith("Qt") and mod.name not in QT_MODULES
]
datas = [(str(root / "VERSION"), "."), (str(stage / "app-icon.png"), "assets")]
for tree in ("extras", "music", "window", "formats"):
    datas.append((str(root / "assets" / tree), "assets/" + tree))
for file in ("shared.zip", "Inter-OFL.txt", "README.md"):
    datas.append((str(root / "assets/appearance" / file), "assets/appearance"))
for dist in ("PySide6-Essentials", "shiboken6"):
    datas += copy_metadata(dist)
datas += collect_data_files("certifi")
a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "_cffi_backend",
        "PySide6.support.deprecated",
        *("PySide6." + m for m in QT_MODULES),
    ],
    hookspath=[str(hooks)],
    excludes=exclude,
)
if sys.platform == "darwin":
    # PyInstaller verifies extensions; also check the frameworks, Shiboken and plugins.
    for dest, src, kind in a.binaries:
        if kind in ("EXTENSION", "BINARY") and dest.startswith(("PySide6/", "shiboken6/")):
            subprocess.run(["lipo", src, "-verify_arch", "arm64", "x86_64"], check=True)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=name,
    console=False,
    upx=False,
    target_arch="universal2" if sys.platform == "darwin" else None,
    icon=str(stage / ("app-icon.icns" if sys.platform == "darwin" else "app-icon.ico")),
)
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=name + ".app",
        icon=str(stage / "app-icon.icns"),
        bundle_identifier="com.cntrl-alt-lenny.edopro-hd-sync",
        version=app_version(),
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": app_version(),
            "LSMinimumSystemVersion": "14.4",
        },
    )
