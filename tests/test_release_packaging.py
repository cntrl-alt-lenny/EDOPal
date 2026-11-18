import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter_ns
from zipfile import ZipFile

import pytest

import config
import extras
from tools import release


@pytest.mark.parametrize(
    ("platform", "program"),
    [("Windows", "EDOPal.exe"), ("macOS", "EDOPal.app"), ("Linux", "./EDOPal")],
)
def test_shipped_readme_names_program_and_existing_data_location(
    platform: str, program: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "PLATFORM", platform)
    instructions = release.readme()
    assert instructions.splitlines()[0] == f"{config.APP_NAME} for {platform}"
    assert program in instructions and config.APP_DATA_NAME in instructions
    assert "@APP_" not in instructions


@pytest.mark.parametrize(
    "member",
    [
        r"assets\appearance\Moon.zip",
        "PySide6/Qt/lib/libQt6QmlModels.so.6",
        "QtMultimedia.framework/Versions/A/QtMultimedia",
        "QtWebEngineProcess.exe",
        "PySide6.QtQuick",
    ],
)
def test_payload_rejects_excluded_files(member: str) -> None:
    with pytest.raises(ValueError, match="Unwanted runtime file|Bundled on-demand skin"):
        release.check_exclusions([member])


def test_missing_legacy_skin_with_no_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extras, "PACKS", tmp_path / "absent")
    game = tmp_path / "ProjectIgnis"
    game.mkdir()
    with pytest.raises(FileNotFoundError, match="Download Moon from the content catalogue"):
        extras.apply_appearance(str(game), str(tmp_path / "config.json"), skin="Moon")
    assert not (game / "config/system.conf").exists()


@pytest.mark.skipif(sys.platform == "darwin", reason="Finder uses ditto bundles")
@pytest.mark.parametrize("extra", ["unexpected.txt", "EDOPal/hidden/extra.txt"])
def test_archive_rejects_additional_entries(tmp_path: Path, extra: str) -> None:
    archive = tmp_path / "release.zip"
    with ZipFile(archive, "w") as zipped:
        for filename in (f"{release.NAME}/{release.EXE}", f"{release.NAME}/ReadMe.txt", extra):
            zipped.writestr(filename, b"")
    with pytest.raises(ValueError, match="Unexpected zip entries"):
        release.extract(archive, tmp_path)


@pytest.mark.parametrize("missing_assets", [False, True])
def test_smoke_entry_is_offline_and_reports_bad_resources(
    tmp_path: Path, missing_assets: bool
) -> None:
    # Enter through main's actual CLI. Fail immediately if it schedules discovery
    # or the update check, even on a developer machine with a working network.
    script = """
from pathlib import Path
from unittest.mock import patch
import main
from studio import theme
from studio.shell import StudioWindow
if MISSING:
    theme.ASSETS = Path.cwd() / "absent"
with patch.object(StudioWindow, "_startup", side_effect=AssertionError("online startup")):
    raise SystemExit(main.main())
""".replace("if MISSING:", f"if {missing_assets}:")
    game = tmp_path / "ProjectIgnis"
    game.mkdir()
    started = perf_counter_ns()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            "--gui-smoke-test",
            "--config",
            str(tmp_path / "config.json"),
            "--edopro-path",
            str(game),
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(release.ROOT)},
    )
    assert result.returncode == int(missing_assets), (result.stdout, result.stderr)
    assert b"Qt platform: offscreen" in result.stdout
    marker = b"First visible window perf_counter_ns="
    stamp = next(line for line in result.stdout.splitlines() if line.startswith(marker))
    assert started < int(stamp.removeprefix(marker)) <= perf_counter_ns()
    if missing_assets:
        assert b"Could not decode app-icon.png" in result.stderr
    else:
        assert b"GUI smoke test passed" in result.stdout
