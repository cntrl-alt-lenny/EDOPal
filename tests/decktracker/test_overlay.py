import os
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QPushButton

from edopal.dev.preview import make_core
from edopal.plugins.decktracker.model import DeckState
from edopal.plugins.decktracker.overlay import DuelOverlay, dock_rect, sorted_cards
from edopal.plugins.decktracker.runtime import TrackerRuntime
from edopal.plugins.decktracker.windows import ATOM, Bounds, X11Windows
from tests.decktracker.test_protocol import draw, frame, session


class FakeWindows:
    physical_pixels = False
    bounds = Bounds(650, 100, 1280, 800)

    def geometry(self):
        return self.bounds


def test_overlay_follows_duel_and_hides_for_lobby_disconnect_lost_history(tmp_path, qtbot):
    core = make_core(None, tmp_path, tmp_path)
    provider = FakeWindows()
    tracker = TrackerRuntime(core, provider)
    qtbot.addWidget(tracker.overlay)
    flags = tracker.overlay.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.FramelessWindowHint and flags & Qt.WindowType.WindowStaysOnTopHint
    tracker._load_art = lambda _: None
    service = SimpleNamespace(
        latest=("Lobby", None),
        thread=SimpleNamespace(is_alive=lambda: True),
        ready=SimpleNamespace(is_set=lambda: True),
        stop=lambda: None,
    )
    tracker.service = service
    tracker.tick()
    assert not tracker.overlay.isVisible()
    stream = session()
    stream.feed(False, draw(0, 33, 22))
    service.latest = ("Live", stream.deck)
    tracker.tick()
    panel = tracker.overlay.panel
    assert tracker.overlay.isVisible() and tracker.overlay.width() == 292
    assert tracker.overlay.x() == 650 + 1280 - 12 - 292
    assert panel.rows.rows[0][4:6] == ("2", "100%")
    assert panel.rows.rows[-1][-1] == "Hand"
    provider.bounds = Bounds(-600, 105, 480, 300)
    tracker.tick()
    assert tracker.overlay.geometry().right() == -133
    tracker.option("side", "left")
    assert tracker.overlay.x() == -588
    panel.title.click()
    assert tracker.overlay.height() == 36 and panel.scroll.isHidden()
    tracker.option("passthrough", True)
    assert tracker.overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    provider.bounds = None
    tracker.tick()
    assert not tracker.overlay.isVisible()
    provider.bounds = Bounds(0, 0, 1280, 800)
    tracker.tick()
    assert tracker.overlay.isVisible()
    stream.feed(False, frame(7))
    for status in ("Side decking", "Disconnected", "Tracking paused"):
        service.latest = (status, None)
        tracker.tick()
        assert not tracker.overlay.isVisible()
    tracker.stop()


def test_conditioned_sort_scrolling_and_empty_deck(qtbot):
    state = DeckState.start([1, 1, 1, 2, 3])
    state.reveal(0, 3)
    assert sorted_cards(state, {})[0] == [3, 1, 2]
    hud = DuelOverlay(gamescope=False)
    qtbot.addWidget(hud)
    hud.panel.set_cards(DeckState.start(range(1, 61)), {}, {})
    hud.follow(QRect(20, 20, 600, 400))
    assert hud.panel.scroll.verticalScrollBar().maximum() > 0
    assert QRect(20, 20, 600, 400).contains(hud.geometry())
    for code in [3, 1, 1, 1, 2]:
        state.remove(code, drawn=True)
    hud.panel.set_cards(state, {}, {})
    assert sorted_cards(state, {})[0] == [] and hud.panel.rows.rows[-1][5] == "—"
    assert dock_rect(QRect(-10, -10, 20, 20), "left", False, 900) == QRect(-10, -10, 20, 20)


@pytest.mark.skipif(os.getenv("QT_QPA_PLATFORM") != "xcb" or not os.getenv("DISPLAY"), reason="requires an X11 server")
def test_native_x11_follow_unmap_and_gamescope_property(qtbot):
    game = QPushButton("EDOPro")
    game.setWindowTitle("Project Ignis: EDOPro")
    game.setGeometry(120, 80, 820, 550)
    qtbot.addWidget(game)
    game.show()
    provider = X11Windows()
    qtbot.waitUntil(lambda: provider.geometry() == Bounds(120, 80, 820, 550))
    hud = DuelOverlay(gamescope=True)
    qtbot.addWidget(hud)
    hud.panel.set_cards(DeckState.start([1, 2, 3]), {}, {})
    hud.configure("right", True, False)
    hud.follow(game.geometry())
    prop = subprocess.check_output(["xprop", "-id", hex(int(hud.winId())), ATOM, "WM_HINTS"], text=True)
    assert f"{ATOM}(CARDINAL) = 1" in prop
    assert "Client accepts input or input focus: False" in prop
    # Use the X server's real hit testing, independently of Qt's synthetic mouse events.
    import ctypes

    x11 = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    display = ctypes.c_void_p(x11.XOpenDisplay(None))
    assert display
    try:
        x11.XSetInputFocus(display, ctypes.c_ulong(int(game.winId())), 1, 0)
        point = hud.panel.geometry().center()
        with qtbot.waitSignal(game.clicked, timeout=1500):
            xtest.XTestFakeMotionEvent(display, -1, point.x(), point.y(), 0)
            xtest.XTestFakeButtonEvent(display, 1, 1, 0)
            xtest.XTestFakeButtonEvent(display, 1, 0, 0)
            x11.XSync(display, 0)
        focus, revert = ctypes.c_ulong(), ctypes.c_int()
        x11.XGetInputFocus(display, ctypes.byref(focus), ctypes.byref(revert))
        assert focus.value == int(game.winId())
    finally:
        x11.XCloseDisplay(display)
    game.setGeometry(200, 150, 720, 480)
    qtbot.waitUntil(lambda: provider.geometry() == Bounds(200, 150, 720, 480))
    hud.follow(game.geometry())
    assert hud.panel.x() == 200 + 720 - 12 - 292
    game.hide()
    qtbot.waitUntil(lambda: provider.geometry() is None)
    hud.follow(None)
    assert not hud.isVisible()


def test_gamescope_surface_uses_root_of_same_compositor(monkeypatch, tmp_path):
    from edopal.plugins.decktracker import windows

    monkeypatch.setenv("GAMESCOPE_WAYLAND_DISPLAY", "gamescope-0")
    monkeypatch.setenv("DISPLAY", ":32")
    for name in ("X30", "X31", "X32"):
        (tmp_path / name).touch()
    monkeypatch.setattr(windows, "Path", lambda _: tmp_path)
    servers = {":30": ("7", 0), ":31": ("9", 0), ":32": ("9", 1)}
    monkeypatch.setattr(windows, "_server_id", servers.get)
    windows.gamescope_root_display.cache_clear()
    try:
        assert windows.gamescope_root_display() == ":31"
        assert os.environ["DISPLAY"] == ":32"
    finally:
        windows.gamescope_root_display.cache_clear()
