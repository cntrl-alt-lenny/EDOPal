"""Live palette-matched swatches for the procedural wallpaper collection."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QIcon, QImage, QPixmap, QShowEvent
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QToolButton, QWidget

import themes
from studio import theme as t
from studio.tasks import Job, TaskRunner, WorkerContext
from studio.widgets import margins


class StyleGrid(QWidget):
    picked = Signal(str)

    def __init__(self, tasks: TaskRunner) -> None:
        super().__init__()
        self.tasks = tasks
        self.theme = themes.PRESETS[0]
        self.job: Job | None = None
        self.drawn: tuple[str, ...] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.render)
        grid = QGridLayout(self)
        margins(grid, 0)
        grid.setSpacing(t.XS)
        self.buttons: dict[str, QToolButton] = {}
        for i, style in enumerate(themes.STYLES):
            button = QToolButton()
            button.setText(style.title())
            button.setAccessibleName(f"{style.title()} wallpaper")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(t.TILE_H, t.HUGE))
            button.setFixedHeight(t.HUGE + t.XXL)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setCheckable(True)
            button.setStyleSheet(
                f"QToolButton {{background:{t.PANEL}; color:{t.TEXT}; border:1px solid {t.EDGE}; padding:{t.XS}px; border-radius:{t.R_SMALL}px;}} QToolButton:checked,QToolButton:focus {{border-color:{t.GOLD};color:{t.GOLD};}}"
            )
            button.clicked.connect(lambda _checked=False, name=style: self.picked.emit(name))
            grid.addWidget(button, i // 3, i % 3)
            self.buttons[style] = button

    def palette_key(self) -> tuple[str, ...]:
        theme = self.theme
        return theme.background, theme.surface, theme.accent, theme.highlight or ""

    def set_theme(self, theme: themes.Theme) -> None:
        self.theme = theme
        for style, button in self.buttons.items():
            button.setChecked(style == theme.wallpaper)
        if self.isVisible() and self.palette_key() != self.drawn:
            self.timer.start(t.MOTION_MS // 2)

    def render(self) -> None:
        key, theme = self.palette_key(), self.theme
        if self.job or key == self.drawn or not self.isVisible():
            return

        def swatches(worker: WorkerContext) -> dict[str, QImage]:
            images = {}
            for style in themes.STYLES:
                if worker.cancel.is_set():
                    break
                images[style] = themes.wallpaper(
                    replace(theme, wallpaper=style, image=None), (168, 96)
                )
            return images

        def ready(images: dict[str, QImage]) -> None:
            if self.isVisible() and key == self.palette_key() and len(images) == len(self.buttons):
                for style, img in images.items():
                    self.buttons[style].setIcon(QIcon(QPixmap.fromImage(img)))
                self.drawn = key

        def finished() -> None:
            cancelled = self.job.cancel_event.is_set()
            self.job = None
            if self.isVisible() and (key != self.palette_key() or cancelled):
                self.render()

        self.job = self.tasks.start(swatches, owner=self)
        self.job.result.connect(ready)
        self.job.finished.connect(finished)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.timer.start(0)

    def hideEvent(self, event: QHideEvent) -> None:
        self.timer.stop()
        if self.job:
            self.job.cancel()
        super().hideEvent(event)
