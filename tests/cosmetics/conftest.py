import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from edopal.dev.preview import make_core
from edopal.features.cosmetics import register
from edopal.features.cosmetics.icons import KEPT, PRESETS, SLOTS


@pytest.fixture
def core(tmp_path):
    tests = Path(__file__).resolve().parents[1]
    packs = tmp_path / "packs"
    shutil.copytree(tests / "themes/fixtures", packs)
    game = tmp_path / "game"
    shutil.copytree(tests / "fixtures/game", game)
    catalog = json.loads((packs / "catalog.json").read_bytes())
    for index, slug in enumerate((*PRESETS, "candidates")):
        folder = packs / "icons" / slug
        files = {}
        for k, key in enumerate(SLOTS):
            if slug == "candidates" and key in KEPT:
                continue
            for n, choice in enumerate("ABCDE" if slug == "candidates" else [slug]):
                rel = f"candidates/{key}/{choice}.png" if slug == "candidates" else f"textures/{key}.png"
                path = folder / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (128, 64), (30 + 40 * index, 20 + 41 * n, 30 + 18 * k, 255)).save(path)
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        if slug == "stock":
            rel = "previews/field.webp"
            path = folder / rel
            path.parent.mkdir()
            Image.new("RGB", (620, 430), (35, 46, 51)).save(path)
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        pack_id = f"icons-{slug}"
        manifest = dict(schema=1, id=pack_id, name=slug, kind="icons", version="1", files=files)
        (folder / "manifest.json").write_text(json.dumps(manifest))
        catalog["packs"].append(
            dict(id=pack_id, name=slug, kind="icons", version="1", url=f"icons/{slug}", size=0, sha256="")
        )
    (packs / "catalog.json").write_text(json.dumps(catalog))
    return make_core(game, packs, tmp_path / "home")


@pytest.fixture
def page(core, qtbot):
    widget = register(core).page(core)
    qtbot.addWidget(widget)
    widget.resize(1050, 725)
    widget.show()
    return widget
