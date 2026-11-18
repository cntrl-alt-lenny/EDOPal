import asyncio
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from typing import IO, Any
from unittest.mock import AsyncMock

import pytest

import main
import state_lock
from artwork_flow import STORAGE_MESSAGE, sync_summary
from config import Config
from state_lock import locked
from studio.pages import cards_undo
from studio.pages.cards_undo import (
    _history,
    has_restore,
    recover_publication,
    remember_changes,
    restore_artwork,
)
from tests.test_field_art import JPEG_BODY, _FakeSession
from tests.test_textures import PNG_BODY, _Session


def installation(tmp_path: Path, *flags: str) -> Config:
    game = tmp_path / "game"
    game.mkdir()
    with closing(sqlite3.connect(game / "cards.cdb")) as conn:
        conn.executescript(
            "CREATE TABLE datas(id,type); CREATE TABLE texts(id,name);"
            "INSERT INTO datas VALUES(101,1); INSERT INTO texts VALUES(101,'Card');"
        )
    return Config(
        [
            "--edopro-path",
            str(game),
            "--config",
            str(tmp_path / "state/config.json"),
            "--quiet",
            "--no-textures",
            "--no-field-art",
            *flags,
        ]
    )


def offline(monkeypatch: pytest.MonkeyPatch, count: int = 1) -> _FakeSession:
    session = _FakeSession([(200, JPEG_BODY)] * count)
    monkeypatch.setattr(main, "_create_http_session", lambda **kw: nullcontext(session))
    monkeypatch.setattr(main, "notify_if_update_available", AsyncMock())
    return session


def test_shared_run_can_undo_force_and_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--force", "--prune")
    pics = Path(cfg.pics_path)
    pics.mkdir()
    card, orphan = pics / "101.jpg", pics / "202.jpg"
    card.write_bytes(b"my card")
    orphan.write_bytes(b"my orphan")
    offline(monkeypatch)
    asyncio.run(main.run(cfg))
    assert card.read_bytes() == JPEG_BODY and not orphan.exists()
    assert has_restore(cfg)
    assert restore_artwork(cfg) == 2
    assert (card.read_bytes(), orphan.read_bytes()) == (b"my card", b"my orphan")


def test_refused_first_replace_keeps_previous_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--force", "--concurrency", "1", "--prune", "--save-report")
    pics = Path(cfg.pics_path)
    pics.mkdir()
    first = pics / "202.jpg"
    with remember_changes(cfg):
        main._write_image(str(first), b"published")
    card = pics / "101.jpg"
    card.write_bytes(b"my card")
    original = os.replace

    def refused(src: str, dest: str) -> None:
        if Path(dest) == card:
            raise PermissionError("file in use")
        original(src, dest)

    monkeypatch.setattr(os, "replace", refused)
    with closing(sqlite3.connect(Path(cfg.edopro_path) / "cards.cdb")) as conn:
        conn.executescript("INSERT INTO datas VALUES(102,1); INSERT INTO texts VALUES(102,'Next');")
    session = offline(monkeypatch, 2)
    with remember_changes(cfg):
        stats = asyncio.run(main.run(cfg))
    assert restore_artwork(cfg) == 1
    assert not first.exists() and card.read_bytes() == b"my card"
    assert stats.storage_error and not stats.transient_ids
    assert session.calls == 1 and not (pics / "102.jpg").exists()
    assert not Path(main._failure_cache_path(cfg)).exists()
    summary = sync_summary(cfg, {"stats": stats}, False, False, False)
    assert summary.title == "Couldn't save artwork"
    assert summary.warning and STORAGE_MESSAGE in summary.note
    assert "retry" not in summary.note
    report = Path(cfg.report_path).read_text("utf-8")
    assert STORAGE_MESSAGE in report and "101.jpg" in report
    assert "All cards downloaded successfully" not in report and "Network errors" not in report


def test_prune_respects_existing_installation_lock(tmp_path: Path) -> None:
    cfg = installation(tmp_path)
    pics = Path(cfg.pics_path)
    pics.mkdir()
    orphan = pics / "333.jpg"
    orphan.write_bytes(b"mine")
    with locked(pics.parent, ".edopro-artwork.lock"), pytest.raises(main.ArtworkStorageError):
        main.prune_orphan_images(str(pics), {101}, cfg=cfg)
    assert orphan.read_bytes() == b"mine"
    assert not has_restore(cfg)


