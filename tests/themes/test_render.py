import random
import statistics
import time
from dataclasses import replace
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

import extras
from themes import (
    PRESETS,
    SCENES,
    WIDGETS,
    Theme,
    derive,
    ensure_gui,
    render_preview,
    render_skin,
    skin_xml,
    wallpaper,
    widget_image,
)
from themes.colors import WHITE, alpha, contrast, mix
from themes.skin import custom
from themes.wallpaper import crop


def test_contrast_for_random_inputs_including_user_overrides() -> None:
    rng = random.Random(41002)

    def color() -> str:
        return f"#{rng.getrandbits(24):06x}"

    for _ in range(400):
        t = Theme(
            "Trial",
            color(),
            color(),
            color(),
            text=color(),
            muted=color(),
            danger=color(),
            positive=color(),
            highlight=color(),
            your_lp=color(),
            opponent_lp=color(),
            depth=rng.randrange(101),
        )
        c = derive(t)
        for bg in (c.surface, c.inset, c.raised, c.selection):
            for ink in (c.text, c.muted, c.accent, c.positive, c.danger, c.highlight):
                assert contrast(bg, ink) >= 4.5, (t, bg, ink)
        for bar in c.bars:
            for step in range(11):
                assert contrast(bar.text, mix(bar.top, bar.bottom, step / 10)) >= 4.5
        values = custom(t)
        for key in (
            "FPS_TEXT_COLOR",
            "DUELFIELD_STACK",
            "DUELFIELD_CARD_LINK",
            "DUELFIELD_LOWER_CARD_ATK",
        ):
            assert contrast(values[key], t.background) >= 4.5
        for key in (
            "DECK_WINDOW_MAIN_INFO_TOP_RIGHT",
            "DECK_WINDOW_SEARCH_RESULT_TOP_LEFT",
            "DECK_WINDOW_HOVERED_CARD_RESULT",
            "ROOMLIST_NORMAL_ROOM",
            "ROOMLIST_CUSTOM_ROOM",
            "ROOMLIST_STARTED_ROOM",
        ):
            r, g, b, a = QColor(values[key]).getRgb()
            fill = f"#{r:02x}{g:02x}{b:02x}"
            # Both texture extremes, including stark white user-supplied wallpaper.
            for under in (WHITE, "#000000", t.background):
                assert contrast(WHITE, mix(under, fill, a / 255)) >= 4.5


def paths(root: ET.Element, prefix: str = "") -> set[str]:
    chain = prefix + "/" + root.tag
    return {chain}.union(*(paths(child, chain) for child in root))


def test_all_pack_keys_borders_textures_and_registry_traps() -> None:
    theme = Theme(
        'Quartz & "Ink"', "#000000", "#111111", "#000000", highlight_turn=False, wallpaper="inherit"
    )
    files = render_skin(theme)
    skin = ET.fromstring(files["skin.xml"])
    assert skin.find("Properties/Name").get("data") == theme.name
    assert len(skin.find("Custom")) == len(custom(theme)) == 107
    assert skin.find("Global").get("guialpha") == "255"
    assert skin.find("Global/EGDC_3D_FACE").attrib == dict.fromkeys("argb", "255")
    off = extras._turn_colors(files["skin.xml"])
    assert all(a == 0 and (r or g or b) for a, r, g, b in off)
    assert [c[0] for c in extras._turn_colors(skin_xml(replace(theme, highlight_turn=True)))] == [
        210,
        135,
    ]
    for pack in extras.SKINS:
        with ZipFile(extras.PACKS / (pack.lower() + ".zip")) as archive:
            reference = ET.fromstring(archive.read(f"skin/Monokai {pack}/skin.xml"))
        assert paths(reference) <= paths(skin)
    assert len(WIDGETS) == 15 and len(files) == 16
    assert skin.find("Button/Pressed").attrib["texture"] == "Pressed.png"
    for spec in WIDGETS.values():
        node = skin.find(spec.path)
        img = QImage.fromData(files[node.attrib["texture"]])
        assert img.size().toTuple() == (spec.width, spec.height)
        for key in ("SrcBorder", "DstBorder"):
            border = tuple(int(node.find(key).attrib[a]) for a in ("tlx", "tly", "brx", "bry"))
            top, left, bottom, right = border
            assert border == spec.border and min(border) > 0
            assert top + bottom < img.height() and left + right < img.width()
        assert node.find("Color").attrib == dict.fromkeys("argb", "255")
    assert WIDGETS["Window"].border == (30, 13, 13, 13)
    assert QColor(alpha("#000000", 0)).getRgb() == (0, 0, 1, 0)
    assert widget_image(theme, "Button") != widget_image(theme, "Pressed")


