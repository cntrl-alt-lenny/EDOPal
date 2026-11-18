"""Inline views and sound-set cards, styled with the Studio component kit."""

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from studio import theme as t
from studio.context import Installation
from studio.pages.card_views import page_layout
from studio.pages.music_sounds_controls import BANNER_HEIGHT
from studio.pages.sounds_model import (
    EVENT_COUNT,
    FilesChanged,
    Snapshot,
    Source,
    coverage,
    display_text,
)
from studio.widgets import (
    Button,
    ElidedLabel,
    Hero,
    Panel,
    SectionHeader,
    glyph,
    label,
    margins,
)


def button_row(*buttons: Button) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(t.SM)
    for button in buttons:
        row.addWidget(button)
    return row


def empty_layout(layout: QGridLayout) -> None:
    while layout.count():
        widget = layout.takeAt(0).widget()
        widget.hide()
        widget.deleteLater()


def elided(role: str) -> ElidedLabel:
    text = ElidedLabel()
    text.setProperty("role", role)
    return text


class Overview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        body = page_layout(self)
        self.hero = Hero(
            "Sounds",
            "Duel sound effects",
            "Hear the difference from the first draw to the final attack.",
            "sounds",
        )
        self.hero.setFixedHeight(BANNER_HEIGHT)
        body.addWidget(self.hero)
        self.compact_title = label("Duel sound effects", "display")
        body.addWidget(self.compact_title)
        self.current = Panel(t.LG)
        current_row = QHBoxLayout()
        current_row.setSpacing(t.XL)
        self.current.body.addLayout(current_row)
        current_row.addWidget(glyph("sounds", t.ICON_BIG), 0, Qt.AlignmentFlag.AlignTop)
        copy = QWidget()
        copy_body = page_layout(copy)
        margins(copy_body, 0)
        copy_body.setSpacing(t.XS)
        copy_body.addWidget(label("INSTALLED SOUNDS", "goldKicker"))
        self.installed = elided("heading")
        copy_body.addWidget(self.installed)
        self.settings = label("", "body", True)
        copy_body.addWidget(self.settings)
        self.effects_warning = label("", "small", True)
        self.volume_warning = label("", "small", True)
        self.backend_warning = label("", "small", True)
        for warning in (self.effects_warning, self.volume_warning, self.backend_warning):
            copy_body.addWidget(warning)
            warning.hide()
        current_row.addWidget(copy, 1)
        self.choose = Button("Choose folder", "secondary", "folder")
        self.rescan = Button("Check again", "secondary")
        self.toggle = Button("Turn sound effects on", "secondary")
        for button in (self.choose, self.rescan, self.toggle):
            current_row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            button.hide()
        self.technical = Button("Technical details", "quiet")
        self.technical.setCheckable(True)
        self.backend_info = label("", "muted", True)
        self.technical.toggled.connect(self.backend_info.setVisible)
        self.current.body.addWidget(self.technical, 0, Qt.AlignmentFlag.AlignLeft)
        self.current.body.addWidget(self.backend_info)
        self.summary = Button("", "secondary")
        self.summary.setCheckable(True)
        self.summary.setToolTip("Show installed sounds and audio settings")
        self.summary.toggled.connect(self.current.setVisible)
        body.addWidget(self.summary)
        body.addWidget(self.current)

        self.mix = Button("Mix your own", "secondary", "settings")
        self.saved = Button("Collection", "quiet", "library")
        self.downloads = Button("Downloads", "quiet", "download")
        self.chants = Button("Card chants", "quiet", "sounds")
        self.sets_heading = SectionHeader("Pick a set")
        self.tools = QGridLayout()
        self.tools.setSpacing(t.SM)
        self.tools.setColumnStretch(0, 1)
        self.tool_strip = QWidget()
        shortcuts = button_row(self.saved, self.downloads, self.chants)
        shortcuts.insertStretch(0, 1)
        self.tool_strip.setLayout(shortcuts)
        self.columns = 0
        self.compact_tools = False
        self.place_tools(False)
        body.addLayout(self.tools)
        self.cards = QGridLayout()
        self.cards.setSpacing(t.MD)
        for col in range(3):
            self.cards.setColumnStretch(col, 1)
        body.addLayout(self.cards)
        self.wide_actions = QVBoxLayout()
        body.addLayout(self.wide_actions)
        body.addStretch(1)

    def place_tools(self, compact: bool) -> None:
        self.compact_tools = compact
        for widget in (self.sets_heading, self.tool_strip, self.mix):
            self.tools.removeWidget(widget)
        self.tools.addWidget(self.sets_heading, 0, 0)
        self.tools.addWidget(self.mix, 0, 2)
        if compact:
            self.tools.addWidget(self.tool_strip, 1, 0, 1, 3)
        else:
            self.tools.addWidget(self.tool_strip, 0, 1)

    def show_status(
        self, install: Installation, snapshot: Snapshot | None, title: str, failed: str
    ) -> None:
        self.choose.setVisible(not install.path)
        self.choose.setText("Change folder" if install.path else "Choose folder")
        instructions = "Preview and save sounds here. Choose a game folder to use them in EDOPro."
        if failed:
            title = "Game folder unavailable" if not install.ready else "Could not read sounds"
            instructions = "Check that the selected folder is available."
        elif install.path and not install.ready:
            title = "Finish setting up EDOPro"
            instructions = "Open EDOPro and let its updates finish, then choose Check again."
        elif install.ready and snapshot is None:
            title = "Reading game sounds…"
            instructions = "Looking at the selected installation."
        self.rescan.setText("Retry" if failed else "Check again")
        self.rescan.setVisible(bool(failed or (install.path and not install.ready)))
        self.toggle.hide()
        warnings = {}
        if snapshot:
            settings = snapshot.settings
            enabled = {
                True: "Effects on",
                False: "Effects off",
                None: "Check Sound Effects in EDOPro’s audio settings.",
            }
            parts = [enabled[settings.enabled]]
            if settings.volume is not None:
                parts.append(f"{settings.volume}% volume")

            instructions = "  ·  ".join(parts)
            if settings.enabled is False:
                warnings[self.effects_warning] = "Turn sound effects on here with EDOPro closed."
            if settings.volume == 0:
                warnings[self.volume_warning] = (
                    "Raise the sound effects volume in EDOPro’s audio settings."
                )
            if settings.backend == "none":
                warnings[self.backend_warning] = "Choose an audio output in EDOPro to hear effects."
            self.toggle.setText(
                "Turn sound effects on" if settings.enabled is False else "Turn sound effects off"
            )
            self.toggle.setVisible(settings.enabled is not None)
            self.toggle.setToolTip("Changes the effects switch and preserves your volume.")
        self.installed.set_value(title, display_text(title))
        self.settings.setText(instructions)
        self.summary.setText(f"Installed sounds · {display_text(title)} · {instructions}")
        backend = snapshot.settings.backend if snapshot else None
        self.technical.setVisible(bool(backend or failed))
        self.backend_info.setText(f"Audio backend: {backend}" if backend else failed)
        self.backend_info.setVisible(bool(backend or failed) and self.technical.isChecked())
        for warning in (
            self.effects_warning,
            self.volume_warning,
            self.backend_warning,
        ):
            warning.setText(warnings.get(warning, ""))
            warning.setVisible(warning in warnings)


