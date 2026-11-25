from PySide6.QtCore import Qt

from edopal.core.updates import UpdateCheck
from edopal.dev.preview import make_core
from edopal.ui import settings_page


def test_manual_release_check_and_download_link(monkeypatch, tmp_path, qtbot):
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    responses = iter([UpdateCheck("v6.1.0", True, False), UpdateCheck("v6.1.0", True, True)])
    monkeypatch.setattr(settings_page, "check_for_updates", lambda cache: next(responses))
    page = settings_page.SettingsPage(core)
    qtbot.addWidget(page)
    qtbot.mouseClick(page._check, Qt.MouseButton.LeftButton)
    assert "v6.1.0" in page._status.text() and page._check.isEnabled()
    assert page._release_url.endswith("/releases/tag/v6.1.0")
    qtbot.mouseClick(page._check, Qt.MouseButton.LeftButton)
    assert "Couldn’t verify" in page._status.text() and "v6.1.0" in page._status.text()


def test_startup_notifies_only_about_verified_updates(monkeypatch, tmp_path):
    core = make_core(None, tmp_path / "packs", tmp_path / "home")
    notices = []
    core.notify = lambda message, kind: notices.append(message)
    results = iter(
        [UpdateCheck("v6.1.0", True, False), UpdateCheck("v6.1.0", True, True), UpdateCheck("v5.9.0", False, False)]
    )
    monkeypatch.setattr(settings_page, "check_for_updates", lambda cache: next(results))
    for _ in range(3):
        settings_page.check_on_startup(core)
    assert len(notices) == 1 and "v6.1.0" in notices[0]
