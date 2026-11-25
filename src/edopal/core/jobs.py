"""Background jobs contract. The real runner (``edopal.ui.jobs.QtJobRunner``) uses a QThreadPool
and delivers callbacks on the GUI thread; tests/previews use :class:`InlineJobRunner`.

Job functions must be Qt-free: ``fn(ctx) -> result`` where ``ctx.progress(done, total, text)``
reports progress and ``ctx.cancelled`` is checked cooperatively.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class JobContext:
    _progress: Callable[[int, int, str], None] = lambda d, t, s: None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def progress(self, done: int, total: int, text: str = "") -> None:
        self._progress(done, total, text)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


class JobHandle(Protocol):
    def cancel(self) -> None: ...
    @property
    def running(self) -> bool: ...


class JobRunner(Protocol):
    def run(
        self,
        fn: Callable[[JobContext], Any],
        *,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
        key: str | None = None,  # at most one running job per key; a new run cancels the old
    ) -> JobHandle: ...


class _Done:
    running = False

    def cancel(self) -> None:
        pass


class InlineJobRunner:
    """Runs jobs synchronously (tests, screenshot previews)."""

    def run(self, fn, *, on_done=None, on_error=None, on_progress=None, key=None):
        ctx = JobContext(on_progress or (lambda d, t, s: None))
        try:
            result = fn(ctx)
        except BaseException as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
            else:
                raise
        else:
            if on_done:
                on_done(result)
        return _Done()
