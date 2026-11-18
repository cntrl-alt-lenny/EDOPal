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


def test_cyclic_collection_stays_on_the_gui_thread(qapp: QApplication, qtbot: QtBot) -> None:
    import gc

    from studio import gc_guard

    runner = TaskRunner()
    collector = gc_guard.install()
    assert collector is not None and gc_guard.install() is collector
    # Automatic collection is off, so a worker allocating heavily cannot start one.
    assert not gc.isenabled()

    class Node:
        def __init__(self) -> None:
            self.me = self

    collected: list[bool] = []
    seen_threads: list[bool] = []

    def churn(_: WorkerContext) -> int:
        before = gc.get_stats()[0]["collections"]
        junk = [Node() for _ in range(50_000)]
        del junk
        seen_threads.append(QThread.currentThread() is not qapp.thread())
        return gc.get_stats()[0]["collections"] - before

    job = runner.start(churn)
    job.result.connect(lambda ran: collected.append(ran == 0))
    qtbot.waitUntil(lambda: bool(collected))
    assert collected == [True] and seen_threads == [True]

    # The GUI-side collector reclaims the same garbage when its timer fires.
    probe = Node()
    done = finalize(probe, lambda: None)
    del probe
    gc.collect(0)  # promote nothing unexpected; start from a clean young generation
    cycle = Node()
    done = finalize(cycle, lambda: None)
    del cycle
    for _ in range(gc.get_threshold()[0] + 10):
        Node()
    collector.check()
    assert not done.alive
