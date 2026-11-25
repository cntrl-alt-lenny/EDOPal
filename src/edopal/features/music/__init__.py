"""EDOPro soundtrack packs and local music."""

from edopal.core.feature import FeatureSpec


def register(core) -> FeatureSpec:
    from weakref import ref

    page_ref = None

    def create(services):
        from .page import MusicPage

        nonlocal page_ref
        page = MusicPage(services)
        page_ref = ref(page)
        return page

    def refresh(_services):
        if page_ref and (page := page_ref()) and page.isVisible():
            page.refresh_game()

    return FeatureSpec(
        id="music",
        label="Music",
        icon="music",
        order=30,
        page=create,
        summary="Preview soundtracks and assign tracks to your game.",
        on_install_changed=refresh,
        on_game_exited=refresh,
    )
