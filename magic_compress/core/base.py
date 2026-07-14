"""Abstract handler interface, exceptions and shared helpers."""

from __future__ import annotations

import os
from typing import Callable, Iterable, Sequence

from .model import ArchiveEntry, ArchiveFormat, CompressionLevel

# A progress callback: report(current, total, message="")
ProgressFn = Callable[[int, int, str], None]
# A cancel probe: returns True when the user has asked to stop.
CancelFn = Callable[[], bool]


class ArchiveError(Exception):
    """Base class for all archive-related failures."""


class PasswordRequired(ArchiveError):
    """The archive is encrypted and no (or an empty) password was supplied."""


class WrongPassword(ArchiveError):
    """A password was supplied but it did not decrypt the archive."""


class ToolMissing(ArchiveError):
    """An external helper tool (e.g. `unrar`) is required but not installed."""


class OperationCancelled(ArchiveError):
    """Raised internally to unwind a long operation the user cancelled."""


class UnsupportedOperation(ArchiveError):
    """The handler does not support the requested operation for this format."""


def _noop_progress(current: int, total: int, message: str = "") -> None:  # pragma: no cover
    pass


def _never_cancelled() -> bool:  # pragma: no cover
    return False


def normalize_arcname(path: str) -> str:
    """Return a clean POSIX-style in-archive path."""
    return path.replace("\\", "/").lstrip("/")


def safe_target(dest_dir: str, arcname: str) -> str:
    """Resolve *arcname* under *dest_dir*, refusing path-traversal escapes.

    Guards against the classic "zip slip" where a crafted archive contains
    entries like ``../../etc/passwd``.
    """
    dest_dir = os.path.abspath(dest_dir)
    # Drop drive letters / leading slashes and normalise separators.
    clean = normalize_arcname(arcname)
    clean = os.path.normpath(clean)
    target = os.path.abspath(os.path.join(dest_dir, clean))
    if target != dest_dir and not target.startswith(dest_dir + os.sep):
        raise ArchiveError(f"Refusing to extract outside destination: {arcname!r}")
    return target


class ArchiveHandler:
    """Base class for reading/modifying an existing archive on disk.

    Subclasses set the capability flags and implement the relevant methods.
    Creation is a classmethod because it does not need an existing instance.
    """

    #: Format this handler manages.
    format: ArchiveFormat

    # --- capability flags (overridden by subclasses) ---
    can_create: bool = False
    can_add: bool = False
    can_delete: bool = False
    can_encrypt: bool = False
    can_encrypt_names: bool = False   # encrypt the file listing too (7z)
    can_comment: bool = False         # archive carries a comment field
    can_edit_comment: bool = False    # ...and we can write it back

    def __init__(self, path: str):
        self.path = path

    # -- reading ---------------------------------------------------------
    def needs_password(self) -> bool:
        """True if the archive can't even be *listed* without a password."""
        return False

    def entries(self, password: str | None = None) -> list[ArchiveEntry]:
        raise NotImplementedError

    def extract(
        self,
        members: Sequence[str],
        dest_dir: str,
        password: str | None = None,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> None:
        raise NotImplementedError

    def extract_all(
        self,
        dest_dir: str,
        password: str | None = None,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> None:
        self.extract(
            [e.path for e in self.entries(password) if not e.is_dir],
            dest_dir,
            password=password,
            report=report,
            is_cancelled=is_cancelled,
        )

    def test(
        self,
        password: str | None = None,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> list[str]:
        """Verify integrity; return a list of failing member paths ([] == OK)."""
        raise NotImplementedError

    # -- modifying -------------------------------------------------------
    def add(
        self,
        sources: Sequence[tuple[str, str]],  # (fs_path, arcname) pairs
        level: CompressionLevel = CompressionLevel.NORMAL,
        password: str | None = None,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> None:
        raise UnsupportedOperation(f"Adding to {self.format.label} is not supported.")

    def delete(
        self,
        members: Sequence[str],
        password: str | None = None,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> None:
        raise UnsupportedOperation(f"Deleting from {self.format.label} is not supported.")

    # -- comment ---------------------------------------------------------
    def read_comment(self) -> str:
        return ""

    def write_comment(self, text: str) -> None:
        raise UnsupportedOperation(f"{self.format.label} archives cannot store a comment.")

    # -- creating --------------------------------------------------------
    @classmethod
    def create(
        cls,
        dest_path: str,
        sources: Sequence[tuple[str, str]],  # (fs_path, arcname) pairs
        level: CompressionLevel = CompressionLevel.NORMAL,
        password: str | None = None,
        encrypt_names: bool = False,
        report: ProgressFn | None = None,
        is_cancelled: CancelFn | None = None,
    ) -> None:
        raise NotImplementedError


def collect_sources(paths: Iterable[str]) -> list[tuple[str, str]]:
    """Expand a list of filesystem paths into (fs_path, arcname) pairs.

    Directories are walked recursively; each selected top-level item keeps its
    own name as the arcname root (so dropping ``C:/data/pics`` stores entries
    under ``pics/...``).
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in paths:
        p = os.path.abspath(raw)
        if p in seen:
            continue
        seen.add(p)
        if os.path.isdir(p):
            root_name = os.path.basename(p.rstrip("\\/")) or p
            for dirpath, dirnames, filenames in os.walk(p):
                rel_dir = os.path.relpath(dirpath, p)
                arc_dir = root_name if rel_dir == "." else f"{root_name}/{rel_dir.replace(os.sep, '/')}"
                # Preserve empty directories.
                if not filenames and not dirnames:
                    pairs.append((dirpath, arc_dir + "/"))
                for fn in filenames:
                    pairs.append((os.path.join(dirpath, fn), f"{arc_dir}/{fn}"))
        elif os.path.isfile(p):
            pairs.append((p, os.path.basename(p)))
    return pairs
