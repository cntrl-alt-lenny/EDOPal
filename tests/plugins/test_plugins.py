import json

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
    assert gallery.cards["deck-tracker"].status.text() == "In Development"
    assert gallery.cards["replay-library"].status.text() == "Planned"
    assert "deck-tracker" not in window.nav
    gallery.cards["deck-tracker"].details.click()
    assert gallery.drawer.isVisible()
    assert gallery.drawer.state.text() == "In Development"
    assert gallery.drawer.enable.isHidden()
    assert not gallery.drawer.install.isEnabled()
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


def test_installable_plugin_progress_and_enable_require_verified_package(qapp, qtbot, monkeypatch, tmp_path):
    theme.apply(qapp)
    root = tmp_path / "packs"
    pack_id = "plugin-deck-tracker"
    package = root / pack_id
    package.mkdir(parents=True)
    (package / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "packs": [
                    {
                        "id": pack_id,
                        "kind": "plugin",
                        "name": "Deck Tracker",
                        "version": "1.0.0",
                        "url": pack_id,
                        "size": 100,
                        "sha256": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    core = make_core(None, root, tmp_path / "home")
    spec = FeatureSpec("deck-tracker", "Deck Tracker", "tracker", 25, lambda _: QLabel("Tracker"), kind="plugin")
    window = ShellWindow(core, [spec], "plugins")
    qtbot.addWidget(window)
    window.show()
    gallery = window.pages["plugins"]
    gallery.cards["deck-tracker"].details.click()

    assert gallery.drawer.state.text() == "Not Installed"
    assert gallery.drawer.install.isEnabled()
    assert not window.plugins.set_enabled("deck-tracker", True)
    assert "deck-tracker" not in window.nav
    ensure = core.packs.ensure

    def observe_progress(pack, progress, cancel):
        assert pack.kind == "plugin"
        progress(35, 100)
        assert gallery.drawer.state.text() == "Downloading…"
        assert gallery.drawer.progress.value() == 35
        assert not gallery.drawer.progress.isHidden()
        assert not gallery.drawer.install.isEnabled()
        return ensure(pack, progress, cancel)

    monkeypatch.setattr(core.packs, "ensure", observe_progress)
    gallery.drawer.install.click()
    assert gallery.drawer.state.text() == "Installed · Disabled"
    assert gallery.drawer.install.isHidden() and gallery.drawer.progress.isHidden()
    gallery.drawer.enable.click()
    assert gallery.drawer.state.text() == "Installed · Enabled"
    assert "deck-tracker" in window.nav
    window.close()

    reopened = ShellWindow(core, [spec], "plugins")
    qtbot.addWidget(reopened)
    reopened.show()
    assert "deck-tracker" in reopened.nav
    assert reopened.plugins.is_installed("deck-tracker")
