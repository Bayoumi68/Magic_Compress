"""7-Zip handler with AES-256 (and optional header encryption), backed by py7zr."""

from __future__ import annotations

import datetime
import os
import re
import shutil
import tempfile
from contextlib import contextmanager

import multivolumefile
import py7zr
from py7zr.callbacks import ExtractCallback

from .base import (
    ArchiveHandler,
    OperationCancelled,
    PasswordRequired,
    WrongPassword,
    _never_cancelled,
    _noop_progress,
    normalize_arcname,
)
from .model import ArchiveEntry, ArchiveFormat, CompressionLevel, lzma_preset

# A multi-volume 7z looks like  name.7z.0001, name.7z.0002 …  (created by the
# multivolumefile library). The "base" handed to multivolumefile is name.7z.
_VOLUME_RE = re.compile(r"^(?P<base>.+\.7z)\.\d{3,4}$", re.IGNORECASE)


def volume_base(path: str) -> str | None:
    """Return the multivolume base for a 7z volume file, else None."""
    m = _VOLUME_RE.match(os.path.basename(path))
    if not m:
        return None
    return re.sub(r"\.\d{3,4}$", "", path)


def _is_password_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "password" in name:
        return True
    msg = str(exc).lower()
    return any(k in msg for k in ("password", "cipher", "mac check", "wrong", "crc"))


class _CountingCallback(ExtractCallback):
    """Turns py7zr's per-file callbacks into (current, total) progress."""

    def __init__(self, total, report, is_cancelled):
        self._total = max(total, 1)
        self._report = report
        self._is_cancelled = is_cancelled
        self._done = 0

    def report_start_preparation(self):
        pass

    def report_start(self, processing_file_path, processing_bytes):
        if self._is_cancelled():
            raise OperationCancelled()
        self._done += 1
        self._report(min(self._done, self._total), self._total, processing_file_path)

    def report_update(self, decompressed_bytes):
        pass

    def report_end(self, processing_file_path, wrote_bytes):
        pass

    def report_postprocess(self):
        pass

    def report_warning(self, message):
        pass


