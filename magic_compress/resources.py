"""Locate bundled asset files, whether running from source or a frozen exe."""

from __future__ import annotations

import os
import sys


def asset_path(name: str) -> str:
    """Absolute path to an asset under the ``assets`` directory."""
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "assets")  # type: ignore[attr-defined]
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
    return os.path.join(base, name)
