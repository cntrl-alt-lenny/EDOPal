"""Shared test setup for the whole suite."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")


@pytest.fixture(scope="session", autouse=True)
def _one_widget_application():
    """Create the QApplication before any engine test can create a QGuiApplication.

    The sleeve and theme engines only need QtGui, so their ensure_gui() helpers
    create a QGuiApplication when none exists. Qt allows one application object
    per process, and the Studio window tests need the QtWidgets subclass. In the
    real app the shell creates it first; here test order would decide.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield None
        return
    yield QApplication.instance() or QApplication([])
