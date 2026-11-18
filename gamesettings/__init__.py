"""Reversible, offline display settings for a single EDOPro installation."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

from gamesettings.document import Change, Document, Values, preview
from gamesettings.options import OPTIONS, equivalent, font_parts
from gamesettings.storage import atomic, digest, read_json, write_json
from music import settings
from state_lock import appearance_lock


class GameOpenError(RuntimeError):
    """The selected installation has not been confirmed closed."""


def require_closed(game: Path) -> None:
    try:
        opened = settings._game_running(game)
    except OSError as exc:
        raise GameOpenError(
            "Could not check whether EDOPro is open. Close the game and retry."
        ) from exc
    if opened is not False:
        raise GameOpenError("Close the selected copy of EDOPro, then retry your changes.")


@dataclass(frozen=True)
class History:
    original: Values
    last: tuple[Change, ...]


def _history(obj: dict) -> History:
    original = obj.get("original", {})
    last = tuple(Change(**row) for row in obj.get("last", []))
    for key, value in [*original.items(), *((c.key, v) for c in last for v in (c.before, c.after))]:
        if key not in OPTIONS or (
            value is not None and (not isinstance(value, str) or "\r" in value or "\n" in value)
        ):
            raise ValueError("The display recovery record has invalid settings.")
    return History(original, last)


@dataclass(frozen=True)
class Snapshot:
    values: Values
    history: History
    fonts: tuple[str, ...]
    window: str


def recovery(snapshot: Snapshot, mode: str) -> tuple[Change, ...]:
    if mode == "undo":
        for c in snapshot.history.last:
            if not equivalent(c.key, snapshot.values[c.key], c.after):
                raise ValueError(
                    "A setting changed after applying. Review Restore original to choose whether to put it back."
                )
        targets = {c.key: c.before for c in snapshot.history.last}
    elif mode == "restore":
        targets = snapshot.history.original
    else:
        raise ValueError("Unknown recovery action.")
    return tuple(
        Change(k, snapshot.values[k], v) for k, v in targets.items() if snapshot.values[k] != v
    )


class DisplaySettings:
    def __init__(self, game: Path, config_dir: Path) -> None:
        self.game = game
        self.config_dir = config_dir

    def _paths(self) -> tuple[Path, Path]:
        root = self.game.resolve(strict=True)
        conf = root / "config" / "system.conf"
        if conf.resolve() != conf:
            raise OSError("The display settings file points outside its usual location.")
        if not conf.is_file():
            raise FileNotFoundError(
                "Open EDOPro once, close it normally, then reload its display settings."
            )
        record = (
            self.config_dir
            / "game-display"
            / "installations"
            / (digest(os.path.normcase(str(root))) + ".json")
        )
        return conf, record

    @staticmethod
    def _record(path: Path, doc: Document) -> History:
        obj = read_json(path)
        if "pending" in obj:
            # A crash can occur on either side of the single atomic game-file replacement.
            changes = tuple(Change(**row) for row in obj["pending"])
            for side, state in (("after", "next"), ("before", "previous")):
                if all(
                    c.key in OPTIONS and equivalent(c.key, doc.values[c.key], getattr(c, side))
                    for c in changes
                ):
                    return _history(obj[state])
            # A later game edit makes automatic undo ambiguous. Original values can
            # still be reviewed against the live file, key by key, through Restore original.
            return History(_history(obj["next"]).original, ())
        return _history(obj)

    def inspect(self) -> Snapshot:
        conf, record = self._paths()
        doc = Document(conf.read_bytes())
        folder = conf.parent.parent / "fonts"
        fonts = tuple(
            sorted(
                p.relative_to(conf.parent.parent).as_posix()
                for p in folder.glob("*")
                if p.suffix.lower() in (".otf", ".ttf")
                and p.is_file()
                and p.resolve().is_relative_to(conf.parent.parent)
                and not any(c.isspace() for c in p.name)
            )
        )
        return Snapshot(doc.values, self._record(record, doc), fonts, doc.window_size())

    def commit(self, snapshot: Snapshot, proposed: dict[str, str], mode: str = "apply") -> None:
        changes = (
            preview(snapshot.values, proposed) if mode == "apply" else recovery(snapshot, mode)
        )
        if not changes:
            return
        require_closed(self.game)
        conf, record = self._paths()
        with appearance_lock(conf.parent.parent):
            original = conf.read_bytes()
            doc = Document(original)
            history = self._record(record, doc)
            if history != snapshot.history:
                raise ValueError(
                    "Another display change was saved. Reload the settings and review again."
                )
            if not conf.stat().st_mode & stat.S_IWUSR:
                raise PermissionError(
                    "The game settings file is read-only. Allow writing and retry."
                )
            updated = doc.replace(changes)
            for c in changes:
                if c.key == "textfont" and mode == "apply" and c.after:
                    name, _ = font_parts(c.after)
                    if name.lower() == "bundled" and (c.before or "").lower().startswith(
                        "bundled "
                    ):
                        continue
                    relative = PureWindowsPath(name)
                    if relative.anchor or ".." in relative.parts:
                        raise ValueError("Choose a font inside this game folder.")
                    font = conf.parent.parent / name
                    if not font.resolve().is_relative_to(conf.parent.parent) or not font.is_file():
                        raise ValueError(
                            "The selected font is missing from this game. Choose an installed font."
                        )
            next_history = History(
                {**{c.key: doc.values[c.key] for c in changes}, **history.original},
                ()
                if mode == "undo"
                else tuple(Change(c.key, doc.values[c.key], c.after) for c in changes),
            )
            pending = {
                "previous": asdict(history),
                "next": asdict(next_history),
                "pending": [asdict(c) for c in changes],
            }

            def guard(staged: Path) -> None:
                shutil.copymode(conf, staged)
                if staged.read_bytes() != updated:
                    raise OSError("Could not verify the staged display settings.")
                write_json(record, pending)
                try:
                    require_closed(self.game)
                    if self._paths()[0] != conf or conf.read_bytes() != original:
                        raise ValueError(
                            "The game settings changed while saving. Reload and review again."
                        )
                except (OSError, ValueError, GameOpenError):
                    # The staged file has not been published; keep the previous undo usable.
                    write_json(record, asdict(history))
                    raise

            atomic(conf, updated, guard)
            write_json(record, asdict(next_history))
