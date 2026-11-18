from __future__ import annotations

import stat
from pathlib import Path
from threading import Event
from typing import IO, Any
from zipfile import ZipFile

import pytest

from decks.banlists import Banlist
from decks.cards import Card, Catalog
from decks.legality import preflight
from decks.model import Sections, preview
from formats import installation as engine
from formats.catalogue import Catalogue, filename, marked, sha
from formats.installation import State
from music.settings import GameProcessStatus


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Catalogue:
    rows = []
    with ZipFile(tmp_path / "lists.zip", "w") as z:
        for fid in (1, 2, 3):
            display = f"Test list {fid}"
            data = marked(fid, f"!{display}\n$whitelist\n1 2\n".encode())
            name = filename(fid)
            z.writestr(name, data)
            rows.append(
                dict(
                    id=fid,
                    file=name,
                    name=display,
                    display=display,
                    sha256=sha(data),
                    limits=[0, 0, 1, 0],
                )
            )
    monkeypatch.setattr(engine.settings, "game_process_status", lambda _: GameProcessStatus.CLOSED)
    return Catalogue(tmp_path, rows, {})


def test_foreign_file_and_player_edit_survive_install_update_and_remove(
    tmp_path: Path, bundle: Catalogue
) -> None:
    g = tmp_path / "game"
    folder = g / "lflists"
    folder.mkdir(parents=True)
    foreign, edit, old = (folder / r["file"] for r in bundle.rows)
    foreign.write_bytes(b"!My personal list\n123 0\n")
    edit.write_bytes(marked(2, b"original") + b"# personal note")
    old.write_bytes(marked(3, b"!Previous\n$whitelist\n3 1\n"))
    before = foreign.read_bytes(), edit.read_bytes()
    assert engine.statuses(g, bundle.rows) == {1: State.MODIFIED, 2: State.MODIFIED, 3: State.OLD}
    for remove in (False, True):
        outcome = engine.change(g, bundle, bundle.rows, remove=remove)
        assert outcome.kept == ["Test list 1", "Test list 2"]
        assert outcome.changed == ["Test list 3"]
        assert (foreign.read_bytes(), edit.read_bytes()) == before
        assert engine.statuses(g, bundle.rows)[3] == (State.ABSENT if remove else State.INSTALLED)
    assert not old.exists()
    assert not (g / "repositories").exists()


def test_bulk_cancel_between_files_then_resume(tmp_path: Path, bundle: Catalogue) -> None:
    cancel = Event()
    progress = []

    def stop(event: tuple[int, int, str]) -> None:
        progress.append(event)
        cancel.set()

    result = engine.change(tmp_path, bundle, bundle.rows, cancel=cancel, progress=stop)
    assert progress == [(1, 3, "Test list 1")]
    assert result.stopped and result.changed == ["Test list 1"]
    assert engine.change(tmp_path, bundle, bundle.rows).changed == ["Test list 2", "Test list 3"]
    assert all(s == State.INSTALLED for s in engine.statuses(tmp_path, bundle.rows).values())


