import json
from pathlib import Path

from PIL import Image

from edopal.core.pack_client import GitHubPacks
from edopal.features.themes.media import SCENES, artwork


def test_scene_images_before_download_and_from_cached_manifest(core):
    # Exercise the production image transport in its supported offline checkout mode.
    packs = GitHubPacks(core.paths, local_dir=Path(__file__).parent / "fixtures")
    pack = next(p for p in packs.catalog("theme") if p.id == "theme-abyss")
    listings = {scene: artwork(packs, pack, scene) for scene in SCENES}
    assert len({path.read_bytes() for path in listings.values()}) == len(SCENES)
    for path in listings.values():
        with Image.open(path) as img:
            assert img.width * 10 == img.height * 16
    assert packs.cached(pack.id) is None

    downloaded = packs.ensure(pack)
    manifest = json.loads((downloaded / "manifest.json").read_bytes())
    for scene, image in listings.items():
        stored = artwork(packs, pack, scene)
        assert stored == downloaded / manifest["previews"][scene]
        assert stored.read_bytes() == image.read_bytes()
