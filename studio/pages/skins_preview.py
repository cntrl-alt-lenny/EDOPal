"""A coalesced live preview. At most one scene render per view is in flight."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

import extras
import themes
from studio import theme as t
from studio.tasks import TaskRunner, WorkerContext
from studio.widgets import Button, Panel, label, margins

from .skins_gallery import Choices
from .skins_model import BASE, Look
from .skins_service import SkinStore

SCENES = {"Main menu": "menu", "Deck editor": "deck", "Duel field": "duel"}


class Canvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.pix = QPixmap()
        self.message = "Preparing preview…"
        self.setMinimumSize(1, t.TILE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.RAIL))
        if self.pix.isNull():
            p.setPen(QColor(t.MUTED))
            p.drawText(
                self.rect().adjusted(t.MD, t.MD, -t.MD, -t.MD),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
        else:
            size = self.pix.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRect(self.rect().topLeft(), size)
            rect.moveCenter(self.rect().center())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(rect, self.pix)


@dataclass(frozen=True)
class Frame:
    look: Look
    theme: themes.Theme
    maker: bool
    game: Path | None
    scale: bool
    scene: str
    opponent: bool
    highlight: bool
    width: int


class Preview(QWidget):
    def __init__(self, store: SkinStore, tasks: TaskRunner) -> None:
        super().__init__()
        self.store, self.tasks = store, tasks
        self.look: Look | None = None
        self.theme = themes.PRESETS[0]
        self.maker = False
        self.game: Path | None = None
        self.scale = True
        self.highlight = False
        self.wanted: Frame | None = None
        self.completed: Frame | None = None
        self.running = False
        # Accessed only by the one render worker, scoped to this page.
        self._asset: tuple | None = None
        self._image: QImage | None = None
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.MD)
        bar = QHBoxLayout()
        self.scenes = Choices(tuple(SCENES))
        bar.addWidget(self.scenes)
        bar.addStretch()
        body.addLayout(bar)
        self.canvas = Canvas()
        body.addWidget(self.canvas, 1)
        turn = QHBoxLayout()
        self.turn = Button("Your turn", "secondary")
        self.turn.setAccessibleName("Switch whose turn is previewed")
        self.turn.setCheckable(True)
        self.turn.toggled.connect(self._turn)
        turn.addWidget(self.turn)
        self.caption = label("", "muted", True)
        turn.addWidget(self.caption, 1)
        body.addLayout(turn)
        self.palette = Panel(t.MD)
        line = QHBoxLayout()
        self.chips = []
        for key in BASE:
            col = QVBoxLayout()
            chip = QWidget()
            chip.setFixedHeight(t.SM)
            col.addWidget(chip)
            col.addWidget(label(BASE[key], "small"))
            self.chips.append(chip)
            line.addLayout(col, 1)
        self.palette.body.addLayout(line)
        body.addWidget(self.palette)
        self.scenes.changed.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._start)

    def _turn(self, on: bool) -> None:
        self.turn.setText("Opponent’s turn" if on else "Your turn")
        self.refresh()

    def clear(self) -> None:
        self.wanted = self.completed = self.look = None
        self.canvas.pix = QPixmap()
        self.canvas.message = "Preparing preview…"
        self.canvas.update()

    def set_look(
        self,
        look: Look,
        theme: themes.Theme,
        maker: bool,
        game: Path | None,
        scale: bool,
        highlight: bool,
    ) -> None:
        self.look, self.theme, self.maker = look, theme, maker
        self.game, self.scale, self.highlight = game, scale, highlight
        self.refresh()

    def refresh(self) -> None:
        if self.look is None:
            return
        scene = SCENES[self.scenes.value]
        pack = self.look.pack and not self.maker
        original = self.look.name in extras.SKINS
        self.scenes.setVisible(not pack or original)
        self.turn.setVisible(scene == "duel" and (not pack or original))
        self.caption.setText(
            "Opponent’s turn shown on the pack capture."
            if pack and original and scene == "duel" and self.turn.isChecked()
            else "Pack preview supplied by its creator."
            if pack
            else "Theme preview · game backgrounds depend on your installation."
            if self.theme.wallpaper == "inherit"
            else "Theme preview"
        )
        self.palette.setVisible(not pack)
        for chip, key in zip(self.chips, BASE, strict=True):
            chip.setStyleSheet(f"background:{getattr(self.theme, key)}; border-radius:{t.XS}px;")
        width = max(320, min(1600, self.canvas.width(), self.canvas.height() * 8 // 5))
        self.wanted = Frame(
            self.look,
            self.theme,
            self.maker,
            self.game,
            self.scale,
            scene,
            self.turn.isChecked(),
            self.highlight,
            width,
        )
        self.timer.start(t.SM)

    def _render(self, frame: Frame, worker: WorkerContext) -> QImage:
        if frame.look.pack and not frame.maker:
            return self.store.pack_image(
                frame.look, frame.scene, frame.highlight, frame.opponent, worker, frame.width
            )
        asset = (
            frame.theme.image,
            frame.game if frame.theme.wallpaper == "inherit" else None,
            frame.scene if frame.theme.wallpaper == "inherit" else "",
        )
        if self._asset != asset:
            self._image = (
                self.store.backdrop(frame.game, frame.scene)
                if frame.theme.wallpaper == "inherit"
                else self.store.library.image(frame.theme)
            )
            self._asset = asset
        return themes.render_preview(
            frame.theme,
            frame.scene,
            (frame.width, frame.width * 5 // 8),
            opponent_turn=frame.opponent,
            image=self._image,
            scale_background=frame.scale,
        )

    def _start(self) -> None:
        frame = self.wanted
        if self.running or frame is None or frame == self.completed:
            return
        self.running = True
        job = self.tasks.start(lambda worker: self._render(frame, worker))
        job.result.connect(lambda image: self._finish(frame, image, "Preview unavailable."))
        job.error.connect(lambda exc: self._finish(frame, QImage(), f"Preview unavailable. {exc}"))

    def _finish(self, frame: Frame, image: QImage, message: str) -> None:
        self.running = False
        if frame == self.wanted:
            self.completed = frame
            self.canvas.message = message
            self.canvas.pix = QPixmap.fromImage(image)
            self.canvas.update()
        self._start()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.refresh()
