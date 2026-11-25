import runpy
import sys
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QLibraryInfo

from tools.build_icons import ICO_SIZES, build_icons

ROOT = Path(__file__).resolve().parents[2]
# The spec's folder is also named packaging; import by path to avoid
# shadowing the third-party packaging distribution used by pip and pytest.
PAYLOAD = runpy.run_path(str(ROOT / "packaging/qt_payload.py"))


def test_platform_plugins_ship_in_essentials():
    directory = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    paths = PAYLOAD["plugin_paths"](sys.platform)
    assert any("qoffscreen" in path.name for path in paths)
    assert all((directory / path).is_file() for path in paths)


@pytest.mark.parametrize(
    ("platform", "unwanted"),
    [
        ("darwin", "PySide6/Qt/lib/QtQuick.framework/Versions/A/QtQuick"),
        ("linux", "PySide6/Qt/lib/libQt6Multimedia.so.6"),
        ("win32", r"PySide6\Qt6WebEngineCore.dll"),
    ],
)
def test_inventory_rejects_native_payload_and_missing_plugins(platform, unwanted):
    modules = [f"PySide6/{module}.{'pyd' if platform == 'win32' else 'abi3.so'}" for module in PAYLOAD["QT_MODULES"]]
    plugins = [f"PySide6/Qt/plugins/{p.as_posix()}" for p in PAYLOAD["plugin_paths"](platform)]
    verify = PAYLOAD["verify_inventory"]
    verify(modules + plugins, platform)
    with pytest.raises(ValueError, match="Unwanted"):
        verify(modules + plugins + [unwanted], platform)
    with pytest.raises(ValueError, match="Qt plugins"):
        verify(modules + plugins[1:], platform)
    with pytest.raises(ValueError, match="Qt bindings"):
        verify(modules[1:] + plugins, platform)


def test_launcher_icons_include_small_and_retina_frames(tmp_path):
    build_icons(tmp_path)
    with Image.open(tmp_path / "app-icon.ico") as icon:
        assert icon.ico.sizes() == {(size, size) for size in ICO_SIZES}
        assert icon.ico.getimage((16, 16)).getbbox()
    with Image.open(tmp_path / "app-icon.icns") as icon:
        assert icon.size == (1024, 1024)
        assert icon.convert("RGBA").getbbox()
