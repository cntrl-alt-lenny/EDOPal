"""Save, name and share themes in the local library."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

from themes import Theme

if TYPE_CHECKING:
    from .skins import SkinsPage

SHARE = "Shareable themes (*.edotheme);;All files (*)"


class LibraryActions:
    def __init__(self, page: SkinsPage) -> None:
        self.page = page
        self.view, self.maker = page.view, page.maker
        self.library = page.store.library
        self.initial_name = ""
        info = self.view.inspector
        page.gallery.import_file.clicked.connect(self.import_file)
        for name in ("Rename", "Duplicate"):
            info.tools[name].clicked.connect(partial(self.ask_name, name))
        info.tools["Delete"].clicked.connect(self.delete)
        info.tools["Export"].clicked.connect(self.export_file)
        self.maker.save.connect(self.save)
        self.maker.save_as.connect(partial(self.ask_name, "Save as"))
        self.maker.choose_image.connect(self.photo)
        self.view.cancel_name.clicked.connect(self.cancel_name)
        self.view.submit.clicked.connect(self.submit_name)
        self.view.form_name.returnPressed.connect(self.submit_name)

    def ask_name(self, action: str) -> None:
        self.page.note()
        self.page.naming = action
        self.view.show_panel(self.view.form)
        self.view.form_title.setText(action + " theme")
        name = self.maker.name.text() if self.page.mode == "maker" else self.page.look.name
        self.initial_name = name if action == "Rename" else name[:58] + " copy"
        self.view.form_name.setText(self.initial_name)
        self.view.form_name.selectAll()
        self.view.form_name.setFocus()

    def cancel_name(self) -> None:
        if self.name_edited and not self.page.confirm(
            "Discard unsaved changes?", "Leave without saving the new name?"
        ):
            return
        self.page.naming = ""
        self.page.note()
        self.view.show_panel(
            self.view.maker_panel if self.page.mode == "maker" else self.view.inspector
        )

    @property
    def name_edited(self) -> bool:
        return bool(self.page.naming and self.view.form_name.text() != self.initial_name)

    def submit_name(self) -> None:
        self._save(self.view.form_name.text(), self.page.naming == "Rename")

    def save(self) -> None:
        self._save(self.maker.name.text(), self.page.look.group == "My themes")

    def _save(self, name: str, existing: bool) -> None:
        try:
            theme = replace(
                self.maker.theme if self.page.mode == "maker" else self.page.look.theme,
                name=name.strip(),
            )
        except ValueError as exc:
            self.page.note(str(exc))
            return
        key = self.page.look.key if existing else None
        old = self.page.look.theme if existing else None
        self.page._run(
            lambda _: self.library.save(theme, key, expected=old),
            partial(self._saved, old=old, saved=theme),
            "Saving your theme…",
        )

    def _saved(self, key: str, *, old: Theme | None = None, saved: Theme | None = None) -> None:
        self.page.to_gallery()

        def undo() -> None:
            self.page._run(
                lambda _: self.library.save(old, key, expected=saved),
                self._saved,
                "Restoring the saved theme…",
            )

        self.page.note("Theme saved in My themes.", undo if old else None, "Undo save")
        self.page.reload(select=key)

    def photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.page, "Choose wallpaper", "", "Pictures (*.png *.jpg *.jpeg)"
        )
        if path:

            def done(digest: str) -> None:
                self.page.note()
                self.maker.update_theme(wallpaper="image", image=digest)

            self.page._run(
                lambda _: self.library.add_image(Path(path)), done, "Reading your picture…"
            )

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.page, "Import a theme", "", SHARE)
        if path:
            self.page._run(
                lambda _: self.library.import_file(Path(path)),
                self._saved,
                "Importing your theme…",
            )

    def export_file(self) -> None:
        look = self.page.look
        path, _ = QFileDialog.getSaveFileName(
            self.page, "Export theme", look.name + ".edotheme", SHARE
        )
        if path:
            self.page._run(
                lambda _: self.library.export_file(look.key, Path(path)),
                lambda _: self.page.note("Theme exported and ready to share."),
                "Exporting your theme…",
            )

    def delete(self) -> None:
        look = self.page.look
        if not self.page.confirm(
            "Remove saved theme?",
            f"Remove {look.name} from your library? Installed copies stay in their games.",
        ):
            return

        def undo() -> None:
            self.page._run(
                lambda _: self.library.save(look.theme, look.key, require_absent=True),
                self._saved,
                "Restoring your theme…",
            )

        def done(_: object) -> None:
            self.page.to_gallery()
            self.page.note(f"Removed {look.name} from your library.", undo, "Undo delete")
            self.page.reload()

        self.page._run(
            lambda _: self.library.delete(look.key, expected=look.theme),
            done,
            "Removing saved theme…",
        )
