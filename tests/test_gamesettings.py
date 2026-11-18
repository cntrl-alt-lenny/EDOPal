"""Offline byte-fidelity and recovery tests for the 41.0.2 display editor."""

import json
import stat
import struct
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import gamesettings as gs
from gamesettings.document import Document, current_profile, preview
from gamesettings.options import PROFILES, normalize, renderer_warning
from gamesettings.storage import profiles, save_profile
from state_lock import appearance_lock


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> gs.DisplaySettings:
    root = tmp_path / "Game"
    (root / "config").mkdir(parents=True)
    (root / "config/system.conf").touch()
    monkeypatch.setattr("music.settings._game_running", lambda _: False)
    return gs.DisplaySettings(root, tmp_path / "local")


@pytest.mark.parametrize("ending", [b"\r\n", b"\n"])
@pytest.mark.parametrize("trailing", [b"", b"\n"])
def test_only_effective_values_change(
    engine: gs.DisplaySettings, ending: bytes, trailing: bytes
) -> None:
    conf = engine.game / "config/system.conf"
    raw = (
        ending.join(
            [
                b"\xef\xbb\xbf# EDOPro",
                b"dpi_scale = 0.9",
                b"# dpi_scale = 99",
                b'fallbackFonts = "bundled 12"',
                b"# \xff\x00",
                b"future = \xca\xfe",
                b"dpi_scale = 1.0000  # player note",
                b"enable_music = 1",
                b"nickname = Player",
            ]
        )
        + trailing
    )
    conf.write_bytes(raw)
    snapshot = engine.inspect()
    assert snapshot.values["dpi_scale"] == "1.0000"
    engine.commit(snapshot, {"dpi_scale": "1.25", "fullscreen": "1"})
    expected = raw.replace(
        b"dpi_scale = 0.9", b"fullscreen = 1" + ending + b"dpi_scale = 0.9"
    ).replace(b"1.0000  #", b"1.250000  #")
    assert conf.read_bytes() == expected
    (record,) = engine.config_dir.rglob("*.json")
    assert "nickname" not in record.read_text()
    assert set(json.loads(record.read_bytes())["original"]) == {"dpi_scale", "fullscreen"}
    conf.write_bytes(expected.replace(b"enable_music = 1", b"enable_music = 0"))
    engine.commit(engine.inspect(), {}, "undo")
    assert conf.read_bytes() == raw.replace(b"enable_music = 1", b"enable_music = 0")


def test_history_is_per_key_across_applies_and_restore_is_reversible(
    engine: gs.DisplaySettings,
) -> None:
    path = engine.game / "config/system.conf"
    path.write_bytes(b"dpi_scale = 1.00\nfullscreen = 0\nvsync = 1")
    engine.commit(engine.inspect(), {"dpi_scale": "1.25", "fullscreen": "1"})
    engine.commit(engine.inspect(), {"dpi_scale": "2", "maxFPS": "30"})
    before_undo = path.read_bytes()
    engine.commit(engine.inspect(), {}, "undo")
    assert path.read_bytes() == b"dpi_scale = 1.250000\nfullscreen = 1\nvsync = 1"
    engine.commit(engine.inspect(), {}, "restore")
    assert path.read_bytes() == b"dpi_scale = 1.00\nfullscreen = 0\nvsync = 1"
    engine.commit(engine.inspect(), {}, "undo")
    assert path.read_bytes() != before_undo
    assert path.read_bytes() == b"dpi_scale = 1.250000\nfullscreen = 1\nvsync = 1"


def test_noop_and_unknown_key(engine: gs.DisplaySettings) -> None:
    snapshot = engine.inspect()
    engine.commit(snapshot, {"dpi_scale": "1", "vsync": "1"})
    assert not engine.config_dir.exists()
    with pytest.raises(ValueError):
        engine.commit(snapshot, {"enable_music": "1"})
    assert (engine.game / "config/system.conf").read_bytes() == b""


