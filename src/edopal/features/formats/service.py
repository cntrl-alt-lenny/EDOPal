"""Qt-free operations used by the Formats page's job runner."""

from __future__ import annotations

import hashlib
import inspect
import unicodedata
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from threading import Lock

from edopal.core.fakes import DirectStorage
from edopal.core.install import Install, RunState
from edopal.core.storage import GameRunningError

from . import deck as ydk
from .client import Deck, DeckPreview, Event, FormatLibrary
from .http_cache import SourceUnavailable
from .lflist import MAX_LIST, read_stock
from .library_pack import FormatPack


@dataclass(frozen=True)
class Standings:
    decks: tuple[Deck, ...]
    stale: bool = False


@dataclass(frozen=True)
class Preview:
    detail: DeckPreview
    thumbnail: bytes = b""
    changes: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    stale: bool = False


def _filename_part(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if c in '<>:"/\\|?*' or unicodedata.category(c).startswith("C") else c for c in text)
    text = " ".join(text.split()).encode("utf-8")[:82].decode("utf-8", errors="ignore").strip(". ")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10))}
    if text.partition(".")[0].upper() in reserved:
        text = "_" + text
    return text or "Unknown"


def _game_path(install: Install, rel: str) -> Path:
    root = install.root.resolve()
    path = root / rel
    if path.parent.parent != root or path.parent.is_symlink() or path.is_symlink():
        raise ValueError("A game file or folder points to another location. Check the selected EDOPro folder.")
    return path


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_LIST:
        return "occupied"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deck_target(install: Install, title: str, content: bytes) -> tuple[str, bool]:
    digest = hashlib.sha256(content).hexdigest()
    for n in count(1):
        suffix = "" if n == 1 else f" ({n})"
        rel = f"deck/{title}{suffix}.ydk"
        existing = _file_digest(_game_path(install, rel))
        if existing is None or existing == digest:
            return rel, existing is None