class InlineView(QWidget):
    def __init__(self, title: str, description: str = "") -> None:
        super().__init__()
        self.body = page_layout(self)
        self.back = Button("Back", "quiet", "back")
        self.body.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        top = QHBoxLayout()
        top.setSpacing(t.MD)
        copy = QWidget()
        copy_body = page_layout(copy)
        margins(copy_body, 0)
        copy_body.setSpacing(t.XS)
        self.heading = label(title, "display", True)
        copy_body.addWidget(self.heading)
        self.description = label(description, "body", True)
        self.description.setVisible(bool(description))
        copy_body.addWidget(self.description)
        top.addWidget(copy, 1)
        self.body.addLayout(top)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(t.SM)
        top.addLayout(self.actions)


class SetCard(Panel):
    def __init__(self, source: Source, use: Callable[[], None] | None) -> None:
        super().__init__(t.LG)
        self.body.setSpacing(t.SM)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sound_set = source.sound_set
        eyebrow = QHBoxLayout()
        eyebrow.setSpacing(t.SM)
        eyebrow.addWidget(glyph("sounds", t.LG))
        eyebrow.addWidget(label(source.category.upper(), "goldKicker"), 1)
        self.body.addLayout(eyebrow)
        name = elided("subheading")
        name.set_value(sound_set.name, display_text(sound_set.name))
        self.body.addWidget(name)
        playable, silent = coverage(sound_set)
        count = f"{playable} of {EVENT_COUNT} sound slots have a clip"
        if silent:
            count += f"  ·  {silent} silent"
        self.body.addWidget(label(count, "body", True))
        credits = sound_set.credits
        credit = elided("muted")
        attribution = (
            f"{credits.author or 'Author unknown'}  ·  {credits.license or 'License undocumented'}"
        )
        credit.set_value(
            "\n".join(filter(None, (attribution, credits.attribution))), display_text(attribution)
        )
        self.body.addWidget(credit)
        self.body.addStretch(1)
        self.play = Button("Preview set", "secondary")
        self.play.setAccessibleName(f"Preview set for {display_text(sound_set.name)}")
        self.play.setEnabled(any(sound_set.assignments.values()))
        buttons = button_row(self.play)
        self.use = None
        if use is not None:
            self.use = Button("Select set", "quiet")
            self.use.clicked.connect(use)
            buttons.addWidget(self.use)
        buttons.addStretch(1)
        self.body.addLayout(buttons)

    def select(self, selected: bool) -> None:
        if self.objectName() == ("chosenSound" if selected else ""):
            return
        if self.use is not None:
            self.use.setText("Selected" if selected else "Select set")
        self.setObjectName("chosenSound" if selected else "")
        self.setStyleSheet(
            f"QFrame#chosenSound {{ border: {t.HAIR}px solid {t.GOLD}; background: {t.ACTIVE}; }}"
        )


