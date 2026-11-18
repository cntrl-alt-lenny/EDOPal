"""Host Game display in the real shell against a disposable offline installation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication

import config
import main
from gamesettings import DisplaySettings, Snapshot
from studio import theme
from studio.context import AppContext, Installation
from studio.pages.settings import SECTIONS, SettingsPage
from studio.pages.settings_display import DISPLAY_SECTION, DisplayView, open_display
from studio.shell import PAGES, StudioWindow
from tools.studio_screenshots import DemoSync, create_demo, grab_window, settle

DEMO = """# Project Ignis: EDOPro system.conf
# Overwritten on normal game exit
driver_type = default
vsync = 1
maxFPS = 60
fullscreen = 0
antialias = 2
textfont = fonts/Inter-Regular.ttf 12
showFPS = 1
dpi_scale = 1.000000
draw_field_spell = 1
"""


def seed(game: Path) -> Path:
    conf = game / "config/system.conf"
    conf.parent.mkdir(exist_ok=True)
    conf.write_bytes(DEMO.encode())
    fonts = game / "fonts"
    fonts.mkdir(exist_ok=True)
    for name in ("Inter-Regular.ttf", "Inter-Medium.ttf"):
        (fonts / name).touch()
    return conf


class DemoSettings(SettingsPage):
    def __init__(self, ctx: AppContext) -> None:
        with patch.dict(SECTIONS, display=DISPLAY_SECTION):
            super().__init__(ctx)

    def open_section(self, key: str) -> None:
        if key == "display":
            self.section = key
            open_display(self)
        else:
            super().open_section(key)


def host(root: Path, size: tuple[int, int]) -> tuple[StudioWindow, DisplayView]:
    app = QApplication.instance() or QApplication([config.APP_NAME])
    theme.apply(app)
    cfg = create_demo(root)
    seed(Path(cfg.edopro_path))
    ctx = AppContext(cfg, main.VERSION, DemoSync(), main._apply_gui_choices)
    with patch(
        "studio.shell.PAGES", tuple(DemoSettings if p is SettingsPage else p for p in PAGES)
    ):
        window = StudioWindow(ctx, startup=False)
    ctx.set_installation(Installation.inspect(cfg.edopro_path))
    window.resize(*size)
    window.show()
    window.navigate("settings")
    page = window.pages["settings"]
    page.tiles["display"].click()
    view = page.stack.currentWidget()
    settle(lambda: not ctx.tasks.jobs)
    return window, view


def render(out: Path, size: tuple[int, int], root: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    window, view = host(root, size)
    ctx = window.ctx
    selected = ctx.installation
    files = []

    def shot(name: str, *, choices: bool = False) -> None:
        target = out / f"{name}.png"
        pix = grab_window(window)
        if choices:
            view.profile.showPopup()
            QApplication.processEvents()
            popup = view.profile.view().window()
            # Offscreen Qt reports an 800px monitor even for a 1600px demo window.
            popup.move(view.profile.mapToGlobal(QPoint(0, view.profile.height())))
            painter = QPainter(pix)
            painter.drawPixmap(window.mapFromGlobal(popup.mapToGlobal(QPoint())), popup.grab())
            painter.end()
            view.profile.hidePopup()
        if (pix.width(), pix.height()) != size or not pix.save(str(target)):
            raise RuntimeError(f"Could not render {target.name} at {size}.")
        files.append(target)

    def idle() -> None:
        settle(lambda: not ctx.tasks.jobs)

    release = Event()
    try:
        shot("01-current")
        shot("22-device-profiles", choices=True)
        view.profile.setCurrentIndex(1)
        shot("02-handheld")
        view.secondary.click()
        view.name.setText("Handheld evenings")
        shot("03-save-profile")
        view.primary.click()
        idle()
        shot("04-saved-profile")
        view.controls["dpi_scale"].setValue(130)
        view.secondary.click()
        view.name.setText("Handheld evenings")
        view.primary.click()
        idle()
        shot("19-profile-name-in-use")
        view.escape()
        view.controls["dpi_scale"].setValue(125)
        view._tab(1)
        shot("05-graphics")
        view.area.verticalScrollBar().setValue(view.area.verticalScrollBar().maximum())
        shot("20-graphics-options")
        driver = view.controls["driver_type"]
        driver.setCurrentIndex(driver.findData("opengl"))
        view.primary.click()
        shot("06-review-renderer")
        with patch("music.settings._game_running", return_value=True):
            view.primary.click()
            idle()
        shot("07-game-open")
        with patch("music.settings._game_running", side_effect=OSError()):
            view.block.retry.click()
            idle()
        shot("08-process-unknown")
        with patch("music.settings._game_running", side_effect=lambda _: not release.wait(15)):
            view.block.retry.click()
            shot("09-saving")
            release.set()
            idle()
        shot("10-applied")
        view._prepare("undo")
        shot("11-undo-review")
        view.primary.click()
        idle()
        shot("12-undone")
        view.escape()
        view.profile.setCurrentIndex(3)
        view.primary.click()
        view.primary.click()
        idle()
        view._prepare("restore")
        shot("13-restore-review")
        view.primary.click()
        idle()
        shot("14-restored")
        ctx.set_installation(Installation())
        shot("15-no-game")
        ctx.set_installation(Installation(selected.path))
        shot("16-game-updating")
        conf = Path(selected.path) / "config/system.conf"
        conf.unlink()
        ctx.set_installation(selected)
        idle()
        shot("17-read-error")
        seed(Path(selected.path))
        view._reload()
        idle()
        view.profile.setCurrentIndex(3)
        view.primary.click()
        conf.write_bytes(conf.read_bytes().replace(b"1.000000", b"1.10"))
        view.primary.click()
        idle()
        shot("18-changed-during-review")
        release.clear()
        inspect = DisplaySettings.inspect

        def slow_read(engine: DisplaySettings) -> Snapshot:
            release.wait(15)
            return inspect(engine)

        with patch.object(type(view.engine), "inspect", slow_read):
            view.primary.click()
            shot("21-reading")
            release.set()
            idle()
        return files
    finally:
        release.set()
        idle()
        window.close()
        QApplication.processEvents()


if __name__ == "__main__":
    app = QApplication([config.APP_NAME])
    with tempfile.TemporaryDirectory(prefix=".display-demo-", dir=Path.cwd()) as tmp:
        window, _ = host(Path(tmp), (1280, 800))
        app.exec()
