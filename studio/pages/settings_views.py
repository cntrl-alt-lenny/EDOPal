"""Local controls and credits for the Settings pages."""

from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout

import config
from studio import theme as t
from studio.context import AppContext
from studio.widgets import Button, Panel, glyph, label

PROJECT = "https://github.com/cntrl-alt-lenny/edopro-hd-cards"


def open_url(url: QUrl, ctx: AppContext) -> None:
    if not QDesktopServices.openUrl(url):
        ctx.notify("Could not open the link or folder on this computer.")


def button(
    text: str, action: Callable[[], None], symbol: str = "", role: str = "secondary"
) -> Button:
    item = Button(text, role, symbol)
    item.clicked.connect(lambda _checked=False: action())
    return item


def actions(*buttons: Button) -> QHBoxLayout:
    row = QHBoxLayout()
    for item in buttons:
        row.addWidget(item)
    row.addStretch()
    return row


def add_about(ctx: AppContext, panel_for: Callable[[str], Panel]) -> None:
    panel = panel_for(f"{config.APP_NAME}   ·   {ctx.version}")
    brand = QHBoxLayout()
    brand.addWidget(glyph("brand", t.BRAND_ICON))
    brand.addWidget(label("Your unofficial companion for EDOPro.", "subheading", True), 1)
    panel.body.addLayout(brand)
    panel.body.addWidget(
        label(
            f"{config.APP_NAME} is an independent fan-made companion for EDOPro. EDOPro is a Project Ignis project. "
            f"{config.APP_NAME} is unaffiliated with Project Ignis, Konami, and Shueisha and has no endorsement from them. "
            "Yu-Gi-Oh! and related marks belong to their respective rights holders.",
            "body",
            True,
        )
    )
    panel.body.addLayout(
        actions(
            button("Project page", lambda: open_url(QUrl(PROJECT), ctx), "link"),
            button("Report an issue", lambda: open_url(QUrl(PROJECT + "/issues"), ctx), "link"),
        )
    )
    credits = panel_for("Licenses & credits")
    for name, detail in (
        (
            "Application",
            "© 2026 Lenny · MIT license. Based on NiiMiyo’s original HD downloader.",
        ),
        ("Typeface", "Inter by The Inter Project Authors · SIL Open Font License 1.1."),
        ("Card artwork", "YGOProDeck and ProjectIgnis"),
        ("Music covers", "LaunchBox Games Database"),
        ("Tournament decks", "Format Library"),
    ):
        row = QHBoxLayout()
        row.addWidget(label(name, "small"), 1)
        row.addWidget(label(detail, "body", True), 3)
        credits.body.addLayout(row)

    credits.body.addLayout(
        actions(
            button(
                "Read MIT license",
                lambda: open_url(QUrl(PROJECT + "/blob/HEAD/LICENSE"), ctx),
                "link",
                "quiet",
            ),
            button(
                "Read font license",
                lambda: open_url(
                    QUrl(PROJECT + "/blob/HEAD/assets/window/fonts/Inter-OFL.txt"), ctx
                ),
                "link",
                "quiet",
            ),
        )
    )
