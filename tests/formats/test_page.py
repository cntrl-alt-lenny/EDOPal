import gc
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QPushButton, QTableWidget
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


def test_page_navigation_clipboard_install_and_download(qtbot, formats_service, tmp_path, monkeypatch):
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

    def open_first_row(table):
        center = table.visualItemRect(table.item(0, 0)).center()
        qtbot.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert len(page.choices) == 128
    dates = [tile.row["date"] for tile in page.tiles.values()]
    assert dates == sorted(dates)
    assert [e.date for e in page.events.events] == sorted((e.date for e in page.events.events), reverse=True)
    click("moreEvents")
    assert page.page_number == 2 and len(page.events.events) == 12 and not page.more.isVisible()
    click("copyHostSettings")
    assert "Gearframe" in QApplication.clipboard().text() and page.mode == "host"
    click("installBanlist")
    assert (core.installs.current().root / "lflists/edopal-hat.lflist.conf").is_file()
    click("browserBack")
    open_first_row(page.events_table)
    assert page.selected_event.name == "Pleiades Payout 25"
    open_first_row(page.standings_table)
    assert page.deck.builder == "goodmorning"
    QApplication.processEvents()
    assert [len(s) for s in page.grid.sections] == [40, 15, 15]
    assert len(page.grid.tiles) == 70
    assert all(page.grid.rect().contains(box.toRect()) for box, _, _ in page.grid.tiles)
    qtbot.mouseMove(page.grid, pos=page.grid.tiles[7][0].center().toPoint())
    assert page.grid.active == 7 and QApplication.activePopupWidget() is None
    qtbot.mouseClick(page.grid, Qt.MouseButton.LeftButton, pos=page.grid.tiles[7][0].center().toPoint())
    popup = QApplication.activePopupWidget()
    assert popup.windowTitle() == page.grid.tiles[7][2]
    qtbot.keyClick(popup, Qt.Key.Key_Escape)
    qtbot.keyClick(page.grid, Qt.Key.Key_Right)
    qtbot.keyClick(page.grid, Qt.Key.Key_Return)
    assert QApplication.activePopupWidget().windowTitle() == page.grid.tiles[page.grid.open_index][2]
    qtbot.keyClick(QApplication.activePopupWidget(), Qt.Key.Key_Escape)
    for action in page.save_menu.actions():
        ext = action.data()
        path = tmp_path / ("my deck." + ext)

        def choose(dialog, *, path=path, ext=ext):
            assert dialog.defaultSuffix() == ext
            assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
            assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
            dialog.selectFile(str(path.with_suffix("")))
            return True

        monkeypatch.setattr(QFileDialog, "exec", choose)
        action.trigger()
        contents = path.read_text()
        if ext == "ydk":
            assert "#main" in contents and "!side" in contents
        else:
            assert all(
                title in contents for title in ("Main Deck (40)", "Extra Deck (15)", "Side Deck (15)", "Genex Controller")
            )
    click("downloadDeck")
    assert (core.installs.current().root / "deck/PPAY25-goodmorning.ydk").is_file()
    click("undoFormatChange")
    assert not (core.installs.current().root / "deck/PPAY25-goodmorning.ydk").exists()
    click("browserBack")
    click("tile-goat")
    qtbot.waitUntil(lambda: page.profile["name"] == "Goat" and page.events is not None)
    assert not page.events.events


def test_scrolling_catalog_filters_sort_and_keyboard(qtbot, formats_service):
    page = register(formats_service.core).page(formats_service.core)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.waitUntil(lambda: len(page.choices) == 128)
    page.library_button.click()
    table = visible(page, QTableWidget, "formatPicker")
    search = visible(page, QLineEdit, "formatSearch")
    assert table.rowCount() == 128 and table.verticalScrollBar().maximum() > 0
    page.regions.setCurrentText("TCG")
    assert len(page.matches) == 93
    page.regions.setCurrentText("OCG")
    assert len(page.matches) == 35
    before = page.profile["id"]
    qtbot.keyClick(table, Qt.Key.Key_Down)
    assert page.profile["region"] == "OCG" and page.profile["id"] != before
    search.setText("Tokyo Dome")
    assert [row["id"] for row in page.matches] == ["tokyo-dome"]
    assert page.profile["date"].startswith("1999-")
    search.setText("nothing-match-0000")
    assert not page.matches and page.profile is None
    search.clear()
    page.regions.setCurrentIndex(0)
    page.sort.setCurrentText("Name A–Z")
    names = [row["name"].casefold() for row in page.matches]
    assert names == sorted(names)
    page.sort.setCurrentText("Newest First")
    dates = [row.get("snapshot_date") or "9999" for row in page.matches]
    assert dates == sorted(dates, reverse=True)
    for key, check in page.checks.items():
        check.setChecked(True)
        assert page.matches and len(page.matches) < 128
        assert all(not row["mechanics"][key] for row in page.matches)
        check.setChecked(False)
    search.setText("Genesys")
    assert page.profile["status"] == "unsupported" and not page.copy_button.isVisible()


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
