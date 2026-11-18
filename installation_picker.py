"""Select a nearby EDOPro installation or browse for a portable copy."""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.font import nametofont

import config


def choose_installation(parent: tk.Misc, initial: str) -> str | None:
    dialog = tk.Toplevel(parent)
    dialog.title("Choose EDOPro installation")
    dialog.transient(parent)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(0, weight=1)
    body = ttk.Frame(dialog, padding=18)
    body.grid(sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(2, weight=1)
    ttk.Label(body, text="Choose your EDOPro installation", style="Section.TLabel").grid(sticky="w")
    status = tk.StringVar(dialog, value="Looking in your usual game folders…")
    ttk.Label(body, textvariable=status, wraplength=480).grid(sticky="w", pady=(4, 12))
    style = ttk.Style(dialog)
    surface = style.lookup("Card.TFrame", "background") or "#20211f"
    ink = style.lookup("TLabel", "foreground") or "#f4f2ee"
    accent = style.lookup("Aqua.TButton", "background") or "#91e3cf"
    style.configure(
        "Installs.Treeview",
        background=surface,
        fieldbackground=surface,
        foreground=ink,
        rowheight=max(28, nametofont("TkDefaultFont").metrics("linespace") + 12),
    )
    style.map(
        "Installs.Treeview", background=[("selected", accent)], foreground=[("selected", "#142721")]
    )
    installs = ttk.Treeview(
        body, style="Installs.Treeview", show="tree", selectmode="browse", height=3
    )
    installs.column("#0", width=min(550, max(280, parent.winfo_screenwidth() - 110)))
    installs.grid(row=2, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(body, command=installs.yview)
    scroll.grid(row=2, column=1, sticky="ns")
    horizontal = ttk.Scrollbar(body, orient="horizontal", command=installs.xview)
    horizontal.grid(row=3, column=0, sticky="ew")

    def scroll_if_needed(bar, first, last):
        bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            bar.grid_remove()
        else:
            bar.grid()

    installs.configure(
        yscrollcommand=lambda first, last: scroll_if_needed(scroll, first, last),
        xscrollcommand=lambda first, last: scroll_if_needed(horizontal, first, last),
    )
    actions = ttk.Frame(body)
    actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(14, 0))
    actions.columnconfigure(1, weight=1)
    result = None

    def accept(path: str):
        nonlocal result
        if config.looks_like_edopro_folder(path):
            result = path
            dialog.destroy()
        else:
            status.set("Choose the game folder containing EDOPro or its card databases.")

    def browse():
        picked = filedialog.askdirectory(
            parent=dialog,
            title="Select the EDOPro game folder",
            mustexist=True,
            initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
        )
        if picked:
            accept(picked)

    def use_selection(_event=None):
        selected = installs.selection()
        if selected:
            accept(selected[0])

    ttk.Button(actions, text="Browse…", command=browse).grid(row=0, column=0)
    ttk.Button(actions, text="Cancel", command=dialog.destroy).grid(row=0, column=2, padx=6)
    use = ttk.Button(actions, text="Use this folder", command=use_selection, style="Aqua.TButton")
    use.grid(row=0, column=3)
    use.state(["disabled"])
    installs.bind("<Double-1>", use_selection)
    installs.bind("<Return>", use_selection)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    matches = queue.SimpleQueue()

    def discover():
        try:
            matches.put(config.find_edopro_folders([initial]))
        except OSError:
            matches.put([])

    def show_results():
        try:
            paths = matches.get_nowait()
        except queue.Empty:
            dialog.after(80, show_results)
            return
        if not paths:
            status.set("No nearby installations found. Browse to your game folder.")
            return
        for path in paths:
            installs.insert("", "end", iid=path, text=path)
        installs.configure(height=min(max(2, len(paths)), 6))
        font = nametofont("TkDefaultFont")
        installs.column("#0", minwidth=max(font.measure(path) for path in paths) + 32)
        first = initial if initial in paths else paths[0]
        installs.selection_set(first)
        installs.focus(first)
        installs.see(first)
        use.state(["!disabled"])
        status.set("Choose the folder to use for artwork, customization, and decks.")

    threading.Thread(target=discover, daemon=True).start()
    dialog.after(20, show_results)
    dialog.update_idletasks()
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_reqwidth()) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_reqheight()) // 2)
    dialog.geometry(f"+{x}+{y}")
    dialog.wait_visibility()
    from gui import _apply_windows_dark_titlebar

    _apply_windows_dark_titlebar(dialog)
    dialog.grab_set()
    installs.focus_set()
    parent.wait_window(dialog)
    return result