def test_fixed_lobby_text_over_painted_sunken_pane() -> None:
    for theme in PRESETS:
        texture = widget_image(theme, "SunkenPane")
        for status in ("NORMAL", "CUSTOM", "STARTED"):
            img = QImage(texture.size(), QImage.Format.Format_RGB32)
            img.fill(QColor(custom(theme)[f"ROOMLIST_{status}_ROOM"]))
            painter = QPainter(img)
            painter.drawImage(0, 0, texture)
            painter.end()
            for y in (3, 12, 24, 40):
                assert contrast(WHITE, img.pixelColor(24, y).name()) >= 4.5


def test_wallpaper_setting_and_two_stage_viewport_crop() -> None:
    src = QImage(400, 100, QImage.Format.Format_RGB32)
    src.fill(QColor("blue"))
    p = QPainter(src)
    p.fillRect(100, 0, 200, 100, QColor("red"))
    p.end()
    theme = replace(PRESETS[0], wallpaper="image", image="0" * 64)
    stretch = wallpaper(theme, (100, 100), image=src)
    cover = wallpaper(theme, (100, 100), image=src, scale_background=False)
    assert stretch.pixelColor(4, 50).name() == "#0000ff"
    assert cover.pixelColor(4, 50).name() == "#ff0000"
    full = QImage.fromData(render_skin(theme, image=src, scale_background=False)["textures/bg.png"])
    assert full.size().toTuple() == (2560, 1600)
    assert crop(QRectF(src.rect()), QRectF(0, 0, 160, 100), False) == QRectF(120, 0, 160, 100)
    assert (
        wallpaper(replace(theme, wallpaper="inherit", image=None), (12, 12), image=src)
        .size()
        .width()
        == 12
    )
    with pytest.raises(ValueError):
        render_preview(theme, "menu")


def test_each_preset_and_live_colour_changes_within_budget() -> None:
    ensure_gui()
    render_preview(PRESETS[0], "menu", (320, 200))  # one-time font registration
    assert len(set(PRESETS)) >= 48
    durations = []
    for i, preset in enumerate(PRESETS):
        for scene in SCENES:
            # Use different slider values so the benchmark includes palette & wallpaper misses.
            t = replace(preset, accent=preset.accent[:5] + f"{(i * 7 + len(scene)) % 256:02x}")
            start = time.perf_counter()
            img = render_preview(t, scene)
            durations.append((time.perf_counter() - start) * 1000)
            assert img.size().toTuple() == (960, 600)
            assert img.pixelColor(0, 0) != img.pixelColor(480, 300)
        assert render_preview(preset, "duel") != render_preview(preset, "duel", opponent_turn=True)
        off = replace(preset, highlight_turn=False)
        assert render_preview(off, "duel") == render_preview(off, "duel", opponent_turn=True)
    p95 = statistics.quantiles(durations, n=20)[18]
    print(
        f"960x600 slider frames: median {statistics.median(durations):.1f} ms, "
        f"95th percentile {p95:.1f} ms, maximum {max(durations):.1f} ms"
    )
    assert p95 < 50
