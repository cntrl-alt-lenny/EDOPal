"""EDOPro 41.0.2 XML and game-relative assets for one skin directory."""

from functools import lru_cache
from types import MappingProxyType
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PySide6.QtGui import QColor, QImage

import extras
from config import APP_NAME

from .colors import WHITE, alpha, derive, mix, pole, towards
from .imaging import png
from .model import Theme
from .wallpaper import wallpaper
from .widgets import WIDGETS, widget_image

CORNERS = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT")
FONT_FILES = ("fonts/InterEDO-Regular.ttf", "fonts/Inter-Medium.ttf", "fonts/Inter-OFL.txt")


def _global(theme: Theme) -> dict[str, str]:
    c = derive(theme)
    groups = (
        (c.background, "3D_DARK_SHADOW APP_WORKSPACE"),
        (c.inset, "GRAY_EDITABLE SCROLLBAR 3D_SHADOW"),
        (c.surface, "WINDOW EDITABLE TOOLTIP_BACKGROUND"),
        (c.raised, "3D_HIGH_LIGHT FOCUSED_EDITABLE"),
        (c.border, "3D_LIGHT ACTIVE_BORDER INACTIVE_BORDER"),
        (c.text, "BUTTON_TEXT ACTIVE_CAPTION TOOLTIP HIGH_LIGHT_TEXT"),
        (c.muted, "INACTIVE_CAPTION GRAY_TEXT ICON WINDOW_SYMBOL GRAY_WINDOW_SYMBOL"),
        (c.selection, "HIGH_LIGHT"),
        (c.accent, "ICON_HIGH_LIGHT"),
        (WHITE, "3D_FACE"),
    )
    return {"EGDC_" + name: color for color, names in groups for name in names.split()} | {
        "CheckBoxColor": c.accent
    }


@lru_cache(maxsize=48)
def custom(theme: Theme) -> MappingProxyType[str, str]:
    c = derive(theme)

    def field_ink(color: str) -> str:
        return towards(color, pole(c.background), c.background)

    values = {
        "ROOMLIST_NORMAL_ROOM": c.row,
        "ROOMLIST_CUSTOM_ROOM": c.row_selected,
        "ROOMLIST_STARTED_ROOM": mix(c.row, "#000000", 0.25),
        "ROOMLIST_TEXTS_COLOR": c.text,
        "CARDINFO_IMAGE_BACKGROUND": c.background,
        "CARDINFO_ARCHETYPE_TEXT_COLOR": c.accent,
        "CARDINFO_STATS_COLOR": c.text,
        "CARDINFO_TYPES_COLOR": c.muted,
        "CARDINFO_PASSCODE_SCOPE_TEXT_COLOR": c.muted,
        "FPS_TEXT_COLOR": field_ink(c.text),
        "PROGRESSBAR_FILL_COLOR": c.accent,
        "PROGRESSBAR_EMPTY_COLOR": c.background,
        "DECK_WINDOW_HOVERED_CARD_RESULT": c.row_selected,
        "DECK_WINDOW_HOVERED_CARD_OUTLINE": c.accent,
    }
    duel_groups = (
        (
            c.text,
            "TOOLTIP_TEXT_COLOR ANNOUNCE_TEXT_COLOR CARD_SELECT_WINDOW_OVERLAY_TEXT",
        ),
        (c.muted, "CARD_SELECT_WINDOW_SET_TEXT"),
        (field_ink(c.text), "STACK UNCHANGED_CARD_ATK UNCHANGED_CARD_DEF"),
        (field_ink(c.muted), "TURN_COUNT CARD_LEVEL CARD_PSCALE"),
        (field_ink(c.highlight), "HIGHLIGHTING_CARD_OUTLINE ATTACK_ARROW CHAIN_COLOR CARD_RANK"),
        (field_ink(c.accent), "SELECTABLE_CARD_OUTLINE SELECTABLE_FIELD_OUTLINE"),
        (field_ink(c.positive), "HIGHER_CARD_ATK HIGHER_CARD_DEF CARD_TUNER_LEVEL"),
        (field_ink(c.danger), "LOWER_CARD_ATK LOWER_CARD_DEF DISABLED_FIELD_COLOR"),
        (
            c.surface,
            "ANNOUNCE_TEXT_BACKGROUND_COLOR TOOLTIP_TEXT_BACKGROUND_COLOR CARD_SELF_WINDOW_BACKGROUND",
        ),
        (c.raised, "CARD_OPPONENT_WINDOW_BACKGROUND"),
        (c.selection, "CARD_SELECTED_WINDOW_BACKGROUND"),
        (alpha(c.accent, 70), "HOVERED LINKED"),
        (alpha(c.highlight, 70), "MUTUAL_LINKED"),
        (alpha(c.accent, 210 if theme.highlight_turn else 0), "TURNPLAYER_OUTLINE_COLOR"),
        (alpha(c.selection, 135 if theme.highlight_turn else 0), "TURNPLAYER_COLOR"),
        (field_ink(theme.your_lp or theme.accent), "CARD_LINK"),
    )
    values.update(
        ("DUELFIELD_" + key, color) for color, keys in duel_groups for key in keys.split()
    )
    for n, bar in enumerate(c.bars, 1):
        values[f"DUELFIELD_LP_{n}"] = bar.text
        values[f"TIMEBAR_{n}_OUTLINE"] = c.border
        for prefix in ("TIMEBAR", "LPBAR"):
            for corner in CORNERS:
                values[f"{prefix}_{n}_{corner}"] = (
                    bar.top if corner.startswith("TOP") else bar.bottom
                )
    for panel in ("MAIN", "EXTRA", "SIDE", "SEARCH_RESULT"):
        for suffix, opacity in (("", 232), ("_INFO", 242)):
            prefix = f"DECK_WINDOW_{panel}{suffix}_"
            values[prefix + "OUTLINE"] = c.border
            fill = c.row if panel == "SEARCH_RESULT" or suffix else c.surface
            for corner in CORNERS:
                values[prefix + corner] = alpha(fill, opacity)
    return MappingProxyType(values)


