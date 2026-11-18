import asyncio
import io
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest
from conftest import PNG, FakeHTTP, pack_entry, send

import extras
import main
from config import Config
from content.cache import PackCache, file_sha256
from content.download import download_pack
from content.manifest import parse_manifest
from tools.build_content_release import build_release


def test_cached_skin_matches_bundled_with_highlight_and_restore(
    tmp_path: Path,
    http: FakeHTTP,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = extras.PACKS
    cache = PackCache(tmp_path / "fetched-state/packs")
    original = b"skin = Existing\r\ntextfont = original.ttf 10\r\n"
    local, fetched = tmp_path / "bundled-game", tmp_path / "cached-game"
    for root in (local, fetched):
        (root / "config").mkdir(parents=True)
        (root / "config/system.conf").write_bytes(original)
        (root / "fonts").mkdir()
        (root / "fonts/Inter-Medium.ttf").write_bytes(b"original")

    local_config = str(tmp_path / "local-state/config.json")
    fetched_config = str(tmp_path / "fetched-state/config.json")
    extras.apply_appearance(str(local), local_config, skin="Moon", highlight_turn=True)
    entries = []
    for pack_id, kind, title in (
        ("moon", "skin", "Moon"),
        ("shared", "sound-effects", "Shared assets"),
    ):
        body = (bundle / f"{pack_id}.zip").read_bytes()
        url = f"/{pack_id}.zip"
        http.routes[url] = lambda handler, data=body: send(handler, data)
        entries.append(
            pack_entry(
                http.url, body, id=pack_id, kind=kind, title=title, download_url=http.url + url
            )
        )
    skin, shared = parse_manifest(json.dumps({"schema_version": 1, "packs": entries})).packs
    for pack in (skin, shared):
        asyncio.run(download_pack(cache, pack, app_version="5.5.0"))

    without_zips = tmp_path / "appearance"
    without_zips.mkdir()
    shutil.copyfile(bundle / "Inter-OFL.txt", without_zips / "Inter-OFL.txt")
    monkeypatch.setattr(extras, "PACKS", without_zips)
    extras.apply_appearance(
        str(fetched),
        fetched_config,
        skin="Moon",
        highlight_turn=True,
        pack_cache=cache,
        skin_pack=skin,
        shared_pack=shared,
    )

    def snapshot(root: Path) -> dict[Path, str]:
        return {
            path.relative_to(root): file_sha256(path) for path in root.rglob("*") if path.is_file()
        }

    assert snapshot(local) == snapshot(fetched)
    assert extras.turn_highlight_enabled(fetched, "Moon")
    extras.apply_appearance(str(fetched), fetched_config, skin="Moon", highlight_turn=False)
    assert not extras.turn_highlight_enabled(fetched, "Moon")
    with ZipFile(bundle / "moon.zip") as archive:
        assert (fetched / "skin/Monokai Moon/skin.xml").read_bytes() == archive.read(
            "skin/Monokai Moon/skin.xml"
        )
    cache.clear()
    extras.apply_appearance(str(fetched), fetched_config, restore=True)
    assert (fetched / "config/system.conf").read_bytes() == original


def test_health_check_works_without_bundled_zips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(extras.PACKS.parent / "window/gallery", tmp_path / "window/gallery")
    monkeypatch.setattr(extras, "PACKS", tmp_path / "appearance")
    cfg = Config(["--config", str(tmp_path / "config.json"), "--edopro-path", str(tmp_path)])
    assert main.run_health_check(cfg)
    assert "Bundled content catalogue" in capsys.readouterr().out


def setup_pack_folder(root: Path, *, unsafe: str | None = None, withheld: bool = False) -> Path:
    folder = root / "input"
    folder.mkdir(parents=True)
    with ZipFile(folder / "example.zip", "w") as archive:
        archive.writestr(
            "pack.json",
            json.dumps({"items": [{"file": "forbidden/example.wav", "scenes": ["duel"]}]}),
        )
        member = ZipInfo("forbidden/example.wav")
        if unsafe:
            # ZipInfo's constructor normalizes backslashes on Windows.
            member.filename = unsafe
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", ""))
            wav.writeframes(bytes(160))
        archive.writestr(member, audio.getvalue())
    metadata = pack_entry("https://editor.test")
    for field in ("size_bytes", "sha256", "download_url", "previews"):
        del metadata[field]
    metadata.update(kind="music", preview_files=["cover.png"], withheld=withheld)
    (folder / "packs.json").write_bytes(json.dumps([metadata]).encode())
    (folder / "cover.png").write_bytes(PNG)
    return folder


def test_publisher_cli_stages_ready_to_upload_release(tmp_path: Path) -> None:
    folder = setup_pack_folder(tmp_path)
    out = tmp_path / "release"
    run = subprocess.run(
        [
            sys.executable,
            "tools/build_content_release.py",
            str(folder),
            "--out",
            str(out),
            "--repo",
            "editor/content",
            "--tag",
            "content-v2",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "gh release create content-v2 --repo editor/content" in run.stdout
    assert "gh release upload content-v2 manifest.json" in run.stdout
    assert "gh release edit content-v2 --repo editor/content --draft=false --latest" in run.stdout
    (pack,) = parse_manifest((out / "manifest.json").read_bytes()).packs
    asset = out / "example.1.0.0.zip"
    assert (
        pack.download_url
        == "https://github.com/editor/content/releases/download/content-v2/example.1.0.0.zip"
    )
    assert asset.read_bytes() == (folder / "example.zip").read_bytes()
    assert pack.size_bytes == asset.stat().st_size
    assert pack.sha256 == file_sha256(asset)
    assert pack.previews[0].sha256 == file_sha256(out / "example.1.0.0-1.png")


@pytest.mark.parametrize(
    "name", ["../escape", "folder/../escape", "folder\\outside", "cover.png:stream"]
)
def test_publisher_rejects_unsafe_archive_paths(tmp_path: Path, name: str) -> None:
    folder = setup_pack_folder(tmp_path, unsafe=name)
    out = tmp_path / "release"
    with pytest.raises(ValueError, match="Unsafe path"):
        build_release(folder, out, repo="editor/content", tag="content-v2")
    assert not out.exists()


def test_withheld_draft_and_base_manifest(tmp_path: Path) -> None:
    out = tmp_path / "release"
    folder = setup_pack_folder(tmp_path, withheld=True)
    with pytest.raises(ValueError, match="require --draft"):
        build_release(folder, out, repo="editor/content", tag="content-v2")
    draft = build_release(folder, out, repo="editor/content", tag="content-v2", draft=True)
    assert parse_manifest(draft.read_bytes()).packs == ()
    assert json.loads(draft.read_bytes())["packs"][0]["withheld"]
    metadata = json.loads((folder / "packs.json").read_bytes())
    metadata[0]["withheld"] = False
    (folder / "packs.json").write_bytes(json.dumps(metadata).encode())
    base = tmp_path / "base.json"
    base.write_bytes(
        json.dumps(
            {"schema_version": 1, "packs": [pack_entry("https://first.test", id="other")]}
        ).encode()
    )
    output = build_release(
        folder, tmp_path / "merged", repo="editor/content", tag="content-v3", base_manifest=base
    )
    assert [pack.id for pack in parse_manifest(output.read_bytes()).packs] == ["other", "example"]
    assert {path.name for path in output.parent.iterdir()} == {
        "manifest.json",
        "example.1.0.0.zip",
        "example.1.0.0-1.png",
    }