def test_nonstandard_spacing_and_numeric_comments_round_trip(engine: gs.DisplaySettings) -> None:
    conf = engine.game / "config/system.conf"
    before = b"unknown = 8\r\nvsync\t=\t1;remember\n# untouched\r\n"
    conf.write_bytes(before)
    engine.commit(engine.inspect(), {"vsync": "0"})
    assert conf.read_bytes() == before.replace(b"1;remember", b"0;remember")
    engine.commit(engine.inspect(), {}, "undo")
    assert conf.read_bytes() == before


def test_unusual_assignment_cannot_shadow_an_added_value(engine: gs.DisplaySettings) -> None:
    path = engine.game / "config/system.conf"
    path.write_bytes(b"dpi_scale =1.25")
    with pytest.raises(ValueError, match="unusual assignment"):
        engine.commit(engine.inspect(), {"dpi_scale": "2"})
    assert path.read_bytes() == b"dpi_scale =1.25"


def test_game_reformat_and_installation_history_isolation(engine: gs.DisplaySettings) -> None:
    conf = engine.game / "config/system.conf"
    conf.write_bytes(b"dpi_scale = 1.00")
    engine.commit(engine.inspect(), {"dpi_scale": "1.25"})
    conf.write_bytes(b"# rewritten\r\ndpi_scale = 1.250\r\nnickname = New")
    engine.commit(engine.inspect(), {}, "undo")
    assert conf.read_bytes() == b"# rewritten\r\ndpi_scale = 1.00\r\nnickname = New"
    other_game = engine.game.with_name("Game 2")
    (other_game / "config").mkdir(parents=True)
    (other_game / "config/system.conf").write_bytes(conf.read_bytes())
    assert not gs.DisplaySettings(other_game, engine.config_dir).inspect().history.original


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("dpi_scale", "-1", "1.000000"),
        ("dpi_scale", "100", "2.000000"),
        ("maxFPS", "10000", "240"),
        ("textfont", "fonts/A.otf 900", "fonts/A.otf 24"),
    ],
)
def test_guardrails(key: str, value: str, expected: str) -> None:
    assert normalize(key, value) == expected


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dpi_scale", "nan"),
        ("fullscreen", "yes"),
        ("driver_type", "unlisted"),
        ("textfont", "x 12\nnickname = X"),
    ],
)
def test_invalid_proposals_are_refused(key: str, value: str) -> None:
    with pytest.raises(ValueError):
        normalize(key, value)


def test_read_only(engine: gs.DisplaySettings) -> None:
    path = engine.game / "config/system.conf"
    path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError, match="read-only"):
            engine.commit(engine.inspect(), {"dpi_scale": "2"})
        assert path.read_bytes() == b""
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_other_appearance_writer_owns_installation(engine: gs.DisplaySettings) -> None:
    snapshot = engine.inspect()
    with appearance_lock(engine.game), ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(engine.commit, snapshot, {"dpi_scale": "2"})
        with pytest.raises(OSError):
            job.result(timeout=10)
    assert engine.inspect() == snapshot


def test_existing_embedded_font_can_be_resized(engine: gs.DisplaySettings) -> None:
    path = engine.game / "config/system.conf"
    path.write_bytes(b"textfont = bundled 12")
    engine.commit(engine.inspect(), {"textfont": "bundled 15"})
    assert path.read_bytes() == b"textfont = bundled 15"


@pytest.mark.parametrize("interrupt", [True, None, OSError("process query failed")])
def test_game_opening_during_staging_preserves_prior_undo(
    engine: gs.DisplaySettings, monkeypatch: pytest.MonkeyPatch, interrupt: object
) -> None:
    engine.commit(engine.inspect(), {"dpi_scale": "1.25"})
    snapshot = engine.inspect()
    answers = iter((False, interrupt))

    def check(_: Path) -> object:
        answer = next(answers)
        if isinstance(answer, OSError):
            raise answer
        return answer

    monkeypatch.setattr("music.settings._game_running", check)
    with pytest.raises(gs.GameOpenError):
        engine.commit(snapshot, {"maxFPS": "30"})
    assert engine.inspect() == snapshot
    assert not list(engine.game.rglob(".display-*"))


