"""Prefer the desktop's Fontconfig for compatibility with its local font configuration."""

import ctypes
import sys
from pathlib import Path

try:
    ctypes.CDLL("libfontconfig.so.1")
except OSError:
    fallback = Path(sys._MEIPASS) / "font-libs"
    ctypes.CDLL(str(fallback / "libfreetype.so.6"))
    ctypes.CDLL(str(fallback / "libfontconfig.so.1"))
