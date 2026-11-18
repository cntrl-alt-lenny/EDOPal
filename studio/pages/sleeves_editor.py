"""Shared inline editor layout and a coalesced, worker-rendered print preview."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from studio import theme as t
from studio.tasks import TaskRunner
from studio.widgets import Button, Panel, label, margins

from .sleeves_widgets import Preview


class Editor(QWidget):
    back = Signal()
    commit = Signal(object)

    def __init__(self, tasks: TaskRunner, title: str) -> None:
        super().__init__()
        self.tasks = tasks
        self.active = True
        self.rendering = False
        self.revision = 0
        self.preview_work: Callable[[], QImage] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._render)
        body = QVBoxLayout(self)
        margins(body, 0)
        body.setSpacing(t.SM)
        header = QHBoxLayout()
        self.back_button = Button("Back", "quiet", "back")
        self.back_button.clicked.connect(self.back.emit)
        header.addWidget(self.back_button)
        header.addWidget(label(title, "heading"), 1)
        self.tools = header
        body.addLayout(header)
        columns = QHBoxLayout()
        columns.setSpacing(t.MD)
        body.addLayout(columns, 1)
        self.preview = Preview()
        columns.addWidget(self.preview, 5)
        panel = self.panel = Panel(t.LG)
        panel.setMaximumWidth(t.TILE_SIZE[0] * 4)
        columns.addWidget(panel, 6)
        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea, QScrollArea > QWidget > QWidget {{background:{t.PANEL};}}"
        )
        panel.body.addWidget(scroll, 1)
        form = QWidget()
        self.form = QVBoxLayout(form)
        self.form.setContentsMargins(0, 0, t.SM, 0)
        self.form.setSpacing(t.SM)
        self.form.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(form)
        self.hint = label("", "body", True)
        panel.body.addWidget(self.hint)
        fields = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Sleeve name")
        self.name.setAccessibleName("Sleeve name")
        self.save = Button("Save sleeve", "primary", "check")
        fields.addWidget(self.name, 1)
        fields.addWidget(self.save)
        panel.body.addLayout(fields)
        self.save.setEnabled(False)
        self.valid = False
        self.name.textChanged.connect(self._can_save)
        self.name.returnPressed.connect(self.save.click)

    @property
    def dirty(self) -> bool:
        return False

    def _can_save(self) -> None:
        self.save.setEnabled(self.valid and bool(self.name.text().strip()))

    def field(self, title: str, widget: QWidget) -> None:
        heading = label(title, "small")
        heading.setBuddy(widget)
        self.form.addWidget(heading)
        self.form.addWidget(widget)

    def invalidate(self) -> None:
        self.revision += 1
        self.preview_work = None
        self.timer.stop()
        self.valid = False
        self._can_save()

    def schedule(self, work: Callable[[], QImage]) -> None:
        self.invalidate()
        self.preview_work = work
        self.timer.start(t.MOTION_MS // 2)

    def _render(self) -> None:
        if self.rendering or not self.active or not self.preview_work:
            return
        self.rendering = True
        version, work = self.revision, self.preview_work
        job = self.tasks.start(lambda _: work())

        def ready(image: QImage) -> None:
            if self.active and version == self.revision:
                self.preview.set_image(image)
                self.valid = True
                self._can_save()

        def error(exc: Exception) -> None:
            if self.active and version == self.revision:
                self.hint.setText(str(exc))
                self.preview.set_image(QImage())

        def done() -> None:
            self.rendering = False
            if self.active and version != self.revision:
                self._render()

        job.result.connect(ready)
        job.error.connect(error)
        job.finished.connect(done)

    def leave(self) -> None:
        self.active = False
        self.invalidate()
