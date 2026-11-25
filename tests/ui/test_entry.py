import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from edopal import __version__, app
from edopal.core.feature import FeatureSpec


def test_registry_sorts_and_substitutes_missing_features(monkeypatch):
    def load(name):
        if name.endswith("music"):
            return SimpleNamespace()  # An in-progress namespace package.
        if name.endswith("themes"):
            raise ModuleNotFoundError(name=name)
        feature = name.rsplit(".", 1)[1]
        return SimpleNamespace(register=lambda _: FeatureSpec(feature, feature, feature, 1, lambda _: None))

    monkeypatch.setattr(app.importlib, "import_module", load)
    specs = app.load_features(None)
    assert [s.id for s in specs] == ["hdcards", "formats", "themes", "music"]
    assert [s.label for s in specs[-2:]] == ["Themes", "Music"]


def test_registry_exposes_missing_feature_dependencies(monkeypatch):
    def fail(name):
        raise ModuleNotFoundError("missing dependency", name="dependency")

    monkeypatch.setattr(app.importlib, "import_module", fail)
    with pytest.raises(ModuleNotFoundError, match="missing dependency"):
        app.load_features(None)


def test_entry_version_and_real_window_smoke(tmp_path):
    src = Path(app.__file__).resolve().parents[1]
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONPATH": str(src),
        "EDOPAL_HOME": str(tmp_path / "profile"),
        "EDOPAL_PACKS_DIR": str(tmp_path / "packs"),
    }
    for flag in ("--version", "--smoke"):
        result = subprocess.run(
            [sys.executable, "-m", "edopal.app", flag],
            check=True,
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if flag == "--version":
            assert result.stdout.strip() == __version__
    assert not (tmp_path / "profile/cache/releases.sqlite3").exists()
