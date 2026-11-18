from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from doctor import (
    DoctorReport,
    Finding,
    StorageBreakdown,
    inspect_installation,
    storage_breakdown,
    support_summary,
)
from studio import theme as t
from studio.context import AppContext
from studio.pages.health_layout import ToolPage, editor, row
from studio.pages.health_report import (
    PAGE_NAMES,
    STATUS,
    byte_size,
    category_folder,
    evidence,
    evidence_page,
    groups,
    heading,
    route,
)
from studio.tasks import WorkerContext
from studio.widgets import Button, ElidedLabel, Panel, SectionHeader, glyph, label, margins

COLOURS = {"problem": t.RED, "warning": t.GOLD, "info": t.TEXT, "ok": t.GREEN}
STORAGE_COLOURS = (t.GOLD, t.GREEN, t.RED, t.BRONZE, t.PALE, t.MUTED)


class HealthPage(ToolPage):
    id, title, icon = "health", "Health check", "health"
    mood = "check"
    introduction = "Check game files, artwork and settings in this installation."

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self.report: DoctorReport | None = None
        self.storage: StorageBreakdown | None = None

    def refresh(self) -> None:
        self.stop_read()
        self.report = self.storage = None
        game = self.ctx.installation.path
        if not game:
            self.no_game()
            return
        config = self.ctx.cfg.config_path
        self.state_view(
            "loading",
            "Checking your installation…",
            "Reading game files and settings. You can keep using the other pages.",
        )
        self.read(
            lambda _: inspect_installation(game, config_path=config),
            self._checked,
            lambda exc: self.state_view(
                "error", "The check could not finish", str(exc), "Retry", self.refresh
            ),
        )

    def _checked(self, report: DoctorReport) -> None:
        self.report = report
        self.overview()

    def overview(self) -> None:
        self.stop_read()
        if self.report is None:
            return
        self.surface("overview")
        findings = self.report.findings
        counts = Counter(item.severity for item in findings)
        concerns = counts["problem"] + counts["warning"]
        if counts["problem"]:
            n = counts["problem"]
            verdict = f"{n} problem{'s' if n != 1 else ''} to fix"
        else:
            verdict = "Worth a look" if concerns else "Checks passed"
        description = next(
            (
                heading(item) + "."
                for status in ("problem", "warning")
                for item in findings
                if item.severity == status
            ),
            "Your installation passed the checks for problems and warnings.",
        )
        if not self.ctx.installation.ready:
            description = "Card data is still missing. Open EDOPro and let its updates finish."
        self.banner(verdict, description)
        panel = Panel(t.LG)
        metrics = row()
        for severity, name in (
            ("problem", "Problems"),
            ("warning", "Warnings"),
            ("info", "For reference"),
            ("ok", "Passed"),
        ):
            metric = QVBoxLayout()
            metric.setSpacing(t.XS)
            value = label(str(counts[severity]), "heading")
            value.setStyleSheet(f"color: {COLOURS[severity]}")
            metric.addWidget(value)
            metric.addWidget(label(name, "small"))
            metrics.addLayout(metric, 1)
        self.rerun = Button("Run again", "secondary", "health")
        self.rerun.clicked.connect(self.refresh)
        metrics.addWidget(self.rerun)
        panel.body.addLayout(metrics)
        self.body.addWidget(panel)
        tools = row()
        tools.addWidget(label("Findings", "heading"), 1)
        self.disk = Button("Storage", "secondary", "coverage")
        self.disk.clicked.connect(self.open_storage)
        self.support = Button("Support summary", "secondary", "library")
        self.support.clicked.connect(self.open_support)
        tools.addWidget(self.disk)
        tools.addWidget(self.support)
        self.body.addLayout(tools)
        self.disclosures: list[tuple[Finding, Button]] = []
        for severity, area, items in groups(findings):
            group = Panel(t.LG)
            section = row()
            name = label(STATUS[severity].upper(), "kicker")
            name.setStyleSheet(f"color: {COLOURS[severity]}")
            section.addWidget(name)
            section.addWidget(label(area, "subheading"), 1)
            section.addWidget(label(str(len(items)), "muted"))
            group.body.addLayout(section)
            for item in items:
                self._finding(group, item)
            self.body.addWidget(group)
        self.body.addStretch()
        self.rerun.setFocus()

    def _finding(self, group: Panel, item: Finding) -> None:
        toggle = Button("", "quiet")
        self.disclosures.append((item, toggle))
        toggle.setAccessibleName(heading(item))
        toggle.setCheckable(True)
        line = QHBoxLayout(toggle)
        line.setContentsMargins(t.SM, t.XS, t.SM, t.XS)
        line.addWidget(
            glyph("check" if item.severity == "ok" else "chevron", t.LG, COLOURS[item.severity])
        )
        line.addWidget(label(heading(item), "muted" if item.severity == "ok" else "body", True), 1)
        marker = label("+", "subheading")
        line.addWidget(marker)
        group.body.addWidget(toggle)
        details = QWidget()
        content = QVBoxLayout(details)
        content.setContentsMargins(t.XL, t.XS, t.MD, t.SM)
        group.body.addWidget(details)
        details.hide()

        def expand(opened: bool) -> None:
            if opened and not content.count():
                self._explain(content, item)
            marker.setText("−" if opened else "+")
            details.setVisible(opened)

        toggle.toggled.connect(expand)

    def _explain(self, content: QVBoxLayout, item: Finding) -> None:
        explanation = item.explanation
        if item.id == "windbot.list_conflicts":
            explanation = (
                "The practice opponent comes with its own decks. Some use more copies of a card "
                "than an installed Forbidden and Limited List allows. Each example names the "
                "deck, the list and the card limit."
            )
        content.addWidget(label(explanation, "body", True))
        if item.action:
            content.addWidget(label(item.action, "body", True))
        for key, value in evidence(item):
            line = row()
            line.addWidget(label(key, "muted"))
            fact = ElidedLabel()
            fact.set_value(value)
            line.addWidget(fact, 1)
            content.addLayout(line)
        for key, value in item.evidence.items():
            if isinstance(value, (tuple, list, dict)) and value:
                name = key.replace("_", " ").capitalize()
                button = Button(f"{name} · See all {len(value):,}", "secondary")
                button.clicked.connect(
                    lambda _=False, name=name, value=value: self.open_evidence(item, name, value)
                )
                content.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        if page := route(item):
            button = Button(f"Open {PAGE_NAMES[page]}", "secondary", "arrow")
            button.clicked.connect(lambda: self.ctx.navigate(page))
            content.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        elif item.id in {
            "installation.missing",
            "installation.data_only",
            "installation.read_only",
        }:
            button = Button("Change folder", "secondary", "folder")
            button.clicked.connect(self.ctx.installations_requested.emit)
            content.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

    def open_evidence(self, item: Finding, name: str, values: tuple | list | dict) -> None:
        self.surface("evidence")
        self.evidence_finding = item
        self.back_button(self._end_evidence)
        self.banner("Finding details", heading(item))
        panel = Panel()
        panel.body.addWidget(label(name, "heading"))
        preview = editor("Local evidence", readonly=True)
        preview.setMinimumHeight(t.HERO_MIN)
        panel.body.addWidget(preview, 1)
        nav = row()
        previous, following = Button("Previous"), Button("Next")
        position = label("", "muted")
        nav.addWidget(previous)
        nav.addStretch()
        nav.addWidget(position)
        nav.addStretch()
        nav.addWidget(following)
        panel.body.addLayout(nav)
        self.body.addWidget(panel, 1)
        offset = 0
        per_page = 20

        def show(start: int) -> None:
            nonlocal offset
            offset = start
            preview.setPlainText(evidence_page(values, start, per_page))
            end = min(len(values), start + per_page)
            position.setText(f"{start + 1:,}–{end:,} of {len(values):,}")
            previous.setEnabled(start > 0)
            following.setEnabled(end < len(values))

        previous.clicked.connect(lambda: show(offset - per_page))
        following.clicked.connect(lambda: show(offset + per_page))
        show(0)
        preview.setFocus()

    def _end_evidence(self) -> None:
        self.overview()
        for finding, toggle in self.disclosures:
            if finding is self.evidence_finding:
                toggle.setChecked(True)
                toggle.setFocus()
                self.scroll.ensureWidgetVisible(toggle)
                break

    def open_support(self) -> None:
        if self.report is None:
            return
        self.surface("support")
        self.back_button(self.overview)
        self.banner("Support summary", "Review the text below before sharing it with someone.")
        panel = Panel()
        panel.body.addWidget(
            SectionHeader(
                "Clipboard preview",
                "The summary leaves out folder paths, filenames and player names.",
            )
        )
        self.preview = editor("Support summary preview", readonly=True)
        self.preview.setPlainText(support_summary(self.report))
        self.preview.setMinimumHeight(t.HERO_MIN)
        panel.body.addWidget(self.preview, 1)
        button = Button("Copy support summary", "primary", "library")
        button.clicked.connect(self._copy)
        panel.body.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        self.body.addWidget(panel, 1)
        button.setFocus()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.preview.toPlainText())
        self.ctx.notify("Support summary copied.")

    def open_storage(self) -> None:
        if self.storage is not None:
            self._storage_ready(self.storage)
            return
        self.surface("storage_loading")
        back = self.back_button(self.overview)
        self.banner(
            "Measuring storage…",
            "Counting files in your installation and profile. Choose Back to stop.",
            large=True,
        )
        back.setFocus()
        game = self.ctx.installation.path
        profile = Path(self.ctx.cfg.config_path).parent

        def measure(worker: WorkerContext) -> StorageBreakdown:
            return storage_breakdown(game, data_dir=profile, cancelled=worker.cancel.is_set)

        self.read(measure, self._storage_ready, self._storage_error)

    def _storage_error(self, exc: Exception) -> None:
        self.state_view(
            "storage_error", "Storage could not be measured", str(exc), "Retry", self.open_storage
        )
        self.back_button(self.overview)

    def _storage_ready(self, result: StorageBreakdown) -> None:
        self.storage = result
        self.surface("storage")
        back = self.back_button(self.overview)
        self.banner("Storage breakdown", "Space used by your game files, soundtracks and backups.")
        panel = Panel()
        panel.body.addWidget(label(f"{byte_size(result.total_bytes)} in use", "heading"))
        categories = sorted(result.categories, key=lambda c: -c.bytes)
        bar = QHBoxLayout()
        margins(bar, 0)
        bar.setSpacing(0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(t.LG)
        grid.setVerticalSpacing(t.SM)
        for index, category in enumerate(categories):
            colour = STORAGE_COLOURS[index % len(STORAGE_COLOURS)]
            if category.bytes:
                piece = QWidget()
                piece.setStyleSheet(f"background: {colour};")
                piece.setFixedHeight(t.MD)
                piece.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                piece.setToolTip(f"{category.title} · {byte_size(category.bytes)}")
                bar.addWidget(piece, max(1, round(10000 * category.bytes / result.total_bytes)))
            dot = QWidget()
            dot.setFixedSize(t.SM, t.SM)
            dot.setStyleSheet(
                f"background: {colour if category.bytes else t.EDGE}; border-radius: {t.XS}px"
            )
            grid.addWidget(dot, index, 0)
            grid.addWidget(
                label(category.title, "body" if category.files else "muted", True), index, 1
            )
            file_count = f"{category.files:,} file" + ("s" if category.files != 1 else "")
            grid.addWidget(label(file_count, "muted"), index, 2)
            grid.addWidget(label(byte_size(category.bytes), "small"), index, 3)
            if category.files:
                folder = category_folder(
                    category.id,
                    Path(self.ctx.installation.path),
                    Path(self.ctx.cfg.config_path).parent,
                )
                button = Button("Open folder", "quiet", "folder")
                button.setToolTip(str(folder))
                button.setAccessibleName(f"Open folder for {category.title}")
                button.clicked.connect(lambda _=False, target=folder: self.open_folder(target))
                grid.addWidget(button, index, 4)
        grid.setColumnStretch(1, 1)
        panel.body.addLayout(bar)
        panel.body.addLayout(grid)
        if result.unreadable:
            panel.body.addWidget(
                label(
                    f"{len(result.unreadable):,} locations could not be read. Their size is missing from the total.",
                    "body",
                    True,
                )
            )
        self.body.addWidget(panel)
        self.body.addStretch()
        back.setFocus()

    def escape(self) -> bool:
        if self.state == "evidence":
            self._end_evidence()
            return True
        if self.state == "overview":
            for _, toggle in reversed(self.disclosures):
                if toggle.isChecked():
                    toggle.setChecked(False)
                    toggle.setFocus()
                    return True
        if self.state in {"support", "storage", "storage_loading", "storage_error"}:
            self.overview()
            return True
        return False
