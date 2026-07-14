"""Application bootstrap: build the QApplication, theme it, show the window."""

from __future__ import annotations

import getpass
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME
from .resources import asset_path
from .single_instance import SingleInstance
from .ui.main_window import MainWindow
from .ui.prefs import Prefs, apply_runtime_prefs
from .ui.style import apply_theme


def _first_path(args: list[str]) -> str | None:
    for a in args:
        if a and not a.startswith("-"):
            return a
    return None


def parse_startup_action(argv: list[str]):
    """Map command-line args (as invoked from Explorer) to a startup action.

    Returns (kind, data) where kind is 'add' | 'extract_here' | 'extract_to' |
    'open', or None for a plain launch.
    """
    rest = argv[1:]
    if "--add" in rest:
        i = rest.index("--add")
        paths = [a for a in rest[i + 1:] if a and not a.startswith("-")]
        return ("add", paths) if paths else None
    if "--extract-here" in rest:
        arc = _first_path(rest[rest.index("--extract-here") + 1:])
        return ("extract_here", arc) if arc else None
    if "--extract-to" in rest:
        arc = _first_path(rest[rest.index("--extract-to") + 1:])
        return ("extract_to", arc) if arc else None
    arc = _first_path(rest)
    return ("open", arc) if arc else None


def _action_to_payload(action) -> dict:
    if action is None:
        return {"action": "raise"}
    kind, data = action
    if kind == "add":
        return {"action": "add", "paths": data}
    return {"action": kind, "path": data}


def _instance_key() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "default"
    safe = "".join(c for c in user if c.isalnum()) or "default"
    return f"MagicCompress-{safe}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    # Headless hooks used by the installer/uninstaller (no window shown).
    if "--register-associations" in argv:
        from .associations import register
        register()
        return 0
    if "--unregister-associations" in argv:
        from .associations import unregister
        unregister()
        return 0

    app = QApplication.instance() or QApplication(argv)
    app.setOrganizationName("MagicCompress")
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(QIcon(asset_path("icon.png")))

    action = parse_startup_action(argv)

    # If another instance is already running, forward this launch to it and exit.
    instance = SingleInstance(_instance_key())
    if not instance.is_primary and instance.send(_action_to_payload(action)):
        return 0

    prefs = Prefs()
    apply_runtime_prefs(prefs)
    dark = bool(prefs.get("dark"))
    apply_theme(app, dark)

    window = MainWindow(dark=dark)
    instance.message_received.connect(window.handle_forwarded)
    window.show()

    if action is not None:
        QTimer.singleShot(0, lambda: window.dispatch_action(action))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