def test_cannot_stage_in_redirected_pictures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    cfg = installation(tmp_path, "--force", "--prune")
    outside = tmp_path / "other-game"
    outside.mkdir()
    pics = Path(cfg.pics_path)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(pics))
    else:
        pics.symlink_to(outside, target_is_directory=True)
    request.addfinalizer(pics.rmdir if os.name == "nt" else pics.unlink)
    sentinel = outside / "101.jpg"
    sentinel.write_bytes(b"player image")
    offline(monkeypatch)
    attempted = []
    path_open = Path.open

    def trace_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if mode == "xb":
            attempted.append(path)
        return path_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", trace_open)
        result = asyncio.run(main.run(cfg))
    assert result.storage_error
    assert not attempted
    assert list(outside.iterdir()) == [sentinel] and sentinel.read_bytes() == b"player image"
    assert not has_restore(cfg)


def test_curated_texture_records_final_name_and_console_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--force")
    dest = Path(cfg.edopro_path) / "textures/player-board.png"
    dest.parent.mkdir()
    dest.write_bytes(b"personal board")
    response = _Session(
        {"assets": [{"name": dest.name, "browser_download_url": "https://fixture/board"}]}
    )
    assert asyncio.run(main.download_curated_textures(response, cfg)) == (1, 0, 1)
    assert dest.read_bytes() == PNG_BODY
    (db,) = Path(cfg.config_path).parent.glob("artwork-restore/*.sqlite")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT name FROM changes").fetchall() == [
            ("textures/player-board.png",)
        ]
    args = ["main.py", *cfg.argv, "--undo-last-sync"]
    monkeypatch.setattr(sys, "argv", [*args, "--dry-run"])
    assert main.main() == 0 and dest.read_bytes() == PNG_BODY
    monkeypatch.setattr(sys, "argv", args)
    assert main.main() == 0 and dest.read_bytes() == b"personal board"
    assert not has_restore(cfg)


