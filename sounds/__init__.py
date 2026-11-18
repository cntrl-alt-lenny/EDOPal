"""The widget-free duel sound-effects engine for EDOPro Studio."""

from .archive import PACK_KIND, SavedSet, SoundLibrary, export_set, load_pack, read_set
from .audio import Clip, Credits, load_clip
from .audition import AuditionPlayer
from .catalogue import CHANT_TYPES, EVENTS, Event, chant_key, event_for
from .install import apply_set, can_restore, can_undo, restore, undo
from .sets import InstalledCue, SoundSet, bundled_sets, installed_set, scan_installation
from .settings import SoundSettings, set_sound_enabled, sound_settings

__all__ = [
    "CHANT_TYPES",
    "EVENTS",
    "PACK_KIND",
    "AuditionPlayer",
    "Clip",
    "Credits",
    "Event",
    "InstalledCue",
    "SavedSet",
    "SoundLibrary",
    "SoundSet",
    "SoundSettings",
    "apply_set",
    "bundled_sets",
    "can_restore",
    "can_undo",
    "chant_key",
    "event_for",
    "export_set",
    "installed_set",
    "load_clip",
    "load_pack",
    "read_set",
    "restore",
    "scan_installation",
    "set_sound_enabled",
    "sound_settings",
    "undo",
]
