"""GUI-thread lifecycle shared by the shell launch hook and the optional settings page."""

from __future__ import annotations

from subprocess import SubprocessError

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from edopal.core.install import RunState
from edopal.core.storage import GameRunningError
from edopal.features.formats.media import _face, _image
from edopal.features.hdcards.catalog import read_catalog

from .overlay import DuelOverlay, logical_rect
from .relay import RelayService, bind_loopback
from .setup import PLUGIN_ID, install_entry, route_info
from .windows import window_provider


class TrackerRuntime(QObject):
    changed = Signal()

    def __init__(self, core, provider=None):
        super().__init__()
        self.core = core
        self.provider = provider or window_provider()
        self.overlay = DuelOverlay()
        self.overlay.collapse_changed.connect(lambda v: self.option("collapsed", v))
        self.timer = QTimer(self, interval=200)
        self.timer.timeout.connect(self.tick)
        self.generation = 0
        self.service = self.job = self.probe = self.snapshot = self.game = None
        self.waiting = []
        self.names, self.art, self.pending_art = {}, {}, set()
        self.info = ([], None, None, False)
        self.message = ""
        self.install = core.installs.current()
        self.busy = False
        self._configure()
        QApplication.instance().aboutToQuit.connect(self.stop)

    def _configure(self):
        opts = self.core.settings.section(PLUGIN_ID)
        self.overlay.configure(opts.get("side", "right"), bool(opts.get("passthrough")), bool(opts.get("collapsed")))

    def option(self, key, value):
        self.core.settings.set(PLUGIN_ID, key, value)
        self._configure()
        self.overlay.follow(self.game)
        self.changed.emit()

    def _status(self, message):
        self.message = message
        self.changed.emit()

    def stop(self, *, drain=False):
        self.generation += 1
        self.timer.stop()
        if self.service:
            if drain:
                self.service.retire()
            else:
                self.service.stop()
        if self.job:
            self.job.cancel()
        self.job = self.service = self.probe = self.snapshot = self.game = None
        self.busy = False
        self.overlay.hide()
        self.overlay.panel.state = None
        self.pending_art.clear()
        waiting, self.waiting = self.waiting, []
        for _, failed in waiting:
            failed("Deck Tracker setup was interrupted.")

    def select_install(self):
        install = self.core.installs.current()
        if self.install != install:
            self.stop(drain=True)
            self.install = install
            self.info = ([], None, None, False)
            self.names, self.art = {}, {}
            self.message = ""

    def _run(self, fn, done, error=None):
        generation = self.generation

        def finish(value):
            if generation == self.generation:
                done(value)

        def fail(exc):
            if generation == self.generation:
                (error or self._failed)(exc)

        return self.core.jobs.run(fn, on_done=finish, on_error=fail)

    def _failed(self, exc):
        self.busy = False
        self._status(str(exc))
        waiting, self.waiting = self.waiting, []
        for _, failed in waiting:
            failed(str(exc))
        if not waiting:
            self.core.notify(str(exc), "warning")

    def inspect(self):
        self.select_install()
        if self.install and not self.busy:
            install = self.install
            preferred = self.core.settings.get(PLUGIN_ID, f"server/{install.root}")
            self._run(lambda _: route_info(self.core.storage, install, preferred), self._inspected)

    def _inspected(self, info):
        self.info = info
        self.changed.emit()

    def prepare(self, *, server=None, confirmed=False):
        self.select_install()
        if self.busy:
            return
        install = self.install
        if not install:
            self._failed(ValueError("Select an EDOPro installation to set up Deck Tracker."))
            return
        self.generation += 1
        self.busy = True
        self._status("Setting up EDOPal Relay…")
        storage = self.core.storage
        preferred = self.core.settings.get(PLUGIN_ID, f"server/{install.root}")

        def setup(ctx):
            _, target, current, _ = route_info(storage, install, server or preferred)
            if not server and current:
                target = current
            if not target:
                raise ValueError("This installation has no configured duel servers.")
            if not ctx.cancelled:
                install_entry(storage, install, target, confirmed_closed=confirmed)
            return route_info(storage, install, target.name)

        def ready(info):
            self.info = info
            if info[2] and info[2].name != preferred:
                self.core.settings.set(PLUGIN_ID, f"server/{install.root}", info[2].name)
            if self.core.settings.get(PLUGIN_ID, self._paused_key()):
                self.core.settings.set(PLUGIN_ID, self._paused_key(), False)
            self.busy = False
            self._status("Ready for launch")
            if self.waiting:
                self._start_listener()

        self.job = self._run(
            setup, ready, lambda exc: self._setup_error(exc, confirmed, lambda: self.prepare(server=server, confirmed=True))
        )

    def _setup_error(self, exc, confirmed, retry):
        if isinstance(exc, GameRunningError) and not confirmed:

            def checked(state):
                self.busy = False
                if (
                    state == RunState.UNKNOWN
                    and QMessageBox.question(
                        QApplication.activeWindow(),
                        "Confirm EDOPro Is Closed",
                        "Is EDOPro closed for this server-list change?",
                    )
                    == QMessageBox.StandardButton.Yes
                ):
                    retry()
                else:
                    self._failed(exc)

            self._run(lambda _: self.core.installs.run_state(), checked)
        else:
            self._failed(exc)

    def _paused_key(self):
        return f"setup-removed/{self.install.root}"

    def launch(self, ready, failed):
        self.select_install()
        if self.install and self.core.settings.get(PLUGIN_ID, self._paused_key()):
            ready()
            return
        if self.service and self.service.thread.is_alive() and not self.service.stop_requested.is_set():
            if self.service.ready.is_set():
                ready()
            else:
                self.waiting.append((ready, failed))
            return
        self.waiting.append((ready, failed))
        self.prepare()

    def _start_listener(self):
        install, server = self.install, self.info[2]
        if not server:
            self._failed(ValueError("Set up EDOPal Relay before launching the game."))
            return
        self.busy = True
        self._status("Preparing the in-game tracker…")

        def load(ctx):
            cards = read_catalog(install, cancelled=lambda: ctx.cancelled).cards
            return {i: c.name for i, c in cards.items()}, {i: c.type for i, c in cards.items()}

        def start(library):
            self.names, types = library
            try:
                sock = bind_loopback()
            except OSError as exc:
                self._failed(RuntimeError(f"Couldn’t open EDOPal Relay. {exc}"))
                return
            self.service = RelayService()
            self.service.start(sock, server, types)
            self.timer.start()
            self.tick()

        self.job = self._run(load, start)

    def undo(self, confirmed=False):
        if self.busy or not self.install:
            return
        self.generation += 1
        self.busy = True
        self.changed.emit()
        install = self.install

        def revert(_):
            result = self.core.storage.undo_last(install, PLUGIN_ID, confirmed_closed=confirmed)
            return result, route_info(self.core.storage, install)

        def done(outcome):
            result, self.info = outcome
            self.busy = False
            if not result.conflicts:
                self.stop()
                self.core.settings.set(PLUGIN_ID, self._paused_key(), self.info[2] is None)
            self._status(
                "Server list was edited. Undo skipped it." if result.conflicts else "Previous server list restored."
            )

        self.job = self._run(revert, done, lambda exc: self._setup_error(exc, confirmed, lambda: self.undo(True)))

    def tick(self):
        service = self.service
        if not service:
            return
        if not service.thread.is_alive():
            message = service.latest[0]
            self.timer.stop()
            self.service = self.snapshot = self.game = None
            self.overlay.hide()
            self._failed(RuntimeError(message))
            return
        latest = service.latest
        if service.ready.is_set() and self.waiting:
            self.busy = False
            waiting, self.waiting = self.waiting, []
            for ready, _ in waiting:
                ready()
        if latest is not self.snapshot:
            self.snapshot = latest
            self._status(latest[0])
            if state := latest[1]:
                self.overlay.panel.set_cards(state, self.names, self.art)
                self._load_art(state)
            else:
                self.game = None
                self.overlay.panel.state = None
                self.overlay.hide()
        if not latest[1] or self.probe and self.probe.running:
            return
        if QApplication.platformName().startswith("wayland"):
            message = "The in-game overlay requires the X11 display backend."
            if message != self.message:
                self._status(message)
            return
        self.probe = self._run(lambda _: self.provider.geometry(), self._located, self._location_error)

    def _location_error(self, exc):
        self.game = None
        self.overlay.hide()
        self._status(f"Couldn’t locate the EDOPro window. {exc}")

    def _located(self, bounds):
        if not self.service or not self.service.latest[1]:
            return
        self.game = logical_rect(bounds, self.provider.physical_pixels) if bounds else None
        try:
            self.overlay.follow(self.game)
        except (RuntimeError, SubprocessError) as exc:
            self._location_error(exc)

    def _load_art(self, state):
        codes = (state.starting.keys() | state.remaining.keys()) - self.pending_art
        if not codes:
            return
        install = self.install
        self.pending_art.update(codes)

        def load(ctx):
            return {c: _image(_face(install, c), (128, 90), crop=True) for c in codes if not ctx.cancelled}

        def loaded(images):
            for code, data in images.items():
                if data:
                    pix = QPixmap()
                    if pix.loadFromData(data):
                        self.art[code] = pix
            self.overlay.panel.rows.update()

        self._run(load, loaded)
