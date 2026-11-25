import asyncio
import shutil
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from edopal.core.install import RunState
from edopal.core.jobs import JobContext
from edopal.core.safe_storage import SafeStorage
from edopal.dev.preview import make_core
from edopal.features.hdcards import artwork, register
from edopal.features.hdcards.catalog import read_catalog
from edopal.features.hdcards.controller import Controller
from edopal.features.hdcards.coverage import Coverage, coverage
from edopal.features.hdcards.sync import sync

DM, BLUE, POT, UMI, ERA = 46986414, 89631139, 55144522, 22702055, 511000824
FIXTURE = Path(__file__).parents[1] / "fixtures/game"


def image(fmt="JPEG", size=(421, 614), color="green"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def core(tmp_path):
    game = tmp_path / "Project Ignis"
    shutil.copytree(FIXTURE, game)
    core = make_core(game, tmp_path / "packs", tmp_path / "home")
    core.storage = SafeStorage(core.paths, core.installs.run_state)
    return core


@pytest.fixture
def cdn(monkeypatch):
    responses, calls = {}, Counter()

    async def fetch(self, printing, url):
        calls[url] += 1
        result = responses.get(url)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result()
        return artwork._asset(result, printing, url) if result else None

    monkeypatch.setattr(artwork.ArtworkClient, "_fetch", fetch)
    return responses, calls


def face(cid):
    return f"{artwork.CDN}/cards/{cid}.jpg"


def run(core, ids, ctx=None, **kwargs):
    game = core.installs.current()
    cat = read_catalog(game)
    return asyncio.run(sync(core, game, cat, coverage(game, cat), ctx or JobContext(), only_ids=set(ids), **kwargs))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_upgrade_visible_png_keep_larger_custom_and_undo_noop(core, cdn):
    game = core.installs.current()
    small = image("PNG", (177, 254))
    large = image("PNG", (600, 800))
    prior = image(size=(210, 300))
    new = image()
    write(game.path(f"pics/{DM}.png"), small)
    write(game.path(f"pics/{DM}.jpg"), prior)
    write(game.path(f"pics/{POT}.png"), large)
    write(game.path(f"expansions/pics/{BLUE}.png"), small)
    responses, calls = cdn
    for cid in (DM, POT, BLUE):
        responses[face(cid)] = new
    first = run(core, [DM, POT, BLUE])
    assert first.downloaded == first.shadowed == 1
    assert not first.errors and not first.unavailable
    assert game.path(f"pics/{DM}.jpg").read_bytes() == new
    assert not game.path(f"pics/{DM}.png").exists()
    assert game.path(f"pics/{POT}.png").read_bytes() == large
    assert not game.path(f"pics/{BLUE}.jpg").exists()
    receipt = core.storage.last_receipt(game, "hdcards")
    assert set(receipt.changed) == {f"pics/{DM}.jpg", f"pics/{DM}.png"}
    assert run(core, [DM, POT, BLUE]).downloaded == 0
    assert calls == {face(DM): 1}  # The installed HD PNG never needs a comparison download.
    assert core.storage.last_receipt(game, "hdcards").id == receipt.id
    core.storage.undo_last(game, "hdcards")
    assert game.path(f"pics/{DM}.png").read_bytes() == small
    assert game.path(f"pics/{DM}.jpg").read_bytes() == prior


def test_deck_only_and_historical_policy(core, cdn):
    game = core.installs.current()
    responses, calls = cdn
    responses[face(DM)] = image()
    responses[face(83555667)] = image(color="red")
    responses[face(ERA)] = image(size=(177, 254))
    write(game.path("deck/Fixture.ydk"), f"#main\n{DM}\n!side\n{ERA}\n".encode())
    controller = Controller(core)
    controller.refresh()
    controller.set_options(True, False)
    controller.start_sync()
    assert controller.state.downloaded == 2 and controller.state.total == 10
    assert game.path(f"pics/{ERA}.jpg").read_bytes() == responses[face(83555667)]
    controller.set_options(True, True)
    controller.start_sync()
    assert controller.state.downloaded == 1 and calls[face(DM)] == 1
    assert game.path(f"pics/{ERA}.jpg").read_bytes() == responses[face(ERA)]
    controller.undo()
    assert game.path(f"pics/{ERA}.jpg").read_bytes() == responses[face(83555667)]
    assert controller.state.detail == "1 file restored"
    controller.start_sync()
    assert controller.state.downloaded == 1
    assert game.path(f"pics/{ERA}.jpg").read_bytes() == responses[face(ERA)]


def test_field_cropped_from_new_face_preserves_published_field(core, cdn):
    game = core.installs.current()
    responses, _ = cdn
    responses[face(UMI)] = image()
    assert run(core, [UMI]).downloaded == 2
    field = game.path(f"pics/field/{UMI}.jpg")
    with Image.open(field) as im:
        assert im.size == (323, 322)
    published = image(size=(180, 180))
    field.write_bytes(published)
    assert run(core, [UMI]).downloaded == 0
    assert field.read_bytes() == published


def test_cancel_commits_only_completed_art(core, cdn):
    game = core.installs.current()
    responses, _ = cdn
    for cid in (DM, POT, BLUE):
        responses[face(cid)] = image()
    ctx = JobContext()
    ctx._progress = lambda done, total, text: ctx._cancel.set() if done == 1 else None
    result = run(core, [DM, POT, BLUE], ctx)
    assert result.cancelled and result.downloaded == 1
    receipt = core.storage.last_receipt(game, "hdcards")
    assert len(receipt.changed) == 1
    core.storage.undo_last(game, "hdcards")
    assert not game.path(receipt.changed[0]).exists()


def test_user_edit_during_download_survives(core, cdn):
    game = core.installs.current()
    old, custom = image(size=(177, 254)), image(color="blue")
    dest = game.path(f"pics/{DM}.jpg")
    write(dest, old)

    def during_fetch():
        dest.write_bytes(custom)
        return image(size=(800, 1200))

    cdn[0][face(DM)] = during_fetch
    result = run(core, [DM])
    assert result.errors == 1 and result.downloaded == 0
    assert dest.read_bytes() == custom
    assert core.storage.last_receipt(game, "hdcards") is None


def test_game_exit_hook_and_badge_work_before_opening_page(core, cdn):
    game = core.installs.current()
    spec = register(core)
    assert (spec.id, spec.label, spec.icon, spec.order) == ("hdcards", "HD Cards", "hdcards", 10)
    assert spec.badge(core) is None
    with closing(sqlite3.connect(game.path("cards.cdb"))) as db, db:
        db.execute("INSERT INTO datas(id,type) VALUES(101,1)")
        db.execute("INSERT INTO texts(id,name) VALUES(101,'New card')")
    spec.on_game_exited(core)
    assert spec.badge(core) == "1"
    cdn[0][face(101)] = image()
    run(core, [101])
    spec.on_game_exited(core)
    assert spec.badge(core) is None


def test_scope_empty_and_retryable_failures(core, cdn):
    game = core.installs.current()
    write(game.path("deck/Fixture.ydk"), b"#main\n")
    assert run(core, [DM], deck_only=True).downloaded == 0 and not cdn[1]
    cdn[0][face(DM)] = artwork.FetchError("Unavailable server")
    first = run(core, [DM, POT])
    assert (first.errors, first.unavailable, first.downloaded) == (1, 1, 0)
    cdn[0][face(DM)] = image()
    assert run(core, [DM, POT]).downloaded == 1
    assert cdn[1][face(POT)] == 1
    assert cdn[1][face(DM)] == 4


def test_freshly_running_game_keeps_entire_sync_out_of_game(core, cdn, monkeypatch):
    game = core.installs.current()
    cdn[0][face(DM)] = image()
    monkeypatch.setattr(core.installs, "run_state", lambda: RunState.RUNNING)
    with pytest.raises(RuntimeError, match="Close EDOPro"):
        run(core, [DM])
    assert not game.path(f"pics/{DM}.jpg").exists()


def test_new_card_button_syncs_just_additions_in_real_job_runner(core, qtbot, monkeypatch):
    from edopal.ui.jobs import QtJobRunner

    core.jobs = QtJobRunner()
    requested = []
    image_bytes = image()

    class Response:
        status = 200
        content_length = len(image_bytes)
        headers = {}

        async def __aenter__(self):
            self.content = self
            return self

        async def __aexit__(self, *args):
            return None

        async def iter_chunked(self, size):
            yield image_bytes

    def get(session, url, *, allow_redirects):
        assert not allow_redirects
        requested.append(url)
        return Response()

    monkeypatch.setattr(artwork.aiohttp.ClientSession, "get", get)
    try:
        spec = register(core)
        page = spec.page(core)
        qtbot.addWidget(page)
        qtbot.waitUntil(lambda: page.state.phase == "idle")
        game = core.installs.current()
        with closing(sqlite3.connect(game.path("cards.cdb"))) as db, db:
            db.execute("INSERT INTO datas(id,type) VALUES(101,1)")
            db.execute("INSERT INTO texts(id,name) VALUES(101,'New card')")
        spec.on_game_exited(core)
        qtbot.waitUntil(lambda: spec.badge(core) == "1")
        page.new_action.click()
        qtbot.waitUntil(lambda: page.state.phase == "done")
        assert requested == [face(101)]
        assert page.state.downloaded == 1 and page.state.hd == 1
        assert spec.badge(core) is None
        assert game.path("pics/101.jpg").read_bytes() == image_bytes
    finally:
        core.jobs.shutdown()


@pytest.mark.parametrize("valid", [True, False])
def test_streams_before_coverage_finishes_but_publishes_only_after_stable_scan(core, cdn, monkeypatch, valid):
    from edopal.features.hdcards import sync as pipeline

    game = core.installs.current()
    cat = read_catalog(game)
    fetched = threading.Event()
    observed = []

    def scan(game, catalog, *, on_batch=None, **kwargs):
        if on_batch is None:
            return Coverage()
        on_batch(Coverage(), (DM,))
        observed.append(fetched.wait(3))
        assert not game.path(f"pics/{DM}.jpg").exists()
        return Coverage(complete=valid)

    def body():
        fetched.set()
        return image()

    cdn[0][face(DM)] = body
    monkeypatch.setattr(pipeline, "coverage", scan)
    job = sync(core, game, cat, None, JobContext(), only_ids={DM})
    if valid:
        assert asyncio.run(job).downloaded == 1
        assert core.storage.undo_last(game, "hdcards").restored == (f"pics/{DM}.jpg",)
    else:
        with pytest.raises(ValueError, match="Could not read"):
            asyncio.run(job)
        assert core.storage.last_receipt(game, "hdcards") is None
    assert observed == [True]
    assert not game.path(f"pics/{DM}.jpg").exists()


def test_fallback_rechecks_primary_after_ttl_and_keeps_undo(core, cdn, monkeypatch):
    from edopal.features.hdcards import sync as pipeline

    clock = [1_700_000_000.0]
    monkeypatch.setattr(pipeline, "time", type("Clock", (), {"time": lambda: clock[0], "monotonic": lambda: clock[0]}))
    monkeypatch.setattr(artwork, "time", pipeline.time)
    game = core.installs.current()
    fallback = f"{artwork.IGNIS}/{DM}.jpg"
    cdn[0][fallback] = image(size=(177, 254))
    assert run(core, {DM}).downloaded == 1
    receipt = core.storage.last_receipt(game, "hdcards")
    assert run(core, {DM}).downloaded == 0
    assert core.storage.last_receipt(game, "hdcards") == receipt
    assert cdn[1] == {face(DM): 1, fallback: 1}
    clock[0] += artwork.MISS_TTL + 1
    cdn[0][face(DM)] = image()
    assert run(core, {DM}).downloaded == 1
    assert game.path(f"pics/{DM}.jpg").read_bytes() == cdn[0][face(DM)]
    core.storage.undo_last(game, "hdcards")
    assert game.path(f"pics/{DM}.jpg").read_bytes() == cdn[0][fallback]


def test_existing_index_recognizes_managed_preferred_printing_after_migration(core, cdn):
    import hashlib
    import os

    game = core.installs.current()
    cat = read_catalog(game)
    data = image(size=(177, 254))  # Settled preferred printing can itself be SD.
    write(game.path(f"pics/{DM}.jpg"), data)
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(game.root.resolve())))).hexdigest()
    folder = core.paths.data / "hdcards"
    folder.mkdir(parents=True)
    path = folder / f"{key}.sqlite3"
    policy = repr((False, artwork.candidates(cat.cards[DM], cat)))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE art (id INTEGER, kind TEXT, digest TEXT, policy TEXT, source TEXT, "
            "printing INTEGER, width INTEGER, height INTEGER, fallback INTEGER, PRIMARY KEY(id,kind,digest))"
        )
        db.execute(
            "INSERT INTO art VALUES (?,?,?,?,?,?,?,?,?)",
            (DM, "face", hashlib.sha256(data).hexdigest(), policy, face(DM), DM, 177, 254, False),
        )
    assert run(core, {DM}).downloaded == 0
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT printing,recheck_after FROM art").fetchall() == [(DM, 0)]
    assert not cdn[1]
    assert core.storage.last_receipt(game, "hdcards") is None


