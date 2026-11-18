"""Blocking page operations. Call these on a Studio worker with captured arguments."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

import config
import extras
import sleeves
import themes
from content import catalogue, download
from content.cache import PackCache
from content.manifest import Pack
from content.media import Media
from music import settings
from state_lock import appearance_lock
from studio import theme as t
from studio.context import Installation
from studio.tasks import WorkerContext

from .skins_model import EMPTY_STATE, PACK_PALETTES, Look, pack_availability, preset_id


@dataclass
class Collection:
    looks: list[Look]
    shared: Pack | None
    catalogue: catalogue.Catalogue
    pending: bool
    restore_pack: bool
    highlights: dict[str, bool]
    scale: bool
    active_name: str
    wallpapers: tuple[Media, ...]
    sleeve_summary: str


def sleeve_summary(library: sleeves.Library, game: Path | None) -> str:
    if not game or not library.root.is_dir():
        return ""
    try:
        slots = sleeves.status(library, game)
        assigned = {slot: status for slot, status in slots.items() if status.entry_id}
        if not assigned:
            return ""
        names = {entry.id: entry.name for entry in library.entries()}
        if any(s.entry_id not in names for s in assigned.values()):
            names.update({entry.id: entry.name for entry in library.entries(in_bin=True)})
        parts = []
        for slot, status in assigned.items():
            owner = "Your sleeve" if slot == "mine" else "Opponent"
            name = names.get(status.entry_id, "Assigned sleeve")
            changed = " (changed in game)" if status.matches is False else ""
            parts.append(f"{owner} · {name}{changed}")
        return "    ·    ".join(parts)
    except (OSError, ValueError, KeyError):
        return "Could not read assigned sleeves"


def ensure_closed(game: Path) -> None:
    try:
        running = settings._game_running(game)
    except OSError as exc:
        raise OSError("Could not check whether EDOPro is open. Close the game and retry.") from exc
    if running:
        raise OSError("EDOPro is running. Close this copy of the game, then choose Retry.")


def read_image(path: Path, width: int) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(QSize(width, width), Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


class SkinStore:
    def __init__(self, cfg: config.Config, version: str) -> None:
        self.config = Path(cfg.config_path)
        self.root = self.config.parent if cfg.cli.config else Path(config.user_data_dir())
        self.library = themes.ThemeLibrary(self.root / "themes")
        self.cache = PackCache.for_config(cfg)
        self.version = version
        self.url = cfg.content_manifest_url

    def collection(
        self, installation: Installation, source: catalogue.Catalogue | None, online: bool
    ) -> Collection:
        try:
            if online:
                source = asyncio.run(catalogue.fetch_catalogue(self.cache, config_url=self.url))
            if source is None:
                source = catalogue.offline_catalogue(self.cache, config_url=self.url)
        except (ValueError, OSError) as exc:
            source = catalogue.Catalogue(
                source.manifest if source else catalogue.bundled_manifest(),
                catalogue.CatalogueSource.CACHED if source else catalogue.CatalogueSource.BUNDLED,
                str(exc),
            )
        game = Path(installation.path) if installation.path else None
        if game is not None and not game.is_dir():
            game = None
        entries = [(preset_id(p), p, "Preset") for p in themes.PRESETS]
        saved = self.library.entries()
        entries.extend((key, value, "Mine") for key, value in saved.items())
        looks = [
            Look(key, value, group, themes.install_state(game, key) if game else EMPTY_STATE)
            for key, value, group in entries
        ]
        if game:
            for key, value in self.library.removed_entries().items():
                if key not in saved and (state := themes.install_state(game, key)).folder:
                    looks.append(Look(key, value, "Installed", state))
        statuses = source.list_packs(self.cache, app_version=self.version)
        shared = next((s for s in statuses if s.pack.id == "shared"), None)
        active = extras.current_skin(str(game)) if game else ""
        for status in statuses:
            pack = status.pack
            if pack.kind != "skin" or pack.id == "shared":
                continue
            colours = PACK_PALETTES.get(pack.title, PACK_PALETTES["Abyss"])
            seed = themes.Theme("My theme", *colours)
            looks.append(
                Look(
                    "pack/" + pack.id,
                    seed,
                    "Downloadable",
                    pack=status,
                    availability=pack_availability(status, shared),
                    active_pack=active == "Monokai " + pack.title,
                )
            )
        return Collection(
            looks,
            shared.pack if shared else None,
            source,
            any(look.state.recovery_pending for look in looks),
            bool(game and extras.can_restore(str(game), str(self.config))),
            {
                name: bool(game and extras.turn_highlight_enabled(game, name))
                for name in extras.SKINS
            },
            themes.scale_background(game) if game else True,
            next(
                (look.name for look in looks if look.active),
                "Installed theme"
                if active.startswith("Studio-")
                else active.removeprefix("Monokai ") or "Game default",
            ),
            self.library.wallpapers(),
            sleeve_summary(sleeves.Library(self.root / "sleeves"), game),
        )

    def backdrop(self, game: Path | None, scene: str) -> QImage | None:
        if game:
            basename = {"deck": "bg_deck", "duel": "bg_duel", "menu": "bg_menu"}[scene]
            for name in (basename, "bg"):
                for ext in ("png", "jpg"):
                    path = game / "textures" / f"{name}.{ext}"
                    if path.is_file():
                        image = read_image(path, 1280)
                        if not image.isNull():
                            return image
        return None

    def pack_image(
        self,
        look: Look,
        scene: str,
        highlight: bool,
        opponent: bool,
        worker: WorkerContext,
        width: int,
    ) -> QImage:
        assert look.pack
        pack = look.pack.pack
        if pack.title not in extras.SKINS:
            path = asyncio.run(
                download.fetch_preview(self.cache, pack, pack.previews[0], cancel=worker.cancel)
            )
            return read_image(path, width)
        folder = t.ASSETS / "window/gallery/large"
        variant = "turn" if scene == "duel" and highlight else scene
        image = read_image(folder / f"{pack.title.lower()}-{variant}.png", width)
        if scene == "duel" and opponent and not image.isNull():
            # The four captures have identical geometry and two full life bars.
            # Move the frames and re-letter the names on their original sides.
            w, h = image.width(), image.height()
            left = QRect(round(w * 0.317), 0, round(w * 0.301), round(h * 0.085))
            right = left.translated(round(w * 0.353), 0)
            a, b = image.copy(left), image.copy(right)
            for bar in (a, b):
                band = QRect(1, round(h * 0.050), bar.width() - 2, round(h * 0.027))
                clean = bar.copy(QRect(bar.width() // 2, band.y(), 1, band.height()))
                brush = QPainter(bar)
                brush.drawImage(band, clean)
                brush.end()
            painter = QPainter(image)
            painter.drawImage(left, b)
            painter.drawImage(right, a)
            painter.setFont(t.font(max(6, round(h * 0.020))))
            painter.setPen(QColor(t.INK))
            for box, name, align in (
                (left, "Duelist", Qt.AlignmentFlag.AlignLeft),
                (right, "Opponent", Qt.AlignmentFlag.AlignRight),
            ):
                inner = box.adjusted(
                    round(w * 0.009), round(h * 0.048), -round(w * 0.009), -round(h * 0.003)
                )
                painter.drawText(inner, align | Qt.AlignmentFlag.AlignVCenter, name)
            painter.end()
        return image

    def thumbnail(
        self, look: Look, game: Path | None, scale: bool, worker: WorkerContext
    ) -> QImage:
        if look.pack:
            return self.pack_image(look, "menu", False, False, worker, 480)
        theme = look.theme
        image = (
            self.backdrop(game, "menu")
            if theme.wallpaper == "inherit"
            else self.library.image(theme)
        )
        return themes.render_preview(theme, "menu", (480, 300), image=image, scale_background=scale)

    async def download(self, skin: Pack, shared: Pack, worker: WorkerContext) -> None:
        for pack, name in ((skin, skin.title), (shared, "Shared fonts")):

            def progress(value: download.Progress, name: str = name) -> None:
                worker.progress((name, value))

            await download.download_pack(
                self.cache, pack, app_version=self.version, cancel=worker.cancel, progress=progress
            )

    def change(
        self, game: Path, action: str, look: Look | None, shared: Pack | None, highlight: bool
    ) -> str:
        if not config.folder_has_card_databases(str(game)):
            raise ValueError(
                "The game folder is no longer ready. Reconnect it or choose another folder."
            )
        with appearance_lock(game):
            ensure_closed(game)
            if action == "sleeves":
                sleeves.reapply(sleeves.Library(self.root / "sleeves"), game, self.config)
                return "Your assigned sleeves are back in game."
            message = self._change_look(game, action, look, shared, highlight)
            # The legacy appearance installer carries sleeves across pack changes itself.
            if action != "restore" and not (look and look.pack):
                try:
                    sleeves.reapply(sleeves.Library(self.root / "sleeves"), game, self.config)
                except (OSError, ValueError, KeyError) as exc:
                    raise extras.SleeveReapplyError(
                        f"{message} Could not restore your sleeves. {exc}"
                    ) from exc
            return message + " Your chosen sleeves carry over."

    def _change_look(
        self, game: Path, action: str, look: Look | None, shared: Pack | None, highlight: bool
    ) -> str:
        if action == "recover":
            completed = themes.recover(game)
            message = {
                "install": "Finished installing the theme that was interrupted.",
                "undo": "Finished undoing the interrupted theme change.",
                "remove": "Finished removing the theme from this game.",
            }.get(completed, "Your game’s appearance is up to date.")
        elif action == "restore":
            extras.apply_appearance(
                str(game), str(self.config), restore=True, pack_cache=self.cache
            )
            message = "Restored the look and fonts you used before the skin packs."
        else:
            assert look
            if action in ("apply", "highlight") and look.pack:
                extras.apply_appearance(
                    str(game),
                    str(self.config),
                    skin=look.name,
                    skin_pack=None if action == "highlight" else look.pack.pack,
                    shared_pack=shared,
                    pack_cache=self.cache,
                    highlight_turn=highlight if look.name in extras.SKINS else None,
                )
            elif action == "apply":
                themes.install_theme(
                    game, look.key, look.theme, image=self.library.image(look.theme)
                )
            else:
                {
                    "back": themes.switch_back,
                    "undo": themes.undo_theme,
                    "remove": themes.remove_theme,
                }[action](game, look.key)
            message = {
                "apply": f"{look.name} is set for the next time you start EDOPro.",
                "highlight": "Turn colours updated for the next time you start EDOPro.",
                "back": "Your previous look and its fonts are selected again.",
                "undo": f"Reversed the last change to {look.name} in the game.",
                "remove": f"Removed {look.name} from the game.",
            }[action]
        return message
