"""Adapt backup writes to Studio's worker progress and recovery reporting."""

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

import backup
from backup.core import Progress
from studio.pages.backup_data import display_name, history, is_interrupted
from studio.tasks import WorkerContext

T = TypeVar("T")


@dataclass(frozen=True)
class WriteError:
    error: Exception
    interrupted: backup.Transaction | None = None
    history_unavailable: bool = False


def run_write(
    worker: WorkerContext,
    work: Callable[[WorkerContext, Progress], T],
    transaction_install: Callable[[], backup.Installation] | None,
) -> T | WriteError:
    last = -1.0

    def report(done: int, total: int, path: str) -> None:
        nonlocal last
        now = monotonic()
        if now - last >= 0.05 or done == total:
            worker.progress((done, total, display_name(path)))
            last = now

    try:
        return work(worker, report)
    except (OSError, ValueError, backup.Cancelled) as error:
        if transaction_install is None:
            return WriteError(error)
        try:
            pending = next(
                (item for item in history(transaction_install()) if is_interrupted(item)), None
            )
        except (OSError, ValueError):
            return WriteError(error, history_unavailable=True)
        return WriteError(error, pending)
