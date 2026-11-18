"""Local controls for browsing published events and standings."""

from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QInputEvent, QKeyEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QStackedWidget,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.icons import icon
from studio.pages.decks_flow import STARTER_FORMATS
from studio.widgets import Button, ElidedLabel, Hero, Panel, SearchField, ToggleSwitch, glyph, label
from tournament_decks import Deck, Event


class Rows(QTreeWidget):
    def __init__(
        self, columns: tuple[str, ...], *, multiple: bool = False, dense: bool = False
    ) -> None:
        super().__init__()
        self.multiple = multiple
        self.row_height = t.XXL if dense else t.BUTTON_H
        self.setHeaderLabels(columns)
        self.setAccessibleName("Standings" if multiple else "Tournament events")
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setMouseTracking(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(t.TILE_H + t.XXL)
        self.header().setStretchLastSection(False)
        if dense:
            self.header().setFixedHeight(t.XXL)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.header().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        if multiple:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setStyleSheet(f"""
            QTreeWidget {{
                background: {t.RAIL}; border: {t.HAIR}px solid {t.EDGE};
                border-radius: {t.R_SMALL}; outline: 0;
            }}
            QTreeWidget:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
            QTreeWidget::item {{ padding-left: {t.SM}px; border-bottom: {t.HAIR}px solid {t.PANEL}; }}
            QTreeWidget::item:selected {{ background: {t.ACTIVE}; color: {t.PALE}; }}
            QTreeWidget::item:hover:!selected {{ background: {t.PANEL}; }}
            QHeaderView {{ background: {t.RAISED}; }}
            QHeaderView::section {{
                padding: {t.SM}px; background: {t.RAISED}; color: {t.MUTED};
                border: 0; font-size: {t.SMALL}px;
            }}
        """)

    def fixed_column(self, column: int, width: int) -> None:
        self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(column, width)

    def row(self, values: tuple[str, ...]) -> QTreeWidgetItem:
        item = QTreeWidgetItem(values)
        item.setSizeHint(0, QSize(0, self.row_height))
        for column, value in enumerate(values):
            item.setToolTip(column, value)
        self.addTopLevelItem(item)
        return item

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.currentItem() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.itemActivated.emit(self.currentItem(), self.currentColumn())
            event.accept()
        else:
            super().keyPressEvent(event)

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        super().drawRow(painter, option, index)
        if self.hasFocus() and index == self.currentIndex().siblingAtColumn(0):
            painter.save()
            painter.setPen(QPen(QColor(t.GOLD), t.HAIR))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(t.HAIR, t.HAIR, -t.HAIR, -t.HAIR))
            painter.restore()

    def selectionCommand(
        self, index: QModelIndex, event: QInputEvent | None = None
    ) -> QItemSelectionModel.SelectionFlag:
        if self.multiple and index.isValid() and isinstance(event, QKeyEvent):
            # Arrow keys move the focus without dropping the batch. Space toggles a row.
            flags = QItemSelectionModel.SelectionFlag
            if event.key() == Qt.Key.Key_Space:
                return flags.Toggle | flags.Rows
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and not event.modifiers():
                return flags.NoUpdate
        return super().selectionCommand(index, event)


class ListMessage(Panel):
    def __init__(self) -> None:
        super().__init__(t.LG)
        self.setStyleSheet(f'QFrame[role="panel"] {{ background: {t.RAIL}; }}')
        center = Qt.AlignmentFlag.AlignHCenter
        self.body.addStretch()
        symbol = QHBoxLayout()
        symbol.addStretch()
        self.icon = glyph("search", t.ICON_BIG)
        symbol.addWidget(self.icon)
        symbol.addStretch()
        self.body.addLayout(symbol)
        self.title = label("", "subheading", True)
        self.description = label("", "muted", True)
        for widget in (self.title, self.description):
            widget.setAlignment(center)
            self.body.addWidget(widget)
        self.retry = Button("Retry")
        self.body.addWidget(self.retry, 0, center)
        self.body.addStretch()

    def set_copy(self, title: str, description: str, retry: bool) -> None:
        self.icon.setPixmap(
            icon("warning" if retry else "search").pixmap(
                self.icon.size(), self.icon.devicePixelRatioF()
            )
        )
        self.title.setText(title)
        self.description.setText(description)
        self.retry.setVisible(retry)


