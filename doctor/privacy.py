"""Render only allowlisted facts from a detailed local report."""

import re
import sys
from collections import Counter

from .model import CATALOGUE, DoctorReport


def _version(value: str | None) -> str:
    return (
        value
        if value and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){1,3}", value)
        else "unavailable"
    )


def support_summary(report: DoctorReport) -> str:
    """Omit local names and free text by construction, even inside relative paths."""
    platform = (
        "Windows"
        if sys.platform == "win32"
        else "macOS"
        if sys.platform == "darwin"
        else "Linux"
        if sys.platform.startswith("linux")
        else "Other"
    )
    lines = [
        "EDOPro Studio installation report",
        f"Studio {_version(report.studio_version)} | EDOPro {_version(report.game_version)} | {platform}",
    ]
    occurrences = Counter(item.id for item in report.findings if item.id in CATALOGUE)
    shown: set[str] = set()
    for item in report.findings:
        if item.id in shown or item.id not in CATALOGUE:
            continue
        shown.add(item.id)
        wording = CATALOGUE[item.id]
        count = occurrences[item.id]
        if count > 1:
            detail = f" ({count} findings)"
        else:
            values = []
            for key in wording.support_counts:
                value = item.evidence.get(key)
                if type(value) is int and value >= 0:
                    values.append(f"{key.replace('_', ' ')} {value:,}")
            detail = f" ({', '.join(values)})" if values else ""
        lines.append(f"[{wording.severity.upper()}] {item.id} | {wording.title}{detail}")
    return "\n".join(lines)
