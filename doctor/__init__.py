"""Read-only EDOPro installation diagnostics and support reporting."""

from .decks import ListConflict
from .model import CATALOGUE, DoctorReport, Finding, Severity
from .privacy import support_summary
from .report import inspect_installation
from .storage import ScanCancelled, StorageBreakdown, StorageCategory, storage_breakdown

__all__ = [
    "CATALOGUE",
    "DoctorReport",
    "Finding",
    "ListConflict",
    "ScanCancelled",
    "Severity",
    "StorageBreakdown",
    "StorageCategory",
    "inspect_installation",
    "storage_breakdown",
    "support_summary",
]
