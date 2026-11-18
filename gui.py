"""Branded app window: options -> progress -> summary (tkinter, all platforms).

One tkinter implementation serves Windows, macOS, and Linux; PyInstaller
bundles Tcl/Tk into the release binaries. The window owns the main thread and
runs the download pipeline in a worker thread, talking to it through a queue
plus the runtime hooks on Config (gui_progress, folder_picker, coverage_sink,
notice_sink, cancel_event). If Tk is missing or no display is available,
run_app raises GuiUnavailable and the caller falls back to the console flow.
"""

import contextlib
import os
import platform
import queue
import sys
import threading
from pathlib import Path

import config
import extras
from artwork_flow import (
    CHECKBOXES,
    Summary,
    coverage_summary,
    make_run_config,
    sync_summary,
    valid_decks,
)
from artwork_flow import (
    GuiProgress as _GuiProgress,
)
from artwork_flow import compact_path as _elide_path
from artwork_flow import (
    recommended_my_decks as _recommended_my_decks,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter import font as tkfont
except Exception:  # tkinter genuinely missing on some Pythons
    tk = None
    ttk = None

WINDOW_ART = Path(__file__).resolve().parent / "assets/window"
BG = "#151615"
BG_CARD = "#242521"
BG_CHIP = "#33342f"
BORDER = "#3b3c36"
GOLD = "#f1cc79"
GOLD_HI = "#ffdf99"
GOLD_DOWN = "#d3aa57"
GOLD_DIM = "#c9b382"
ON_GOLD = "#272013"
TEXT = "#f7f4ec"
TEXT_DIM = "#cecac1"
TEXT_FAINT = "#aba99f"
GREEN = "#8ae8c2"
RED = "#f19893"
FOCUS = "#e5bf72"
DISABLED = "#3a3b36"
CHECK_EDGE = "#57584e"
CONTENT_WIDTH = 848


class GuiUnavailable(RuntimeError):
    """The window can't be created (no tkinter, no display, broken Tk)."""


def gui_available() -> bool:
    return tk is not None


def _ui_fonts() -> dict:
    system = platform.system()
    if system == "Windows":
        base = "Segoe UI"
    elif system == "Darwin":
        base = ".AppleSystemUIFont"  # Tk's alias for the SF system font
    else:
        base = "DejaVu Sans"
        try:
            if base not in tkfont.families():
                base = "TkDefaultFont"
        except Exception:
            base = "TkDefaultFont"
    return {
        "body": (base, 10),
        "hint": (base, 9),
        "section": (base, 9, "bold"),
        "title": (base, 16, "bold"),
        "hero": (base, 40, "bold"),
        "big": (base, 26, "bold"),
        "button": (base, 10, "bold"),
    }


def _set_windows_dpi_awareness() -> None:
    """Must run before the first Tk window exists or text renders blurry."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    with contextlib.suppress(Exception):
        ctypes.windll.user32.SetProcessDPIAware()


def _apply_windows_dark_titlebar(root) -> None:
    """Ask DWM for a dark title bar so the navy window doesn't get a white cap."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 pre-20H1)
            if (
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                )
                == 0
            ):
                break
        # Force a repaint so the change shows immediately.
        root.withdraw()
        root.deiconify()


