"""RAR handler — read/extract only (creating .rar is proprietary to WinRAR)."""

from __future__ import annotations

import datetime
import os

import rarfile

from .base import (
    ArchiveHandler,
    OperationCancelled,
    PasswordRequired,
    ToolMissing,
    WrongPassword,
    _never_cancelled,
    _noop_progress,
    normalize_arcname,
)
from .model import ArchiveEntry, ArchiveFormat

_TOOL_HELP = (
    "Extracting RAR archives needs an external tool. Install UnRAR "
    "(bundled with WinRAR) or 7-Zip and make sure it's on your PATH, "
    "then reopen the archive. (Browsing the file list works without it.)"
)


def _entry_encrypted(info: "rarfile.RarInfo") -> bool:
    probe = getattr(info, "needs_password", None)
    try:
        return bool(probe()) if callable(probe) else False
    except Exception:  # noqa: BLE001
        return False


class RarHandler(ArchiveHandler):
    format = ArchiveFormat.RAR
    can_create = False
    can_add = False
    can_delete = False
    can_encrypt = False
    can_comment = True
    can_edit_comment = False

    def read_comment(self) -> str:
        try:
            with rarfile.RarFile(self.path) as rf:
                return rf.comment or ""
        except rarfile.Error:
            return ""

    def needs_password(self) -> bool:
        try:
            with rarfile.RarFile(self.path) as rf:
                return bool(rf.needs_password())
        except rarfile.PasswordRequired:
            return True
        except rarfile.Error:
            return False

    def entries(self, password: str | None = None) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        try:
            with rarfile.RarFile(self.path) as rf:
                if password:
                    rf.setpassword(password)
                for info in rf.infolist():
                    name = normalize_arcname(info.filename)
                    is_dir = info.isdir()
                    try:
                        dt: datetime.datetime | None = datetime.datetime(*info.date_time)
                    except (ValueError, TypeError):
                        dt = None
                    crc = getattr(info, "CRC", None)
                    out.append(
                        ArchiveEntry(
                            path=name + ("/" if is_dir and not name.endswith("/") else ""),
                            is_dir=is_dir,
                            size=info.file_size,
                            compressed_size=getattr(info, "compress_size", None),
                            modified=dt,
                            crc=f"{crc & 0xFFFFFFFF:08X}" if crc else None,
                            encrypted=_entry_encrypted(info),
                        )
                    )
        except rarfile.PasswordRequired as exc:
            raise PasswordRequired("This archive's file list is encrypted — a password is required.") from exc
        return out

    def extract(self, members, dest_dir, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        os.makedirs(dest_dir, exist_ok=True)
        wanted = {normalize_arcname(m).rstrip("/") for m in members} if members else None
        try:
            with rarfile.RarFile(self.path) as rf:
                if password:
                    rf.setpassword(password)
                infos = rf.infolist()
                if wanted is not None:
                    infos = [i for i in infos if normalize_arcname(i.filename).rstrip("/") in wanted]
                total = len(infos)
                for i, info in enumerate(infos, 1):
                    if is_cancelled():
                        raise OperationCancelled()
                    report(i, total, info.filename)
                    rf.extract(info, path=dest_dir)
        except (rarfile.RarCannotExec, rarfile.RarExecError) as exc:
            raise ToolMissing(_TOOL_HELP) from exc
        except rarfile.PasswordRequired as exc:
            raise PasswordRequired("This archive is encrypted — a password is required.") from exc
        except rarfile.RarWrongPassword as exc:
            raise WrongPassword("Incorrect password.") from exc

    def test(self, password=None, report=None, is_cancelled=None) -> list[str]:
        report = report or _noop_progress
        report(0, 1, "Testing archive…")
        try:
            with rarfile.RarFile(self.path) as rf:
                if password:
                    rf.setpassword(password)
                bad = rf.testrar()  # returns name of first bad file, or None
        except (rarfile.RarCannotExec, rarfile.RarExecError) as exc:
            raise ToolMissing(_TOOL_HELP) from exc
        except rarfile.PasswordRequired as exc:
            raise PasswordRequired("This archive is encrypted — a password is required.") from exc
        except rarfile.RarWrongPassword as exc:
            raise WrongPassword("Incorrect password.") from exc
        report(1, 1, "Done")
        return [bad] if bad else []
