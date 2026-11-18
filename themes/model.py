"""Versioned theme definitions. Display names and IDs are deliberately independent."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PureWindowsPath
from typing import Any

from .style_names import PROCEDURAL_STYLES

STYLES = PROCEDURAL_STYLES
OVERRIDES = ("text", "muted", "highlight", "positive", "danger", "your_lp", "opponent_lp")
MAX_DEFINITION = 4096
_DIGEST = re.compile(r"[0-9a-f]{64}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def check_id(value: str) -> str:
    if type(value) is not str or not re.fullmatch("[0-9a-f]{32}", value):
        raise ValueError("Expected a 32-character lower-case hexadecimal theme ID.")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("Repeated JSON field.")
    return obj


@dataclass(frozen=True, slots=True)
class Theme:
    name: str
    background: str
    surface: str
    accent: str
    version: int = 1
    text: str | None = None
    muted: str | None = None
    highlight: str | None = None
    positive: str | None = None
    danger: str | None = None
    your_lp: str | None = None
    opponent_lp: str | None = None
    highlight_turn: bool = True
    roundness: int = 5
    depth: int = 50
    wallpaper: str = "aurora"
    image: str | None = None
    font: str = "inherit"

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Unsupported theme version.")
        if (
            type(self.name) is not str
            or not 1 <= len(self.name) <= 64
            or self.name != self.name.strip()
            or not self.name.isprintable()
            or any(c in self.name for c in "/\\:")
            or self.name.endswith(".")
            or PureWindowsPath(self.name).is_reserved()
            or any(ord(c) in (0xFFFE, 0xFFFF) for c in self.name)
        ):
            raise ValueError("Enter a printable theme name of 1–64 characters.")
        for key in ("background", "surface", "accent", *OVERRIDES):
            value = getattr(self, key)
            if key in OVERRIDES and value is None:
                continue
            if type(value) is not str or not _COLOR.fullmatch(value):
                raise ValueError(f"{key} must be a #RRGGBB colour.")
        if type(self.highlight_turn) is not bool:
            raise ValueError("highlight_turn must be true or false.")
        for key, maximum in (("roundness", 8), ("depth", 100)):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{key} must be an integer from 0 to {maximum}.")
        if self.font not in ("inherit", "inter") or self.wallpaper not in (
            *STYLES,
            "image",
            "inherit",
        ):
            raise ValueError("Unknown font or wallpaper choice.")
        if self.wallpaper == "image":
            if type(self.image) is not str or not _DIGEST.fullmatch(self.image):
                raise ValueError("Image wallpapers need a SHA-256 asset ID.")
        elif self.image is not None:
            raise ValueError("Only an image wallpaper may specify an image.")

    def to_json(self) -> bytes:
        fields = {k: v for k, v in asdict(self).items() if v is not None}
        return (json.dumps(fields, ensure_ascii=False, indent=2) + "\n").encode()

    @classmethod
    def from_json(cls, data: bytes) -> "Theme":
        if len(data) > MAX_DEFINITION:
            raise ValueError("Theme definition is too large.")
        try:
            obj = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
            if not isinstance(obj, dict) or "version" not in obj:
                raise ValueError("Expected a versioned theme object.")
            return cls(**obj)
        except (TypeError, RecursionError) as exc:
            raise ValueError("Invalid theme fields.") from exc
