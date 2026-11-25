import json
import shutil
from pathlib import Path

import pytest

from edopal.core.install import Install, RunState
from edopal.core.safe_storage import SafeStorage
from edopal.core.storage import GameRunningError
from edopal.dev.preview import make_core
from edopal.plugins.decktracker import before_launch, get_runtime, on_enabled_changed, register
from edopal.plugins.decktracker.setup import PLUGIN_ID, USER_CONFIG, Server, install_entry, servers

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


def test_enable_launch_without_visiting_page_and_undo(tmp_path, qtbot, monkeypatch):
    import importlib

    from edopal.plugins.decktracker.relay import bind_loopback

    tracker_module = importlib.import_module("edopal.plugins.decktracker.runtime")
    game = tmp_path / "game"
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/game", game)
    (game / "config/configs.json").write_text(json.dumps({"servers": [EU.__dict__]}))
    core = make_core(game, tmp_path / "packs", tmp_path / "profile")
    core.storage = SafeStorage(core.paths, lambda: RunState.CLOSED)
    sock = bind_loopback(0)
    monkeypatch.setattr(tracker_module, "bind_loopback", lambda: sock)
    spec = register(core)
    assert not spec.gallery_entry().pack_id
    assert "server list" in spec.gallery_entry().detail
    before_launch(core, core.installs.launch, pytest.fail)
    assert core.installs.launched == 1 and not (game / USER_CONFIG).exists()
    core.settings.set("plugins", PLUGIN_ID, True)
    on_enabled_changed(core, True)
    tracker = get_runtime(core)
    assert (game / USER_CONFIG).exists() and tracker.service is None
    assert tracker.info[2] == EU
    before_launch(core, core.installs.launch, pytest.fail)
    qtbot.waitUntil(lambda: core.installs.launched == 2)
    assert tracker.service.ready.is_set() and not tracker.overlay.isVisible()
    assert len(tuple(core.storage.history(core.installs.current()))) == 1
    service = tracker.service
    spec.on_game_exited(core)
    qtbot.waitUntil(lambda: not service.thread.is_alive())
    assert tracker.service is None
    page = spec.page(core)
    qtbot.addWidget(page)
    page.undo.click()
    assert not (game / USER_CONFIG).exists()
    before_launch(core, core.installs.launch, pytest.fail)
    assert core.installs.launched == 3 and not (game / USER_CONFIG).exists()


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


def test_launch_recalls_the_duel_endpoint_for_servers_sharing_a_room_list(tmp_path, qtbot):
    from dataclasses import replace

    first, second = EU, replace(EU, name="Second", duelport=5555)
    install = Install(tmp_path / "game")
    install.path("config").mkdir(parents=True)
    install.path("config/configs.json").write_text(json.dumps({"servers": [first.__dict__, second.__dict__]}))
    core = make_core(install.root, tmp_path / "packs", tmp_path / "profile")
    tracker = get_runtime(core)
    qtbot.addWidget(tracker.overlay)
    tracker.prepare(server="Second")
    assert tracker.info[2] == second
    tracker.inspect()
    assert tracker.info[2] == second
    tracker.prepare()
    assert tracker.info[2] == second


def test_early_launch_waits_for_enable_and_error_can_retry(tmp_path, qtbot, monkeypatch):
    from edopal.core.jobs import JobContext
    from edopal.plugins.decktracker import runtime as tracker_module
    from edopal.plugins.decktracker.relay import bind_loopback

    class DeferredJobs:
        def __init__(self):
            self.queue = []

        def run(self, work, on_done, on_error):
            self.queue.append((work, on_done, on_error))

        def finish(self):
            work, done, failed = self.queue.pop(0)
            try:
                value = work(JobContext())
            except Exception as exc:
                failed(exc)
            else:
                done(value)

    install = Install(tmp_path / "game")
    install.path("config").mkdir(parents=True)
    install.path("config/configs.json").write_text(json.dumps({"servers": [EU.__dict__]}))
    core = make_core(install.root, tmp_path, tmp_path / "profile")
    core.jobs = DeferredJobs()
    core.settings.set("plugins", PLUGIN_ID, True)
    errors = []
    on_enabled_changed(core, True)
    before_launch(core, core.installs.launch, errors.append)
    assert len(core.jobs.queue) == 1 and not install.path(USER_CONFIG).exists()
    core.jobs.finish()
    assert install.path(USER_CONFIG).exists() and core.installs.launched == 0

    def occupied():
        raise OSError("Port already in use")

    monkeypatch.setattr(tracker_module, "bind_loopback", occupied)
    core.jobs.finish()
    assert errors and "Port already in use" in errors[0] and core.installs.launched == 0
    monkeypatch.setattr(tracker_module, "bind_loopback", lambda: bind_loopback(0))
    before_launch(core, core.installs.launch, errors.append)
    core.jobs.finish()
    core.jobs.finish()
    qtbot.waitUntil(lambda: core.installs.launched == 1)
    tracker = get_runtime(core)
    qtbot.addWidget(tracker.overlay)
    service = tracker.service
    on_enabled_changed(core, False)
    qtbot.waitUntil(lambda: not service.thread.is_alive())
    assert not tracker.overlay.isVisible() and service.retired.is_set()
