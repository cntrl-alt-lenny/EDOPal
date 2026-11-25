"""Connect catalog games to installed audio and remember the user's music library."""

from __future__ import annotations

import re
from dataclasses import replace

from .service import YOUR_MUSIC

DEFAULTS = "EDOPro Defaults"
FAVORITES = "★ Favorites"
ALL = "All Soundtracks"
TABS = (DEFAULTS, FAVORITES, ALL, "Duel Monsters", "GX", "5D's", "ZEXAL", "SEVENS / Rush", "Crossover")


def bind_library(albums, state):
    """Recognize installed soundtrack filenames without fetching an audio pack."""
    stock = next((a for a in albums if a.id == "music-edopro-default"), None)
    games = [a for a in albums if a is not stock]
    if state is None:
        return games, None
    prefixes = {}
    for album in games:
        slug = album.id.removeprefix("music-")
        prefixes[slug + "-"] = album.id
        prefixes.setdefault(re.sub(r"^(gx|5ds|zexal)-", "", slug) + "-", album.id)
    ordered = sorted(prefixes, key=len, reverse=True)
    by_game = {}
    defaults = []
    for track in state.local.tracks:
        stem = track.path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if track.path in state.managed_paths.values():
            continue
        prefix = next((p for p in ordered if stem.startswith(p)), None)
        if prefix:
            title = stem[len(prefix) :].replace("-", " ").title()
            by_game.setdefault(prefixes[prefix], []).append(replace(track, title=title))
        else:
            defaults.append(replace(track, title=stock.builtin_titles.get(track.path, track.title)) if stock else track)
    result = []
    for album in games:
        extra = by_game.get(album.id, [])
        result.append(
            replace(
                album,
                tracks=album.tracks + tuple(extra),
                track_count=album.track_count + len(extra),
                install_root=state.local.install_root,
            )
        )
    return result, replace(
        state.local,
        name=DEFAULTS,
        game=stock.game if stock else "EDOPro",
        year=stock.year if stock else None,
        cover=stock.cover if stock else None,
        tracks=tuple(defaults),
        track_count=len(defaults),
    )


class MusicLibrary:
    def __init__(self, settings):
        self.settings = settings

    def favorites(self):
        return self.settings.get("music", "favorites", [])

    def toggle(self, album, track):
        ref = self.favorite_ref(album, track)
        items = self.favorites()
        items = [item for item in items if item != ref] if ref in items else [*items, ref]
        self.settings.set("music", "favorites", items)

    @staticmethod
    def favorite_ref(album, track):
        return [album.id, track.id, str(album.install_root) if track.role else ""]

    def setups(self, install):
        key = str(install.root) if install else ""
        return self.settings.get("music", "install_setups", {}).get(key, {"active": "Default", "choices": {"Default": {}}})

    def save(self, install, data):
        by_install = dict(self.settings.get("music", "install_setups", {}))
        by_install[str(install.root)] = data
        self.settings.set("music", "install_setups", by_install)

    @staticmethod
    def snapshot(state, albums, choices=None):
        refs = dict(state.sources)
        # Registries from earlier builds identify tracks by their managed filename.
        for role, path in state.managed_paths.items():
            if role in refs:
                continue
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            local_prefix = f"edopal-{YOUR_MUSIC}-"
            if stem.startswith(local_prefix):
                track_id = stem[len(local_prefix) :]
                source = next((a.id for a in albums if any(t.role and t.id == track_id for t in a.tracks)), YOUR_MUSIC)
                refs[role] = [source, track_id]
                continue
            for album in sorted(albums, key=lambda a: -len(a.id)):
                prefix = "edopal-" + album.id.removeprefix("music-") + "-"
                if stem.startswith(prefix):
                    refs[role] = [album.id, stem[len(prefix) :]]
                    break
        # Selecting an original already in its scene needs no managed file.
        for role, ref in (choices or {}).items():
            if role not in state.managed_roles and any(
                track.role == role and track.id == ref[1] for track in state.local.tracks
            ):
                refs[role] = ref
        return refs
