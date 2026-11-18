import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pytest

import config
import extras
from sleeves import Layout, Library, Recipe, compose, decode, png_bytes, read_source, render


def test_default_library_uses_per_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "user_data_dir", lambda: str(tmp_path))
    library = Library()
    assert library.root == tmp_path / "sleeves"
    assert {e.name for e in library.entries()} == {"Vortex", "1998 OCG"}


def test_collection_roundtrip_search_and_shared_content(
    library: Library, blocks: bytes, tmp_path: Path
) -> None:
    presets = library.entries(source="preset")
    with ZipFile(extras.PACKS / "shared.zip") as assets:
        assert {e.name: library.image_path(e.id).read_bytes() for e in presets} == {
            name: assets.read(member) for name, member in extras.SLEEVES.items()
        }
    path = tmp_path / "picture"
    path.write_bytes(blocks)
    layout = Layout("fill", (5, 2, 21, 30))
    first = library.add_image("River", path, layout)
    path.unlink()
    second = library.add_image("Bird", blocks, layout, passcode=42)
    design = library.add_design("Moon", Recipe("eclipse", params={"phase": 0.7}))
    assert library.image_path(second.id) == library.image_path(first.id)
    library.edit(first.id, name="River bend", tags=["Water", "Blue", "Water"], favourite=True)
    reopened = Library(library.root)
    (e,) = reopened.entries("BLUE river", favourites=True)
    assert e.tags == ("Water", "Blue") and e.source == "imported"
    assert reopened.entries("42", source="card art")[0].id == second.id
    assert not reopened.entries(recent=True)
    assert (
        png_bytes(compose(read_source(library.image_path(e.id, original=True)), e.layout))
        == library.image_path(e.id).read_bytes()
    )
    assert png_bytes(render(design.recipe)) == library.image_path(design.id).read_bytes()
    thumb = library.thumbnail(first.id)
    before = thumb.stat().st_mtime_ns
    assert library.thumbnail(first.id).stat().st_mtime_ns == before
    thumb.write_bytes(b"damaged")
    assert decode(library.thumbnail(first.id).read_bytes()).width() == 177
    thumb.unlink()
    assert library.thumbnail(second.id).is_file()
    library.delete(first.id)
    rendered = library.image_path(second.id)
    orig = library.image_path(second.id, original=True)
    assert rendered.exists() and orig.exists() and thumb.exists()
    library.delete(second.id)
    assert not any(p.exists() for p in (rendered, orig, thumb))


@pytest.mark.parametrize("damage", ["missing", "broken", "malformed"])
def test_manifest_and_blob_recovery(library: Library, blocks: bytes, damage: str) -> None:
    e = library.add_image("Archive", blocks, Layout("fit"))
    manifest = library.root / "manifest.json"
    original = library.image_path(e.id, original=True)
    if damage == "missing":
        manifest.unlink()
    elif damage == "broken":
        manifest.write_bytes(b"not json")
    else:
        obj = json.loads(manifest.read_bytes())
        obj["entries"][e.id]["tags"] = 42
        manifest.write_text(json.dumps(obj))
    assert Library(library.root).entries("Archive")[0].id == e.id
    (library.root / "manifest.backup.json").unlink()
    manifest.write_bytes(b"broken")
    (recovered,) = library.entries(source="recovered")
    assert recovered.image == e.image
    assert original.read_bytes() == blocks
    assert len(library.entries(source="preset")) == 2
    assert any(library.root.glob("damaged-*.json"))


def test_concurrent_library_clients(library: Library, blocks: bytes) -> None:
    def add(i: int) -> str:
        return Library(library.root).add_image(str(i), blocks).id

    with ThreadPoolExecutor(4) as pool:
        ids = set(pool.map(add, range(8)))
    assert ids == {e.id for e in library.entries(source="imported")}
    assert len({e.image for e in library.entries(source="imported")}) == 1


def test_unknown_future_catalogue_is_preserved(library: Library) -> None:
    library.entries()
    path = library.root / "manifest.json"
    raw = b'{"version":9}'
    path.write_bytes(raw)
    assert len(library.entries()) == 2
    with pytest.raises(RuntimeError, match="newer"):
        library.delete(library.entries()[0].id)
    assert path.read_bytes() == raw
