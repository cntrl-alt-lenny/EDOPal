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

import config
from doctor import (
    DoctorReport,
    Finding,
    StorageBreakdown,
    inspect_installation,
    storage_breakdown,
    support_summary,
)
from studio import theme as t
from studio.context import AppContext, Installation
from studio.pages.health_layout import ToolPage, editor, row, task_heading
from studio.pages.health_links import open_finding
from studio.pages.health_report import (
    PAGE_NAMES,
    byte_size,
    category_folder,
    evidence,
    evidence_page,
    groups,
    heading,
    practice_summary,
    route,
)
from studio.tasks import Job, WorkerContext
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
        self.reports: dict[Path, DoctorReport] = {}
        self.scan_job: Job | None = None
        self.scan_path: Path | None = None
        self.evidence_finding: Finding | None = None

    def shown(self) -> None:
        self.entered = True
        self._show_report()

    def hidden(self) -> bool:
        if self.scan_job:
            self.scan_job.cancel()
        self.evidence_finding = None
        return super().hidden()

    def installation_changed(self, installation: Installation) -> None:
        if self.scan_job and self.scan_path != Path(installation.path):
            self.scan_job.cancel()
        if self.entered:
            self._show_report()

    def _show_report(self) -> None:
        self.report = self.reports.get(Path(self.ctx.installation.path))
        self.storage = None
        if self.report is None:
            self.refresh()
        else:
            self.overview()

    def refresh(self) -> None:
        self.stop_read()
        self.report = self.storage = None
        game = self.ctx.installation.path
        if not game:
            self.no_game()
            return
        self.state_view(
            "loading",
            "Checking your installation…",
            "Reading game files and settings. You can keep using the other pages.",
        )
        if self.scan_job is not None:
            return
        self.scan_path = Path(game)
        config_path = self.ctx.cfg.config_path
        job = self.ctx.tasks.start(
            lambda worker: inspect_installation(
                game, config_path=config_path, cancelled=worker.cancel.is_set
            )
        )
        self.scan_job = job
        job.result.connect(self._checked)
        job.error.connect(self._scan_failed)
        job.finished.connect(self._scan_finished)

    def _checked(self, report: DoctorReport) -> None:
        if self.scan_job is None or self.scan_job.cancel_event.is_set():
            return
        assert self.scan_path is not None
        self.reports[self.scan_path] = report
        self.report = report
        self.overview()

    def _scan_failed(self, exc: Exception) -> None:
        if self.scan_job is not None and not self.scan_job.cancel_event.is_set():
            self.state_view(
                "error",
                "Health check could not finish",
                self.folder_read_error(),
                "Retry",
                self.refresh,
                technical=str(exc),
            )

    def _scan_finished(self) -> None:
        self.scan_job = self.scan_path = None
        if self.entered and self.state == "loading":
            self.refresh()

    def overview(self) -> None:
        self.stop_read()
        if self.report is None:
            return
        self.surface("overview")
        findings = self.report.findings
        ordered = [item for _, _, items in groups(findings) for item in items]
        concerns = [item for item in ordered if item.severity in ("problem", "warning")]
        problems = sum(item.severity == "problem" for item in concerns)
        warnings = len(concerns) - problems
        counts = [
            f"{count:,} {name}{'s' if count != 1 else ''}"
            for name, count in (("problem", problems), ("warning", warnings))
            if count
        ]
        verdict = ", ".join(counts) or "No problems found"
        description = "Checks local files and settings."
        if not self.ctx.installation.ready:
            description = "Card data is still missing. Open EDOPro and let its updates finish."
        self.banner(verdict, description)
        tools = row()
        tools.addStretch()
        self.rerun = Button("Run health check again", "secondary", "health")
        self.rerun.clicked.connect(self.refresh)
        self.disk = Button("View storage", "secondary", "coverage")
        self.disk.clicked.connect(self.open_storage)
        self.support = Button("Support summary", "secondary", "library")
        self.support.clicked.connect(self.open_support)
        tools.addWidget(self.disk)
        tools.addWidget(self.support)
        tools.addWidget(self.rerun)
        self.body.addLayout(tools)
        self.disclosures: list[tuple[Finding, Button]] = []
        self.sections: dict[Button, Button] = {}
        if concerns:
            panel = Panel(t.LG)
            panel.body.addWidget(label("Needs attention", "heading"))
            for item in concerns[:3]:
                self._finding(panel.body, item)
            self.body.addWidget(panel)
        self._finding_section("More findings", concerns[3:])
        self._finding_section("Notes", [item for item in ordered if item.severity == "info"])
        self._finding_section("Passed checks", [item for item in ordered if item.severity == "ok"])
        self.body.addStretch()
        self.rerun.setFocus()

    def _finding_section(self, name: str, items: list[Finding]) -> None:
        if not items:
            return
        panel = Panel(t.SM)
        toggle = Button(f"{name} ({len(items):,})", "quiet", "chevron")
        toggle.setAccessibleName(name)
        toggle.setCheckable(True)
        panel.body.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        details = QWidget()
        layout = QVBoxLayout(details)
        margins(layout, t.SM)
        layout.setSpacing(t.XS)
        for item in items:
            self.sections[self._finding(layout, item)] = toggle
        panel.body.addWidget(details)
        details.hide()
        toggle.toggled.connect(details.setVisible)
        self.body.addWidget(panel)

    def _finding(self, layout: QVBoxLayout, item: Finding) -> Button:
        toggle = Button("", "quiet")
        self.disclosures.append((item, toggle))
        toggle.setAccessibleName(heading(item))
        if item.severity in ("problem", "warning"):
            toggle.setAccessibleDescription(item.severity.capitalize())
        toggle.setCheckable(True)
        line = QHBoxLayout(toggle)
        line.setContentsMargins(t.SM, t.XS, t.SM, t.XS)
        symbol = {"ok": "check", "info": "chevron", "problem": "health"}.get(
            item.severity, "warning"
        )
        line.addWidget(glyph(symbol, t.LG, COLOURS[item.severity]))
        if item.severity in ("problem", "warning"):
            severity = label(item.severity.capitalize(), "small")
            severity.setStyleSheet(f"color: {COLOURS[item.severity]}")
            line.addWidget(severity)
        line.addWidget(label(heading(item), "muted" if item.severity == "ok" else "body", True), 1)
        marker = label("+", "subheading")
        line.addWidget(marker)
        layout.addWidget(toggle)
        for child in toggle.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        details = QWidget()
        content = QVBoxLayout(details)
        content.setContentsMargins(t.XL, t.XS, t.MD, t.SM)
        layout.addWidget(details)
        details.hide()

        def expand(opened: bool) -> None:
            if opened:
                for _, other in self.disclosures:
                    if other is not toggle:
                        other.setChecked(False)
            if opened and not content.count():
                self._explain(content, item)
            marker.setText("−" if opened else "+")
            details.setVisible(opened)

        toggle.toggled.connect(expand)
        return toggle

    def _explain(self, content: QVBoxLayout, item: Finding) -> None:
        practice = item.id == "windbot.list_conflicts"
        explanation = practice_summary(item) if practice else item.explanation
        action = item.action
        if item.id == "state.lock_idle":
            explanation = "You can leave it in place and continue using the update tools."
            action = ""
        elif item.id == "state.interrupted" and route(item) in {"backup", "skins"}:
            explanation = "An unfinished change was found for this EDOPro folder."
            action = f"Close EDOPro, then open {PAGE_NAMES[route(item)]} to recover the change."
        content.addWidget(label(explanation, "body", True))
        if action and not practice:
            content.addWidget(label(action, "body", True))
        for key, value in () if item.id == "state.lock_idle" else evidence(item):
            line = row()
            line.addWidget(label(key, "muted"))
            fact = ElidedLabel()
            fact.set_value(value)
            line.addWidget(fact, 1)
            content.addLayout(line)
        for key, value in item.evidence.items():
            if isinstance(value, (tuple, list, dict)) and value:
                name = "Card limit matches" if practice else key.replace("_", " ").capitalize()
                text = (
                    "Review card limit matches" if practice else f"{name} · See all {len(value):,}"
                )
                button = Button(text, "secondary")
                button.clicked.connect(
                    lambda _=False, name=name, value=value: self.open_evidence(item, name, value)
                )
                content.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        if page := route(item):
            button = Button(f"Open {PAGE_NAMES[page].replace('&', '&&')}", "primary", "arrow")
            button.clicked.connect(lambda: open_finding(self, item, page))
            content.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)
        elif item.id in {
            "installation.missing",
            "installation.data_only",
            "installation.read_only",
        }:
            button = Button("Change folder", "secondary", "folder")
            button.clicked.connect(self.ctx.installations_requested.emit)
            content.addWidget(button, 0, Qt.AlignmentFlag.AlignRight)

    def open_evidence(self, item: Finding, name: str, values: tuple | list | dict) -> None:
        self.surface("evidence")
        self.evidence_finding = item
        self.back_button(self._end_evidence)
        task_heading(self.body, "Technical details", heading(item))
        panel = Panel()
        panel.body.addWidget(label(name, "heading"))
        preview = editor("Technical details", readonly=True)
        preview.setMinimumHeight(t.TILE_LARGE)
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
        previous = self.evidence_finding
        self.evidence_finding = None
        self.overview()
        for finding, toggle in self.disclosures:
            if finding is previous:
                if section := self.sections.get(toggle):
                    section.setChecked(True)
                toggle.setChecked(True)
                toggle.setFocus()
                self.scroll.ensureWidgetVisible(toggle)
                break

    def open_support(self) -> None:
        if self.report is None:
            return
        self.surface("support")
        self.back_button(lambda: self._return_to_findings("support"))
        task_heading(self.body, f"{config.APP_NAME} support summary", "Review before sharing.")
        panel = Panel()
        panel.body.addWidget(
            SectionHeader(
                "Support summary preview",
                "The summary leaves out folder paths, filenames and player names.",
            )
        )
        self.preview = editor("Support summary preview", readonly=True)
        lines = support_summary(self.report).splitlines()
        # Retitle the allowlisted report without adding any local finding details.
        lines[0] = f"{config.APP_NAME} support summary"
        self.preview.setPlainText("\n".join(lines))
        self.preview.setMinimumHeight(t.TILE_LARGE)
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
        back = self.back_button(lambda: self._return_to_findings("disk"))
        task_heading(
            self.body,
            "Measuring storage…",
            f"Counting files in your EDOPro folder and {config.APP_NAME} data. Choose Back to stop.",
        )
        self.body.addStretch()
        back.setFocus()
        game = self.ctx.installation.path
        profile = Path(self.ctx.cfg.config_path).parent

        def measure(worker: WorkerContext) -> StorageBreakdown:
            return storage_breakdown(game, data_dir=profile, cancelled=worker.cancel.is_set)

        self.read(measure, self._storage_ready, self._storage_error)

    def _storage_error(self, exc: Exception) -> None:
        self.state_view(
            "storage_error",
            "Storage could not be measured",
            "Reconnect the drive or check that the folders are readable, then try again.",
            "Retry",
            self.open_storage,
            back=lambda: self._return_to_findings("disk"),
            technical=str(exc),
        )

    def _storage_ready(self, result: StorageBreakdown) -> None:
        self.storage = result
        self.surface("storage")
        back = self.back_button(lambda: self._return_to_findings("disk"))
        task_heading(
            self.body,
            "Storage breakdown",
            "Space used by your game files, soundtracks and backups.",
        )
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
            title = category.title
            if category.bytes:
                piece = QWidget()
                piece.setStyleSheet(f"background: {colour};")
                piece.setFixedHeight(t.MD)
                piece.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                piece.setToolTip(f"{title} · {byte_size(category.bytes)}")
                bar.addWidget(piece, max(1, round(10000 * category.bytes / result.total_bytes)))
            dot = QWidget()
            dot.setFixedSize(t.SM, t.SM)
            dot.setStyleSheet(
                f"background: {colour if category.bytes else t.EDGE}; border-radius: {t.XS}px"
            )
            grid.addWidget(dot, index, 0)
            grid.addWidget(label(title, "body" if category.files else "muted", True), index, 1)
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
                button.setAccessibleName(f"Open folder for {title}")
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
                section = self.sections.get(toggle)
                if toggle.isChecked() and (section is None or section.isChecked()):
                    toggle.setChecked(False)
                    toggle.setFocus()
                    return True
            for section in reversed(dict.fromkeys(self.sections.values())):
                if section.isChecked():
                    section.setChecked(False)
                    section.setFocus()
                    return True
        if self.state in {"support", "storage", "storage_loading", "storage_error"}:
            self._return_to_findings("support" if self.state == "support" else "disk")
            return True
        return False

    def _return_to_findings(self, control: str) -> None:
        self.overview()
        getattr(self, control).setFocus()
