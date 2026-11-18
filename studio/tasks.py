"""One worker pool. Each Job delivers ordered events on its owner's Qt thread."""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from artwork_flow import GuiProgress
from config import Config


@dataclass
class FolderRequest:
    initial: str
    answered: threading.Event = field(default_factory=threading.Event)
    path: str | None = None

    def answer(self, path: str | None) -> None:
        self.path = path
        self.answered.set()


class WorkerContext:
    def __init__(self, cancel: threading.Event, send: Callable[[object], None]) -> None:
        self.cancel = cancel
        self.progress = send


@dataclass
class _Counts:
    task: int
    description: str = ""
    steps: int = 0


class _SyncEvents:
    """At most one queued Qt wakeup, even if 50 workers finish together."""

    def __init__(self, post: Callable[[object], None]) -> None:
        self.post = post
        self.pending: list[tuple | _Counts] = []
        self.lock = threading.Lock()

    def put(self, event: tuple) -> None:
        with self.lock:
            wake = not self.pending
            kind = event[0]
            if kind in ("desc", "adv"):
                task = event[1]
                last = self.pending[-1] if self.pending else None
                if not isinstance(last, _Counts) or last.task != task:
                    last = _Counts(task)
                    self.pending.append(last)
                if kind == "desc":
                    last.description = event[2]
                else:
                    last.steps += event[2]
            else:
                self.pending.append(event)
        if wake:
            self.post(self)

    def drain(self) -> list[tuple]:
        with self.lock:
            pending, self.pending = self.pending, []
        events = []
        for item in pending:
            if isinstance(item, _Counts):
                if item.description:
                    events.append(("desc", item.task, item.description))
                if item.steps:
                    events.append(("adv", item.task, item.steps))
            else:
                events.append(item)
        return events


Work = Callable[[WorkerContext], Any]
RunCoro = Callable[[Config], Coroutine[Any, Any, Any]]


class Job(QObject):
    progress = Signal(object)
    result = Signal(object)
    error = Signal(object)
    cancelled = Signal()
    finished = Signal()
    _packet = Signal(str, object)
    _settled = Signal()

    def __init__(self, owner: QObject | None = None) -> None:
        super().__init__()
        self.cancel_event = threading.Event()
        self.active = True
        self._deliver_to_ui = True
        if owner is not None:
            owner.destroyed.connect(self.abandon)
        self._packet.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def abandon(self) -> None:
        """The receiver is gone. Still drain the worker and release it from the pool."""
        self._deliver_to_ui = False
        if self.active:
            self.cancel()

    @Slot(str, object)
    def _deliver(self, kind: str, value: object) -> None:
        if kind == "progress":
            if not self._deliver_to_ui:
                return
            if isinstance(value, _SyncEvents):
                for event in value.drain():
                    if self._deliver_to_ui:
                        self.progress.emit(event)
            else:
                self.progress.emit(value)
            return
        self.active = False
        if self._deliver_to_ui:
            (self.error if kind == "error" else self.result).emit(value)
        if self._deliver_to_ui and self.cancel_event.is_set():
            self.cancelled.emit()
        if self._deliver_to_ui:
            self.finished.emit()
        self._settled.emit()


class _Worker(QRunnable):
    def __init__(self, job: Job, work: Work) -> None:
        super().__init__()
        self.job, self.work = job, work

    def run(self) -> None:
        send = self.job._packet.emit
        ctx = WorkerContext(self.job.cancel_event, lambda event: send("progress", event))
        try:
            result = self.work(ctx)
        except (Exception, SystemExit, asyncio.CancelledError) as exc:
            send("error", exc)
        else:
            send("result", result)


class TaskRunner(QObject):
    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.jobs: set[Job] = set()

    def start(self, work: Work, *, owner: QObject | None = None) -> Job:
        job = Job(owner)
        job.setParent(self)
        self.jobs.add(job)
        job._settled.connect(self._release)
        self.pool.start(_Worker(job, work))
        return job

    @Slot()
    def _release(self) -> None:
        job = self.sender()
        self.jobs.discard(job)
        # Subscribers can capture widgets. Release their Qt connections on the GUI thread.
        job.deleteLater()
        if not self.jobs:
            self.idle.emit()

    def sync(
        self, cfg: Config, run_coro: RunCoro, prepare: Callable[[], None] | None = None
    ) -> Job:
        def work(ctx: WorkerContext) -> Any:
            events = _SyncEvents(ctx.progress)

            def pick(initial: str) -> str | None:
                request = FolderRequest(initial)
                events.put(("folder", request))
                deadline = monotonic() + 900
                while not request.answered.wait(0.1):
                    if ctx.cancel.is_set() or monotonic() >= deadline:
                        return None
                return request.path

            cfg.cancel_event = ctx.cancel
            cfg.gui_progress = GuiProgress(events)
            cfg.coverage_sink = lambda data: events.put(("coverage", data))
            cfg.notice_sink = lambda notice: events.put(("notice", notice))
            cfg.folder_picker = pick
            try:
                if prepare:
                    prepare()
                if ctx.cancel.is_set():
                    return None
                if cfg.folder_detected and cfg.cli.edopro_path is None:
                    cfg.set_edopro_path(cfg.edopro_path, save=True)
                return asyncio.run(run_coro(cfg))
            finally:
                cfg.gui_progress = cfg.folder_picker = cfg.coverage_sink = cfg.notice_sink = None

        return self.start(work)

    def cancel_all(self) -> None:
        for job in self.jobs:
            job.cancel()

    @Slot()
    def abandon_all(self) -> None:
        for job in self.jobs:
            job.abandon()
