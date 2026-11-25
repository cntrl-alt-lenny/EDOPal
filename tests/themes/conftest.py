import shutil
from pathlib import Path

import pytest

from edopal.dev.preview import make_core

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def core(tmp_path):
    game = tmp_path / "game"
    shutil.copytree(FIXTURES.parents[1] / "fixtures" / "game", game)
    return make_core(game, FIXTURES, tmp_path / "home")


@pytest.fixture
def packs(core):
    return {pack.id: pack for pack in core.packs.catalog()}