def test_interrupted_after_publish_recovers(
    engine: gs.DisplaySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    write = gs.write_json

    def fail_final(path: Path, record: dict) -> None:
        if "pending" not in record:
            raise OSError("interrupted")
        write(path, record)

    with monkeypatch.context() as patch:
        patch.setattr(gs, "write_json", fail_final)
        with pytest.raises(OSError, match="interrupted"):
            engine.commit(engine.inspect(), {"maxFPS": "30"})
    snapshot = engine.inspect()
    assert snapshot.history.original == {"maxFPS": None}
    engine.commit(snapshot, {}, "undo")
    assert (engine.game / "config/system.conf").read_bytes() == b""


def test_stale_preview_and_undo_guard(engine: gs.DisplaySettings) -> None:
    conf = engine.game / "config/system.conf"
    snapshot = engine.inspect()
    conf.write_bytes(b"dpi_scale = 1.1")
    with pytest.raises(ValueError, match="review"):
        engine.commit(snapshot, {"dpi_scale": "1.25"})
    engine.commit(engine.inspect(), {"dpi_scale": "1.25"})
    conf.write_bytes(b"dpi_scale = 1.5")
    with pytest.raises(ValueError, match="Restore original"):
        engine.commit(engine.inspect(), {}, "undo")
    engine.commit(engine.inspect(), {}, "restore")
    assert conf.read_bytes() == b"dpi_scale = 1.1"


def test_profiles_stay_on_the_device(
    engine: gs.DisplaySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gamesettings.storage.uuid.getnode", lambda: 0x2020)
    profile = PROFILES[0]
    save_profile(engine.config_dir, "Night", profile.values)
    with pytest.raises(ValueError, match="in use"):
        save_profile(engine.config_dir, "night", {"vsync": "0"})
    assert profiles(engine.config_dir)[0].name == "Night"
    monkeypatch.setattr("gamesettings.storage.uuid.getnode", lambda: 0x1010)
    assert profiles(engine.config_dir) == ()
    assert all(p.values.keys() <= gs.OPTIONS.keys() for p in PROFILES)


def test_font_choice_and_size_require_existing_file(engine: gs.DisplaySettings) -> None:
    with pytest.raises(ValueError, match="font is missing"):
        engine.commit(engine.inspect(), {"textfont": "fonts/Example.ttf 16"})
    folder = engine.game / "fonts"
    folder.mkdir()
    (folder / "Example.ttf").touch()
    snapshot = engine.inspect()
    assert snapshot.fonts == ("fonts/Example.ttf",)
    engine.commit(snapshot, {"textfont": "fonts/Example.ttf 16"})
    assert engine.inspect().values["textfont"] == "fonts/Example.ttf 16"


def test_placement_and_platform_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    placement = struct.pack("<11i", 44, 0, 1, 0, 0, 0, 0, 32, 24, 1312, 824)
    doc = Document(b"windowStruct = " + b64encode(placement))
    assert "1280 × 800" in doc.window_size()
    assert not preview(doc.values, {"driver_type": "default"})
    monkeypatch.setattr("sys.platform", "linux")
    assert "1024 × 640" in doc.window_size()
    assert "unavailable" in renderer_warning("d3d9")
    assert renderer_warning("default") == ""
    monkeypatch.setattr("sys.platform", "darwin")
    doc = Document(b"windowStruct = {{-32.5, 20}, {1440, 900}}")
    assert "1440 × 900 points" in doc.window_size()


def test_snapshot_profile_excludes_values_that_would_be_clamped() -> None:
    values = Document(b"maxFPS = -1\ndpi_scale = 5.0\nfullscreen = 1").values
    result = current_profile(values)
    assert "maxFPS" not in result and "dpi_scale" not in result
    assert result["fullscreen"] == "1"


def test_stale_during_publish_keeps_external_changes(
    engine: gs.DisplaySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = engine.game / "config/system.conf"

    def change_during_check(_: Path) -> bool:
        if list(engine.config_dir.rglob("*.json")):
            conf.write_bytes(b"nickname = Updated")
        return False

    monkeypatch.setattr("music.settings._game_running", change_during_check)
    with pytest.raises(ValueError, match="while saving"):
        engine.commit(engine.inspect(), {"dpi_scale": "2"})
    assert conf.read_bytes() == b"nickname = Updated"
    assert not engine.inspect().history.last
