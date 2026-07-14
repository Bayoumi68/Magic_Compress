"""ZIP handler with WinZip-AES (256-bit) support, backed by pyzipper."""

from __future__ import annotations

import datetime
import os
import shutil
import zipfile

import pyzipper

from .base import (
    ArchiveHandler,
    OperationCancelled,
    PasswordRequired,
    WrongPassword,
    _never_cancelled,
    _noop_progress,
    normalize_arcname,
    safe_target,
)
from .model import ArchiveEntry, ArchiveFormat, CompressionLevel, deflate_level


def _is_under(name: str, deleted: set[str]) -> bool:
    """True if *name* equals a deleted entry or lives under a deleted folder."""
    for d in deleted:
        d = d.rstrip("/")
        if name == d or name == d + "/" or name.startswith(d + "/"):
            return True
    return False


def _map_password_error(exc: Exception, had_password: bool) -> Exception:
    msg = str(exc).lower()
    if "password required" in msg or ("encrypted" in msg and "password" in msg):
        return PasswordRequired("This archive is encrypted — a password is required.")
    if "bad password" in msg or "mac" in msg or "checksum" in msg:
        return WrongPassword("Incorrect password.")
    if had_password:
        return WrongPassword("Incorrect password.")
    return exc


class ZipHandler(ArchiveHandler):
    format = ArchiveFormat.ZIP
    can_create = True
    can_add = True
    can_delete = True
    can_encrypt = True
    can_encrypt_names = False
    can_comment = True
    can_edit_comment = True

    # -- reading ---------------------------------------------------------
    def entries(self, password: str | None = None) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        with pyzipper.AESZipFile(self.path) as z:
            for info in z.infolist():
                name = normalize_arcname(info.filename)
                is_dir = info.is_dir()
                try:
                    dt: datetime.datetime | None = datetime.datetime(*info.date_time)
                except (ValueError, TypeError):
                    dt = None
                out.append(
                    ArchiveEntry(
                        path=name,
                        is_dir=is_dir,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        modified=dt,
                        crc=None if is_dir else f"{info.CRC & 0xFFFFFFFF:08X}",
                        encrypted=bool(info.flag_bits & 0x1),
                    )
                )
        return out

    def extract(self, members, dest_dir, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        pw = password.encode("utf-8") if password else None

        with pyzipper.AESZipFile(self.path) as z:
            if pw:
                z.setpassword(pw)
            by_name = {normalize_arcname(i.filename): i for i in z.infolist()}
            wanted = list(members) if members else list(by_name.keys())
            infos = [by_name[normalize_arcname(m)] for m in wanted if normalize_arcname(m) in by_name]
            total = len(infos)
            for i, info in enumerate(infos, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, info.filename)
                target = safe_target(dest_dir, info.filename)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                try:
                    with z.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, length=1 << 20)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    raise _map_password_error(exc, bool(pw)) from exc

    def test(self, password=None, report=None, is_cancelled=None) -> list[str]:
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        bad: list[str] = []
        pw = password.encode("utf-8") if password else None
        with pyzipper.AESZipFile(self.path) as z:
            if pw:
                z.setpassword(pw)
            files = [i for i in z.infolist() if not i.is_dir()]
            total = len(files)
            for i, info in enumerate(files, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, info.filename)
                try:
                    with z.open(info) as src:
                        while src.read(1 << 20):
                            pass
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    mapped = _map_password_error(exc, bool(pw))
                    if isinstance(mapped, (PasswordRequired, WrongPassword)):
                        raise mapped from exc
                    bad.append(info.filename)
        return bad

    # -- comment ---------------------------------------------------------
    def read_comment(self) -> str:
        with pyzipper.AESZipFile(self.path) as z:
            return z.comment.decode("utf-8", "replace")

    def write_comment(self, text: str) -> None:
        # The comment lives in the end-of-central-directory record, so this is
        # a cheap trailer rewrite regardless of archive size.
        with pyzipper.AESZipFile(self.path, "a") as z:
            z.comment = text.encode("utf-8")

    # -- modifying -------------------------------------------------------
    def add(self, sources, level=CompressionLevel.NORMAL, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        encryption = pyzipper.WZ_AES if password else None
        ctype = zipfile.ZIP_STORED if level == CompressionLevel.STORE else zipfile.ZIP_DEFLATED
        sources = list(sources)
        with pyzipper.AESZipFile(self.path, "a", compression=ctype, encryption=encryption) as z:
            if password:
                z.setpassword(password.encode("utf-8"))
            total = len(sources)
            for i, (fs_path, arcname) in enumerate(sources, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, arcname)
                if arcname.endswith("/"):
                    z.writestr(arcname, b"")
                    continue
                z.write(
                    fs_path,
                    arcname,
                    compress_type=ctype,
                    compresslevel=deflate_level(level) if ctype == zipfile.ZIP_DEFLATED else None,
                )

    def delete(self, members, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        deleted = {normalize_arcname(m) for m in members}
        pw = password.encode("utf-8") if password else None
        tmp = self.path + ".mc_tmp"

        try:
            with pyzipper.AESZipFile(self.path) as zin:
                if pw:
                    zin.setpassword(pw)
                encryption = pyzipper.WZ_AES if password else None
                keep = [i for i in zin.infolist() if not _is_under(normalize_arcname(i.filename), deleted)]
                with pyzipper.AESZipFile(tmp, "w", encryption=encryption) as zout:
                    if password:
                        zout.setpassword(pw)
                    total = len(keep)
                    for idx, info in enumerate(keep, 1):
                        if is_cancelled():
                            raise OperationCancelled()
                        report(idx, total, info.filename)
                        if info.is_dir():
                            zout.writestr(info.filename, b"")
                            continue
                        try:
                            data = zin.read(info.filename)
                        except (RuntimeError, zipfile.BadZipFile) as exc:
                            raise _map_password_error(exc, bool(pw)) from exc
                        zout.compression = info.compress_type
                        zout.writestr(info.filename, data)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # -- creating --------------------------------------------------------
    @classmethod
    def create(cls, dest_path, sources, level=CompressionLevel.NORMAL, password=None,
               encrypt_names=False, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        encryption = pyzipper.WZ_AES if password else None
        ctype = zipfile.ZIP_STORED if level == CompressionLevel.STORE else zipfile.ZIP_DEFLATED
        sources = list(sources)
        with pyzipper.AESZipFile(dest_path, "w", compression=ctype, encryption=encryption) as z:
            if password:
                z.setpassword(password.encode("utf-8"))
            total = len(sources)
            for i, (fs_path, arcname) in enumerate(sources, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, arcname)
                if arcname.endswith("/"):
                    z.writestr(arcname, b"")
                    continue
                z.write(
                    fs_path,
                    arcname,
                    compress_type=ctype,
                    compresslevel=deflate_level(level) if ctype == zipfile.ZIP_DEFLATED else None,
                )
