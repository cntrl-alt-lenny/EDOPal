"""Edit effective assignments without reserializing the rest of system.conf."""

from __future__ import annotations

import base64
import re
import struct
import sys
from dataclasses import dataclass

from gamesettings.options import OPTIONS, equivalent, normalize

Values = dict[str, str | None]
# The client skips one byte on either side of "=" and processes assignments in order.
_LINE = re.compile(
    rb"^(?P<key>\w+)[ \t]=[ \t](?P<leading>[ \t]*)(?P<value>[^\r\n]*?)(?P<suffix>[ \t]*(?:[ \t][#;][^\r\n]*)?)\r?$",
    re.M,
)


@dataclass(frozen=True)
class Change:
    key: str
    before: str | None
    after: str | None


def _value(match: re.Match[bytes]) -> bytes:
    value = match["value"]
    if match["key"] not in (b"textfont", b"driver_type"):
        # The numeric parsers stop at a comment even without a separating space.
        value = re.split(rb"[#;]", value, maxsplit=1)[0].rstrip(b" \t")
    return value


class Document:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.lines = {
            m["key"].decode("ascii"): m for m in _LINE.finditer(data) if m["key"].isascii()
        }
        self.values = {
            k: _value(self.lines[k]).decode("utf-8") if k in self.lines else None for k in OPTIONS
        }
        self.odd_keys = set()
        for raw in data.split(b"\n"):
            left, sep, right = raw.partition(b"=")
            if (
                sep
                and left[:-1].isascii()
                and (left[-1:] not in (b" ", b"\t") or right[:1] not in (b" ", b"\t"))
            ):
                self.odd_keys.add(left[:-1].decode("ascii"))

    def window_size(self) -> str:
        placement = self.lines.get("windowStruct")
        if placement and placement["value"] and sys.platform in ("win32", "darwin"):
            if sys.platform == "win32":
                try:
                    data = base64.b64decode(placement["value"], validate=True)
                    if len(data) == 44 and int.from_bytes(data[:4], "little") == 44:
                        x, y, right, bottom = struct.unpack_from("<4i", data, 28)
                        if right > x and bottom > y:
                            return f"Saved window bounds · {right - x} × {bottom - y}"
                except ValueError:
                    pass
            else:
                number = rb"-?(?:\d+(?:\.\d*)?|\.\d+)"
                frame = re.fullmatch(
                    rb"\{\{("
                    + number
                    + rb"),\s*("
                    + number
                    + rb")\},\s*\{("
                    + number
                    + rb"),\s*("
                    + number
                    + rb")\}\}",
                    placement["value"],
                )
                if frame and float(frame[3]) > 0 and float(frame[4]) > 0:
                    return f"Saved window bounds · {float(frame[3]):g} × {float(frame[4]):g} points"
            return "Saved window placement · managed by EDOPro"
        try:
            scale = float(self.values["dpi_scale"] or OPTIONS["dpi_scale"].default)
            return f"Starting window · {int(1024 * scale)} × {int(640 * scale)}"
        except (ValueError, OverflowError):
            return "Starting window · size unavailable"

    def replace(self, changes: tuple[Change, ...]) -> bytes:
        edits: list[tuple[int, int, bytes]] = []
        additions = bytearray()
        newline = b"\r\n" if self.data.partition(b"\n")[0].endswith(b"\r") else b"\n"
        for change in changes:
            key, value = change.key, change.after
            if key not in OPTIONS or (value is not None and ("\r" in value or "\n" in value)):
                raise ValueError("Invalid display change.")
            if not equivalent(key, self.values[key], change.before):
                raise ValueError("A reviewed setting changed. Reload the values and review again.")
            match = self.lines.get(key)
            if key in self.odd_keys:
                raise ValueError(
                    f"{OPTIONS[key].title} has an unusual assignment. Open and close EDOPro to rewrite its settings, then retry."
                )
            if key == "driver_type" and match and (match["leading"] or match["suffix"]):
                raise ValueError(
                    "The renderer line contains extra text that EDOPro treats as part of its name. Open and close EDOPro to refresh it before changing renderer."
                )
            if match and value is None:
                if (
                    match["suffix"].strip()
                    or _value(match) != match["value"]
                    or sum(m["key"] == key.encode() for m in _LINE.finditer(self.data)) > 1
                ):
                    raise ValueError(
                        "This added setting now has a comment or a duplicate. Keep the current line and review it in EDOPro."
                    )
                end = match.end()
                edits.append((match.start(), end + int(self.data[end : end + 1] == b"\n"), b""))
            elif match and value is not None:
                start = match.start("value")
                edits.append((start, start + len(_value(match)), value.encode()))
            elif value is not None:
                additions.extend(key.encode() + b" = " + value.encode() + newline)
        data = self.data
        for start, end, replacement in sorted(edits, reverse=True):
            data = data[:start] + replacement + data[end:]
        # Insert before an existing line, so even a file without a final newline round-trips.
        offset = re.match(rb"(?:(?:\xef\xbb\xbf)?#[^\n]*\n)*", data).end()
        return data[:offset] + additions + data[offset:]


def preview(values: Values, proposed: dict[str, str]) -> tuple[Change, ...]:
    result = []
    for key, value in proposed.items():
        new = normalize(key, value)
        old = values[key]
        if not equivalent(key, old if old is not None else OPTIONS[key].default, new):
            result.append(Change(key, old, new))
    return tuple(result)


def current_profile(values: Values) -> dict[str, str]:
    """Include only values the editor can offer again without clamping or guessing."""
    result = {}
    for key, option in OPTIONS.items():
        value = values[key] if values[key] is not None else option.default
        try:
            safe = normalize(key, value)
        except ValueError:
            continue
        if equivalent(key, value, safe):
            result[key] = safe
    return result