class SevenZipHandler(ArchiveHandler):
    format = ArchiveFormat.SEVENZIP
    can_create = True
    can_add = True
    can_delete = True
    can_encrypt = True
    can_encrypt_names = True

    def __init__(self, path: str):
        super().__init__(path)
        # If opened via any volume file, read through the multivolume base.
        self._mv_base = volume_base(path)
        if self._mv_base:
            # Modifying a split set in place isn't supported; read-only.
            self.can_add = False
            self.can_delete = False

    @contextmanager
    def _reader(self, password=None):
        """Yield an open py7zr reader, transparently spanning volumes."""
        vol = None
        try:
            if self._mv_base:
                vol = multivolumefile.open(self._mv_base, mode="rb")
                archive = py7zr.SevenZipFile(vol, "r", password=password)
            else:
                archive = py7zr.SevenZipFile(self.path, "r", password=password)
        except Exception as exc:  # noqa: BLE001 — normalise to our taxonomy
            if vol is not None:
                vol.close()
            if _is_password_error(exc):
                if password:
                    raise WrongPassword("Incorrect password.") from exc
                raise PasswordRequired("This archive is encrypted — a password is required.") from exc
            raise
        try:
            yield archive
        finally:
            archive.close()
            if vol is not None:
                vol.close()

    def needs_password(self) -> bool:
        # A header-encrypted archive can't be opened for listing without a key.
        try:
            with self._reader(None) as a:
                return bool(a.needs_password())
        except PasswordRequired:
            return True

    # -- reading ---------------------------------------------------------
    def entries(self, password: str | None = None) -> list[ArchiveEntry]:
        with self._reader(password) as a:
            content_encrypted = bool(a.needs_password())
            infos = a.list()
        out: list[ArchiveEntry] = []
        for fi in infos:
            name = normalize_arcname(fi.filename)
            is_dir = bool(fi.is_directory)
            mtime = fi.creationtime
            if isinstance(mtime, datetime.datetime):
                mtime = mtime.replace(tzinfo=None)
            out.append(
                ArchiveEntry(
                    path=name + ("/" if is_dir and not name.endswith("/") else ""),
                    is_dir=is_dir,
                    size=fi.uncompressed or 0,
                    compressed_size=fi.compressed,  # often None: 7z is solid
                    modified=mtime if isinstance(mtime, datetime.datetime) else None,
                    crc=None if is_dir or fi.crc32 is None else f"{fi.crc32 & 0xFFFFFFFF:08X}",
                    encrypted=content_encrypted,
                )
            )
        return out

    def _file_count(self, password) -> int:
        with self._reader(password) as a:
            return sum(0 if fi.is_directory else 1 for fi in a.list())

    def extract(self, members, dest_dir, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        members = [normalize_arcname(m).rstrip("/") for m in members] if members else None
        total = len(members) if members else self._file_count(password)
        cb = _CountingCallback(total, report, is_cancelled)
        os.makedirs(dest_dir, exist_ok=True)
        try:
            with self._reader(password) as a:
                if members:
                    a.extract(path=dest_dir, targets=members, callback=cb)
                else:
                    a.extractall(path=dest_dir, callback=cb)
        except OperationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_password_error(exc):
                raise (WrongPassword("Incorrect password.") if password
                       else PasswordRequired("A password is required.")) from exc
            raise

    def test(self, password=None, report=None, is_cancelled=None) -> list[str]:
        report = report or _noop_progress
        report(0, 1, "Testing archive…")
        try:
            with self._reader(password) as a:
                # testzip() returns None when OK, else the first failing member.
                bad = a.testzip()
        except Exception as exc:  # noqa: BLE001
            if _is_password_error(exc):
                raise (WrongPassword("Incorrect password.") if password
                       else PasswordRequired("A password is required.")) from exc
            raise
        report(1, 1, "Done")
        return [] if bad is None else [bad]

    # -- modifying (extract → recreate: robust for solid & encrypted 7z) --
    def add(self, sources, level=CompressionLevel.NORMAL, password=None, report=None, is_cancelled=None):
        self._rewrite(add=list(sources), delete=set(), level=level, password=password,
                      report=report, is_cancelled=is_cancelled)

    def delete(self, members, password=None, report=None, is_cancelled=None):
        self._rewrite(add=[], delete={normalize_arcname(m).rstrip("/") for m in members},
                      level=CompressionLevel.NORMAL, password=password,
                      report=report, is_cancelled=is_cancelled)

    def _rewrite(self, add, delete, level, password, report, is_cancelled):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        header_enc = self.needs_password() if password else False
        staging = tempfile.mkdtemp(prefix="mc_7z_")
        tmp_archive = self.path + ".mc_tmp"
        try:
            report(0, 1, "Reading existing archive…")
            self.extract_all(staging, password=password, is_cancelled=is_cancelled)

            def is_deleted(rel: str) -> bool:
                rel = rel.replace(os.sep, "/")
                return any(rel == d or rel.startswith(d + "/") for d in delete)

            pairs: list[tuple[str, str]] = []
            for dp, _dirs, files in os.walk(staging):
                for fn in files:
                    fs = os.path.join(dp, fn)
                    rel = os.path.relpath(fs, staging).replace(os.sep, "/")
                    if not is_deleted(rel):
                        pairs.append((fs, rel))
            pairs.extend(add)

            SevenZipHandler.create(
                tmp_archive, pairs, level=level, password=password,
                encrypt_names=header_enc, report=report, is_cancelled=is_cancelled,
            )
            os.replace(tmp_archive, self.path)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if os.path.exists(tmp_archive):
                os.remove(tmp_archive)

    # -- creating --------------------------------------------------------
    @classmethod
    def create(cls, dest_path, sources, level=CompressionLevel.NORMAL, password=None,
               encrypt_names=False, volume_size=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        if level == CompressionLevel.STORE:
            filters = [{"id": py7zr.FILTER_COPY}]
        else:
            filters = [{"id": py7zr.FILTER_LZMA2, "preset": lzma_preset(level)}]
        kwargs = {"filters": filters}
        if password:
            kwargs["password"] = password
            kwargs["header_encryption"] = bool(encrypt_names)
        sources = list(sources)

        def _write(archive):
            total = len(sources)
            for i, (fs_path, arcname) in enumerate(sources, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, arcname)
                if arcname.endswith("/"):
                    if os.path.isdir(fs_path):
                        archive.write(fs_path, arcname.rstrip("/"))
                    continue
                archive.write(fs_path, arcname)

        if volume_size and volume_size > 0:
            # Split output: writes dest_path.0001, dest_path.0002, …
            with multivolumefile.open(dest_path, mode="wb", volume=int(volume_size)) as vol:
                with py7zr.SevenZipFile(vol, "w", **kwargs) as archive:
                    _write(archive)
        else:
            with py7zr.SevenZipFile(dest_path, "w", **kwargs) as archive:
                _write(archive)