class FormatsService:
    def __init__(self, core, client: FormatLibrary | None = None):
        self.core = core
        self.pack = FormatPack(core.packs)
        self.client = client if client is not None else FormatLibrary(core.paths.cache / "format-library")
        self._write_lock = Lock()

    def profiles(self, ctx):
        rows = self.pack.profiles(ctx)
        install = self.core.installs.current()
        for row in rows:
            if row.get("status") == "stock":
                try:
                    stock = read_stock(install, row)
                except (OSError, ValueError):
                    row["display"] = "Current stock TCG" if row["id"] == "advanced" else "Current stock Traditional"
                    row["hash"] = ""
                else:
                    row["display"], row["hash"] = stock.name, stock.room_hash
        return rows

    def events(self, ctx, profile, page):
        return self.client.events(profile["name"], page)

    def standings(self, ctx, event):
        return Standings(self.client.standings(event), self.client.stale)

    def preview(self, ctx, profile, install, deck):
        detail = self.client.preview(deck)
        stale = self.client.stale
        try:
            thumbnail = self.client.thumbnail(deck)
        except (ValueError, OSError):
            thumbnail = b""
        if not install or not detail.original or ctx.cancelled:
            return Preview(detail, thumbnail, stale=stale)
        try:
            prepared = self._prepare(ctx, profile, install, detail.source or ydk.encode_ydk(detail.original), detail)
        except (ValueError, OSError, RuntimeError) as exc:
            return Preview(detail, thumbnail, issues=(str(exc),), stale=stale)
        return Preview(detail, thumbnail, prepared.changes, prepared.issues, stale)

    def _prepare(self, ctx, profile, install, source, detail):
        status = profile.get("status", "historical")
        if status == "unsupported":
            return ydk.PreparedDeck(source, (), (profile["reason"],))
        try:
            if status == "stock":
                limits = read_stock(install, profile).limits
            else:
                limits = self.pack.whitelist(profile["id"], ctx).limits
        except (ValueError, OSError, RuntimeError) as exc:
            return ydk.PreparedDeck(source, (), (str(exc),))
        return ydk.prepare(install, limits, source, detail, stock_tcg=status == "stock", sizes=profile["host"])

    @staticmethod
    def host_text(profile) -> str:
        status = profile.get("status", "historical")
        if status == "unsupported":
            return f"{profile['name']} · Not playable in EDOPro yet\n{profile['reason']}"
        host = profile["host"]
        list_name = profile["display"]
        if room_hash := profile.get("hash"):
            list_name += f" ({room_hash})"
        source_list = f"Format Library banlist · {profile['banlist']}"
        if status == "historical" and profile.get("banlist_date"):
            source_list += f" · Effective {profile['banlist_date']}"
        bounds = " · ".join(f"{key.title()} {host[key][0]}–{host[key][1]}" for key in ("main", "extra", "side"))
        return "\n".join(
            [
                f"{profile['name']} · {profile['region']} · {profile['date']}",
                f"Forbidden List · {list_name}",
                source_list,
                "LAN Mode → Create Host → Duel",
                f"Rule · {host['rule']} · 1 vs 1 · Best of 3 · 180 seconds",
                "Shuffle and both deck checks enabled · Relay and Extra Rules off",
                f"Starting LP {host['lp']:,} · Starting hand {host['hand']} · Cards per draw {host['draw']}",
                bounds,
                *host["options"],
                "Use the same list hash and card data on both computers.",
                *profile["known_gaps"],
            ]
        )

    def status(self, ctx, profile, install):
        if profile.get("status") == "unsupported":
            return "unsupported"
        if profile.get("status") == "stock":
            try:
                read_stock(install, profile)
            except (OSError, ValueError):
                return "stock-missing"
            return "stock"
        if not install:
            return "no-game"
        rel = self._list_name(profile)
        try:
            digest = _file_digest(_game_path(install, rel))
        except ValueError:
            return "edited"
        if digest is None:
            return "available"
        if digest == profile.get("sha256"):
            return "installed"
        if digest in self._managed_versions(install, rel):
            return "update"
        return "edited"

    @staticmethod
    def _list_name(profile):
        return f"lflists/edopal-{profile['id']}.lflist.conf"

    @staticmethod
    def _setting(install, key):
        return f"{install.root.resolve()}|{key}"

    def _managed_versions(self, install, rel):
        versions = self.core.settings.get("formats", self._setting(install, rel), [])
        return [versions] if isinstance(versions, str) else versions

    def _check_target(self, ctx, install, confirmed_closed):
        if ctx.cancelled or self.core.installs.current() != install:
            raise ValueError("The selected EDOPro installation changed. Select the action again.")
        if not install or not install.root.is_dir():
            raise ValueError("Select an EDOPro installation first.")
        state = self.core.installs.run_state()
        if state == RunState.RUNNING:
            raise GameRunningError("Close EDOPro before changing game files.")
        if state == RunState.UNKNOWN and not confirmed_closed:
            raise GameRunningError("Confirm that EDOPro is closed before changing game files.")

    def _remember(self, install, writes):
        if not isinstance(self.core.storage, DirectStorage):
            return  # Production storage journals and checks the committed digests itself.
        receipt = self.core.storage.last_receipt(install, "formats")
        if receipt:
            self.core.settings.set(
                "formats",
                self._setting(install, "last-write"),
                {"id": receipt.id, "digests": {p: hashlib.sha256(b).hexdigest() for p, b in writes.items()}},
            )

    def install(self, ctx, profile, install, confirmed_closed=False):
        if profile.get("status") == "unsupported":
            raise ValueError(profile["reason"])
        if profile.get("status") == "stock":
            stock = read_stock(install, profile)
            return f"Use the stock Forbidden List {stock.name} in EDOPro."
        self._check_target(ctx, install, confirmed_closed)
        whitelist = self.pack.whitelist(profile["id"], ctx)
        row = whitelist.profile
        rel = self._list_name(row)
        with self._write_lock:
            self._check_target(ctx, install, confirmed_closed)
            state = self.status(ctx, row, install)
            if state == "installed":
                return f"{row['name']} banlist already installed."
            if state == "edited":
                raise ValueError(f"{rel} contains a different list. Move your file before installing this banlist.")
            with self.core.storage.changeset(
                install, "formats", f"Install {row['name']} banlist", confirmed_closed=confirmed_closed
            ) as change:
                if self.status(ctx, row, install) != state:
                    raise ValueError("The banlist changed during installation. Your file was kept.")
                change.write(rel, whitelist.data)
            versions = self._managed_versions(install, rel)
            if row["sha256"] not in versions:
                self.core.settings.set("formats", self._setting(install, rel), [*versions, row["sha256"]])
            self._remember(install, {rel: whitelist.data})
        return f"{row['name']} banlist installed. Restart EDOPro and choose {row['display']}."

    def save(self, ctx, profile, install, event: Event, deck: Deck, detail: Preview, confirmed_closed=False):
        self._check_target(ctx, install, confirmed_closed)
        preview_source = False
        try:
            source = self.client.download(deck)
        except SourceUnavailable:
            if not detail.detail.source:
                raise
            source = detail.detail.source
            preview_source = True
        try:
            prepared = self._prepare(ctx, profile, install, source, detail.detail)
        except ydk.DeckChanged:
            self.client.forget_deck(deck)
            raise
        issues = prepared.issues
        title = f"{_filename_part(event.code)}-{_filename_part(deck.builder)}"
        with self._write_lock:
            self._check_target(ctx, install, confirmed_closed)
            copies = {}
            if issues or prepared.changes:
                copies[title + " (source)"] = source
            if not issues:
                copies[title] = prepared.content if prepared.changes else source
            writes = {}
            for stem, body in copies.items():
                rel, missing = _deck_target(install, stem, body)
                if missing:
                    writes[rel] = body
            if not writes:
                return f"Already saved · {rel}"
            with self.core.storage.changeset(
                install, "formats", f"Download {deck.name}", confirmed_closed=confirmed_closed
            ) as change:
                for target, body in writes.items():
                    if _file_digest(_game_path(install, target)) is not None:
                        raise ValueError("A deck appeared at the destination. Download again to keep both copies.")
                    change.write(target, body)
            self._remember(install, writes)
        origin = " · Used the cached Format Library preview." if preview_source else ""
        if issues:
            return f"Saved source only · {rel}. {issues[0]}{origin}"
        return f"Saved {rel}" + (" · Original source kept alongside." if prepared.changes else "") + origin

    def can_undo(self, ctx, install):
        return bool(install and self.core.storage.last_receipt(install, "formats"))

    def undo(self, ctx, install, confirmed_closed=False):
        with self._write_lock:
            self._check_target(ctx, install, confirmed_closed)
            receipt = self.core.storage.last_receipt(install, "formats")
            if not receipt:
                return "No Formats change to undo."
            # Preview storage has a simple undo journal. Check the last published bytes
            # here as well, so edits made by the player always win.
            last = self.core.settings.get("formats", self._setting(install, "last-write"), {})
            if isinstance(self.core.storage, DirectStorage) and last.get("id") == receipt.id:
                for rel, digest in last["digests"].items():
                    if _file_digest(_game_path(install, rel)) != digest:
                        raise ValueError(f"{rel} changed since download or installation. Your file was kept.")
            undo = self.core.storage.undo_last
            if confirmed_closed and "confirmed_closed" in inspect.signature(undo).parameters:
                result = undo(install, "formats", confirmed_closed=True)
            else:
                result = undo(install, "formats")
        if result.conflicts:
            return "Undo kept personal edits to " + ", ".join(result.conflicts)
        return f"Undid {receipt.title}."
