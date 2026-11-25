import gc
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QListView, QPushButton, QTableWidget
from shiboken6 import delete, isValid

from edopal.features.formats import register
from edopal.ui import theme


def test_selected_format_title_fits_minimum_window(qtbot, qapp, formats_service):
    theme.apply(qapp)
    page = register(formats_service.core).page(formats_service.core)
    qtbot.addWidget(page)
    # The 1024×640 shell reserves 75px for navigation and 80px for the install bar.
    page.resize(949, 560)
    page.show()
    qtbot.waitUntil(lambda: page.profile is not None)
    assert page.hero.title.height() >= page.hero.title.fontMetrics().height()
    assert page.hero.title.geometry().bottom() < page.hero.description.geometry().top()


def visible(widget, cls, name):
    QApplication.processEvents()
    return next(child for child in widget.findChildren(cls, name) if child.isVisible())


def test_page_navigation_clipboard_install_and_download(qtbot, formats_service):
    core = formats_service.core
    page = register(core).page(core)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.waitUntil(lambda: page.events is not None)

    def click(name):
        b = visible(page, QPushButton, name)
        assert b.isEnabled()
        qtbot.mouseClick(b, Qt.MouseButton.LeftButton)

    def open_first_row():
        table = visible(page, QTableWidget, "tournamentRows")
        center = table.visualItemRect(table.item(0, 0)).center()
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert len(page.choices) == 128
    click("nextEvents")
    assert page.page_number == 2 and not page.events.events
    click("previousEvents")
    click("copyHostSettings")
    assert "Gearframe" in QApplication.clipboard().text() and page.mode == "host"
    click("installBanlist")
    assert (core.installs.current().root / "lflists/edopal-hat.lflist.conf").is_file()
    click("recentEvents")
    open_first_row()
    assert page.selected_event.name == "Pleiades Payout 25"
    open_first_row()
    assert page.deck.builder == "goodmorning"
    click("extraDeck")
    click("sideDeck")
    click("downloadDeck")
    assert (core.installs.current().root / "deck/PPAY25-goodmorning.ydk").is_file()
    click("undoFormatChange")
    assert not (core.installs.current().root / "deck/PPAY25-goodmorning.ydk").exists()
    picker = visible(page, QListView, "formatPicker")
    model = picker.model()
    goat = next(model.index(i, 0) for i in range(model.rowCount()) if model.index(i, 0).data() == "Goat")
    picker.setCurrentIndex(goat)
    qtbot.waitUntil(lambda: page.profile["name"] == "Goat" and page.events is not None)
    assert not page.events.events


def test_region_search_year_groups_and_keyboard(qtbot, formats_service):
    page = register(formats_service.core).page(formats_service.core)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.waitUntil(lambda: len(page.choices) == 128)
    view = visible(page, QListView, "formatPicker")
    search = visible(page, QLineEdit, "formatSearch")
    role = Qt.ItemDataRole.UserRole

    def entries():
        return [view.model().index(i, 0) for i in range(view.model().rowCount())]

    assert entries()[0].data().casefold() == "popular"
    rows = [i for i in entries() if i.data(role)]
    assert len(rows) == 128 and len({i.data(role) for i in rows}) == 128
    assert view.sizeHintForRow(rows[0].row()) <= 48
    assert any(i.data() == "1999" for i in entries())
    qtbot.mouseClick(visible(page, QPushButton, "regionTCG"), Qt.MouseButton.LeftButton)
    assert len([i for i in entries() if i.data(role)]) == 93
    qtbot.mouseClick(visible(page, QPushButton, "regionOCG"), Qt.MouseButton.LeftButton)
    assert len([i for i in entries() if i.data(role)]) == 35
    before = page.profile["id"]
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert page.profile["region"] == "OCG" and page.profile["id"] != before
    search.setText("Tokyo Dome")
    assert [i.data(role) for i in entries() if i.data(role)] == ["tokyo-dome"]
    assert page.profile["date"].startswith("1999-")
    search.setText("nothing-match-0000")
    assert not any(i.data(role) for i in entries()) and page.profile is None
    search.clear()
    qtbot.mouseClick(visible(page, QPushButton, "regionAll"), Qt.MouseButton.LeftButton)
    search.setText("Genesys")
    assert page.profile["status"] == "unsupported"
    assert not page.findChild(QPushButton, "copyHostSettings").isVisible()


def test_install_change_after_native_page_destruction(qapp, formats_service):
    core = formats_service.core
    page = register(core).page(core)
    reference = weakref.ref(page)
    delete(page)
    assert not isValid(page)
    core.installs.select(core.installs.current().root)
    del page
    gc.collect()
    assert reference() is None
    core.installs.select(core.installs.current().root)


def test_in_flight_job_can_finish_after_page_is_gone(qapp, formats_service):
    core = formats_service.core
    pending = []

    class HeldJobs:
        def run(self, work, *, on_done, on_error, key):
            pending.append((on_done, on_error))

    core.jobs = HeldJobs()
    page = register(core).page(core)
    page.show()
    QApplication.processEvents()
    assert len(pending) == 1
    reference = weakref.ref(page)
    delete(page)
    del page
    gc.collect()
    assert reference() is None
    done, failed = pending.pop()
    done([])
    failed(ValueError("offline"))
