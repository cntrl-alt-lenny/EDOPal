from pathlib import Path

import pytest

import extras
from sleeves import Library, Recipe, apply, reapply, status, undo


def test_slots_skin_switch_drift_and_undo(library: Library, tmp_path: Path) -> None:
    root = tmp_path / "game"
    config = tmp_path / "state" / "config.json"
    (root / "textures").mkdir(parents=True)
    (root / "config").mkdir()
    mine = root / "textures" / "cover.png"
    mine.write_bytes(b"old mine")
    assert status(library, root)["opponent"].path == mine
    a, b = [e.id for e in library.entries()]
    opponent = root / "textures" / "cover2.png"
    assert apply(library, root, config, mine=a, opponent=b) == (mine, opponent)
    assert apply(library, root, config, mine=a) == ()
    assert status(library, root)["mine"].matches
    assert status(Library(library.root), root)["opponent"].entry_id == b
    assert status(library, tmp_path / "game2")["mine"].entry_id is None
    assert {e.id for e in library.entries(recent=True)} == {a, b}
    skin = root / "skin" / "Ink" / "textures"
    skin.mkdir(parents=True)
    (root / "config" / "system.conf").write_bytes(b"skin = Ink")
    jpg = skin / "cover.jpg"
    jpg.write_bytes(b"skin jpg")
    assert status(library, root)["mine"].matches is False
    png = skin / "cover.png"
    assert reapply(library, root, config) == (png,)
    original = png.read_bytes()
    png.write_bytes(b"external edit")
    with pytest.raises(ValueError, match="changed"):
        undo(library, root, config)
    assert png.read_bytes() == b"external edit"
    png.write_bytes(original)
    assert undo(library, root, config) == (png,)
    assert not png.exists() and jpg.read_bytes() == b"skin jpg"
    assert status(library, root)["mine"].entry_id == a
    assert status(library, root)["mine"].matches is False
    assert status(library, root)["opponent"].matches


def test_restore_prior_preferences_and_bytes(library: Library, tmp_path: Path) -> None:
    root, config = tmp_path / "game", tmp_path / "config.json"
    a, b = [e.id for e in library.entries()]
    apply(library, root, config, mine=a)
    apply(library, root, config, mine=b, opponent=b)
    undo(library, root, config)
    current = status(library, root)
    assert current["mine"].entry_id == a and current["mine"].matches
    assert current["opponent"].entry_id is None
    assert not (root / "textures/cover2.png").exists()
    library.delete(a)
    assert status(library, root)["mine"].entry_id is None


def test_failed_second_write_rolls_back_and_keeps_assignments(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = tmp_path / "game", tmp_path / "state" / "config.json"
    previous = library.add_design("Dawn", Recipe("sun"))
    apply(library, root, config, mine=previous.id)
    new = library.entries(source="preset")[0]
    write = extras._atomic_write

    def fail_second(path: Path, data: bytes) -> None:
        if path == root / "textures/cover2.png":
            raise OSError("Simulated disk full")
        write(path, data)

    monkeypatch.setattr(extras, "_atomic_write", fail_second)
    with pytest.raises(OSError, match="disk full"):
        apply(library, root, config, mine=new.id, opponent=new.id)
    assert status(library, root)["mine"].matches
    assert status(library, root)["mine"].entry_id == previous.id
    assert status(library, root)["opponent"].entry_id is None


def test_interrupted_pair_can_be_undone_after_reopening(
    library: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, config = tmp_path / "game", tmp_path / "config.json"
    a, b = [e.id for e in library.entries()]
    apply(library, root, config, mine=a)
    write = extras._atomic_write

    def interrupt(path: Path, data: bytes) -> None:
        if path == root / "textures/cover2.png":
            raise KeyboardInterrupt
        write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(extras, "_atomic_write", interrupt)
        with pytest.raises(KeyboardInterrupt):
            apply(library, root, config, mine=b, opponent=a)

    reopened = Library(library.root)
    assert status(reopened, root)["opponent"].matches is False
    assert undo(reopened, root, config) == (root / "textures/cover.png",)
    assert status(reopened, root)["mine"].entry_id == a
    assert status(reopened, root)["opponent"].entry_id is None
    # The previously committed group is still the next valid undo.
    undo(reopened, root, config)
    assert status(reopened, root)["mine"].entry_id is None


def test_gallery_undo_preserves_studio_preference(library: Library, tmp_path: Path) -> None:
    root, config = tmp_path / "game", tmp_path / "config.json"
    studio = library.add_design("Shell", Recipe("nautilus"))
    apply(library, root, config, mine=studio.id)
    extras.apply_appearance(str(root), str(config), player_sleeve="Vortex")
    assert status(library, root)["mine"].matches is False
    undo(library, root, config)
    assert status(library, root)["mine"].entry_id == studio.id
    assert status(library, root)["mine"].matches


def test_missing_independent_cover_is_detected_even_when_fallback_matches(
    library: Library, tmp_path: Path
) -> None:
    root, config = tmp_path / "game", tmp_path / "config.json"
    entry = library.entries()[0]
    apply(library, root, config, mine=entry.id, opponent=entry.id)
    opponent = root / "textures/cover2.png"
    opponent.unlink()
    slot = status(library, root)["opponent"]
    assert slot.path == root / "textures/cover.png" and slot.matches is False
    assert reapply(library, root, config) == (opponent,)
