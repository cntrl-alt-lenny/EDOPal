"""Compact sound-pack tiles and the searchable game picker."""

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from edopal.features.music.widgets import icon as audio_icon
from edopal.ui import theme as t
from edopal.ui.page_chrome import label
from edopal.ui.widgets import QuietButton

from .releases import GAMES, REGIONS, release_date

_FOCUS = {
    "gx-tag-force-3": 0.98,
    "gx-spirit-caller": 0.73,
    "gx-duel-academy": 0.65,
    "gx-tag-force": 0.75,
    "gx-tag-force-2": 0.71,
    "gx-beginning-of-destiny": 0.72,
}
_COLUMNS = (("Series", 83), ("Release", 91), *((region, 88) for region in REGIONS), ("Status", 119))

_PICKER_STYLE = f"""
    QDialog#soundPicker {{ background: {t.PANEL}; border: 1px solid #80725c; border-radius: 6px; }}
    QPushButton#soundPickerClose {{ background: {t.PANEL}; border: 1px solid {t.EDGE}; padding: 0; }}
    QPushButton#soundPickerClose:hover {{ border-color: #927951; }}
    QPushButton#soundReleaseOrder {{ color: {t.GOLD}; text-align: left; font-size: 12px; padding: 0; }}
    QListWidget {{ background: transparent; border: 0; outline: 0; padding: 0; }}
    QListWidget::item {{ background: #27272a; border: 1px solid {t.RULE}; border-radius: 4px; }}
    QListWidget::item:selected, QListWidget::item:hover {{ background: #39332b; border-color: #927951; }}
    QComboBox {{ background: {t.RAISED}; border: 1px solid {t.EDGE}; border-radius: 4px; padding: 4px 8px; }}
    QLabel[role="soundTileTitle"] {{ font: 500 15px Outfit; }}
    QLabel[role="soundPackStatus"][downloaded="true"] {{ color: {t.GREEN}; }}
    QLabel[role="soundPackStatus"] {{ color: {t.MUTED}; font-size: 12px; }}
"""


@lru_cache(maxsize=80)
def _banner(path):
    reader = QImageReader(str(path))
    size = reader.size()
    if size.width() > 480:
        reader.setScaledSize(QSize(480, round(480 * size.height() / size.width())))
    return QPixmap.fromImage(reader.read())


class GameCover(QWidget):
    def __init__(self, *, tint=None, symbol=None):
        super().__init__()
        self.tint = tint or "#242426"
        self.symbol = symbol
        self.cover = QPixmap()
        self.focus = 0.60

    def set_cover(self, slug, path):
        self.cover = _banner(path) if path else QPixmap()
        self.focus = _FOCUS.get(slug, 0.60)
        self.update()

    def paintEvent(self, _event):
        box = QRectF(self.rect())
        with QPainter(self) as painter:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
            clip = QPainterPath()
            clip.addRoundedRect(box, 3, 3)
            painter.setClipPath(clip)
            painter.fillPath(clip, QColor(self.tint))
            if not self.cover.isNull():
                scale = max(box.width() / self.cover.width(), box.height() / self.cover.height())
                width, height = box.width() / scale, box.height() / scale
                source = QRectF((self.cover.width() - width) * self.focus, (self.cover.height() - height) / 2, width, height)
                painter.drawPixmap(box, self.cover, source)
            elif self.symbol:
                size = round(min(box.width(), box.height()) * 0.68)
                t.icon(self.symbol, t.GOLD if self.symbol == "logo" else t.MUTED).paint(
                    painter, round((box.width() - size) / 2), round((box.height() - size) / 2), size, size
                )


class SoundTile:
    def __init__(self, title, subtitle, *, button=False, logo=False):
        self.widget = QuietButton("") if button else QFrame()
        self.widget.setObjectName("soundTile")
        self.widget.setProperty("stock", logo)
        self.widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.widget.setAccessibleName(title)
        self.cover = GameCover(tint="#242321", symbol="logo") if logo else None
        self.title = label(title, "soundTileTitle")
        self.subtitle = label(subtitle, "muted") if subtitle is not None else None
        line = QHBoxLayout(self.widget)
        line.setContentsMargins(6 if logo else 12, 5, 10, 5)
        line.setSpacing(11)
        if self.cover:
            line.addWidget(self.cover)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(self.title)
        if self.subtitle:
            text.addWidget(self.subtitle)
        line.addLayout(text, 1)
        if button or logo:
            arrow = label("")
            icon = "chevron-down" if button else "check"
            arrow.setPixmap(t.icon(icon, t.GOLD).pixmap(QSize(14, 14)))
            line.addWidget(arrow)
        if button:
            for child in self.widget.findChildren(QWidget):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_height(self, height):
        self.widget.setFixedHeight(height)
        size = height - 12
        if self.cover:
            self.cover.setFixedSize(size, size)


class _GameItem(QListWidgetItem):
    def __init__(self, parent, game):
        super().__init__(parent)
        self.game = game
        self.setSizeHint(QSize(0, 61))
        self.setData(Qt.ItemDataRole.UserRole, game.slug)

    def __lt__(self, other):
        return (self.game.first_release, self.game.name.casefold()) < (other.game.first_release, other.game.name.casefold())