def _color(parent: ET.Element, tag: str, hex_color: str) -> None:
    red, green, blue, opacity = QColor(hex_color).getRgb()
    ET.SubElement(
        parent, tag, dict(zip("argb", map(str, (opacity, red, green, blue)), strict=True))
    )


def skin_xml(theme: Theme) -> bytes:
    root = ET.Element("Skin")
    props = ET.SubElement(root, "Properties")
    for key, value in (
        ("Name", theme.name),
        ("Version", str(theme.version)),
        ("Author", APP_NAME),
    ):
        ET.SubElement(props, key, data=value)
    global_node = ET.SubElement(root, "Global", guialpha="255")
    for key, color in _global(theme).items():
        _color(global_node, key, color)
    ET.SubElement(global_node, "Caption", tbardistancex="9", tbardistancey="2")
    ET.SubElement(global_node, "WindowButton", width="20")
    custom_node = ET.SubElement(root, "Custom")
    for key, color in custom(theme).items():
        _color(custom_node, key, color)
    for spec in WIDGETS.values():
        parent = root if "/" not in spec.path else root.find(spec.path.split("/")[0])
        node = ET.SubElement(parent, spec.name, texture=spec.name + ".png")
        _color(node, "Color", WHITE)
        for key in ("SrcBorder", "DstBorder"):
            ET.SubElement(
                node,
                key,
                dict(zip(("tlx", "tly", "brx", "bry"), map(str, spec.border), strict=True)),
            )
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_skin(
    theme: Theme, *, image: QImage | None = None, scale_background: bool = True
) -> dict[str, bytes]:
    files = {name + ".png": png(widget_image(theme, name)) for name in WIDGETS}
    if theme.font == "inter":
        with ZipFile(extras.PACKS / "shared.zip") as archive:
            for name in FONT_FILES[:2]:
                files[name] = archive.read(name)
        files[FONT_FILES[2]] = (extras.PACKS / "Inter-OFL.txt").read_bytes()
    if theme.wallpaper != "inherit":
        files["textures/bg.png"] = png(
            wallpaper(theme, image=image, scale_background=scale_background)
        )
    files["skin.xml"] = skin_xml(theme)
    return files
