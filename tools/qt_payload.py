"""Selected Qt 6 wheel payload for the one-file release."""

from pathlib import PurePosixPath

QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtSvg")
PLUGINS = {
    "win32": ("platforms/qwindows", "platforms/qoffscreen", "styles/qmodernwindowsstyle"),
    "darwin": ("platforms/qcocoa", "styles/qmacstyle"),
    "linux": (
        "platforms/qxcb",
        "platforms/qwayland",
        "xcbglintegrations/qxcb-egl-integration",
        "xcbglintegrations/qxcb-glx-integration",
        "wayland-shell-integration/xdg-shell",
        "wayland-decoration-client/bradient",
        "wayland-graphics-integration-client/qt-plugin-wayland-egl",
        "platformthemes/qxdgdesktopportal",
    ),
}


def plugin_paths(platform: str) -> list[PurePosixPath]:
    prefix, suffix = {"win32": ("", ".dll"), "darwin": ("lib", ".dylib"), "linux": ("lib", ".so")}[
        platform
    ]
    codecs = ("imageformats/qjpeg", "imageformats/qico", "imageformats/qwebp")
    paths = [PurePosixPath(path) for path in (*PLUGINS[platform], *codecs)]
    return [path.with_name(prefix + path.name + suffix) for path in paths]