class GamePicker(QDialog):
    def __init__(self, parent, selected, banners, get_status):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("soundPicker")
        self.setStyleSheet(_PICKER_STYLE)
        self.setAccessibleName("Game Sound Packs")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.game = None
        self.rows = []
        self.covers = {}
        self.statuses = {}
        self.sort_order = Qt.SortOrder.AscendingOrder
        self.resize(min(1040, parent.width() - 36), min(613, parent.height() - 36))
        column = QVBoxLayout(self)
        column.setContentsMargins(15, 12, 15, 12)
        column.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(label("Game Sound Packs", "heading"), 1)
        close = QuietButton("×")
        close.setObjectName("soundPickerClose")
        close.setFixedSize(30, 29)
        close.setAccessibleName("Close Game Sound Packs")
        close.clicked.connect(self.reject)
        heading.addWidget(close)
        column.addLayout(heading)
        self.count = label("", "muted")
        column.addWidget(self.count)
        filters = QHBoxLayout()
        filters.setSpacing(12)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search Games")
        self.query.setClearButtonEnabled(True)
        self.query.setFixedHeight(33)
        self.query.addAction(audio_icon("search", t.MUTED), QLineEdit.ActionPosition.LeadingPosition)
        filters.addWidget(self.query, 1)
        self.series = QComboBox()
        self.series.setAccessibleName("Game Series")
        self.series.addItems(["All Series", *sorted({game.series for game in GAMES})])
        self.series.setFixedSize(165, 33)
        filters.addWidget(self.series)
        column.addLayout(filters)
        self.list = QListWidget()
        self.list.setSpacing(1)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerItem)

        headers = self._columns()
        headers.setContentsMargins(9, 0, 10 + self.list.verticalScrollBar().sizeHint().width(), 0)
        headers.addWidget(label("Game", "muted"), 1)
        self.release = QuietButton("")
        self.release.setObjectName("soundReleaseOrder")
        self.release.clicked.connect(self._toggle_release_order)
        self._update_sort_header()
        for name, width in _COLUMNS:
            heading = self.release if name == "Release" else label(name, "muted")
            if name in REGIONS:
                heading.setToolTip(REGIONS[name])
            heading.setFixedWidth(width)
            headers.addWidget(heading)
        column.addLayout(headers)
        column.addWidget(self.list, 1)
        for game in GAMES:
            item = _GameItem(self.list, game)
            self.list.setItemWidget(item, self._row(game, game == selected))
            self.rows.append((game, item))
            if game == selected:
                self.list.setCurrentItem(item)
        self.set_banners(banners)
        self.update_statuses(get_status)
        self.list.itemClicked.connect(self._choose)
        self.list.itemActivated.connect(self._choose)
        self.query.textChanged.connect(self._filter)
        self.series.currentIndexChanged.connect(self._filter)
        self._filter()

    @staticmethod
    def _columns(widget=None):
        line = QHBoxLayout(widget) if widget else QHBoxLayout()
        line.setContentsMargins(8, 5, 9, 5)
        line.setSpacing(9)
        return line

    def _row(self, game, selected):
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        line = self._columns(widget)
        self.covers[game.slug] = cover = GameCover()
        cover.setFixedSize(145, 49)
        line.addWidget(cover)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = label(game.name, "soundTileTitle", wrap=True)
        if selected:
            title.setStyleSheet(f"color: {t.GOLD};")
        text.addWidget(title)
        text.addWidget(label(game.platform, "muted"))
        line.addLayout(text, 1)
        self.statuses[game.slug] = status = label("", "soundPackStatus", wrap=True)
        cells = (
            label(game.series, "muted", wrap=True),
            label(release_date(game.first_release), "muted"),
            *(label(release_date(game.dates.get(region, ("", ""))[0]), "small") for region in REGIONS),
            status,
        )
        for (name, width), cell in zip(_COLUMNS, cells, strict=True):
            cell.setObjectName(f"soundGame{name}")
            cell.setFixedWidth(width)
            line.addWidget(cell)
        return widget

    def _update_sort_header(self):
        ascending = self.sort_order == Qt.SortOrder.AscendingOrder
        self.release.setText("Release ↑" if ascending else "Release ↓")
        order = "Oldest First" if ascending else "Newest First"
        self.release.setAccessibleName(f"Release, {order}")
        self.release.setToolTip(f"{order} · Click to Reverse")

    def _toggle_release_order(self):
        orders = Qt.SortOrder
        self.sort_order = orders.DescendingOrder if self.sort_order == orders.AscendingOrder else orders.AscendingOrder
        self.list.sortItems(self.sort_order)
        self._update_sort_header()
        self._filter()
        self.list.scrollToTop()

    def update_statuses(self, get_status):
        for game, item in self.rows:
            status = get_status(game.slug)
            cell = self.statuses[game.slug]
            if cell.text() == status:
                continue
            cell.setText(status)
            downloaded = status == "Downloaded"
            if cell.property("downloaded") != downloaded:
                cell.setProperty("downloaded", downloaded)
                cell.style().unpolish(cell)
                cell.style().polish(cell)
            dates = ", ".join(f"{r} {release_date(game.dates.get(r, ('', ''))[0])}" for r in REGIONS)
            summary = f"{game.name}, {game.platform}, {release_date(game.first_release)}, {dates}, {status}"
            item.setData(Qt.ItemDataRole.AccessibleTextRole, summary)

    def set_banners(self, banners):
        for slug, cover in self.covers.items():
            if path := banners.get("music-" + slug):
                cover.set_cover(slug, path)

    def showEvent(self, event):
        super().showEvent(event)
        if item := self.list.currentItem():
            self.list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self.query.setFocus()

    def _choose(self, item):
        self.game = item.game
        self.accept()

    def _filter(self):
        term = self.query.text().strip().casefold()
        category = self.series.currentText() if self.series.currentIndex() else ""
        count = 0
        for game, item in self.rows:
            show = (not category or game.series == category) and term in f"{game.name} {game.title}".casefold()
            item.setHidden(not show)
            count += show
        self.count.setText(f"{count} Game{'s' if count != 1 else ''}")
