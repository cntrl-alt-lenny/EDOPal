"""The grouped card-back collection, equipped slots, and focused design."""

from functools import partial
from html import escape

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from edopal.features.themes.controls import CardSlot, label, thumbnail
from edopal.ui import theme as t
from edopal.ui.page_chrome import rule

from .duel import fit

GROUPS = {
    "tcg-ocg": "TCG / OCG",
    "anime": "Anime",
    "manga": "Manga",
    "video-games": "Video Games",
    "other-tcgs": "Other TCGs",
}
GROUP_ROWS = (("tcg-ocg",), ("anime", "manga"), ("video-games", "other-tcgs"))
CURRENT_GAME_BACK = "Current Game Back"
# Short historical source labels for designs whose printed name does not identify the product.
SOURCES = {
    "vortex-classic": "Yu-Gi-Oh! TCG",
    "obelisk-g4": "G4 Promo",
    "skill-yami-yugi": "Speed Duel",
    "rush-duel-day-red": "Rush Duel",
    "manga-original": "Yu-Gi-Oh! Manga",
    "dungeon-dice-monsters": "Konami DDM",
}


class CardArt(QWidget):
    def __init__(self):
        super().__init__()
        self.pix = QPixmap()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_art(self, path, size=320):
        self.pix = thumbnail(path, QSize(size, round(size * 1.6)))
        self.update()

    def paintEvent(self, event):
        if not self.pix.isNull():
            with QPainter(self) as p:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.drawPixmap(fit(QRectF(self.rect()), self.pix), self.pix, QRectF(self.pix.rect()))


class MuseumTile(QPushButton):
    focused = Signal(str)

    def __init__(self, pack, art):
        super().__init__()
        self.pack = pack
        self.setObjectName(f"back-{pack.id}")
        self.setCheckable(True)
        self.setProperty("role", "tile")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(0)
        label_text = pack.meta.get("label", pack.name)
        self.setAccessibleName(f"{pack.name} · {label_text} · {pack.meta.get('era', '')}")
        self.setToolTip(self.accessibleName())
        body = QVBoxLayout(self)
        body.setContentsMargins(5, 6, 5, 5)
        body.setSpacing(2)
        self.art = CardArt()
        self.art.set_art(art)
        body.addWidget(self.art, 1)
        slug = pack.id.removeprefix("cardback-")
        source = SOURCES.get(slug)
        name = pack.name
        if (
            pack.meta.get("group") == "video-games"
            and "duel links" in label_text.lower()
            and "duel links" not in name.lower()
        ):
            name = f"Duel Links · {name}"
        title = label(name, "body")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        title.setFixedHeight(32)
        title.setStyleSheet("font-size: 12px; font-weight: 550;")
        body.addWidget(title)
        caption = " · ".join(filter(None, (source, pack.meta.get("era", ""))))
        detail = label(caption, "small")
        detail.setStyleSheet(f"font-size: 10px; color: {t.MUTED};")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(detail)
        for widget in self.findChildren(QWidget):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.focused.emit(self.pack.id)


