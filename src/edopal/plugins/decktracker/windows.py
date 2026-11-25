"""Locate the visible EDOPro client area. Native reads run on a jobs worker."""

from __future__ import annotations

import ctypes
import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_GAME = re.compile(r"^(?:Project Ignis\s*[:·-]\s*)?EDOPro(?:\b|$)", re.I)
ATOM = "GAMESCOPE_EXTERNAL_OVERLAY"


@dataclass(frozen=True)
class Bounds:
    x: int
    y: int
    width: int
    height: int


def gamescope_session():
    return bool(os.environ.get("GAMESCOPE_WAYLAND_DISPLAY") or "gamescope" in os.getenv("XDG_CURRENT_DESKTOP", "").lower())


def _x(*args):
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=1, env={**os.environ, "LC_ALL": "C"}
    ).stdout


def _server_id(display):
    props = _x("xprop", "-display", display, "-root", "GAMESCOPE_PID", "GAMESCOPE_XWAYLAND_SERVER_ID")
    pid = re.search(r"GAMESCOPE_PID\(CARDINAL\) = (\d+)", props)
    index = re.search(r"GAMESCOPE_XWAYLAND_SERVER_ID\(CARDINAL\) = (\d+)", props)
    return (pid[1], int(index[1])) if pid and index else None


@lru_cache(maxsize=1)
def gamescope_root_display():
    """EDOPal's Qt surface must be on server zero. Keep the game's DISPLAY intact."""
    current = os.getenv("DISPLAY")
    if not current or not gamescope_session():
        return None
    try:
        server = _server_id(current)
        if not server or server[1] == 0:
            return current
        for sock in sorted(Path("/tmp/.X11-unix").glob("X[0-9]*")):
            number = sock.name[1:]
            if number.isdecimal() and _server_id(":" + number) == (server[0], 0):
                return ":" + number
    except (OSError, subprocess.SubprocessError):
        pass
    return current


def mark_external(xid):
    """Set XA_CARDINAL/32 before map, including after recreating the native handle."""
    display = gamescope_root_display()
    target = ["-display", display] if display else []
    subprocess.run(
        ["xprop", *target, "-id", hex(xid), "-f", ATOM, "32c", "-set", ATOM, "1"], check=True, capture_output=True, timeout=1
    )


class X11Windows:
    physical_pixels = True

    def __init__(self):
        self.xid = None

    def geometry(self):
        if root := gamescope_root_display():
            # The overlay belongs to the root server. The focused game can be on
            # a separate Gamescope-managed Xwayland server with its own XIDs.
            props = _x("xprop", "-display", root, "-root", "GAMESCOPE_FOCUSED_WINDOW")
            focus = re.search(r"GAMESCOPE_FOCUSED_WINDOW\(CARDINAL\) = (\d+)", props)
            if focus:
                # XIDs are scoped to a server. Check the title on the root server
                # and the game servers exported by Gamescope before accepting it.
                displays = [
                    os.environ.get("DISPLAY"),
                    root,
                    *(v for k, v in os.environ.items() if k.startswith("STEAM_GAME_DISPLAY_")),
                ]
                for display in dict.fromkeys(d for d in displays if d):
                    info = _x("xwininfo", "-display", display, "-id", hex(int(focus[1])))
                    if bounds := self._bounds(info):
                        return bounds
                return None
        active = _x("xprop", "-root", "_NET_ACTIVE_WINDOW")
        active = re.search(r"# (0x[\da-fA-F]+)", active)
        focused = int(active[1], 16) if active else 0
        if focused:
            self.xid = focused
        elif self.xid is None:
            tree = _x("xwininfo", "-root", "-tree")
            for xid, title in re.findall(r'^\s*(0x[\da-fA-F]+) "([^"]*)"', tree, re.M):
                if _GAME.match(title):
                    self.xid = int(xid, 16)
                    break
        if self.xid is None:
            return None
        bounds = self._bounds(_x("xwininfo", "-id", hex(self.xid)))
        if bounds is None:
            self.xid = None
        return bounds

    @staticmethod
    def _bounds(info):
        title = re.search(r'Window id: 0x[\da-fA-F]+ "([^"]*)"', info)
        if not title or not _GAME.match(title[1]) or "Map State: IsViewable" not in info:
            return None
        values = [
            re.search(r"^\s*" + key + r":\s*(-?\d+)", info, re.M)
            for key in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height")
        ]
        return Bounds(*(int(value[1]) for value in values)) if all(values) else None


class Win32Windows:
    physical_pixels = True

    def geometry(self):
        from ctypes import wintypes

        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        hwnd = wintypes.HWND(user.GetForegroundWindow())
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, title, len(title))
        if not _GAME.match(title.value) or user.IsIconic(hwnd):
            return None
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if user.GetClientRect(hwnd, ctypes.byref(rect)) and user.ClientToScreen(hwnd, ctypes.byref(origin)):
            return Bounds(origin.x, origin.y, rect.right, rect.bottom)
        return None


class QuartzWindows:
    physical_pixels = False  # Quartz reports global points with a top-left origin.

    def __init__(self):
        from ctypes.util import find_library

        cg = ctypes.CDLL(find_library("CoreGraphics"))
        cf = ctypes.CDLL(find_library("CoreFoundation"))
        ptr, size = ctypes.c_void_p, ctypes.c_long
        self.copy_windows = cg.CGWindowListCopyWindowInfo
        self.copy_windows.argtypes, self.copy_windows.restype = [ctypes.c_uint32, ctypes.c_uint32], ptr
        self.serialize = cf.CFPropertyListCreateData
        self.serialize.argtypes, self.serialize.restype = [ptr, ptr, size, ctypes.c_ulong, ptr], ptr
        self.length = cf.CFDataGetLength
        self.length.argtypes, self.length.restype = [ptr], size
        self.data = cf.CFDataGetBytePtr
        self.data.argtypes, self.data.restype = [ptr], ptr
        self.release = cf.CFRelease
        self.release.argtypes, self.release.restype = [ptr], None

    def geometry(self):
        array = self.copy_windows(1, 0)  # kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        if not array:
            return None
        try:
            blob = self.serialize(None, array, 200, 0, None)  # binary plist v1
            if not blob:
                return None
            try:
                windows = plistlib.loads(ctypes.string_at(self.data(blob), self.length(blob)))
            finally:
                self.release(blob)
        finally:
            self.release(array)
        for window in windows:
            if window.get("kCGWindowLayer") != 0:
                continue
            if not any(_GAME.match(window.get(key, "")) for key in ("kCGWindowOwnerName", "kCGWindowName")):
                return None
            box = window["kCGWindowBounds"]
            return Bounds(*(round(box[key]) for key in ("X", "Y", "Width", "Height")))
        return None


def window_provider():
    if sys.platform == "win32":
        return Win32Windows()
    return QuartzWindows() if sys.platform == "darwin" else X11Windows()
