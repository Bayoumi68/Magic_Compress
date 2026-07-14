"""Small formatting helpers for the UI."""

from __future__ import annotations

from datetime import datetime


def human_size(num: int | None) -> str:
    if num is None:
        return ""
    if num < 1024:
        return f"{num} B"
    value = float(num)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} EB"


def human_ratio(ratio: float | None) -> str:
    if ratio is None:
        return ""
    return f"{round(ratio * 100)}%"


def human_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")
