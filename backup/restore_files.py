"""Read-only destination checks and cleanup of transaction-owned publication files."""

import os
import stat
from pathlib import Path


def destination_stat(path: Path) -> os.stat_result:
    info = path.stat()
    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
        raise PermissionError(f"{path} is read-only. Clear the read-only attribute and try again.")
    return info


def clean_temp(path: Path, *, check_only: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    attributes = getattr(info, "st_file_attributes", 0)
    if not stat.S_ISREG(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise PermissionError(f"Expected a regular restore temporary file at {path}.")
    readonly = attributes & stat.FILE_ATTRIBUTE_READONLY
    if readonly and info.st_nlink > 1:
        raise PermissionError(f"Cannot clear the read-only attribute on a shared file: {path}")
    try:
        if not check_only:
            if readonly:
                path.chmod(info.st_mode | stat.S_IWRITE)
            path.unlink(missing_ok=True)
        elif os.name == "nt":
            import _winapi

            # Ask for DELETE (and FILE_WRITE_ATTRIBUTES when needed) without changing the
            # temp. An open handle that denies delete sharing is invisible to os.access().
            access = 0x10000 | (0x100 if readonly else 0)
            handle = _winapi.CreateFile(str(path), access, 7, 0, _winapi.OPEN_EXISTING, 0, 0)
            _winapi.CloseHandle(handle)
        elif not os.access(path.parent, os.W_OK | os.X_OK):
            raise PermissionError
    except OSError as exc:
        raise PermissionError(
            f"Cannot remove the restore temporary file {path}. "
            "Close any program using it and check the folder permissions, then try again."
        ) from exc
