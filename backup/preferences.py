"""The portable subset of EDOPro 41.0.2's gframe/game_config.inl."""

import codecs
import re

KEYS = frozenset(
    {"nickname", "hideHandsInReplays", "mute_opponent", "mute_spectators", "autochain"}
)
LIMIT = 1024 * 1024
PREFIX = re.compile(rb"^[ \t]*([A-Za-z0-9_]+)[ \t]*=[ \t]*")


def validate(key: str, value: bytes) -> None:
    if key not in KEYS or len(value) > 256:
        raise ValueError(f"Invalid portable preference: {key}")
    if key == "nickname":
        if any(not c.isprintable() for c in value.decode("utf-8")):
            raise ValueError("Nickname contains control characters")
    elif value not in (b"0", b"1"):
        raise ValueError(f"Expected 0 or 1 for {key}")


def _setting(line: bytes) -> tuple[str, int, int] | None:
    match = PREFIX.match(line)
    if not match or (key := match[1].decode()) not in KEYS:
        return None
    start = match.end()
    value = line[start:].rstrip(b"\r\n \t")
    if key != "nickname" and re.fullmatch(rb"[01][ \t]*[#;].*", value):
        value = value[:1]
    validate(key, value)
    return key, start, start + len(value)


def values(content: bytes) -> dict[str, bytes]:
    result = {}
    for line in content.removeprefix(codecs.BOM_UTF8).splitlines(keepends=True):
        if parsed := _setting(line):
            key, start, end = parsed
            result[key] = line[start:end]
    return result


def merge(content: bytes, changes: dict[str, bytes]) -> bytes:
    """Replace just the last (effective) occurrence. Keep spacing and unrelated bytes."""
    bom = codecs.BOM_UTF8 if content.startswith(codecs.BOM_UTF8) else b""
    lines = content[len(bom) :].splitlines(keepends=True)
    positions = {}
    for index, line in enumerate(lines):
        if parsed := _setting(line):
            key, start, end = parsed
            positions[key] = (index, start, end)
    ending = re.search(rb"\r\n|\n|\r", content)
    newline = ending[0] if ending else b"\n"
    for key, value in changes.items():
        validate(key, value)
        if key in positions:
            index, start, end = positions[key]
            lines[index] = lines[index][:start] + value + lines[index][end:]
        else:
            if lines and not lines[-1].endswith((b"\r", b"\n")):
                lines.append(newline)
            lines.append(key.encode() + b" = " + value + newline)
    return bom + b"".join(lines)