def _apply_style(root) -> None:
    f = _ui_fonts()
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")  # the only built-in theme that honors all colors below

    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        font=f["body"],
        bordercolor=BORDER,
        focuscolor=FOCUS,
        lightcolor=BG,
        darkcolor=BG,
        troughcolor=BG_CHIP,
        selectbackground=BG_CHIP,
        selectforeground=TEXT,
    )

    style.configure("TFrame", background=BG)
    style.configure(
        "Card.TFrame",
        background=BG_CARD,
        bordercolor=BORDER,
        relief="solid",
        borderwidth=1,
        lightcolor=BG_CARD,
        darkcolor=BG_CARD,
    )

    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Title.TLabel", font=f["title"])
    style.configure(
        "Chip.TLabel", background=BG_CHIP, foreground=GOLD_HI, font=f["hint"], padding=(8, 2)
    )
    style.configure("Section.TLabel", foreground=GOLD_DIM, font=f["section"])
    style.configure("CardBody.TLabel", background=BG_CARD)
    style.configure("Hint.TLabel", background=BG_CARD, foreground=TEXT_DIM, font=f["hint"])
    style.configure("Status.TLabel", foreground=TEXT_DIM)
    style.configure("Faint.TLabel", foreground=TEXT_FAINT, font=f["hint"])
    style.configure("Notice.TLabel", foreground=GOLD_DIM, font=f["hint"])
    style.configure("Good.TLabel", foreground=GREEN)
    style.configure("Bad.TLabel", foreground=RED)
    style.configure("Hero.TLabel", font=f["hero"], foreground=GOLD_HI)
    style.configure("Big.TLabel", font=f["big"], foreground=GOLD_HI)
    style.configure("BigCalm.TLabel", font=f["big"], foreground=TEXT_FAINT)
    style.configure("BigBad.TLabel", font=f["big"], foreground=RED)

    # clam draws an "x" glyph in a checked box and wraps it in a light 3D bevel,
    # both of which look broken on a dark theme. Hiding the glyph (gold on gold)
    # and flattening the bevel (upper/lowerbordercolor - NOT bordercolor, which
    # this element ignores) leaves a clean filled square: gold = on, empty = off.
    style.configure(
        "Card.TCheckbutton",
        background=BG_CARD,
        foreground=TEXT,
        font=f["body"],
        indicatorbackground=BG,
        indicatorforeground=BG,
        upperbordercolor=CHECK_EDGE,
        lowerbordercolor=CHECK_EDGE,
        indicatormargin=(0, 2, 10, 2),
        padding=(0, 2),
        focuscolor=FOCUS,
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", BG_CARD)],  # kill clam's light hover flash
        foreground=[("disabled", TEXT_FAINT), ("active", GOLD_HI)],
        indicatorbackground=[
            ("disabled", BG_CHIP),
            ("selected", GOLD),
            ("pressed", BG_CHIP),
        ],
        indicatorforeground=[("disabled", BG_CHIP), ("selected", GOLD)],
        upperbordercolor=[("selected", GOLD), ("active", GOLD_HI)],
        lowerbordercolor=[("selected", GOLD), ("active", GOLD_HI)],
    )

    style.configure(
        "Primary.TButton",
        background=GOLD,
        foreground=ON_GOLD,
        font=f["button"],
        # Vertical padding must match Secondary.TButton exactly, or adjacent
        # buttons render at different heights and the row looks misaligned.
        padding=(20, 9),
        borderwidth=0,
        relief="flat",
        bordercolor=GOLD,
        lightcolor=GOLD,
        darkcolor=GOLD,
        focusthickness=2,
        focuscolor=FOCUS,
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", DISABLED), ("pressed", GOLD_DOWN), ("active", GOLD_HI)],
        foreground=[("disabled", TEXT_FAINT)],
        bordercolor=[("pressed", GOLD_DOWN), ("active", GOLD_HI)],
        lightcolor=[("pressed", GOLD_DOWN), ("active", GOLD_HI)],
        darkcolor=[("pressed", GOLD_DOWN), ("active", GOLD_HI)],
    )

    style.configure(
        "Secondary.TButton",
        background=BG,
        foreground=TEXT,
        font=f["body"],
        padding=(20, 9),  # keep in sync with Primary.TButton
        borderwidth=1,
        # "solid" + a light-enough bordercolor: with relief=flat and BORDER the
        # outline never rendered and the button looked like plain text.
        relief="solid",
        bordercolor=CHECK_EDGE,
        lightcolor=BG,
        darkcolor=BG,
        focusthickness=2,
        focuscolor=FOCUS,
    )
    style.map(
        "Secondary.TButton",
        background=[("pressed", BG_CHIP), ("active", BG_CHIP)],
        foreground=[("disabled", TEXT_FAINT), ("active", GOLD_HI)],
        bordercolor=[("active", "#3a4c74")],
    )

    style.configure("Brand.TLabel", font=(f["body"][0], 12, "bold"))
    style.configure(
        "Quiet.TButton",
        padding=(8, 6),
        background=BG,
        foreground=TEXT_DIM,
        borderwidth=0,
        focuscolor=FOCUS,
    )
    style.map("Quiet.TButton", background=[("active", BG_CHIP)], foreground=[("active", TEXT)])
    style.configure("Scope.TRadiobutton", padding=(14, 6), background=BG, foreground=TEXT_DIM)
    style.layout(
        "Scope.TRadiobutton",
        [
            (
                "Radiobutton.focus",
                {
                    "sticky": "nsew",
                    "children": [
                        (
                            "Radiobutton.padding",
                            {
                                "sticky": "nsew",
                                "children": [("Radiobutton.label", {"sticky": "nsew"})],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.map(
        "Scope.TRadiobutton",
        background=[("selected", BG_CHIP), ("active", BG_CARD)],
        foreground=[("selected", GOLD_HI)],
    )
    style.configure(
        "Gallery.TCheckbutton",
        background=BG,
        foreground=TEXT,
        indicatorbackground=BG_CARD,
        indicatorforeground=GOLD,
    )
    style.map("Gallery.TCheckbutton", background=[("active", BG_CHIP)])
    style.configure(
        "Aqua.TButton",
        padding=(17, 8),
        background="#8ae8d5",
        foreground="#102924",
        font=f["button"],
        borderwidth=0,
    )
    style.map(
        "Aqua.TButton",
        background=[("disabled", DISABLED), ("active", "#a8f2df")],
        foreground=[("disabled", TEXT_FAINT)],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", BG_CHIP)],
        foreground=[("readonly", TEXT)],
        selectforeground=[("readonly", TEXT)],
        selectbackground=[("readonly", BG_CHIP)],
    )

    # Flat progressbar: bevel colors match fill/trough so no broken 3D edges.
    style.configure(
        "Gold.Horizontal.TProgressbar",
        background=GOLD,
        troughcolor=BG_CHIP,
        bordercolor=BG_CHIP,
        lightcolor=GOLD,
        darkcolor=GOLD,
        thickness=10,
    )


class _App:
    def __init__(self, cfg, version: str, run_coro, apply_choices):
        self.cfg = cfg
        self.version = version
        self.run_coro = run_coro
        self.apply_choices = apply_choices
        self.events: queue.Queue = queue.Queue()
        self.exit_code = 0
        self.mode = "sync"  # or "coverage"
        self.total = 0
        self.done = 0
        self.finished = False
        self.folder_cancelled = False
        self.coverage_shown = False
        self.current = None  # which screen frame is on top (tkraise keeps all mapped)
        self._screens: list = []
        self._inner_of: dict = {}
        self.result: dict = {}
        self.cancel_event = threading.Event()

        _set_windows_dpi_awareness()
        try:
            self.root = tk.Tk()
        except Exception as exc:  # no display, broken Tk install, ...
            raise GuiUnavailable(str(exc)) from exc

        try:
            self.root.title(f"{config.APP_NAME} · {version}")
            self.content_width = min(CONTENT_WIDTH, max(1, self.root.winfo_screenwidth() - 20))
            self.root.resizable(False, False)
            _apply_style(self.root)
            _apply_windows_dark_titlebar(self.root)
            self._set_app_icon()

            self.shell = ttk.Frame(self.root)
            self.shell.grid(sticky="nsew")
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            self.shell.columnconfigure(0, weight=1)
            self.shell.rowconfigure(0, weight=1)

            self._build_options()
            self._build_progress()
            self._build_summary()
            # _show sizes the window to whichever screen is on top; CONTENT_WIDTH
            # is the floor so it never ends up a tall, skinny strip.
            self._show(self.options_frame)

            with contextlib.suppress(Exception):
                self.root.attributes("-topmost", True)
                # Drop topmost once it has served its purpose (macOS quirk).
                self.root.after(400, lambda: self.root.attributes("-topmost", False))
                self.root.lift()
                self.root.focus_force()
        except GuiUnavailable:
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.root.destroy()
            raise GuiUnavailable(str(exc)) from exc

    def _set_app_icon(self) -> None:
        """Use the project icon instead of Tk's default feather in the taskbar."""
        with contextlib.suppress(Exception):
            base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
            for candidate in (
                os.path.join(base, "app-icon.png"),
                os.path.join(base, "assets", "app-icon.png"),
            ):
                if os.path.exists(candidate):
                    # Keep a reference or Tk garbage-collects the image.
                    self._icon = tk.PhotoImage(file=candidate)
                    self.root.iconphoto(True, self._icon)
                    return

    # -- screens ---------------------------------------------------------

    def _screen(self, center: bool = False) -> "ttk.Frame":
        """A stacked screen. All three share one window size (the tallest one),
        so the shorter screens center their content instead of clinging to the
        top with a large void underneath.
        """
        padding = (14, 12, 14, 14) if self.content_width < CONTENT_WIDTH else (24, 20, 24, 22)
        frame = ttk.Frame(self.shell, padding=padding)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        self._screens.append(frame)
        if not center:
            return frame
        frame.rowconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        inner = ttk.Frame(frame)
        inner.grid(row=1, column=0, sticky="ew")
        inner.columnconfigure(0, weight=1)
        self._inner_of[frame] = inner
        return inner

    def _image(self, name):
        if name not in self.images:
            self.images[name] = tk.PhotoImage(file=str(WINDOW_ART / f"{name}.png"))
        return self.images[name]

    def _build_options(self) -> None:
        f = self.options_frame = self._screen()
        self.images = {}
        self.appearance_busy = False
        self.gallery_frame = None
        self.notice_var = tk.StringVar()
        defaults = {
            "field_art": self.cfg.field_art,
            "hd_era_art": not self.cfg.original_text,
            "my_decks": _recommended_my_decks(self.cfg),
        }
        self.variables = {
            key: tk.BooleanVar(value=defaults.get(key, default))
            for key, _label, _hint, default in CHECKBOXES
        }

        header = ttk.Frame(f)
        header.grid(sticky="ew", pady=(0, 13))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            image=self._image("cards"),
            compound="left",
            text=f"  {config.APP_NAME}",
            style="Brand.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            header,
            text="Sync settings",
            style="Quiet.TButton",
            command=lambda: self._show(self.settings_frame),
        ).grid(row=0, column=1)

        width = 800 if self.content_width == CONTENT_WIDTH else max(1, self.content_width - 28)
        hero = tk.Canvas(f, width=width, height=212, highlightthickness=0, bg=BG)
        hero.grid(sticky="ew")
        hero.create_image(0, 0, image=self._image("archive"), anchor="nw")
        hero.create_text(
            26,
            22,
            anchor="nw",
            text="CARD ARTWORK",
            fill=GOLD,
            font=(_ui_fonts()["body"][0], -12, "bold"),
        )
        serif = "Georgia" if sys.platform in ("win32", "darwin") else "DejaVu Serif"
        hero.create_text(
            25, 48, anchor="nw", text="Your cards,\nin HD.", fill=TEXT, font=(serif, -49, "bold")
        )
        hero.create_text(
            27,
            174,
            anchor="nw",
            text="Download missing artwork for your library.",
            fill="#d9d5cb",
            font=(_ui_fonts()["body"][0], -15),
        )

        folder = ttk.Frame(f)
        folder.grid(sticky="ew", pady=(16, 11))
        folder.columnconfigure(0, weight=1)
        ttk.Label(folder, text="GAME INSTALLATION", style="Section.TLabel").grid(sticky="w")
        self.folder_name = tk.StringVar()
        self.folder_var = tk.StringVar()
        self.folder_hint_var = tk.StringVar()
        ttk.Label(
            folder,
            textvariable=self.folder_name,
            image=self._image("folder"),
            compound="left",
            padding=(0, 2),
            style="Brand.TLabel",
        ).grid(sticky="w")
        ttk.Label(folder, textvariable=self.folder_var, style="Status.TLabel").grid(sticky="w")
        self.folder_hint = ttk.Label(
            folder, textvariable=self.folder_hint_var, style="Notice.TLabel", wraplength=580
        )
        self.folder_hint.grid(row=3, column=0, sticky="w")
        ttk.Button(
            folder, text="Change folder", style="Secondary.TButton", command=self._on_change_folder
        ).grid(row=0, column=1, rowspan=3)
        self._rule(f)
        scope = ttk.Frame(f)
        scope.grid(sticky="ew", pady=10)
        scope.columnconfigure(0, weight=1)
        self.scope_hint = tk.StringVar(value="Artwork to sync")
        ttk.Label(scope, textvariable=self.scope_hint).grid(row=0, column=0, sticky="w")
        for col, (name, value) in enumerate((("Full library", False), ("My decks", True)), 1):
            ttk.Radiobutton(
                scope,
                text=name,
                value=value,
                variable=self.variables["my_decks"],
                style="Scope.TRadiobutton",
            ).grid(row=0, column=col, padx=(2, 0))
        self._rule(f)
        footer = ttk.Frame(f)
        footer.grid(sticky="ew", pady=(13, 0))
        footer.columnconfigure(2, weight=1)
        self.customize_button = ttk.Button(
            footer,
            text="  Customize EDOPro",
            image=self._image("swatches"),
            compound="left",
            style="Quiet.TButton",
            command=self._open_gallery,
        )
        self.customize_button.grid(row=0, column=0, sticky="w")
        self.music_button = ttk.Button(
            footer,
            text="  Music library",
            image=self._image("gallery/music-library"),
            compound="left",
            style="Quiet.TButton",
            command=self._open_music,
        )
        self.music_button.grid(row=0, column=1, sticky="w", padx=(5, 0))
        self.decks_button = ttk.Button(
            footer,
            text="  Tournament decks",
            image=self._image("gallery/deck-library"),
            compound="left",
            style="Quiet.TButton",
            command=self._open_decks,
        )
        self.decks_button.grid(row=0, column=2, sticky="w", padx=(5, 6))
        self.start_button = ttk.Button(
            footer,
            text="  Sync HD cards",
            image=self._image("download"),
            compound="left",
            style="Primary.TButton",
            command=self._on_start,
        )
        self.start_button.grid(row=0, column=3)
        self._build_sync_settings()
        self.root.bind("<<TournamentDeckSaved>>", self._on_deck_saved)
        self._render_folder()
        self.root.bind("<Return>", self._on_return)
        self.root.bind("<Escape>", lambda _e: self._on_escape())
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    @staticmethod
    def _rule(frame):
        tk.Frame(frame, bg=BORDER, height=1).grid(sticky="ew")

    def _build_sync_settings(self):
        f = self.settings_frame = self._screen()
        ttk.Label(f, text="Sync settings", style="Title.TLabel").grid(sticky="w", pady=(0, 14))
        for key, label, hint, _ in CHECKBOXES:
            if key in ("my_decks", "audio"):
                continue
            box = ttk.Frame(f, style="Card.TFrame", padding=(12, 5))
            box.grid(sticky="ew", pady=(0, 4))
            ttk.Checkbutton(
                box, text=label, variable=self.variables[key], style="Card.TCheckbutton"
            ).grid(sticky="w")
            ttk.Label(box, text=hint, style="Hint.TLabel").grid(sticky="w", padx=(25, 0))
        actions = ttk.Frame(f)
        actions.grid(sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.coverage_button = ttk.Button(
            actions, text="Show coverage", style="Secondary.TButton", command=self._on_coverage
        )
        self.coverage_button.grid(row=0, column=0, sticky="w")
        ttk.Button(
            actions,
            text="Done",
            style="Primary.TButton",
            command=lambda: self._show(self.options_frame),
        ).grid(row=0, column=1)

    def _open_music(self, *, game=None):
        from music_window import open_music_library

        try:
            open_music_library(self.root, self.cfg.edopro_path, game=game)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Music library", str(exc), parent=self.root)

    def _open_decks(self):
        from tournament_window import open_tournament_decks

        try:
            open_tournament_decks(
                self.root, self.cfg.edopro_path, on_sync_artwork=self._sync_imported_decks
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Tournament decks", str(exc), parent=self.root)

    def _on_deck_saved(self, _event):
        self.scope_hint.set("Deck saved · Choose My decks to sync its art")
        self._render_folder()

    def _sync_imported_decks(self, game_path: Path, paths: tuple[Path, ...]) -> bool:
        if self.appearance_busy or self.current is self.progress_frame:
            messagebox.showinfo(
                "Deck artwork", "Wait for the current task to finish.", parent=self.root
            )
            return False
        if not valid_decks(self.cfg.edopro_path, game_path, paths):
            messagebox.showinfo(
                "Deck artwork",
                "Select the EDOPro installation containing these decks and its card databases.",
                parent=self.root,
            )
            return False
        self._launch(stats_only=False, deck_paths=paths)
        return True

    def _open_gallery(self):
        from appearance_gallery import AppearanceGallery

        if self.gallery_frame is None:
            self.gallery_frame = self._screen()
            self.gallery_frame.configure(padding=0)
            self.gallery_frame.rowconfigure(0, weight=1)
            self.gallery = AppearanceGallery(
                self.gallery_frame,
                family=_ui_fonts()["body"][0],
                back=lambda: self._show(self.options_frame),
                apply=self._apply_gallery,
                open_music=self._open_music,
                on_layout=self._gallery_layout,
            )
            self.gallery.grid(sticky="nsew")
        self.gallery.open(self.cfg.edopro_path, self.cfg.config_path)
        self._show(self.gallery_frame)
        self.gallery.tabs[self.gallery.tab.get()].focus_set()

    def _gallery_layout(self):
        if self.current is self.gallery_frame:
            self._fit_to_screen()

    def _apply_gallery(self, selection):
        if self.appearance_busy or not selection:
            return
        target, settings = self.cfg.edopro_path, self.cfg.config_path
        restore = selection.get("restore", False)
        undo = selection.get("undo_group", "")
        self.appearance_busy = True
        self.gallery.set_busy(
            "Restoring previous selection…" if restore or undo else "Applying changes…"
        )

        def worker():
            try:
                extras.apply_appearance(target, settings, **selection)
            except Exception as exc:
                self.events.put(("appearance", False, str(exc)))
            else:
                result = {
                    "sleeves": "Previous sleeves restored.",
                    "effects": "Previous duel sound effects restored.",
                    "music": "Previous background music restored.",
                }.get(
                    undo,
                    "Previous skin restored."
                    if restore
                    else "Changes applied. Restart EDOPro if it is open.",
                )
                self.events.put(("appearance", True, result))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(60, self._pump)

    # -- EDOPro folder -------------------------------------------------------

    def _render_folder(self) -> None:
        """Show the folder the sync will use, and gate Start on having one."""
        path = getattr(self.cfg, "edopro_path", "") or ""
        hint = ""
        self.folder_name.set(
            "  " + os.path.basename(os.path.normpath(path))
            if config.looks_like_edopro_folder(path)
            else "Choose an installation"
        )
        ready = config.folder_has_card_databases(path)
        if ready:
            self.folder_var.set(_elide_path(path, 76))
        elif config.looks_like_edopro_folder(path):
            self.folder_var.set(_elide_path(path, 76))
            hint = "No card databases here yet — open EDOPro once, then run this again"
        else:
            self.folder_var.set("Not found")
            hint = "Select Change folder and choose your ProjectIgnis installation."
        state = ["!disabled"] if ready else ["disabled"]
        customize_state = ["!disabled"] if config.looks_like_edopro_folder(path) else ["disabled"]
        for button in (self.customize_button, self.music_button, self.decks_button):
            button.state(customize_state)
        self.start_button.state(state)
        self.coverage_button.state(state)

        self.folder_hint_var.set(hint)
        if hint:
            self.folder_hint.grid()
        else:
            self.folder_hint.grid_remove()
        self._fit_to_screen()

    def _ask_for_folder(self, initial: str) -> str | None:
        """Show the native folder dialog. Must run on the Tk (main) thread."""
        with contextlib.suppress(Exception):
            return (
                filedialog.askdirectory(
                    parent=self.root,
                    initialdir=initial or "~",
                    title="Select your ProjectIgnis (EDOPro) folder",
                    mustexist=True,
                )
                or None
            )
        return None

    def _on_change_folder(self) -> None:
        from installation_picker import choose_installation

        picked = choose_installation(self.root, getattr(self.cfg, "edopro_path", ""))
        if not picked:
            return
        self.cfg.set_edopro_path(picked, save=True)
        self.scope_hint.set("Artwork to sync")
        self._render_folder()

    def _build_progress(self) -> None:
        f = self._screen(center=True)
        self.progress_frame = self._screens[-1]

        # A big percentage anchors the screen: without it the sync state is a
        # thin strip of widgets floating in a window sized for the options list.
        self.pct_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.pct_var, style="Hero.TLabel", anchor="center").grid(
            sticky="ew"
        )
        self.stage_var = tk.StringVar(value="Scanning card databases…")
        ttk.Label(f, textvariable=self.stage_var, style="Status.TLabel", anchor="center").grid(
            sticky="ew", pady=(2, 22)
        )
        self.bar = ttk.Progressbar(f, style="Gold.Horizontal.TProgressbar", mode="indeterminate")
        self.bar.grid(sticky="ew")

        counts = ttk.Frame(f)
        counts.grid(sticky="ew", pady=(12, 26))
        counts.columnconfigure(0, weight=1)
        self.count_var = tk.StringVar(value="")
        ttk.Label(counts, textvariable=self.count_var, style="Faint.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.remaining_var = tk.StringVar(value="")
        ttk.Label(counts, textvariable=self.remaining_var, style="Faint.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        # Notices (e.g. "no decks found — syncing everything") share the same
        # variable as the options screen so they're visible mid-sync too.
        ttk.Label(f, textvariable=self.notice_var, style="Notice.TLabel", anchor="center").grid(
            sticky="ew", pady=(0, 14)
        )
        self.cancel_button = ttk.Button(
            f, text="Cancel", style="Secondary.TButton", command=self._on_cancel
        )
        self.cancel_button.grid()

    def _build_summary(self) -> None:
        f = self._screen(center=True)
        self.summary_frame = self._screens[-1]
        self.summary_title_var = tk.StringVar(value="Sync complete")
        ttk.Label(f, textvariable=self.summary_title_var, style="Title.TLabel").grid(
            sticky="w", pady=(0, 16)
        )

        hero = ttk.Frame(f)
        hero.grid(sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=1)
        self.hero_ok_var = tk.StringVar(value="0")
        self.hero_bad_var = tk.StringVar(value="0")
        self.hero_ok_label = ttk.Label(hero, textvariable=self.hero_ok_var, style="Big.TLabel")
        self.hero_ok_label.grid(row=0, column=0)
        self.hero_bad_label = ttk.Label(
            hero, textvariable=self.hero_bad_var, style="BigCalm.TLabel"
        )
        self.hero_bad_label.grid(row=0, column=1)
        self.hero_ok_caption = ttk.Label(hero, text="downloaded", style="Faint.TLabel")
        self.hero_ok_caption.grid(row=1, column=0)
        self.hero_bad_caption = ttk.Label(hero, text="unavailable", style="Faint.TLabel")
        self.hero_bad_caption.grid(row=1, column=1)

        self.breakdown_card = ttk.Frame(f, style="Card.TFrame", padding=(14, 10))
        self.breakdown_card.grid(sticky="ew", pady=(16, 6))
        self.breakdown_card.columnconfigure(0, weight=1)

        self.summary_note_var = tk.StringVar(value="")
        ttk.Label(
            f, textvariable=self.summary_note_var, style="Status.TLabel", wraplength=440
        ).grid(sticky="w", pady=(2, 16))
        actions = ttk.Frame(f)
        actions.grid(sticky="ew")
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Back to home", command=self._back_to_home).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(actions, text="Close", style="Primary.TButton", command=self._close).grid(
            row=0, column=1
        )

    # -- layout helpers ----------------------------------------------------

    def _fit_to_screen(self) -> None:
        """Size the window to the screen on top, not to the tallest of the three.

        All three frames share one grid cell, so Tk asks for the tallest one's
        height and the short screens (progress, summary) would otherwise sit in
        a half-empty window.
        """
        if self.current is None:
            return
        with contextlib.suppress(Exception):
            # Centered screens hand back their inner frame, so climb to the one
            # actually gridded into the shell or the padding gets left out.
            outer = self.current
            while outer is not None and outer.master is not self.shell:
                outer = outer.master
            if outer is None:
                return
            self.root.update_idletasks()
            width = max(self.shell.winfo_reqwidth(), self.content_width)
            height = outer.winfo_reqheight()
            self.root.minsize(width, height)
            self.root.geometry(f"{width}x{height}")

    def _show(self, frame) -> None:
        if (
            self.current is not None
            and self.current is self.gallery_frame
            and frame is not self.gallery_frame
        ):
            self.gallery.stop_preview()
        for screen in self._screens:
            if screen is not frame:
                screen.grid_remove()
        frame.grid()
        self.current = frame
        frame.tkraise()
        self._fit_to_screen()
        self.root.update_idletasks()
        width = self.root.winfo_width()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - self.root.winfo_height()) // 3
        self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _breakdown_rows(self, rows: list[tuple[str, str]]) -> None:
        for child in self.breakdown_card.winfo_children():
            child.destroy()
        for i, (name, value) in enumerate(rows):
            ttk.Label(self.breakdown_card, text=name, style="CardBody.TLabel").grid(
                row=i, column=0, sticky="w", pady=3
            )
            ttk.Label(self.breakdown_card, text=value, style="Hint.TLabel").grid(
                row=i, column=1, sticky="e", pady=3
            )
        if not rows:
            self.breakdown_card.grid_remove()
        else:
            self.breakdown_card.grid()

    # -- actions -----------------------------------------------------------

    def _picks(self) -> dict:
        return {key: var.get() for key, var in self.variables.items()}

    def _launch(self, stats_only: bool, deck_paths: tuple[Path, ...] = ()) -> None:
        picks = self._picks()
        picks["stats"] = stats_only
        self.mode = "coverage" if stats_only else "sync"
        # Filters and runtime flags belong to this job. A second sync can target
        # another folder or the full library without inheriting the first job.
        self.run_config = cfg = make_run_config(self.cfg, picks, self.apply_choices, deck_paths)
        if cfg.folder_detected and cfg.cli.edopro_path is None:
            cfg.set_edopro_path(cfg.edopro_path, save=True)
        self.finished = self.coverage_shown = self.folder_cancelled = False
        self.exit_code = self.total = self.done = 0
        self.hero_ok_caption.configure(text="downloaded")
        self.hero_bad_caption.configure(text="unavailable")
        self.cancel_button.state(["!disabled"])
        for var in (self.pct_var, self.count_var, self.remaining_var, self.notice_var):
            var.set("")
        if deck_paths:
            self.notice_var.set(
                "Artwork for the imported deck"
                if len(deck_paths) == 1
                else f"Artwork for {len(deck_paths)} imported decks"
            )
        self.scope_hint.set("Artwork to sync")

        # Fresh event per launch so a stray earlier cancel can't poison the run.
        self.cancel_event = threading.Event()
        cfg.gui_progress = _GuiProgress(self.events)
        cfg.cancel_event = self.cancel_event
        cfg.notice_sink = lambda text: self.events.put(("notice", text))
        cfg.coverage_sink = lambda data: self.events.put(("coverage", data))
        cfg.folder_picker = self._folder_picker_from_worker

        self.root.bind("<Return>", lambda _e: None)
        self.root.bind("<Escape>", lambda _e: self._on_escape())
        self.stage_var.set(
            "Reading card databases…" if not stats_only else "Measuring artwork coverage…"
        )
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        self._show(self.progress_frame)

        def worker() -> None:
            import asyncio

            payload: dict = {}
            try:
                payload["stats"] = asyncio.run(self.run_coro(cfg))
            except SystemExit as exc:
                payload["exit"] = exc.code if isinstance(exc.code, int) else 1
                if hasattr(exc, "user_message"):
                    payload["error"] = exc.user_message
            except Exception as exc:
                payload["error"] = f"{type(exc).__name__}: {exc}"
            self.events.put(("done", payload))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(80, self._pump)

    def _back_to_home(self):
        self.root.bind("<Return>", self._on_return)
        self.root.bind("<Escape>", lambda _e: self._on_escape())
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._show(self.options_frame)
        self._render_folder()
        self.start_button.focus_set()

    def _on_return(self, event):
        # Focused buttons and radio controls own their keyboard activation.
        if self.current is self.options_frame and event.widget is self.root:
            self._on_start()

    def _on_start(self) -> None:
        if not self.start_button.instate(["disabled"]):
            self._launch(stats_only=False)

    def _on_coverage(self) -> None:
        if not self.coverage_button.instate(["disabled"]):
            self._launch(stats_only=True)

    def _on_cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.state(["disabled"])
        self.stage_var.set("Finishing current downloads…")

    def _on_escape(self) -> None:
        # All three frames stay "mapped" once gridded (tkraise only reorders),
        # so gate on the explicitly tracked current screen.
        if self.current is self.progress_frame and not self.finished:
            self._on_cancel()
            return
        if self.appearance_busy:
            return
        if self.current in (self.gallery_frame, self.settings_frame):
            self._show(self.options_frame)
            return
        self._close()

    def _on_window_close(self):
        if self.appearance_busy:
            return
        if self.current is self.progress_frame and not self.finished:
            self._on_cancel()
        else:
            self._close()

    def _close(self) -> None:
        with contextlib.suppress(Exception):
            self.root.destroy()

    def _folder_picker_from_worker(self, initial_dir: str):
        """Called from the worker thread; the Tk dialog must run on this thread."""
        ready = threading.Event()
        box: dict = {}
        self.events.put(("pick_folder", initial_dir, ready, box))
        ready.wait(timeout=900)
        return box.get("path")

    # -- event pump ----------------------------------------------------------

    def _render_counts(self) -> None:
        total = max(self.total, 1)
        self.pct_var.set(f"{min(100 * self.done // total, 100)}%")
        self.count_var.set(f"{self.done:,} of {self.total:,}")
        left = max(self.total - self.done, 0)
        self.remaining_var.set(f"{left:,} to go" if left else "finishing…")

    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle(event)
        except queue.Empty:
            pass
        if self.appearance_busy or (self.current is self.progress_frame and not self.finished):
            self.root.after(80, self._pump)

    def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "appearance":
            self.appearance_busy = False
            self.gallery.finish(event[1], event[2])
        elif kind == "task":
            _, _tid, description, total = event
            self.total = int(total)
            self.done = 0
            self.stage_var.set(description)
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=max(self.total, 1), value=0)
            self._render_counts()
        elif kind == "adv":
            self.done += int(event[2])
            self.bar.configure(value=self.done)
            self._render_counts()
        elif kind == "desc":
            text = str(event[2])
            self.stage_var.set(text if len(text) <= 56 else text[:55] + "…")
        elif kind == "notice":
            self.notice_var.set(str(event[1]))
        elif kind == "coverage":
            self._show_coverage(event[1])
        elif kind == "pick_folder":
            _, initial, ready, box = event
            with contextlib.suppress(Exception):
                box["path"] = (
                    filedialog.askdirectory(
                        parent=self.root,
                        initialdir=initial or "~",
                        title="Select your ProjectIgnis (EDOPro) folder",
                        mustexist=True,
                    )
                    or None
                )
            if box.get("path") is None:
                self.folder_cancelled = True
            ready.set()
        elif kind == "done":
            self._finish(event[1])

    # -- final screens ---------------------------------------------------------

    def _show_coverage(self, data: dict) -> None:
        self.mode = "coverage"
        self.coverage_shown = True
        self._render_result(coverage_summary(data))

    def _render_result(self, summary: Summary) -> None:
        self.summary_title_var.set(summary.title)
        self.hero_ok_var.set(summary.first[0])
        self.hero_ok_caption.configure(text=summary.first[1])
        self.hero_bad_var.set(summary.second[0])
        self.hero_bad_caption.configure(text=summary.second[1])
        self.hero_bad_label.configure(
            style="BigBad.TLabel" if summary.warning else "BigCalm.TLabel"
        )
        self._breakdown_rows(summary.rows)
        self.summary_note_var.set(summary.full_note)

    def _finish(self, payload: dict) -> None:
        self.finished = True
        self.bar.stop()
        cfg = self.run_config
        self.cfg.saved_original_text = cfg.saved_original_text
        if cfg.edopro_path != self.cfg.edopro_path:
            self.cfg.set_edopro_path(cfg.edopro_path)

        # Exit code and failure detection come first, whatever the mode —
        # a SystemExit or cancelled folder dialog must never masquerade as
        # "Already up to date" (or as a default coverage screen).
        if "exit" in payload:
            self.exit_code = payload["exit"] or 0
        elif "error" in payload:
            self.exit_code = 1
        if not self.coverage_shown or "error" in payload or payload.get("exit"):
            self._render_result(
                sync_summary(
                    cfg,
                    payload,
                    self.cancel_event.is_set(),
                    self.folder_cancelled,
                    self.mode == "coverage",
                )
            )

        self._show(self.summary_frame)
        self.root.bind("<Return>", self._on_return)
        self.root.bind("<Escape>", lambda _e: self._back_to_home())
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code


def run_app(cfg, version: str, run_coro, apply_choices) -> int:
    """Open the app window and drive a full sync from it. Returns an exit code.

    Raises GuiUnavailable when the window can't be created, so the caller can
    fall back to the plain console flow.
    """
    if tk is None:
        raise GuiUnavailable("tkinter is not available")
    return _App(cfg, version, run_coro, apply_choices).run()
