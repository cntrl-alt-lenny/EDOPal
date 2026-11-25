import json
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from edopal.core.install import Install, RunState
from edopal.core.safe_storage import SafeStorage
from edopal.core.storage import GameRunningError
from edopal.dev.preview import make_core
from edopal.plugins.decktracker import register
from edopal.plugins.decktracker.setup import PLUGIN_ID, USER_CONFIG, Server, install_entry, servers
from edopal.ui.shell import ShellWindow

EU = Server("EU Casual", "example.test", 7912, "rooms.example.test", 443, "https")


def test_preserve_config_idempotence_running_guard_undo_and_conflict(tmp_path):
    install = Install(tmp_path / "game")
    install.path("config").mkdir(parents=True)
    original = b'{"urls":[7],"servers":[{"name":"custom","note":42}]}\n'
    path = install.path(USER_CONFIG)
    path.write_bytes(original)
    state = RunState.RUNNING
    storage = SafeStorage(tmp_path / "data", lambda: state)
    with pytest.raises(GameRunningError):
        install_entry(storage, install, EU)
    assert path.read_bytes() == original
    state = RunState.CLOSED
    install_entry(storage, install, EU)
    installed = json.loads(path.read_bytes())
    assert installed["urls"] == [7]
    assert installed["servers"] == [
        {"name": "custom", "note": 42},
        {
            "name": "EDOPal Relay",
            "address": "127.0.0.1",
            "duelport": 17912,
            "roomaddress": "rooms.example.test",
            "roomlistport": 443,
            "roomlistprotocol": "https",
        },
    ]
    state = RunState.RUNNING
    install_entry(storage, install, EU)
    assert len(tuple(storage.history(install))) == 1
    state = RunState.CLOSED
    assert storage.undo_last(install, PLUGIN_ID).restored == (USER_CONFIG,)
    assert path.read_bytes() == original
    install_entry(storage, install, EU)
    path.write_bytes(b'{"servers":[]}\n')
    assert storage.undo_last(install, PLUGIN_ID).conflicts == (USER_CONFIG,)
    assert path.read_bytes() == b'{"servers":[]}\n'


def test_invalid_config_and_name_collision_are_preserved(tmp_path):
    install = Install(tmp_path / "game")
    install.path("config").mkdir(parents=True)
    storage = SafeStorage(tmp_path / "data", lambda: RunState.CLOSED)
    for doc in ('{"servers":42}', '{"servers":[{"name":"EDOPal Relay","address":"custom"}]}', "[]", "bad"):
        install.path(USER_CONFIG).write_text(doc)
        with pytest.raises(ValueError):
            install_entry(storage, install, EU)
        assert install.path(USER_CONFIG).read_text() == doc


def test_gallery_sidebar_setup_compact_stop_and_undo(tmp_path, qtbot, monkeypatch):
    from edopal.plugins.decktracker import page

    # An ephemeral port avoids interfering with EDOPal running on the developer's desktop.
    from edopal.plugins.decktracker.relay import bind_loopback

    sock = bind_loopback(0)
    port = sock.getsockname()[1]
    monkeypatch.setattr(page, "bind_loopback", lambda: sock)
    game = tmp_path / "game"
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/game", game)
    (game / "config/configs.json").write_text(json.dumps({"servers": [EU.__dict__]}))
    packs = tmp_path / "packs"
    from edopal.plugins.decktracker.bundle import build

    build(packs)
    core = make_core(game, packs, tmp_path / "profile")
    core.storage = SafeStorage(core.paths, lambda: RunState.CLOSED)
    assert servers(core.installs.current()) == [EU]
    window = ShellWindow(core, [register(core)], "plugins")
    qtbot.addWidget(window)
    window.show()
    gallery = window.pages["plugins"]
    assert gallery.cards[PLUGIN_ID].status.text() == "Available"
    gallery.cards[PLUGIN_ID].details.click()
    gallery.drawer.install.click()
    gallery.drawer.enable.click()
    window.navigate(PLUGIN_ID)
    tracker = window.pages[PLUGIN_ID]
    assert tracker.preview[0] == "Fixture"
    assert tracker.view.table.rowCount() > 0
    tracker.start.click()
    qtbot.waitUntil(lambda: "Listening" in tracker.status.text())
    assert json.loads((game / USER_CONFIG).read_text())["servers"][0]["duelport"] == port
    tracker._open_compact()
    assert tracker.compact.isVisible() and tracker.compact.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    tracker.start.click()
    qtbot.waitUntil(lambda: tracker.service is None)
    tracker.undo.click()
    assert not (game / USER_CONFIG).exists()


def test_entry_selector_skips_relay_and_keeps_other_servers(tmp_path):
    install = Install(tmp_path)
    install.path("config").mkdir()
    install.path("config/configs.json").write_text(json.dumps({"servers": [EU.__dict__]}))
    install.path(USER_CONFIG).write_text(json.dumps({"servers": [EU.entry(17912), {"name": "Incomplete"}]}))
    assert servers(install) == [EU]


def test_private_package_matches_catalog_and_manifest(tmp_path):
    import hashlib
    from zipfile import ZipFile

    from edopal.plugins.decktracker.bundle import build

    archive = build(tmp_path)
    (entry,) = json.loads((tmp_path / "catalog.json").read_text())["packs"]
    assert entry["kind"] == "plugin" and entry["id"] == "plugin-deck-tracker"
    assert (entry["sha256"], entry["size"]) == (hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size)
    with ZipFile(archive) as zip_:
        files = json.loads(zip_.read("manifest.json"))["files"]
        assert "decktracker/relay.py" in files
        assert set(zip_.namelist()) == {*files, "manifest.json"}
        assert all(hashlib.sha256(zip_.read(name)).hexdigest() == digest for name, digest in files.items())


def test_accidental_self_route_is_rejected_before_writing(tmp_path):
    install = Install(tmp_path)
    install.path("config").mkdir()
    storage = SafeStorage(tmp_path / "data", lambda: RunState.CLOSED)
    target = Server("Old loopback route", "localhost", 17912, "rooms.example.test", 443, "https")
    with pytest.raises(ValueError, match="itself"):
        install_entry(storage, install, target)
    assert not install.path(USER_CONFIG).exists()
