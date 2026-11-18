"""Look identities and gallery choices shared by the gallery and theme maker."""

from dataclasses import dataclass, replace
from random import choice
from uuid import NAMESPACE_URL, uuid5

from content.catalogue import PackState, PackStatus
from content.download import DownloadError, DownloadFailure
from themes import PRESETS, InstallState, Theme
from themes.colors import luminance

FILTERS = ("All looks", "Dark", "Light", "Mine", "Downloadable")
STATE_NAMES = {
    PackState.NOT_DOWNLOADED: "Not downloaded",
    PackState.DOWNLOADING: "Downloading",
    PackState.DOWNLOADED: "Ready",
    PackState.UPDATE_AVAILABLE: "Update available",
    PackState.INCOMPATIBLE: "Needs a newer app",
}
EMPTY_STATE = InstallState(None, False, False, False)
BASE = {"background": "Background", "surface": "Surface", "accent": "Accent"}
EXTRA = {
    "text": "Text",
    "muted": "Secondary text",
    "highlight": "Highlight",
    "positive": "Success",
    "danger": "Warning",
    "your_lp": "Your life points",
    "opponent_lp": "Opponent’s life points",
}
PACK_PALETTES = {
    "Moon": ("#211f28", "#382f40", "#c9c1d8"),
    "Abyss": ("#101e25", "#20343c", "#70c7bb"),
    "Nebula": ("#151b34", "#2b3050", "#99a8ee"),
    "Ristretto": ("#271d1b", "#43332e", "#e3b384"),
}


@dataclass(frozen=True)
class Look:
    key: str
    theme: Theme
    group: str
    state: InstallState = EMPTY_STATE
    pack: PackStatus | None = None
    availability: str = ""
    active_pack: bool = False

    @property
    def name(self) -> str:
        return self.pack.pack.title if self.pack else self.theme.name

    @property
    def active(self) -> bool:
        return self.state.active or self.active_pack

    @property
    def light(self) -> bool:
        return luminance(self.theme.background) > 0.35

    @property
    def caption(self) -> str:
        if self.active_pack:
            return f"Active · {self.availability}"
        if self.active:
            return "Active in game"
        if self.pack:
            return self.availability
        return (
            "My theme" if self.group == "Mine" else ("Light theme" if self.light else "Dark theme")
        )


def preset_id(theme: Theme) -> str:
    # The same built-in look must find its game history across app sessions and profiles.
    return uuid5(NAMESPACE_URL, "edopro-studio/presets/" + theme.name).hex


def filter_looks(looks: list[Look], group: str, query: str) -> list[Look]:
    query = query.strip().casefold()
    return [
        look
        for look in looks
        if query in look.name.casefold()
        and (
            group == "All looks"
            or group == look.group
            or (group in ("Light", "Dark") and not look.pack and look.light == (group == "Light"))
        )
    ]


def surprise(theme: Theme) -> Theme:
    palette = choice([p for p in PRESETS if p.accent != theme.accent])
    return replace(theme, **{key: getattr(palette, key) for key in BASE})


def pack_availability(skin: PackStatus, shared: PackStatus | None) -> str:
    if skin.state == PackState.INCOMPATIBLE:
        return STATE_NAMES[skin.state]
    if not shared:
        return "Shared fonts unavailable"
    for state in (
        PackState.INCOMPATIBLE,
        PackState.DOWNLOADING,
        PackState.UPDATE_AVAILABLE,
        PackState.NOT_DOWNLOADED,
    ):
        if state in (skin.state, shared.state):
            return STATE_NAMES[state]
    return "Ready"


def download_message(exc: DownloadError) -> str:
    return {
        DownloadFailure.NOT_FOUND: "This pack is no longer available. Refresh the pack catalogue.",
        DownloadFailure.DISK_SPACE: "There is not enough free space for this pack. Free some space and retry.",
        DownloadFailure.INTEGRITY: "The downloaded files failed verification. Retry to get a fresh copy.",
        DownloadFailure.TRANSIENT: "The download was interrupted. Retry to continue.",
        DownloadFailure.HTTP: "The pack server could not complete this download. Try again later.",
    }.get(exc.reason, str(exc))
