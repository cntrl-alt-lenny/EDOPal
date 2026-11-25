import sys
from pathlib import Path
from types import SimpleNamespace

from tools import macos_universal


def test_only_single_arch_wheels_need_replacement(monkeypatch):
    def wheel(name, tag):
        return SimpleNamespace(metadata={"Name": name}, version="1.5", read_text=lambda _: f"Tag: {tag}")

    wheels = [
        wheel("Pillow", "cp312-cp312-macosx_11_0_arm64"),
        wheel("shiboken6", "cp310-abi3-macosx_13_0_universal2"),
        wheel("certifi", "py3-none-any"),
    ]
    monkeypatch.setattr(macos_universal.metadata, "distributions", lambda: wheels)
    assert macos_universal.thin_wheel_requirements() == ["Pillow==1.5"]


def test_prepare_matches_wheels_by_project(monkeypatch):
    merged = []
    installed = []

    def fuse(arm, intel, destination):
        assert arm.name.split("-")[0] == intel.name.split("-")[0]
        assert "arm64" in arm.name and "x86_64" in intel.name
        merged.append(arm.name.split("-")[0])
        return destination / arm.name.replace("arm64", "universal2")

    def pip(cmd, *, check):
        assert check
        if "install" in cmd:
            installed.extend(path.name for path in cmd if isinstance(path, Path))
            return
        assert cmd[-2:] == ["Pillow==13.0.0", "cffi==2.1.1"]
        dest = Path(cmd[cmd.index("--dest") + 1])
        dest.mkdir()
        (dest / f"pillow-13.0.0-cp312-cp312-macosx_15_0_{dest.name}.whl").touch()
        # If pip selects a compatible universal wheel, it can be used directly.
        (dest / "cffi-2.1.1-cp312-cp312-macosx_11_0_universal2.whl").touch()

    monkeypatch.setitem(sys.modules, "delocate.fuse", SimpleNamespace(fuse_wheels=fuse))
    monkeypatch.setattr(macos_universal.subprocess, "run", pip)
    monkeypatch.setattr(macos_universal, "thin_wheel_requirements", lambda: ["Pillow==13.0.0", "cffi==2.1.1"])
    macos_universal.prepare()
    assert merged == ["pillow"]
    assert set(installed) == {
        "pillow-13.0.0-cp312-cp312-macosx_15_0_universal2.whl",
        "cffi-2.1.1-cp312-cp312-macosx_11_0_universal2.whl",
    }
