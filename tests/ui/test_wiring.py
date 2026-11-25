import sys
from types import ModuleType

from edopal.core.fakes import DirectStorage, LocalPacks, StaticInstalls
from edopal.core.wiring import make_core
from edopal.ui.jobs import QtJobRunner


def test_development_fallbacks(monkeypatch, tmp_path, qapp):
    for module in ("installs", "safe_storage", "pack_client"):
        monkeypatch.setitem(sys.modules, f"edopal.core.{module}", None)
    monkeypatch.setenv("EDOPAL_HOME", str(tmp_path))
    monkeypatch.delenv("EDOPAL_PACKS_DIR", raising=False)
    core = make_core()
    assert isinstance(core.installs, StaticInstalls) and core.installs.current() is None
    assert isinstance(core.storage, DirectStorage) and core.storage.scratch == tmp_path / "data"
    assert isinstance(core.packs, LocalPacks) and core.packs.cache == tmp_path / "cache"
    assert isinstance(core.jobs, QtJobRunner)
    core.jobs.shutdown()


def test_real_services_and_explicit_local_packs(monkeypatch, tmp_path, qapp):
    class Installs:
        def __init__(self, settings):
            self.settings = settings

        def run_state(self):
            pass

    class Storage:
        def __init__(self, paths, run_state):
            self.paths, self.run_state = paths, run_state

    class Packs:
        def __init__(self, paths, local_dir=None):
            self.cache, self.local_dir = paths.cache, local_dir

    for name, cls_name, cls in [
        ("installs", "RealInstalls", Installs),
        ("safe_storage", "SafeStorage", Storage),
        ("pack_client", "GitHubPacks", Packs),
    ]:
        module = ModuleType(f"edopal.core.{name}")
        setattr(module, cls_name, cls)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("EDOPAL_HOME", str(tmp_path))
    monkeypatch.delenv("EDOPAL_PACKS_DIR", raising=False)
    core = make_core()
    assert isinstance(core.installs, Installs) and core.installs.settings is core.settings
    assert isinstance(core.storage, Storage) and core.storage.run_state == core.installs.run_state
    assert isinstance(core.packs, Packs) and core.packs.cache == core.paths.cache
    core.jobs.shutdown()
    monkeypatch.setenv("EDOPAL_PACKS_DIR", str(tmp_path / "checkout"))
    overridden = make_core()
    assert isinstance(overridden.packs, Packs) and overridden.packs.local_dir == tmp_path / "checkout"
    overridden.jobs.shutdown()


def test_real_wiring_verifies_art_and_icon_packs(monkeypatch, tmp_path, qapp):
    import hashlib
    import json

    from edopal.core import pack_client

    # Record the pre-wiring allowance for the other tests that import this module.
    monkeypatch.setattr(pack_client, "_KINDS", pack_client._KINDS)
    packs = tmp_path / "packs"
    entries = []
    for kind in ("art", "icons"):
        key = f"{kind}-test"
        pack = packs / key
        pack.mkdir(parents=True)
        payload = b"test asset"
        (pack / "asset.txt").write_bytes(payload)
        (pack / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "id": key,
                    "kind": kind,
                    "version": "1.0.0",
                    "files": {"asset.txt": hashlib.sha256(payload).hexdigest()},
                }
            )
        )
        entries.append({"id": key, "kind": kind, "name": key, "version": "1.0.0", "url": key, "size": 0, "sha256": ""})
    (packs / "catalog.json").write_text(json.dumps({"schema": 1, "packs": entries}))
    monkeypatch.setenv("EDOPAL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("EDOPAL_PACKS_DIR", str(packs))
    core = make_core()
    try:
        for kind in ("art", "icons"):
            (pack,) = core.packs.catalog(kind)
            path = core.packs.ensure(pack)
            assert (path / "asset.txt").read_bytes() == payload
            assert core.packs.cached(pack.id) == path
    finally:
        core.jobs.shutdown()
