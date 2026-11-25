"""Run Qt-free work in a private pool and deliver callbacks on the runner's GUI thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, Qt, QThreadPool, Signal, Slot

from edopal.core.jobs import JobContext, JobHandle

log = logging.getLogger(__name__)


@dataclass(eq=False)
class _Handle:
    key: str | None
    cancellation: threading.Event = field(default_factory=threading.Event)
    running: bool = True

    def cancel(self) -> None:
        self.cancellation.set()


@dataclass
class _Job:
    work: Callable[[JobContext], Any]
    on_done: Callable[[Any], None] | None
    on_error: Callable[[BaseException], None] | None
    on_progress: Callable[[int, int, str], None] | None
    successor: _Handle | None = None


class _Worker(QRunnable):
    def __init__(self, handle, fn, context, finish):
        super().__init__()
        self.handle = handle
        self.fn = fn
        self.context = context
        self.finish = finish

    def run(self) -> None:
        try:
            if self.context.cancelled:
                raise CancelledError()
            result = self.fn(self.context)
        except BaseException as exc:
            success, result = False, exc
        else:
            success = True
        # A callable may capture the page. The runner retains its last reference
        # until the GUI receives the result or drains the pool during shutdown.
        del self.fn
        self.finish(self.handle, success, result)
        del result  # break the traceback cycle when work raised


class QtJobRunner(QObject):
    """Submit work and shut down on the GUI thread. Handles support cooperative cancellation.

    Submitting the same key replaces any pending job and lets the active worker
    finish its cleanup before the replacement starts. Only the latest job delivers
    callbacks. Explicit cancellation still delivers the worker's terminal outcome.
    """

    _progress = Signal(object, object)
    _finished = Signal(object, bool, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(min(4, self._pool.maxThreadCount()))
        self._jobs: dict[_Handle, _Job] = {}
        self._active: dict[str, _Handle] = {}
        self._closed = False
        self._progress.connect(self._deliver_progress, Qt.ConnectionType.QueuedConnection)
        self._finished.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    def run(
        self,
        fn: Callable[[JobContext], Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        key: str | None = None,
    ) -> JobHandle:
        if self._closed:
            raise RuntimeError("The job runner has shut down")
        handle = _Handle(key)
        self._jobs[handle] = _Job(fn, on_done, on_error, on_progress)
        if key in self._active:
            previous = self._active[key]
            previous.cancel()
            active_job = self._jobs[previous]
            if pending := active_job.successor:
                pending.cancel()
                pending.running = False
                del self._jobs[pending]
            active_job.successor = handle
        else:
            self._start(handle)
        return handle

    def _start(self, handle: _Handle) -> None:
        job = self._jobs[handle]
        if handle.key is not None:
            self._active[handle.key] = handle
        context = JobContext(_cancel=handle.cancellation)
        if job.on_progress is not None:

            def progress(done: int, total: int, text: str) -> None:
                if not handle.cancellation.is_set():
                    self._progress.emit(handle, (done, total, text))

            context._progress = progress
        self._pool.start(_Worker(handle, job.work, context, self._finished.emit))

    @Slot(object, object)
    def _deliver_progress(self, handle: _Handle, values: tuple[int, int, str]) -> None:
        job = self._jobs.get(handle)
        if job and job.on_progress and not handle.cancellation.is_set():
            job.on_progress(*values)

    @Slot(object, bool, object)
    def _finish(self, handle: _Handle, success: bool, result: Any) -> None:
        job = self._jobs.pop(handle)
        handle.running = False
        if handle.key is not None:
            del self._active[handle.key]
        if job.successor is not None:
            self._start(job.successor)
        elif success:
            if job.on_done:
                job.on_done(result)
        elif job.on_error:
            job.on_error(result)
        elif not isinstance(result, CancelledError):
            log.error("Background job failed", exc_info=(type(result), result, result.__traceback__))

    @Slot()
    def shutdown(self) -> None:
        """Cancel all work, join workers and release their callbacks on the GUI thread."""
        if self._closed:
            return
        self._closed = True
        for handle in self._jobs:
            handle.cancel()
        self._pool.clear()
        self._pool.waitForDone()
        QCoreApplication.removePostedEvents(self, QEvent.Type.MetaCall)
        for handle in self._jobs:
            handle.running = False
        self._jobs.clear()
        self._active.clear()