def test_retry_recovers_during_last_pass_without_repeating_successes(core, cdn):
    replies, calls = cdn
    old = image(size=(177, 254), color="blue")
    write(core.installs.current().path(f"pics/{DM}.jpg"), old)

    def flaky():
        if calls[face(DM)] < 3:
            raise artwork.FetchError("Transient")
        return image()

    replies[face(DM)] = flaky
    replies[face(POT)] = image()
    # A low fallback cannot hide the transient HD failure.
    replies[f"{artwork.IGNIS}/{DM}.jpg"] = old
    result = run(core, [DM, POT])
    assert result.downloaded == 2 and not result.errors
    assert calls[face(DM)] == 3 and calls[face(POT)] == 1
    controller = Controller(core)
    controller.refresh()
    state = controller.state
    assert not state.failures and state.history[0].cards == 2 and state.history[0].result == "Complete"
    name, original, hd = state.pairs[0]
    assert name and original != hd
    controller.undo()
    assert not controller.state.pairs
    assert controller.state.known == 2 and not controller.state.hd


def test_retry_failed_is_per_install_kind_and_survives_restart(core, cdn):
    replies, calls = cdn
    replies[face(UMI)] = artwork.FetchError("Transient")
    field = f"{artwork.CDN}/cards_cropped/{UMI}.jpg"
    replies[field] = image(size=(261, 260))
    first = run(core, [UMI])
    assert (first.downloaded, first.errors) == (1, 1)
    assert calls[face(UMI)] == 3 and calls[field] == 1
    controller = Controller(core)
    controller.refresh()
    assert controller.state.failures == {(UMI, "face")}
    assert (controller.state.history[0].cards, controller.state.history[0].failed) == (0, 1)
    replies[face(UMI)] = image()
    controller.start_sync(retry_failed=True)
    assert controller.state.phase == "done"
    assert not controller.state.failures
    assert [(r.kind, r.cards) for r in controller.state.history] == [("Retry", 1), ("Full", 0)]
    assert calls[face(UMI)] == 4 and calls[field] == 1
    new_game = core.paths.cache / "another-game"
    shutil.copytree(FIXTURE, new_game)
    core.installs.select(new_game)
    controller.refresh()
    assert not controller.state.failures and not controller.state.history