class Museum(QWidget):
    focused = Signal(str)
    equip = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.packs = {}
        self.images = {}
        self.tiles = {}
        self.slots = {}
        self.buttons = {}
        self.selected = ""
        self.state = None
        self.can_apply = False
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(8)
        body.addLayout(left, 7)
        equipped = QHBoxLayout()
        for slot in ("you", "opponent"):
            button = CardSlot(slot)
            button.owner.setText(slot.title())
            button.owner.setStyleSheet(f"color: {t.GOLD}; font-size: 12px;")
            button.portrait.setFixedSize(50, 72)
            button.setFixedHeight(84)
            button.clicked.connect(partial(self.show_equipped, slot))
            equipped.addWidget(button, 1)
            self.slots[slot] = button
        left.addLayout(equipped)
        heading = QHBoxLayout()
        heading.addWidget(label("Card Back Museum", "heading"), 1)
        self.count = label("", "small")
        heading.addWidget(self.count)
        left.addLayout(heading)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        left.addLayout(self.rows, 1)

        panel = QFrame()
        panel.setObjectName("backDetails")
        panel.setStyleSheet(f"QFrame#backDetails {{background: {t.PANEL}; border: 1px solid {t.EDGE}; border-radius: 6px;}}")
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        body.addWidget(panel, 3)
        details = QVBoxLayout(panel)
        details.setContentsMargins(16, 14, 16, 14)
        details.setSpacing(7)
        self.category = label("", "goldKicker")
        self.name = label("", "heading")
        self.name.setWordWrap(True)
        details.addWidget(self.category)
        details.addWidget(self.name)
        self.art = CardArt()
        details.addWidget(self.art, 1)
        self.source = label("", "body")
        self.source.setWordWrap(True)
        details.addWidget(self.source)
        details.addWidget(rule())
        details.addWidget(label("Credits", "subheading"))
        self.credits = QTextBrowser()
        self.credits.setAccessibleName("Card back credits")
        self.credits.setOpenExternalLinks(True)
        self.credits.setStyleSheet(f"background: transparent; border: 0; color: {t.TEXT}; font-size: 12px;")
        self.credits.document().setDocumentMargin(0)
        self.credits.setFixedHeight(64)
        details.addWidget(self.credits)
        for slot in self.slots:
            button = QPushButton(f"Use For {slot.title()}")
            if slot == "you":
                button.setProperty("role", "primary")
            button.setFixedHeight(t.BUTTON_H + 4)
            button.clicked.connect(partial(self._equip, slot))
            details.addWidget(button)
            self.buttons[slot] = button

    def populate(self, packs, images):
        self.images = images
        self.packs = {p.id: p for p in packs}
        self.count.setText(f"{len(packs)} designs")
        for groups in GROUP_ROWS:
            row = QHBoxLayout()
            row.setSpacing(8)
            for group in groups:
                items = sorted(
                    (p for p in packs if p.meta.get("group") == group), key=lambda p: (p.meta.get("era", ""), p.name)
                )
                if not items:
                    continue
                section = QVBoxLayout()
                section.setSpacing(4)
                head = QHBoxLayout()
                head.addWidget(label(GROUPS[group], "subheading"))
                head.addWidget(rule(), 1)
                section.addLayout(head)
                strip = QHBoxLayout()
                strip.setSpacing(5)
                for pack in items:
                    button = MuseumTile(pack, images.get(pack.id))
                    button.clicked.connect(partial(self.select, pack.id))
                    button.focused.connect(self.select)
                    self.tiles[pack.id] = button
                    strip.addWidget(button, 1)
                section.addLayout(strip, 1)
                row.addLayout(section, len(items))
            if row.count():
                self.rows.addLayout(row, 1)
        if packs:
            initial = self.state.cardbacks["you"] if self.state else "cardback-vortex-classic"
            self.select(initial if initial in self.packs else packs[0].id)
        self.refresh_slots()

    def set_thumbnail(self, key, path):
        if path:
            self.images[key] = path
            self.tiles[key].art.set_art(path)
            if self.selected == key and self.art.pix.isNull():
                self.art.set_art(path)
            self.refresh_slots()

    def select(self, key):
        if key not in self.packs:
            return
        pack = self.packs[key]
        for pack_id, tile in self.tiles.items():
            tile.setChecked(key == pack_id)
        changed = self.selected != key
        self.selected = key
        if changed:
            self.name.setText(pack.name)
            self.source.setText(pack.meta.get("label", ""))
            self.category.setText("   ·   ".join(filter(None, (GROUPS.get(pack.meta.get("group")), pack.meta.get("era")))))
            self.art.set_art(self.images.get(key))
            self.credits.setPlainText("Loading credits…")
            self.focused.emit(key)
        self.refresh_slots()

    def detail(self, key, manifest, path):
        if self.selected != key:
            return
        self.name.setText(manifest["name"])
        self.art.set_art(path, 708)
        credits = []
        for credit in manifest.get("credits", []):
            source = escape(credit["source"])
            url = credit.get("url", "")
            if url.startswith(("https://", "http://")):
                source = f'<a style="color:{t.TEXT}" href="{escape(url, quote=True)}">{source}</a>'
            credits.append(source)
        self.credits.setHtml("<br>".join(dict.fromkeys(credits)) or "No credits supplied.")

    def refresh_slots(self):
        for slot, button in self.slots.items():
            state = self.state
            key = state.cardbacks.get(slot) if state else None
            known = bool(key and slot not in state.unknown_back_slots)
            pack = self.packs.get(key) if known else None
            path = (state.back_images.get(slot) if state else None) or self.images.get(key)
            button.set_back(pack.name if pack else CURRENT_GAME_BACK, "", path)
            button.setEnabled(bool(pack or path))
            equipped = bool(pack and self.selected == key and slot in state.back_images)
            self.buttons[slot].setText(f"Equipped for {slot.title()}" if equipped else f"Use For {slot.title()}")
            self.buttons[slot].setEnabled(self.can_apply and bool(self.selected) and not equipped)

    def show_equipped(self, slot):
        if not self.state:
            return
        if slot not in self.state.unknown_back_slots:
            self.select(self.state.cardbacks.get(slot))
            return
        path = self.state.back_images.get(slot)
        if path:
            self.selected = ""
            for tile in self.tiles.values():
                tile.setChecked(False)
            self.category.setText(f"Equipped · {slot.title()}")
            self.name.setText(CURRENT_GAME_BACK)
            self.source.setText(path.name)
            self.art.set_art(path, 708)
            self.credits.setPlainText("This image comes from your EDOPro game folder.")
            self.refresh_slots()

    def _equip(self, slot):
        self.equip.emit(slot, self.selected)