class FormatPicker(QComboBox):
    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("Tournament format")
        self.addItems(STARTER_FORMATS)
        self.setCurrentText("Goat")
        self.setMinimumHeight(t.BUTTON_H)
        self.setMinimumWidth(t.TILE_SIZE[0])
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setStyleSheet(f"""
            QComboBox {{
                background: {t.RAISED}; border: {t.HAIR}px solid {t.EDGE};
                border-radius: {t.R_SMALL}; padding: 0 {t.XXL}px 0 {t.MD}px;
            }}
            QComboBox:hover {{ border-color: {t.MUTED}; }}
            QComboBox:focus {{ border: {t.FOCUS}px solid {t.PALE}; }}
            QComboBox::drop-down {{ width: {t.XXL}px; border: none; }}
            QComboBox QAbstractItemView {{ background: {t.PANEL}; color: {t.INK}; }}
        """)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self.isEnabled():
            painter = QPainter(self)
            painter.translate(self.width() - t.LG, self.height() / 2)
            painter.rotate(90)
            icon("chevron", t.TEXT).paint(painter, -t.SM, -t.SM, t.LG, t.LG)


class Browser(QWidget):
    def __init__(self) -> None:
        super().__init__()
        body = QVBoxLayout(self)
        body.setSpacing(t.MD)
        body.setContentsMargins(t.XXL, t.XL, t.XXL, t.XL)
        self.hero = Hero(
            "Deck library",
            "Tournament decks",
            "Browse tournament results and bring published decks into EDOPro.",
            "decks",
        )
        self.hero.setMinimumHeight(t.HERO_MIN - t.HUGE - t.SM)
        self.compact: bool | None = None
        body.addWidget(self.hero)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(t.SM)
        self.format = FormatPicker()
        self.search = SearchField("Search by event name or code")
        self.search.setMinimumHeight(t.BUTTON_H)
        self.submit = Button("Search")
        self.credit = Button("Format Library", "quiet", "link")
        toolbar.addWidget(self.format)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.submit)
        toolbar.addWidget(self.credit)
        body.addLayout(toolbar)

        panels = QHBoxLayout()
        panels.setSpacing(t.LG)
        events_panel = Panel(t.LG)
        events_panel.body.setSpacing(t.SM)
        events_panel.body.addWidget(label("Events", "heading"))
        self.event_context = label("", "muted", True)
        events_panel.body.addWidget(self.event_context)
        self.events = Rows(("Tournament", "Date"), dense=True)
        self.events.fixed_column(1, t.TILE_H + t.XL)
        self.event_message = ListMessage()
        self.event_views = QStackedWidget()
        self.event_views.addWidget(self.events)
        self.event_views.addWidget(self.event_message)
        events_panel.body.addWidget(self.event_views, 1)
        paging = QHBoxLayout()
        self.previous = Button("Previous", "quiet", "back")
        self.next = Button("Next", "quiet", "arrow")
        self.page_number = label("Page 1", "muted")
        paging.addWidget(self.previous)
        paging.addStretch()
        paging.addWidget(self.page_number)
        paging.addStretch()
        paging.addWidget(self.next)
        events_panel.body.addLayout(paging)
        panels.addWidget(events_panel, 4)

        standings_panel = Panel(t.LG)
        standings_panel.body.setSpacing(t.SM)
        self.standings_title = ElidedLabel()
        self.standings_title.setProperty("role", "heading")
        self.standings_title.set_value("Standings")
        standings_panel.body.addWidget(self.standings_title)
        self.event_meta = label("Public decklists", "muted", True)
        standings_panel.body.addWidget(self.event_meta)
        self.standings = Rows(("Place", "Player", "Deck"), multiple=True, dense=True)
        self.standings.fixed_column(0, t.HUGE + t.SM)
        self.standings_message = ListMessage()
        self.standings_views = QStackedWidget()
        self.standings_views.addWidget(self.standings)
        self.standings_views.addWidget(self.standings_message)
        standings_panel.body.addWidget(self.standings_views, 1)
        tools = QHBoxLayout()
        self.event_link = Button("View event", "quiet", "link")
        self.select_all = Button("Select all", "quiet")
        tools.addWidget(self.event_link)
        tools.addStretch()
        tools.addWidget(self.select_all)
        standings_panel.body.addLayout(tools)
        panels.addWidget(standings_panel, 6)
        self.results = QWidget()
        panels.setContentsMargins(0, 0, 0, 0)
        self.results.setLayout(panels)
        body.addWidget(self.results, 1)

        self.unavailable = Panel()
        self.unavailable.body.addStretch()
        self.unavailable.body.addWidget(glyph("folder", t.BRAND_ICON))
        self.unavailable.body.addWidget(label("Choose an EDOPro installation", "heading", True))
        self.unavailable.body.addWidget(
            label("Choose where your tournament decks will be saved.", "body", True)
        )
        self.choose = Button("Choose folder", "primary", "folder")
        self.unavailable.body.addWidget(self.choose, 0, Qt.AlignmentFlag.AlignLeft)
        self.unavailable.body.addStretch()
        body.addWidget(self.unavailable, 1)

        footer = Panel(t.MD)
        footer.body.setSpacing(t.SM)
        self.footer = footer
        self.goat = ToggleSwitch(
            "Use GOAT card versions",
            "Save a separate historical copy.",
            False,
        )
        self.goat.setMinimumWidth(t.TILE_LARGE * 2 + t.LG)
        self.footer_row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.footer_row.setSpacing(t.SM)
        footer.body.addLayout(self.footer_row)
        self.footer_row.addWidget(self.goat)
        actions = QHBoxLayout()
        actions.setSpacing(t.SM)
        details = QVBoxLayout()
        details.setSpacing(t.XS)
        self.selection_count = label("0 selected", "subheading")
        self.destination = ElidedLabel()
        details.addWidget(self.selection_count)
        details.addWidget(self.destination)
        actions.addLayout(details, 1)
        actions.setAlignment(details, Qt.AlignmentFlag.AlignVCenter)
        self.preview = Button("Preview deck")
        self.recent = Button("Last save", "quiet")
        self.save = Button("Add to EDOPro", "primary", "download")
        actions.addWidget(self.recent)
        actions.addWidget(self.preview)
        actions.addWidget(self.save)
        self.footer_row.addLayout(actions, 1)
        self.readiness = label(
            "Open EDOPro once to download its cards before playing or getting artwork.",
            "small",
            True,
        )
        footer.body.addWidget(self.readiness)
        body.addWidget(footer)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.footer_row.setDirection(
            QBoxLayout.Direction.LeftToRight
            if self.width() >= t.HERO_MAX + t.TILE_LARGE * 2
            else QBoxLayout.Direction.TopToBottom
        )
        compact = event.size().width() + t.RAIL_COMPACT < t.COMPACT_WIDTH
        if compact != self.compact:
            self.compact = compact
            self.hero.headline.setStyleSheet(f"font-size: {t.XXL}px;" if compact else "")
            trim = t.XXL if compact else t.SM
            self.hero.setMinimumHeight(t.HERO_MIN - t.HUGE - trim)

    def set_formats(self, choices: tuple[str, ...]) -> None:
        selected = self.format.currentText()
        with QSignalBlocker(self.format):
            self.format.clear()
            self.format.addItems(choices)
            if selected not in choices:
                self.format.addItem(selected)
            self.format.setCurrentText(selected)

    def set_events(self, events: tuple[Event, ...]) -> None:
        with QSignalBlocker(self.events):
            self.events.clear()
            for event in events:
                self.events.row((event.name, event.date or "—"))
            if events:
                self.events.setCurrentItem(self.events.topLevelItem(0))
        self.event_views.setCurrentWidget(self.events if events else self.event_message)

    def set_standings(self, decks: tuple[Deck, ...], saved: dict[int, set[Path]]) -> None:
        with QSignalBlocker(self.standings):
            self.standings.clear()
            for deck in decks:
                item = self.standings.row((str(deck.place or "—"), deck.builder, deck.name))
                item.setData(0, Qt.ItemDataRole.UserRole, deck.id)
                if deck.id in saved:
                    item.setForeground(2, QColor(t.GREEN))
                    item.setText(2, f"✓  {deck.name}")
                    item.setToolTip(2, f"{deck.name} · Saved in this installation")
            if decks:
                self.standings.setCurrentItem(self.standings.topLevelItem(0))
        self.standings_views.setCurrentWidget(self.standings if decks else self.standings_message)
