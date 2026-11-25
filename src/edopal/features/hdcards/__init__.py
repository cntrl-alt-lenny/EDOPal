"""HD card artwork and new-card notifications."""

from edopal.core.feature import FeatureSpec


def register(core):
    from .controller import Controller

    controller = Controller(core)
    controller.refresh()

    def page(core):
        from .page import HDCardPage

        return HDCardPage(core, controller)

    def refresh(_core):
        controller.refresh()

    def badge(_core):
        return str(controller.state.need) if controller.state.need else None

    return FeatureSpec(
        "hdcards",
        "HD Cards",
        "hdcards",
        10,
        page,
        "Sync card faces and field artwork in HD.",
        on_game_exited=refresh,
        on_install_changed=refresh,
        badge=badge,
    )
