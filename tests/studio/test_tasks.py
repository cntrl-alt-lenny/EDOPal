import threading
from weakref import finalize

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from studio.tasks import TaskRunner, WorkerContext


def test_work_closure_is_released_on_gui_thread(qtbot: QtBot) -> None:
    runner = TaskRunner()
    release = threading.Event()
    disposed_on = []

    def work(_: WorkerContext) -> None:
        assert release.wait(5)

    finalize(work, lambda: disposed_on.append(threading.get_ident()))
    runner.start(work)
    del work
    release.set()
    assert runner.pool.waitForDone(5000)
    qtbot.waitUntil(lambda: not runner.jobs)
    assert disposed_on == [threading.get_ident()]


def test_work_and_all_signals_use_the_right_threads(qapp: QApplication, qtbot: QtBot) -> None:
    threads = []
    values = []
    runner = TaskRunner()

    def work(ctx: WorkerContext) -> int:
        threads.append(QThread.currentThread())
        ctx.progress(1)
        return 42

    job = runner.start(work)

    def record(value: int) -> None:
        threads.append(QThread.currentThread())
        values.append(value)

    job.progress.connect(record)
    job.result.connect(record)
    with qtbot.waitSignal(job.finished, timeout=5000):
        pass
    assert values == [1, 42]
    assert threads[0] != qapp.thread()
    assert threads[1:] == [qapp.thread(), qapp.thread()]
    assert not runner.jobs


def test_exception_delivered_with_message(qtbot: QtBot) -> None:
    runner = TaskRunner()

    def fail(_: WorkerContext) -> None:
        raise ValueError("Damaged artwork")

    job = runner.start(fail)
    with qtbot.waitSignal(job.error, timeout=5000) as received:
        pass
    assert str(received.args[0]) == "Damaged artwork"


def test_cancel_is_per_job_and_retains_partial_result(qtbot: QtBot) -> None:
    runner = TaskRunner()
    entered = threading.Event()

    def work(ctx: WorkerContext) -> str:
        entered.set()
        assert ctx.cancel.wait(5)
        return "partial"

    job = runner.start(work)
    assert entered.wait(3)
    results = []
    job.result.connect(results.append)
    with qtbot.waitSignal(job.cancelled, timeout=5000):
        job.cancel()
    assert results == ["partial"]
    again = runner.start(lambda ctx: ctx.cancel.is_set())
    with qtbot.waitSignal(again.result, timeout=5000) as received:
        pass
    assert received.args == [False]
