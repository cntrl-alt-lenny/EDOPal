import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import doctor.report as engine
from artwork_coverage import JPEG_END, JPEG_MAGIC
from doctor import Finding, inspect_installation, support_summary
from doctor.platform_checks import game_version, lock_active
from doctor.settings import _STOCK_XML
from extras import _group_dir
from state_lock import locked


def put(root: Path, path: str, data: str | bytes) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data if isinstance(data, bytes) else data.encode())
    return target


def database(root: Path, path: str, cards: list[tuple[int, int, str, int]]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS datas (id INTEGER PRIMARY KEY, alias INTEGER, type INTEGER)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS texts (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany(
            "INSERT INTO datas VALUES (?, ?, ?)",
            ((code, alias, kind) for code, alias, _, kind in cards),
        )
        db.executemany(
            "INSERT INTO texts VALUES (?, ?)", ((code, name) for code, _, name, _ in cards)
        )


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(engine, "_game_running", lambda _: False)
    monkeypatch.setattr(engine, "directory_writable", lambda _: True)
    game = tmp_path / "game"
    put(game, "EDOPro.exe", b"synthetic executable")
    put(game, "config/configs.json", '{"repos":[]}')
    put(
        game,
        "config/system.conf",
        'skin = none\ntextfont = fonts/ui.ttf 12\nnumfont = fonts/ui.ttf\nfallbackFonts = "bundled 12"\n',
    )
    put(game, "fonts/ui.ttf", b"font")
    put(game, "sound/draw.wav", b"effect")
    put(game, "lflists/local.conf", "!Test list\n1 3\n")
    put(game, "deck/subfolder/valid.ydk", "#created by player\n#main\n1\n#extra\n!side\n")
    put(game, "pics/1.jpg", JPEG_MAGIC + b"x" * 1024 + JPEG_END)
    database(game, "expansions/cards.cdb", [(1, 0, "First card", 1)])
    return game


def inspect(game: Path) -> dict[str, Finding]:
    return {
        item.id: item
        for item in inspect_installation(
            game, config_path=game.parent / "local/config.json"
        ).findings
    }


def test_healthy_installation_is_read_only_and_has_no_warnings(installation: Path) -> None:
    before = {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in installation.rglob("*")
        if path.is_file()
    }
    findings = inspect(installation)
    assert all(item.severity in {"ok", "info"} for item in findings.values())
    assert findings["artwork.coverage"].evidence["cards_have"] == 1
    assert findings["decks.inventory"].evidence["parsed"] == 1
    assert findings["databases.available"].evidence["cards"] == 1
    assert findings["music.disabled"].evidence["files"] == 0
    assert "state.clean" in findings
    assert before == {
        path: (path.stat().st_mtime_ns, path.read_bytes())
        for path in installation.rglob("*")
        if path.is_file()
    }
    assert not (installation.parent / "local").exists()


def test_repository_databases_mappings_and_banlists(installation: Path) -> None:
    root = installation
    put(
        root,
        "config/configs.json",
        json.dumps(
            {
                "repos": [
                    {
                        "url": "https://example.invalid/cards",
                        "repo_path": "./repositories/data",
                        "data_path": "./cards",
                        "lflist_path": ".",
                        "script_path": "missing-scripts",
                    },
                    {"repo_path": "repositories/missing", "url": "https://example.invalid/missing"},
                    {"repo_path": "repositories/empty", "url": "https://example.invalid/empty"},
                    {"repo_path": "repositories/failed", "url": "https://example.invalid/failed"},
                    {"repo_path": "repositories/disabled", "should_read": False},
                    {"repo_path": "../outside", "not_git_repo": True},
                ]
            }
        ),
    )
    (root / "repositories/empty").mkdir(parents=True)
    put(root, "repositories/failed/README", "incomplete")
    put(root, "repositories/data/.git/HEAD", "ref: refs/heads/main\n")
    put(root, "repositories/data/.git/packed-refs", ("9" * 40) + " refs/heads/main\n")
    database(root, "repositories/data/cards/custom.cdb", [(77, 0, "Repository card", 0)])
    database(
        root,
        "repositories/data/cards/extra.delta.cdb",
        [(77, 0, "Duplicate", 0), (79, 0, "Field", 0x80002)],
    )
    put(root, "repositories/data/cards/mappings.json", '{"mappings":[[78,77]]}')
    put(root, "repositories/data/TCG.conf", "!Current TCG\n1 0\n!Historic TCG\n$whitelist\n77 3\n")
    (root / "lflists/local.conf").unlink()
    put(root, "deck/subfolder/valid.ydk", "#main\n77\n78\n")
    findings = inspect(root)
    assert findings["databases.available"].evidence["cards"] == 3
    assert findings["databases.available"].evidence["duplicates"] == 1
    assert findings["banlists.available"].evidence["lists"] == 2
    assert findings["banlists.available"].evidence["whitelists"] == 1
    assert findings["repositories.config"].evidence["repositories"][0]["revision"] == "9" * 40
    assert {
        "repositories.missing",
        "repositories.empty",
        "repositories.clone_incomplete",
        "repositories.disabled",
        "repositories.resource_missing",
        "repositories.config_invalid",
    } <= findings.keys()
    assert "decks.unknown_cards" not in findings


def test_artwork_index_and_playability_have_different_scopes(installation: Path) -> None:
    put(
        installation,
        "config/user_configs.json",
        '{"repos":[{"repo_path":"repositories/local","not_git_repo":true}]}',
    )
    database(installation, "repositories/local/all.cdb", [(100, 0, "Only in game", 0)])
    database(installation, "repositories/local/a.delta.cdb", [(200, 0, "Both", 0)])
    findings = inspect(installation)
    assert findings["databases.available"].evidence["cards"] == 3
    assert findings["artwork.coverage"].evidence["cards_total"] == 2


def test_corrupt_empty_and_placeholder_databases(installation: Path) -> None:
    put(installation, "cards.cdb", b"")
    put(installation, "expansions/corrupt.cdb", b"bad sqlite")
    database(installation, "expansions/empty.cdb", [])
    put(installation, "expansions/mappings.json", "bad")
    put(installation, "deck/unreadable.ydk", b"\xff")
    put(installation, "deck/private.ydk", "#main\n999\nunparseable\n")
    findings = inspect(installation)
    assert {
        "databases.placeholder",
        "databases.unreadable",
        "databases.empty",
        "databases.mappings_unreadable",
        "decks.unknown_cards",
        "decks.unreadable",
    } <= findings.keys()
    assert findings["decks.unknown_cards"].evidence["card_ids"] == (999,)
    (installation / "expansions/cards.cdb").unlink()
    assert "databases.missing" in inspect(installation)


def test_missing_and_unreadable_configuration(installation: Path) -> None:
    (installation / "config/configs.json").unlink()
    (installation / "config/system.conf").unlink()
    put(installation, "lflists/local.conf", "unparseable")
    findings = inspect(installation)
    assert {
        "repositories.config_missing",
        "settings.unavailable",
        "banlists.unreadable",
        "banlists.missing",
    } <= findings.keys()


def test_skin_font_and_audio_findings(installation: Path) -> None:
    put(
        installation,
        "config/system.conf",
        'skin = Test\ntextfont = fonts/missing.ttf 12\nnumfont = bundled\nfallbackFonts = "bundled 12" "fonts/missing2.ttf 9"\nenable_music = 1\nenable_sound = 0\nsound_backend = none\n',
    )
    findings = inspect(installation)
    assert {
        "skin.missing",
        "fonts.missing",
        "music.missing",
        "sound.disabled",
        "audio.backend_disabled",
    } <= findings.keys()
    assert findings["fonts.missing"].evidence["affected"] == 2
    put(installation, "skin/Test/skin.xml", '<Skin><Button texture="missing.png"/></Skin>')
    assert "skin.images" in inspect(installation)
    put(installation, "skin/Test/missing.png", b"broken PNG")
    assert "skin.images" in inspect(installation)
    put(installation, "skin/Test/skin.xml", "<Skin/>")
    assert "skin.available" in inspect(installation)


def test_stock_skin_warning_is_attributed_to_upstream(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    put(
        installation,
        "config/system.conf",
        "skin = Indigo\ntextfont = bundled 12\nnumfont = bundled\n",
    )
    bad_xml = b"<Skin><!-- outer <!-- nested --> --></Skin>"
    target = put(installation, "skin/Indigo/skin.xml", bad_xml)
    monkeypatch.setitem(_STOCK_XML, "Indigo", hashlib.sha256(bad_xml).hexdigest())
    assert inspect(installation)["skin.stock_malformed"].severity == "info"
    target.write_bytes(bad_xml + b" changed")
    assert inspect(installation)["skin.malformed"].severity == "warning"


def test_audio_enabled_and_volume_findings(installation: Path) -> None:
    config = "textfont = bundled 12\nnumfont = bundled\nenable_music = 1\nenable_sound = 1\n"
    put(installation, "config/system.conf", config + "music_volume = 0\nsound_volume = 0\n")
    assert {"sound.zero_volume", "music.zero_volume"} <= inspect(installation).keys()
    put(installation, "config/system.conf", config)
    for scene in ("advantage", "deck", "disadvantage", "lose", "menu", "win"):
        put(installation, f"sound/BGM/{scene}/song.ogg", b"music")
    put(installation, "sound/BGM/loose.flac", b"duel music")
    (installation / "sound/draw.wav").unlink()
    findings = inspect(installation)
    assert {"music.available", "sound.missing"} <= findings.keys()
    assert findings["music.available"].evidence["files"] == 7


def test_windbot_list_conflicts_aliases_and_unknown_cards(installation: Path) -> None:
    root = installation
    put(root, "WindBot/WindBot.exe", "bot")
    assert "windbot.incomplete" in inspect(root)
    put(root, "WindBot/ExecutorBase.dll", "support")
    put(root, "WindBot/bots.json", '[{"deck":"ABC"},{"deck":"Missing"},{"deck":"Lucky"}]')
    put(root, "WindBot/Decks/AI_ABC.ydk", "#main\n1\n2\n#extra\n3\n!side\n1\n999\n")
    database(root, "expansions/bot.cdb", [(2, 1, "Alternate art", 0), (3, 0, "Other", 0)])
    put(root, "lflists/local.conf", "!Copies\n1 1\n!Whitelisted\n$whitelist\n1 3\n")
    findings = inspect(root)
    assert {"windbot.available", "windbot.unmatched", "windbot.deck_unreadable"} <= findings.keys()
    assert findings["windbot.unmatched"].evidence["affected"] == 1
    conflicts = findings["windbot.list_conflicts"].evidence["details"]
    assert {(row.list_name, row.card_id, row.copies, row.limit) for row in conflicts} == {
        ("Copies", 1, 3, 1),
        ("Copies", 2, 2, 1),
        ("Whitelisted", 3, 1, 0),
    }


def test_state_leftovers_disk_space_process_and_permissions(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(installation, "pics/1.jpg", "broken")
    marker = put(installation, "textures/.edopro-hd-sync.lock", b"0")
    put(installation, "sound/.edopro-hd-sync/deck/pending.json", "{}")
    group = _group_dir(installation, str(installation.parent / "local/config.json"), "sleeves")
    put(group, "restoring.json", "{}")
    monkeypatch.setattr(engine, "_game_running", lambda _: True)
    monkeypatch.setattr(engine, "directory_writable", lambda path: path.name != "pics")
    monkeypatch.setattr(engine.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(1, 1, 0))
    findings = inspect(installation)
    assert {
        "storage.low",
        "process.running",
        "installation.read_only",
        "state.interrupted",
        "state.lock_idle",
    } <= findings.keys()
    assert "state.clean" not in findings
    assert marker.read_bytes() == b"0"
    with locked(marker.parent, marker.name):
        assert lock_active(marker) is True
        assert "state.lock_busy" in inspect(installation)


def test_unknown_probe_states_are_distinct_from_failures(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args: object) -> None:
        raise OSError("unavailable")

    put(installation, "textures/.edopro-hd-sync.lock", b"0")
    monkeypatch.setattr(engine, "_game_running", unavailable)
    monkeypatch.setattr(engine, "directory_writable", lambda _: None)
    monkeypatch.setattr(engine, "lock_active", lambda _: None)
    monkeypatch.setattr(engine, "scan_artwork_coverage", unavailable)
    assert {
        "artwork.unavailable",
        "storage.unknown",
        "process.unknown",
        "installation.write_unknown",
        "state.lock_unknown",
    } <= inspect(installation).keys()


def test_stock_list_comments_and_negative_limit(installation: Path) -> None:
    put(installation, "lflists/local.conf", "!Test list\n--Section break\n1 -1\n")
    put(installation, "WindBot/WindBot.exe", "bot")
    put(installation, "WindBot/Decks/AI_Test.ydk", "#main\n1\n")
    findings = inspect(installation)
    assert "banlists.unreadable" not in findings
    (conflict,) = findings["windbot.list_conflicts"].evidence["details"]
    assert conflict.limit == 0


def test_missing_installation_and_mac_bundle_version(tmp_path: Path) -> None:
    assert set(inspect(tmp_path / "unknown")) == {"installation.missing"}
    put(
        tmp_path,
        "EDOPro.app/Contents/Info.plist",
        '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleShortVersionString</key><string>41.0.2</string></dict></plist>',
    )
    assert game_version(tmp_path / "EDOPro.app/Contents/Info.plist") == "41.0.2"
    database(tmp_path, "game/cards.cdb", [(1, 0, "Card", 0)])
    assert "installation.data_only" in inspect(tmp_path / "game")


def test_support_summary_redacts_personal_data_even_in_relative_names(installation: Path) -> None:
    put(
        installation,
        "config/system.conf",
        "nickname = PrivateNickname\nlasthost = 192.0.2.5\nskin = PrivateSkin\ntextfont = C:\\Users\\PrivateUser\\font.ttf 12\n",
    )
    put(installation, "deck/subfolder/Personal Deck.ydk", "some invalid content")
    put(
        installation,
        "config/configs.json",
        '{"repos":[{"repo_path":"repositories/PrivateRepository","url":"https://private.example/secret"}]}',
    )
    report = inspect_installation(
        installation,
        config_path=installation.parent / "private-data" / "config.json",
    )
    summary = support_summary(report)
    for private in (
        str(installation),
        "PrivateNickname",
        "PrivateSkin",
        "PrivateUser",
        "192.0.2.5",
        "Personal Deck",
        "PrivateRepository",
        "private.example",
        "private-data",
    ):
        assert private not in summary
    assert "[WARNING] decks.unreadable" in summary
    assert "cards 1" in summary