@pytest.mark.parametrize("published", [False, True])
def test_reopen_after_process_exit_uses_file_hashes(tmp_path: Path, published: bool) -> None:
    cfg = installation(tmp_path)
    pics = Path(cfg.pics_path)
    pics.mkdir()
    first, second = pics / "101.jpg", pics / "102.jpg"
    with remember_changes(cfg):
        main._write_image(str(first), b"first")
    # Terminate on either side of the actual atomic rename, before SQLite promotion.
    script = """
import os, sys
from pathlib import Path
from config import Config
from main import _write_image
from studio.pages.cards_undo import remember_changes
publish, game, config = sys.argv[1:]
dest = Path(game) / 'pics/102.jpg'
replace = os.replace
def interrupt(src, target):
    if publish == 'True':
        replace(src, target)
    os._exit(71)
os.replace = interrupt
with remember_changes(Config(['--edopro-path', game, '--config', config])):
    _write_image(str(dest), b'second')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(published), cfg.edopro_path, cfg.config_path],
        check=False,
        timeout=20,
    )
    assert result.returncode == 71
    assert has_restore(cfg) and restore_artwork(cfg) == 1
    assert first.exists() == published and not second.exists()
    assert not list(pics.glob("*.part"))


def test_preview_leaves_installation_and_state_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--dry-run", "--force", "--repair", "--prune", "--save-report")
    offline(monkeypatch)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    cfg.set_edopro_path(cfg.edopro_path, save=True)
    asyncio.run(main.run(cfg))
    assert before == {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()
    }
    assert not Path(cfg.pics_path).exists() and not Path(cfg.config_path).parent.exists()


def test_previous_version_restore_store(tmp_path: Path) -> None:
    cfg = installation(tmp_path)
    root = Path(cfg.edopro_path)
    dest = root / "pics/101.jpg"
    dest.parent.mkdir()
    dest.write_bytes(b"installed")
    folder = Path(cfg.config_path).parent / "artwork-restore"
    folder.mkdir(parents=True)
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(root.resolve())))).hexdigest()
    with closing(sqlite3.connect(folder / f"{key}.sqlite")) as db, db:
        db.execute(
            "CREATE TABLE changes(name TEXT PRIMARY KEY, original BLOB, before TEXT, after TEXT)"
        )
        db.execute(
            "INSERT INTO changes VALUES(?,?,?,?)",
            (
                "pics/101.jpg",
                b"personal",
                hashlib.sha256(b"personal").hexdigest(),
                hashlib.sha256(b"installed").hexdigest(),
            ),
        )
    assert has_restore(cfg) and restore_artwork(cfg) == 1
    assert dest.read_bytes() == b"personal"


def test_failed_later_write_retains_only_successful_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--force", "--concurrency", "1")
    pics = Path(cfg.pics_path)
    pics.mkdir()
    (pics / "101.jpg").write_bytes(b"previous")
    with closing(sqlite3.connect(Path(cfg.edopro_path) / "cards.cdb")) as db:
        db.executescript("INSERT INTO datas VALUES(102,1); INSERT INTO texts VALUES(102,'Second');")
    with remember_changes(cfg):
        main._write_image(str(pics / "103.jpg"), b"previous run")
    replace = os.replace

    def refuse_second(src: Path, dest: Path) -> None:
        if Path(dest).name == "102.jpg":
            raise PermissionError("in use")
        replace(src, dest)

    monkeypatch.setattr(os, "replace", refuse_second)
    offline(monkeypatch, 2)
    result = asyncio.run(main.run(cfg))
    assert result.storage_error and result.total_ok == 1
    assert restore_artwork(cfg) == 1
    assert (pics / "101.jpg").read_bytes() == b"previous"
    assert not (pics / "102.jpg").exists()
    assert (pics / "103.jpg").read_bytes() == b"previous run"


def test_preview_never_creates_sqlite_sidecars(tmp_path: Path) -> None:
    cfg = installation(tmp_path, "--dry-run")
    game = Path(cfg.edopro_path)
    with closing(sqlite3.connect(game / "cards.cdb")) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    before = (game / "cards.cdb").read_bytes()
    with pytest.raises(main.SyncInputError):
        asyncio.run(main.run(cfg))
    assert sorted(p.name for p in game.iterdir()) == ["cards.cdb"]
    assert (game / "cards.cdb").read_bytes() == before
    assert not Path(cfg.config_path).parent.exists()


def test_packaged_preview_reads_legacy_settings_without_migrating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path)
    state = Path(cfg.config_path)
    legacy = tmp_path / "old-bundle"
    legacy.mkdir()
    settings = legacy / main.config_module.CONFIG_FILENAME
    settings.write_text(json.dumps({"edopro_path": cfg.edopro_path}), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.config_module, "_runtime_dir", lambda: str(legacy))
    monkeypatch.setattr(main.config_module, "default_config_path", lambda: str(state))
    monkeypatch.setattr(sys, "argv", ["EDOPal", "--dry-run", "--no-pause", "--quiet"])
    launches = []

    async def preview(selected: Config) -> None:
        launches.append((selected.config_path, selected.edopro_path))

    monkeypatch.setattr(main, "run", preview)
    assert main.main() == 0
    assert launches == [(str(state), cfg.edopro_path)]
    assert not state.parent.exists()
    assert list(legacy.iterdir()) == [settings]


@pytest.mark.parametrize("action", ["--generate-config", "--gui-smoke-test"])
def test_dry_run_prevents_write_only_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    cfg = installation(tmp_path)
    monkeypatch.setattr(sys, "argv", ["main.py", *cfg.argv, "--dry-run", action])
    assert main.main() == 2
    assert not Path(cfg.config_path).parent.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to replace a read-only file.")
def test_readonly_card_allows_retry_and_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = installation(tmp_path, "--force", "--concurrency", "1")
    pics = Path(cfg.pics_path)
    pics.mkdir()
    card = pics / "101.jpg"
    card.write_bytes(b"personal")
    card.chmod(stat.S_IREAD)
    offline(monkeypatch, 2)
    try:
        failed = asyncio.run(main.run(cfg))
        assert failed.storage_error and card.read_bytes() == b"personal"
        assert not list(pics.glob("*.part"))
    finally:
        card.chmod(stat.S_IWRITE)
    assert not asyncio.run(main.run(cfg)).storage_error
    assert restore_artwork(cfg) == 1 and card.read_bytes() == b"personal"


@pytest.mark.parametrize("player_edited", [False, True])
def test_recover_abandoned_readonly_stage(
    tmp_path: Path, player_edited: bool, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = installation(tmp_path)
    pics = Path(cfg.pics_path)
    with remember_changes(cfg) as writer:
        writer.publish(pics / "202.jpg", b"previous sync")
    card = pics / "101.jpg"
    current = b"player edit" if player_edited else b"original"
    card.write_bytes(current)
    stage = "a" * 32
    part = pics / f".101.jpg.{stage}.part"
    part.write_bytes(b"incoming")
    part.chmod(stat.S_IREAD)
    with closing(sqlite3.connect(_history(cfg))) as conn, conn:
        conn.execute(
            "INSERT INTO pending VALUES(?,?,?,?,?,?)",
            (
                "pics/101.jpg",
                b"original",
                hashlib.sha256(b"original").hexdigest(),
                hashlib.sha256(b"incoming").hexdigest(),
                1,
                stage,
            ),
        )
    recover_publication(cfg)
    assert not part.exists() and card.read_bytes() == current
    with closing(sqlite3.connect(_history(cfg))) as conn:
        assert conn.execute("SELECT name FROM pending").fetchall() == []
        assert conn.execute("SELECT name FROM changes").fetchall() == [("pics/202.jpg",)]
    if player_edited:
        assert "pics/101.jpg" in caplog.text
    assert restore_artwork(cfg) == 1 and card.read_bytes() == current


@pytest.mark.parametrize("bundled", [True, False], ids=["bundled", "curated"])
def test_artwork_lock_precedes_appearance_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundled: bool
) -> None:
    cfg = installation(tmp_path)
    acquired = []

    @contextmanager
    def trace(directory: Path, filename: str) -> Iterator[None]:
        with locked(directory, filename):
            acquired.append(filename)
            yield

    monkeypatch.setattr(state_lock, "locked", trace)
    monkeypatch.setattr(cards_undo, "locked", trace)
    if bundled:
        cfg.theme = True
        offline(monkeypatch)
        assert not asyncio.run(main.run(cfg)).storage_error
    else:
        session = _Session(
            {"assets": [{"name": "board.png", "browser_download_url": "https://fixture/board"}]}
        )
        assert asyncio.run(main.download_curated_textures(session, cfg)) == (1, 0, 1)
    expected = [".edopro-artwork.lock", ".edopro-appearance.lock"]
    assert acquired[:2] == expected
    acquired.clear()
    assert restore_artwork(cfg) > 0
    assert acquired[:2] == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows refuses to replace a read-only file.")
def test_readonly_undo_destination_leaves_no_stage(tmp_path: Path) -> None:
    cfg = installation(tmp_path)
    dest = Path(cfg.pics_path) / "101.jpg"
    dest.parent.mkdir()
    dest.write_bytes(b"original")
    with remember_changes(cfg) as writer:
        writer.publish(dest, b"installed")
    dest.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            restore_artwork(cfg)
        assert list(dest.parent.iterdir()) == [dest]
        assert has_restore(cfg)
    finally:
        dest.chmod(stat.S_IWRITE)
    assert restore_artwork(cfg) == 1 and dest.read_bytes() == b"original"


def test_console_partial_undo_reports_edited_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = installation(tmp_path)
    pics = Path(cfg.pics_path)
    with remember_changes(cfg) as writer:
        writer.publish(pics / "101.jpg", b"installed")
        writer.publish(pics / "202.jpg", b"installed")
    (pics / "202.jpg").write_bytes(b"player edit")
    monkeypatch.setattr(sys, "argv", ["main.py", *cfg.argv, "--undo-last-sync"])
    assert main.main() == 0
    out = capsys.readouterr().out
    assert "Restored 1 file" in out and "pics/202.jpg" in out
    assert not (pics / "101.jpg").exists() and (pics / "202.jpg").read_bytes() == b"player edit"
