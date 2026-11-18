"""Prepare backup reviews and report writes on Studio workers."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TypeVar

import backup
from backup.core import Progress
from studio.pages.backup_data import (
    Review,
    check_destination,
    comparison_rows,
    display_name,
    history,
    is_interrupted,
    restore_review,
)
from studio.tasks import WorkerContext

T = TypeVar("T")


def create_archive(
    install: backup.Installation,
    path: Path,
    categories: tuple[str, ...],
    worker: WorkerContext,
    progress: Progress,
) -> tuple[Path, backup.Manifest]:
    path = check_destination(path, install)
    manifest = backup.create(
        install, path, categories, progress=progress, cancel=worker.cancel.is_set
    )
    return path, manifest


def prepare_restore(
    install: backup.Installation,
    archive: Path,
    expected: backup.Manifest,
    policy: backup.Policy,
    worker: WorkerContext,
) -> tuple[backup.Plan, Review]:
    cancel = worker.cancel.is_set
    plan = backup.plan_restore(archive, install, policy, cancel=cancel)
    if plan.manifest != expected:
        raise ValueError("Backup changed after preview")
    return plan, restore_review(plan, cancel)


def prepare_comparison(
    install: backup.Installation, archive: Path, expected: backup.Manifest, worker: WorkerContext
) -> Review:
    cancel = worker.cancel.is_set
    differences = backup.compare(install, archive, cancel=cancel)
    if backup.inspect(archive) != expected:
        raise ValueError("Backup changed during comparison")
    return comparison_rows(differences, cancel)


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
