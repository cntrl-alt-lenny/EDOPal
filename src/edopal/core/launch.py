"""Resolve and dispatch an installation's launch target without changing game files."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from edopal.core import process
from edopal.core.install import Install, RunState
from edopal.core.locks import install_lock

_CONTAINER = Path("/run/.containerenv")
_FLATPAK = Path("/.flatpak-info")


def _host_bridge() -> tuple[str, ...]:
    """Steam belongs to the host desktop session when EDOPal runs in Distrobox."""
    if _CONTAINER.is_file():
        if bridge := shutil.which("distrobox-host-exec"):
            return (bridge,)
        if bridge := shutil.which("host-spawn"):
            return (bridge, "--no-pty")
        raise RuntimeError("Opening host Steam from this container requires distrobox-host-exec or host-spawn.")
    if _FLATPAK.is_file():
        if bridge := shutil.which("flatpak-spawn"):
            return (bridge, "--host")
        raise RuntimeError("Opening the host launcher requires flatpak-spawn.")
    return ()


def launch_command(install: Install) -> tuple[str, ...]:
    """Return exactly the argv dispatch would use, including any host bridge. Read-only."""
    target = install.launch
    if target is None:
        if install.kind == "proton":
            raise ValueError("Link this Windows client to a Steam shortcut with its configured Proton or Wine runner.")
        raise ValueError("This folder has no EDOPro launcher.")
    if target.uri is None:
        if not target.argv:
            raise ValueError("This folder has no EDOPro launcher.")
        if sys.platform.startswith("linux") and install.kind == "flatpak":
            return (*_host_bridge(), *target.argv)
        return target.argv

    uri = target.uri
    game_id = re.fullmatch(r"steam://rungameid/([1-9][0-9]{0,19})", uri)
    if not game_id or int(game_id[1]) >= 1 << 64:
        raise ValueError("Invalid Steam shortcut URI.")
    if sys.platform == "darwin":
        return ("/usr/bin/open", "-b", "com.valvesoftware.steam", uri)
    if sys.platform == "win32":
        if steam_root := install.extra.get("steam_root"):
            steam = Path(steam_root) / "steam.exe"
            if steam.is_file():
                return (str(steam), uri)
        raise FileNotFoundError("Could not find steam.exe in the Steam installation that owns this shortcut.")
    if sys.platform.startswith("linux"):
        host = _host_bridge()
        if install.extra.get("flatpak_steam"):
            return (*host, "flatpak", "run", "com.valvesoftware.Steam", uri)
        if host:
            return (*host, "steam", uri)
        if steam := shutil.which("steam"):
            return (steam, uri)
        if opener := shutil.which("xdg-open"):
            return (opener, uri)
        raise FileNotFoundError("Could not find Steam or a steam:// URI handler.")
    raise RuntimeError("Steam dispatch is unavailable on this platform.")


def _spawn(argv: tuple[str, ...], cwd: Path, description: str) -> None:
    detach = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        shell=False,
        **detach,
    )
    # 'open' and Steam can finish immediately or stay alive with the game. Surface
    # immediate dispatch failures, then reap long-lived children off the GUI thread.
    try:
        result = child.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        threading.Thread(target=child.wait, name="edopal-launch", daemon=True).start()
    else:
        if result:
            raise RuntimeError(f"{description or 'EDOPro launcher'} exited with status {result}.")


def launch(install: Install) -> str:
    """Check the selected copy under the storage lock and dispatch its detached launch."""
    argv = launch_command(install)
    target = install.launch
    assert target is not None
    with install_lock(install.root):
        if process.run_state(install) is RunState.RUNNING:
            return "EDOPro is already running."
        _spawn(argv, target.cwd or install.root, target.description)
    if process.run_state(install) is RunState.RUNNING:
        return "EDOPro is running."
    if target.uri:
        return "Launch requested through Steam."
    return "EDOPro launch requested."
