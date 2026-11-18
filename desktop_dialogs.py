"""Small dialogs used when the full desktop interface is unavailable."""

import contextlib
import os
import sys


def show_startup_error(message: str) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
        return
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "EDOPro Studio", 0x10)
        return
    with contextlib.suppress(Exception):
        from tkinter import messagebox

        messagebox.showerror("EDOPro Studio", message)


def console_folder_dialog(initial: str) -> tuple[str | None, bool]:
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update_idletasks()
        selected = filedialog.askdirectory(
            parent=root,
            initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
            title="Select your EDOPro folder",
            mustexist=True,
        )
        return selected or None, True
    except Exception:
        return None, False
    finally:
        if root is not None:
            with contextlib.suppress(Exception):
                root.destroy()
