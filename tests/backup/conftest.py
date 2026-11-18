from pathlib import Path

import pytest

from backup import Installation


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Installation, Installation]:
    def make(name: str) -> Installation:
        game = tmp_path / name / "ProjectIgnis"
        game.mkdir(parents=True)
        return Installation.at(game, tmp_path / name / "Studio" / "config.json")

    return make("pc"), make("deck")


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    return tmp_path / "transfer.zip"
