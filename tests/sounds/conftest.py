from io import BytesIO
from pathlib import Path
from struct import pack
from wave import open as open_wave

import pytest


@pytest.fixture
def wav():
    def make(seconds: float = 0.14, value: int = 8, rate: int = 8000) -> bytes:
        with BytesIO() as output:
            with open_wave(output, "wb") as audio:
                audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                audio.writeframes(pack("<h", value) * round(rate * seconds))
            return output.getvalue()

    return make


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "EDOPro"
    (root / "sound").mkdir(parents=True)
    return root
