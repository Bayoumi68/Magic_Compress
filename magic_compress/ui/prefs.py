"""User preferences, persisted via QSettings with one place for defaults."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from ..core.model import ArchiveFormat, CompressionLevel

# Every user-facing option with its default. Types here drive coercion on read.
DEFAULTS: dict[str, object] = {
    "dark": False,                                  # dark theme
    "default_format": ArchiveFormat.ZIP.value,      # Create dialog default
    "default_level": int(CompressionLevel.NORMAL),  # Create dialog default
    "confirm_delete": True,                          # ask before deleting members
    "open_after_extract": True,                      # tick "open folder" by default
    "remember_geometry": True,                       # restore window size/position
    "doubleclick_open": True,                        # double-click a file to open it
    "unrar_path": "",                                # explicit unrar/7z tool for RAR
}


class Prefs:
    """Typed accessor over QSettings. Requires the app org/name to be set."""

    def __init__(self) -> None:
        self._s = QSettings()

    def get(self, key: str):
        default = DEFAULTS[key]
        return self._s.value(key, default, type=type(default))

    def set(self, key: str, value) -> None:
        self._s.setValue(key, value)

    # convenience typed getters used a lot
    @property
    def default_format(self) -> ArchiveFormat:
        try:
            return ArchiveFormat(self.get("default_format"))
        except ValueError:
            return ArchiveFormat.ZIP

    @property
    def default_level(self) -> CompressionLevel:
        try:
            return CompressionLevel(int(self.get("default_level")))
        except (ValueError, KeyError):
            return CompressionLevel.NORMAL


def apply_runtime_prefs(prefs: Prefs) -> None:
    """Apply preferences that affect library behaviour (not just the UI)."""
    unrar = prefs.get("unrar_path")
    if unrar:
        try:
            import rarfile
            rarfile.UNRAR_TOOL = unrar
        except Exception:  # noqa: BLE001 — never let a bad path break startup
            pass
