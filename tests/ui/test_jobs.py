import threading
import weakref
from concurrent.futures import CancelledError

import pytest

from edopal.ui.jobs import QtJobRunner


@pytest.fixture
def runner(qapp):
    jobs = QtJobRunner()
    yield jobs
    jobs.shutdown()


def test_progress_and_result_arrive_in_order_on_gui_thread(runner, qtbot):
    gui_thread = threading.get_ident()
    received = []
    finalized = []
    size = 5_000_000_000

    def work(ctx):
        assert threading.get_ident() != gui_thread
        ctx.progress(size - 1, size, "Finishing")
        return "done"

    weakref.finalize(work, lambda: finalized.append(threading.get_ident()))
    handle = runner.run(
        work,
        on_progress=lambda *args: received.append((threading.get_ident(), args)),
        on_done=lambda value: received.append((threading.get_ident(), value)),
    )
    del work
    qtbot.waitUntil(lambda: not handle.running)
    assert received == [(gui_thread, (size - 1, size, "Finishing")), (gui_thread, "done")]
    assert finalized == [gui_thread]


def test_error_callback_on_gui_thread(runner, qtbot):
    errors = []

    def work(ctx):
        raise ValueError("Database unavailable")

    handle = runner.run(work, on_error=lambda exc: errors.append((threading.get_ident(), exc)))
    qtbot.waitUntil(lambda: not handle.running)
    ident, exc = errors.pop()
    assert ident == threading.get_ident()
    assert isinstance(exc, ValueError)
    assert str(exc) == "Database unavailable"


def test_keyed_jobs_finish_cleanup_before_running_only_latest_request(runner, qtbot):
    entered, release = threading.Event(), threading.Event()
    execution, delivered = [], []

    def first(ctx):
        ctx.progress(1, 2, "Old")
        entered.set()
        assert release.wait(3)
        assert ctx.cancelled
        execution.append("old cleaned up")
        raise ValueError("old error")

    old = runner.run(first, key="preview", on_error=delivered.append, on_progress=lambda *p: delivered.append(p))
    assert entered.wait(3)
    skipped = runner.run(lambda ctx: execution.append("middle"), key="preview", on_done=delivered.append)
    latest = runner.run(lambda ctx: execution.append("latest") or "new image", key="preview", on_done=delivered.append)
    release.set()
    qtbot.waitUntil(lambda: not latest.running)
    assert not skipped.running and not old.running
    assert execution == ["old cleaned up", "latest"]
    assert delivered == ["new image"]


def test_explicit_cancel_preserves_partial_result(runner, qtbot):
    entered, release = threading.Event(), threading.Event()
    received = []

    def work(ctx):
        entered.set()
        assert release.wait(3)
        assert ctx.cancelled
        return "2 cards synced"

    handle = runner.run(work, on_done=received.append)
    assert entered.wait(3)
    handle.cancel()
    release.set()
    qtbot.waitUntil(lambda: not handle.running)
    assert received == ["2 cards synced"]


def test_cancelled_pending_request_skips_work_and_reports_cancellation(runner, qtbot):
    entered, release = threading.Event(), threading.Event()
    errors, executed = [], []

    def first(ctx):
        entered.set()
        assert release.wait(3)

    runner.run(first, key="preview")
    assert entered.wait(3)
    pending = runner.run(lambda ctx: executed.append(True), key="preview", on_error=errors.append)
    pending.cancel()
    release.set()
    qtbot.waitUntil(lambda: not pending.running)
    assert not executed
    assert len(errors) == 1 and isinstance(errors[0], CancelledError)


def test_shutdown_cancels_joins_and_discards_queued_callbacks(runner, qapp):
    entered, cleaned = threading.Event(), threading.Event()
    delivered = []

    def work(ctx):
        ctx.progress(1, 2, "Writing")
        entered.set()
        if ctx._cancel.wait(3):
            cleaned.set()
        return "closed"

    handle = runner.run(work, on_done=delivered.append, on_progress=lambda *p: delivered.append(p))
    assert entered.wait(3)
    runner.shutdown()
    assert cleaned.is_set()
    assert not handle.running
    qapp.processEvents()
    assert not delivered
    with pytest.raises(RuntimeError, match="shut down"):
        runner.run(work)
