"""Qt Python bindings and dynamically loaded plugins used by the desktop app."""

import re
from collections.abc import Iterable
from pathlib import Path

QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtSvg")
COMMON_PLUGINS = (
    "platforms/qoffscreen",
    "iconengines/qsvgicon",
    "imageformats/qsvg",
    "imageformats/qjpeg",
    "imageformats/qwebp",
    "imageformats/qico",
)
PLATFORM_PLUGINS = {
    "linux": (
        "platforms/qxcb",
        "platforms/qwayland",
        "platformthemes/qxdgdesktopportal",
        "xcbglintegrations/qxcb-egl-integration",
        "xcbglintegrations/qxcb-glx-integration",
        "wayland-shell-integration/xdg-shell",
        "wayland-decoration-client/bradient",
        "wayland-graphics-integration-client/qt-plugin-wayland-egl",
    ),
    "win32": ("platforms/qwindows", "styles/qmodernwindowsstyle"),
    "darwin": ("platforms/qcocoa", "styles/qmacstyle"),
}


def plugin_paths(platform: str) -> list[Path]:
    prefix, suffix = {"linux": ("lib", ".so"), "win32": ("", ".dll"), "darwin": ("lib", ".dylib")}[platform]
    plugins = [Path(name) for name in (*COMMON_PLUGINS, *PLATFORM_PLUGINS[platform])]
    return [path.with_name(prefix + path.name + suffix) for path in plugins]


def verify_inventory(destinations: Iterable[str], platform: str) -> None:
    """Inspect PyInstaller's resolved destinations, including transitive native libraries."""
    paths = {name.replace("\\", "/").lower() for name in destinations}
    # Linux and Windows library names include a Qt major (Qt6Quick); macOS
    # frameworks and the corresponding Python modules use the name QtQuick.
    excluded = re.compile(
        r"qml|qt6?(quick|webengine|multimedia)|tkinter|_(tk|tcl)_data|(?:^|/)(?:lib)?(?:tk|tcl)(?:\d|\.framework)"
    )
    unexpected = sorted(path for path in paths if excluded.search(path))
    if unexpected:
        raise ValueError(f"Unwanted Qt or Tk payload: {unexpected}")

    bindings = {
        path.split("/")[1].split(".")[0]
        for path in paths
        if path.startswith("pyside6/qt") and path.count("/") == 1 and path.endswith((".pyd", ".so"))
    }
    expected = {module.lower() for module in QT_MODULES}
    if bindings != expected:
        raise ValueError(f"Qt bindings: missing {expected - bindings}, unexpected {bindings - expected}")
    plugins = {p.split("/plugins/", 1)[1] for p in paths if p.startswith("pyside6/") and "/plugins/" in p}
    expected = {p.as_posix().lower() for p in plugin_paths(platform)}
    if plugins != expected:
        raise ValueError(f"Qt plugins: missing {expected - plugins}, unexpected {plugins - expected}")
