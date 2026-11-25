# Build on the target OS with: python -m PyInstaller --noconfirm packaging/edopal.spec
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import PySide6
from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).resolve().parent
src = root / "src"
sys.path[:0] = [str(src), str(root / "tools"), SPECPATH]
from build_icons import build_icons
from edopal import APP_NAME, __version__
from edopal.app import FEATURES
from qt_payload import QT_MODULES, plugin_paths, verify_inventory

try:
    distribution("PySide6-Addons")
except PackageNotFoundError:
    pass
else:
    raise SystemExit("Build in an environment with PySide6-Essentials and shiboken6 only.")

stage = Path(workpath)
qt_package = Path(PySide6.__file__).resolve().parent
plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
binaries = []
for relative in plugin_paths(sys.platform):
    plugin = plugins / relative
    if not plugin.is_file():
        raise SystemExit(f"The Qt wheel is missing {plugin}")
    binaries.append((str(plugin), str(plugin.parent.relative_to(qt_package.parent))))

# Keep PyInstaller's base hook for Shiboken and platform DLLs. Replace the
# four broad module hooks before native dependency analysis adds plugin deps.
hooks = stage / "qt-hooks"
hooks.mkdir(parents=True, exist_ok=True)
for module in QT_MODULES:
    (hooks / f"hook-PySide6.{module}.py").write_text("# Plugins selected in edopal.spec.\n", encoding="utf-8")

excluded = ["tkinter", "_tkinter", "PyQt5", "PyQt6", "PySide2", "pytest", "edopal.dev"]
excluded += [f"PySide6.{module}" for module in PySide6.__all__ if module not in QT_MODULES]
datas = [(str(root / "LICENSE"), "licenses/edopal")]
# Formats uses generated Python summaries. The pack builder's historical source
# databases and instructions are development inputs.
app_data = collect_data_files("edopal", includes=["assets", "features"], excludes=["features/formats/builder"])
datas += app_data
print(f"Bundling {sum(Path(source).stat().st_size for source, _ in app_data):,} bytes of EDOPal assets and feature data")
for package in ("PySide6-Essentials", "shiboken6"):
    datas += copy_metadata(package)

analysis = Analysis(
    [str(root / "packaging/entrypoint.py")],
    pathex=[str(src)],
    binaries=binaries,
    datas=datas,
    # The registry and CFFI audio backend resolve imports dynamically.
    hiddenimports=[
        *(f"edopal.features.{feature}" for feature in FEATURES),
        *(f"PySide6.{module}" for module in QT_MODULES),
        "PySide6.support.deprecated",
        "_cffi_backend",
        "miniaudio",
        "certifi",
        "aiohttp",
        "PIL.Image",
    ],
    hookspath=[str(hooks)],
    runtime_hooks=[str(root / "packaging/linux_fonts.py")] if sys.platform == "linux" else [],
    excludes=excluded,
)
verify_inventory((dest for dest, _, _ in analysis.binaries + analysis.datas + analysis.pure), sys.platform)

if sys.platform == "linux":
    # Newer desktops may use fontconfig directives unknown to the build host.
    # The runtime hook loads the desktop copy first and keeps this pair in reserve.
    font_libs = {"libfontconfig.so.1", "libfreetype.so.6"}
    analysis.binaries = [
        (f"font-libs/{dest}" if dest in font_libs else dest, source, kind) for dest, source, kind in analysis.binaries
    ]

icon = None
windows_version = None
if sys.platform in {"win32", "darwin"}:
    build_icons(stage)
    icon = str(stage / ("app-icon.ico" if sys.platform == "win32" else "app-icon.icns"))

if sys.platform == "win32":
    from packaging.version import Version
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    release = Version(__version__).release
    numbers = release + (0,) * (4 - len(release))
    fields = {
        "FileDescription": APP_NAME,
        "FileVersion": __version__,
        "InternalName": APP_NAME,
        "OriginalFilename": APP_NAME + ".exe",
        "ProductName": APP_NAME,
        "ProductVersion": __version__,
    }
    windows_version = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in fields.items()])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

exe = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    upx=False,
    icon=icon,
    version=windows_version,
    target_arch="universal2" if sys.platform == "darwin" else None,
)
directory = COLLECT(exe, analysis.binaries, analysis.datas, name=APP_NAME, upx=False)
if sys.platform == "darwin":
    app = BUNDLE(
        directory,
        name=APP_NAME + ".app",
        icon=icon,
        bundle_identifier="com.cntrl-alt-lenny.edopal",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "LSMinimumSystemVersion": "15.0",
        },
    )
