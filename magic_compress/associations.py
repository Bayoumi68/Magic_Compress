"""Windows Explorer file-type integration (per-user, no admin required).

Adds WinRAR-style right-click actions:
  * "Add to archive…"        on every file and folder  (creates an archive)
  * "Open with Magic Compress"  on archive files
  * "Extract Here"              on archive files
  * "Extract to subfolder"      on archive files
plus an "Open with" registration so the app appears in the shell's Open-with UI.

Everything is written under HKEY_CURRENT_USER\\Software\\Classes and is fully
reversible via unregister(). It never seizes the default handler — on Windows
that is protected by a per-user "UserChoice" hash, so the correct approach is to
offer actions the user can invoke.
"""

from __future__ import annotations

import os
import sys

PROGID = "MagicCompress.Archive"
APP_NAME = "Magic Compress"

SUPPORTED_EXTENSIONS = [
    ".zip", ".zipx", ".7z", ".rar", ".tar",
    ".gz", ".bz2", ".xz", ".tgz", ".tbz2", ".txz",
]

_CLASSES = r"Software\Classes"


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("File associations are only supported on Windows.")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _launcher_prefix() -> str:
    """Quoted command prefix that launches the app (frozen exe or python)."""
    if is_frozen():
        return f'"{sys.executable}"'
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            pyw = cand
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{pyw}" "{main_py}"'


def app_launch_command() -> str:
    """Command that opens the app with a file argument (used by the ProgID)."""
    return f'{_launcher_prefix()} "%1"'


def _icon_spec() -> str | None:
    return f'"{sys.executable}",0' if is_frozen() else None


def _norm_ext(ext: str) -> str:
    return ext if ext.startswith(".") else "." + ext


# -- registry helpers -------------------------------------------------------
def _set(subkey: str, name: str | None, value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _verb(subkey: str, label: str, command: str) -> None:
    """Write a shell verb: a label, optional icon, and its command."""
    _set(subkey, None, label)
    icon = _icon_spec()
    if icon:
        _set(subkey, "Icon", icon)
    _set(subkey + r"\command", None, command)


def _delete_tree(subkey: str) -> None:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    try:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(subkey + "\\" + child)
    finally:
        winreg.CloseKey(key)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass


def _delete_value(subkey: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def notify_shell() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:  # noqa: BLE001
        pass


# -- public API -------------------------------------------------------------
def is_registered() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{_CLASSES}\*\shell\{PROGID}.Add").Close()
        return True
    except FileNotFoundError:
        return False


def _cleanup_legacy(exts: list[str]) -> None:
    """Remove keys written by earlier versions so we don't leave duplicates.

    The first release used ``…\\shell\\MagicCompress.Archive`` as the single
    "Open" verb; the current layout uses ``…\\MagicCompress.Archive.Open`` etc.
    """
    for raw in exts:
        ext = _norm_ext(raw)
        _delete_tree(rf"{_CLASSES}\SystemFileAssociations\{ext}\shell\{PROGID}")


def register(extensions: list[str] | None = None) -> None:
    _require_windows()
    exts = extensions or SUPPORTED_EXTENSIONS
    _cleanup_legacy(exts)
    prefix = _launcher_prefix()
    icon = _icon_spec()

    # ProgID (so the app shows up in the shell "Open with" chooser).
    _set(rf"{_CLASSES}\{PROGID}", None, f"{APP_NAME} Archive")
    if icon:
        _set(rf"{_CLASSES}\{PROGID}\DefaultIcon", None, icon)
    _set(rf"{_CLASSES}\{PROGID}\shell\open\command", None, f'{prefix} "%1"')

    # "Add to archive…" on every file and folder, plus folder background.
    add_cmd = f'{prefix} --add "%1"'
    for scope in ("*", "Directory"):
        _verb(rf"{_CLASSES}\{scope}\shell\{PROGID}.Add", f"Add to archive…  ({APP_NAME})", add_cmd)
    _verb(rf"{_CLASSES}\Directory\Background\shell\{PROGID}.Add",
          f"Add to archive…  ({APP_NAME})", f'{prefix} --add "%V"')

    # Per-archive-type verbs (independent of the current default handler).
    for raw in exts:
        ext = _norm_ext(raw)
        _set(rf"{_CLASSES}\{ext}\OpenWithProgids", PROGID, "")
        base = rf"{_CLASSES}\SystemFileAssociations\{ext}\shell"
        _verb(rf"{base}\{PROGID}.Open", f"Open with {APP_NAME}", f'{prefix} "%1"')
        _verb(rf"{base}\{PROGID}.ExtractHere", "Extract Here", f'{prefix} --extract-here "%1"')
        _verb(rf"{base}\{PROGID}.ExtractTo", "Extract to subfolder", f'{prefix} --extract-to "%1"')

    notify_shell()


def unregister(extensions: list[str] | None = None) -> None:
    _require_windows()
    exts = extensions or SUPPORTED_EXTENSIONS
    _cleanup_legacy(exts)
    for scope in ("*", "Directory", r"Directory\Background"):
        _delete_tree(rf"{_CLASSES}\{scope}\shell\{PROGID}.Add")
    for raw in exts:
        ext = _norm_ext(raw)
        _delete_value(rf"{_CLASSES}\{ext}\OpenWithProgids", PROGID)
        for verb in ("Open", "ExtractHere", "ExtractTo"):
            _delete_tree(rf"{_CLASSES}\SystemFileAssociations\{ext}\shell\{PROGID}.{verb}")
    _delete_tree(rf"{_CLASSES}\{PROGID}")
    notify_shell()
