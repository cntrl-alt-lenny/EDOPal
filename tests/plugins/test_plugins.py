from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from edopal.core.feature import FeatureSpec
from edopal.core.settings import Settings
from edopal.dev.preview import make_core
from edopal.features.plugins import PluginRegistry
from edopal.ui import theme
from edopal.ui.shell import ShellWindow


def test_concepts_show_screenshots_and_cannot_be_enabled(qapp, qtbot, tmp_path):
    theme.apply(qapp)
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    window = ShellWindow(core, [], "plugins")
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(window.isActiveWindow)
    gallery = window.pages["plugins"]
    assert len(gallery.cards) == 6
    assert gallery.cards["deck-tracker"].status.text() == "Coming Soon"
    assert gallery.cards["replay-library"].status.text() == "Planned"
    assert "deck-tracker" not in window.nav
    gallery.cards["deck-tracker"].details.click()
    assert gallery.drawer.isVisible()
    assert gallery.drawer.state.text() == "Beta Preview · Coming Soon"
    assert gallery.drawer.enable.isHidden()
    assert ("deck-tracker.png", "Deck Tracker Concept") in gallery.drawer.entry.screenshots
    assert not window.plugins.set_enabled("deck-tracker", True)
    qtbot.keyClick(gallery.drawer.close_button, Qt.Key.Key_Escape)
    assert gallery.drawer.isHidden() and gallery.selected is None


def test_enable_disable_round_trip_updates_sidebar_and_settings(qapp, qtbot, tmp_path):
    theme.apply(qapp)
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    calls = []

    def create(_):
        calls.append("created")
        return QLabel("Example Plugin")

    spec = FeatureSpec("example", "Example Plugin", "tracker", 25, create, "An example.", kind="plugin")
    window = ShellWindow(core, [spec], "plugins")
    qtbot.addWidget(window)
    gallery = window.pages["plugins"]

    assert "example" not in window.nav
    core.navigate("example")
    assert not calls
    gallery.cards["example"].details.click()
    assert gallery.drawer.enable.text() == "Enable"
    gallery.drawer.enable.click()
    assert "example" in window.nav and gallery.count.text() == "7 Plugins  ·  1 Enabled"
    assert gallery.drawer.enable.text() == "Disable"
    reread = Settings(core.settings.path)
    assert PluginRegistry([spec], reread).is_enabled("example")

    core.navigate("example")
    assert window.stack.currentWidget().text() == "Example Plugin"
    assert calls == ["created"]
    window._set_plugin_enabled("example", False)
    assert window.current_id == "plugins"
    assert "example" not in window.nav and "example" not in window.pages
    gallery.drawer.enable.click()
    gallery.drawer.enable.click()
    assert not PluginRegistry([spec], Settings(core.settings.path)).is_enabled("example")
