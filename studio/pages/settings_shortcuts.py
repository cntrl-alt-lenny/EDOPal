"""Optional links to the current portable app. The installation is never copied."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFile, QFileInfo, QStandardPaths

import config

MARK = "X-EDOPro-Studio=true"


@dataclass(frozen=True)
class Shortcut:
    title: str
    path: Path
    windows: bool


def shortcut_locations() -> tuple[Shortcut, ...]:
    if not getattr(sys, "frozen", False) or sys.platform == "darwin":
        return ()
    windows = sys.platform == "win32"
    places = QStandardPaths.StandardLocation
    filename = f"{config.APP_NAME}.lnk" if windows else "edopro-studio.desktop"
    return tuple(
        Shortcut(title, Path(folder) / filename, windows)
        for title, place in (
            ("Desktop", places.DesktopLocation),
            ("Start menu" if windows else "Applications menu", places.ApplicationsLocation),
        )
        if (folder := QStandardPaths.writableLocation(place))
    )


def desktop_entry(executable: Path) -> str:
    path = str(executable)
    if any(ch in path for ch in "\r\n"):
        raise ValueError("Move the app to a folder without line breaks before creating a shortcut.")
    # Exec quoting and desktop-entry value escaping are separate layers.
    quoted = "".join("\\" + ch if ch in '`"$\\' else ch for ch in path)
    quoted = quoted.replace("\\", "\\\\").replace("%", "%%")
    return (
        f"[Desktop Entry]\nType=Application\nName={config.APP_NAME}\n"
        f'Exec="{quoted}"\nTerminal=false\nCategories=Game;\n{MARK}\n'
    )


def shortcut_status(link: Shortcut, executable: Path, previous: str) -> str:
    path = link.path
    if not os.path.lexists(path):
        return "absent"
    if path.is_symlink() and not link.windows:
        return "occupied"
    if link.windows:
        target = QFileInfo(str(path)).symLinkTarget()
        if target and Path(target) == executable:
            return "installed"
        if target and previous and Path(target) == Path(previous):
            return "moved"
    else:
        with path.open("rb") as source:
            data = source.read(64 * 1024)
        if data == desktop_entry(executable).encode():
            return "installed"
        if MARK.encode() in data.splitlines():
            return "moved"
    return "occupied"


def change_shortcut(link: Shortcut, executable: Path, previous: str, *, remove: bool) -> None:
    status = shortcut_status(link, executable, previous)
    if status == "occupied":
        raise ValueError("Another file already uses that shortcut name.")
    if remove:
        if status != "absent":
            link.path.unlink()
        return
    if status == "installed":
        return
    link.path.parent.mkdir(parents=True, exist_ok=True)
    if status == "moved":
        # Keep the previous working link until its replacement has been written.
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(dir=link.path.parent) as temp:
            staged = Shortcut(link.title, Path(temp) / link.path.name, link.windows)
            change_shortcut(staged, executable, "", remove=False)
            staged.path.replace(link.path)
    elif link.windows:
        source = QFile(str(executable))
        if not source.link(str(link.path)):
            raise OSError(source.errorString())
    else:
        content = desktop_entry(executable)
        target = link.path.open("x", encoding="utf-8", newline="\n")
        try:
            with target:
                target.write(content)
            link.path.chmod(0o755)
        except OSError:
            link.path.unlink(missing_ok=True)
            raise