def test_published_list_created_directly_in_destination_with_normal_permissions(
    tmp_path: Path, bundle: Catalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "lflists"
    folder.mkdir()
    normal = folder / "neighbour.conf"
    normal.write_text("!Personal list\n")
    created = []
    original_open = Path.open

    def record(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if mode == "xb" and path.suffix == ".tmp":
            created.append(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", record)
    row = bundle.rows[0]
    engine.change(tmp_path, bundle, [row])
    assert len(created) == 1 and created[0].parent == folder
    assert not created[0].exists()
    assert stat.S_IMODE((folder / row["file"]).stat().st_mode) == stat.S_IMODE(
        normal.stat().st_mode
    )


def test_bundled_list_can_preflight_offline_without_installation(
    tmp_path: Path, bundle: Catalogue
) -> None:
    before = set(tmp_path.iterdir())
    row = bundle.rows[0]
    banlist = bundle.bundled_list(row["id"])
    assert isinstance(banlist, Banlist) and banlist.name == row["display"]
    assert banlist.whitelist and banlist.limits == {1: 2} and not banlist.issues
    assert banlist.path == tmp_path / "lists.zip" / row["file"]
    monster = Card(1, "Example", 0, 3, 33, "Effect Monster", "DARK", "level", 4, ())
    catalog = Catalog({1: monster}, {}, set(), ())
    verdict = preflight(preview(Sections((1, 1), (), (1,)), catalog), banlist, catalog)
    assert any(f.rule == "semi-limited" and f.ids == (1,) for f in verdict.findings)
    assert set(tmp_path.iterdir()) == before
    with pytest.raises(KeyError):
        bundle.bundled_list(999)
    bundle.rows.append(dict(id=4, unsupported="Rolling format"))
    with pytest.raises(ValueError, match="Rolling format"):
        bundle.bundled_list(4)
    row["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="validation"):
        bundle.bundled_list(row["id"])


@pytest.mark.parametrize("status", [GameProcessStatus.RUNNING, GameProcessStatus.UNKNOWN])
def test_game_open_and_unknown_do_not_write(
    tmp_path: Path, bundle: Catalogue, monkeypatch: pytest.MonkeyPatch, status: GameProcessStatus
) -> None:
    monkeypatch.setattr(engine.settings, "game_process_status", lambda _: status)
    for remove in (False, True):
        with pytest.raises(engine.GameBlocked):
            engine.change(tmp_path, bundle, bundle.rows, remove=remove)
    assert not (tmp_path / "lflists").exists()
    assert not (tmp_path / ".edopro-appearance.lock").exists()


def test_undo_mixed_add_and_update_restores_exact_old_bytes_and_keeps_player_edits(
    tmp_path: Path, bundle: Catalogue
) -> None:
    folder = tmp_path / "lflists"
    folder.mkdir()
    old, added, edited = (folder / r["file"] for r in bundle.rows)
    original = marked(1, b"!Earlier name\n$whitelist\n77 1\n")
    old.write_bytes(original)
    change = engine.change(tmp_path, bundle, bundle.rows)
    edit = edited.read_bytes() + b"# Custom edit"
    edited.write_bytes(edit)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="different game"):
        engine.change(other, bundle, bundle.rows, undo=change.previous)
    result = engine.change(tmp_path, bundle, bundle.rows, undo=change.previous)
    assert result.kept == [bundle.rows[2]["name"]]
    assert not added.exists() and edited.read_bytes() == edit and old.read_bytes() == original


def test_validate_before_publish_and_preserve_prior_list(tmp_path: Path, bundle: Catalogue) -> None:
    row = bundle.rows[0]
    old = tmp_path / "lflists" / row["file"]
    old.parent.mkdir()
    old.write_bytes(marked(1, b"!Previous\n$whitelist\n99 3\n"))
    before = old.read_bytes()
    invalid = marked(1, b"!Test list 1\n$whitelist\n1 2\nwrong\n")
    row["sha256"] = sha(invalid)
    with ZipFile(tmp_path / "lists.zip", "w") as z:
        z.writestr(row["file"], invalid)
    with pytest.raises(ValueError, match="validation"):
        engine.change(tmp_path, bundle, [row])
    assert old.read_bytes() == before and list(old.parent.iterdir()) == [old]


def test_reject_redirected_folder(
    tmp_path: Path, bundle: Catalogue, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the canonical-path gate even on Windows hosts lacking symlink privileges.
    original = Path.resolve

    def redirected(path: Path, *args: object, **kwargs: object) -> Path:
        resolved = original(path, *args, **kwargs)
        return tmp_path / "repositories" / "lists" if path.name == "lflists" else resolved

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="another location"):
        engine.change(tmp_path, bundle, bundle.rows)
    assert not (tmp_path / "repositories").exists()