class ActionBar(Panel):
    def __init__(self) -> None:
        super().__init__(t.MD)
        row = QHBoxLayout()
        row.setSpacing(t.SM)
        copy = QWidget()
        body = page_layout(copy)
        margins(body, 0)
        body.setSpacing(t.XS)
        self.name = elided("subheading")
        self.detail = label("", "muted", True)
        self.restart = label("Applies the next time you start EDOPro.", "muted", True)
        body.addWidget(self.name)
        body.addWidget(self.detail)
        body.addWidget(self.restart)
        row.addWidget(copy, 1)
        self.save = Button("Save sound set", "secondary")
        self.apply = Button("Apply sound effects", "primary", "check")
        row.addLayout(button_row(self.save, self.apply))
        self.body.addLayout(row)
        self.undo = Button("Undo last sound change", "quiet")
        self.restore = Button("Restore original sounds", "quiet")
        self.history = QWidget()
        history_body = page_layout(self.history)
        margins(history_body, 0)
        history_body.setSpacing(t.XS)
        self.undo_help = label(
            "Put back the sounds from just before your last change.", "muted", True
        )
        self.restore_help = label(
            "Put back the files from before the first recorded change to each sound.", "muted", True
        )
        history_options = QHBoxLayout()
        history_options.setSpacing(t.MD)
        for button, hint in ((self.undo, self.undo_help), (self.restore, self.restore_help)):
            button.setAccessibleDescription(hint.text())
            button.setToolTip(hint.text())
            option = QVBoxLayout()
            option.setSpacing(t.XS)
            option.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            option.addWidget(hint)
            history_options.addLayout(option, 1)
        history_body.addLayout(history_options)
        self.body.addWidget(self.history)


class Feedback(Panel):
    def __init__(self) -> None:
        super().__init__(t.SM)
        row = QHBoxLayout()
        row.setSpacing(t.SM)
        self.copy = label("", "notice", True)
        row.addWidget(self.copy, 1)
        self.dismiss = Button("Dismiss", "quiet")
        self.more = Button("Technical details", "quiet")
        self.more.setCheckable(True)
        row.addWidget(self.more)
        row.addWidget(self.dismiss)
        self.body.addLayout(row)
        self.details = label("", "muted", True)
        self.body.addWidget(self.details)
        self.more.toggled.connect(self.details.setVisible)
        self.hide()

    def show_message(self, message: str, pending: bool = False, *, details: str = "") -> None:
        self.more.setChecked(False)
        self.more.setVisible(bool(details))
        self.details.setText(details)
        self.details.hide()
        self.copy.setText(message)
        self.copy.setToolTip("")
        self.dismiss.setVisible(not pending)
        self.setVisible(bool(message))

    def write_failed(self, action: str, exc: BaseException) -> None:
        if isinstance(exc, FilesChanged):
            self.show_message(
                "The game’s sounds changed outside the app. Your files were kept. Check the installed sounds before applying again."
            )
        elif "changed after installation" in str(exc):
            self.show_message(
                "A sound changed outside the app. Put back its previous file before using sound history.",
                details=str(exc),
            )
        elif action in ("enable", "disable"):
            self.show_message(
                "Could not change the effects switch. Use EDOPro’s audio settings to change it."
            )
        else:
            self.show_message(
                "Check that the EDOPro folder is writable and retry the sound change.",
                details=str(exc),
            )


class NameView(InlineView):
    def __init__(self) -> None:
        super().__init__("Save sound set", "Give this mix a name so you can find it again.")
        form = Panel(t.XL)
        self.name_label = name_label = label("Mix name", "subheading")
        self.name = QLineEdit()
        self.name.setMinimumHeight(t.BUTTON_H)
        self.name.setMaxLength(120)
        self.name.setPlaceholderText("For example, Saturday night duels")
        self.name.setAccessibleName(name_label.text())
        name_label.setBuddy(self.name)
        form.body.addWidget(name_label)
        form.body.addWidget(self.name)
        self.submit = Button("Save sound set", "primary", "check")
        form.body.addWidget(self.submit, 0, Qt.AlignmentFlag.AlignRight)
        self.body.addWidget(form)
        self.body.addStretch(1)
        self.summary = label("", "body", True)
        form.body.insertWidget(0, self.summary)
        self.name.returnPressed.connect(self.submit.click)


def add_section(cards: QGridLayout, widgets: Iterable[QWidget], columns: int = 1) -> None:
    for index, widget in enumerate(widgets):
        cards.addWidget(widget, index // columns, index % columns)
