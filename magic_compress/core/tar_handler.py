"""TAR family handler (.tar/.tar.gz/.tar.bz2/.tar.xz) via the stdlib tarfile."""

from __future__ import annotations

import datetime
import os
import shutil
import tarfile
import tempfile

from .base import (
    ArchiveHandler,
    OperationCancelled,
    _never_cancelled,
    _noop_progress,
    normalize_arcname,
    safe_target,
)
from .model import (
    ArchiveEntry,
    ArchiveFormat,
    CompressionLevel,
    bzip2_level,
    deflate_level,
    lzma_preset,
)

# format -> (write mode, kwargs builder)
_WRITE_MODE = {
    ArchiveFormat.TAR: "w",
    ArchiveFormat.TAR_GZ: "w:gz",
    ArchiveFormat.TAR_BZ2: "w:bz2",
    ArchiveFormat.TAR_XZ: "w:xz",
}


def _write_kwargs(fmt: ArchiveFormat, level: CompressionLevel) -> dict:
    if fmt == ArchiveFormat.TAR_GZ:
        return {"compresslevel": max(deflate_level(level), 1)}
    if fmt == ArchiveFormat.TAR_BZ2:
        return {"compresslevel": bzip2_level(level)}
    if fmt == ArchiveFormat.TAR_XZ:
        return {"preset": lzma_preset(level)}
    return {}


class TarHandler(ArchiveHandler):
    format = ArchiveFormat.TAR  # generic; the concrete flavour is detected on read
    can_create = True
    can_add = True
    can_delete = True
    can_encrypt = False

    # -- reading ---------------------------------------------------------
    def entries(self, password: str | None = None) -> list[ArchiveEntry]:
        out: list[ArchiveEntry] = []
        with tarfile.open(self.path, "r:*") as tf:
            for m in tf.getmembers():
                is_dir = m.isdir()
                name = normalize_arcname(m.name)
                try:
                    dt: datetime.datetime | None = datetime.datetime.fromtimestamp(m.mtime)
                except (OverflowError, OSError, ValueError):
                    dt = None
                out.append(
                    ArchiveEntry(
                        path=name + ("/" if is_dir and not name.endswith("/") else ""),
                        is_dir=is_dir,
                        size=m.size,
                        compressed_size=None,  # tar stores no per-member packed size
                        modified=dt,
                        crc=None,
                        encrypted=False,
                    )
                )
        return out

    def extract(self, members, dest_dir, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        os.makedirs(dest_dir, exist_ok=True)
        wanted = {normalize_arcname(m).rstrip("/") for m in members} if members else None
        with tarfile.open(self.path, "r:*") as tf:
            all_members = tf.getmembers()
            if wanted is not None:
                selected = [m for m in all_members if normalize_arcname(m.name).rstrip("/") in wanted]
            else:
                selected = all_members
            total = len(selected)
            for i, m in enumerate(selected, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, m.name)
                target = safe_target(dest_dir, m.name)  # guards against tar-slip
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                elif m.isfile():
                    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                    src = tf.extractfile(m)
                    if src is None:
                        continue
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, length=1 << 20)
                # Links / devices / fifos are skipped for safety.

    def test(self, password=None, report=None, is_cancelled=None) -> list[str]:
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        bad: list[str] = []
        with tarfile.open(self.path, "r:*") as tf:
            files = [m for m in tf.getmembers() if m.isfile()]
            total = len(files)
            for i, m in enumerate(files, 1):
                if is_cancelled():
                    raise OperationCancelled()
                report(i, total, m.name)
                try:
                    src = tf.extractfile(m)
                    if src is None:
                        continue
                    with src:
                        while src.read(1 << 20):
                            pass
                except (tarfile.TarError, OSError):
                    bad.append(m.name)
        return bad

    # -- modifying -------------------------------------------------------
    def add(self, sources, level=CompressionLevel.NORMAL, password=None, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        fmt = self._detect_write_format()
        sources = list(sources)
        if fmt == ArchiveFormat.TAR:
            # Uncompressed tar supports true append.
            with tarfile.open(self.path, "a") as tf:
                self._add_members(tf, sources, report, is_cancelled)
        else:
            # Compressed tars can't be appended; recreate from a staging copy.
            self._rewrite(add=sources, delete=set(), fmt=fmt, level=level,
                          report=report, is_cancelled=is_cancelled)

    def delete(self, members, password=None, report=None, is_cancelled=None):
        deleted = {normalize_arcname(m).rstrip("/") for m in members}
        self._rewrite(add=[], delete=deleted, fmt=self._detect_write_format(),
                      level=CompressionLevel.NORMAL, report=report, is_cancelled=is_cancelled)

    def _detect_write_format(self) -> ArchiveFormat:
        low = self.path.lower()
        if low.endswith((".tar.gz", ".tgz")):
            return ArchiveFormat.TAR_GZ
        if low.endswith((".tar.bz2", ".tbz2", ".tbz")):
            return ArchiveFormat.TAR_BZ2
        if low.endswith((".tar.xz", ".txz")):
            return ArchiveFormat.TAR_XZ
        return ArchiveFormat.TAR

    @staticmethod
    def _add_members(tf, sources, report, is_cancelled):
        total = len(sources)
        for i, (fs_path, arcname) in enumerate(sources, 1):
            if is_cancelled():
                raise OperationCancelled()
            report(i, total, arcname)
            if arcname.endswith("/"):
                ti = tarfile.TarInfo(arcname.rstrip("/"))
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tf.addfile(ti)
            else:
                tf.add(fs_path, arcname=arcname, recursive=False)

    def _rewrite(self, add, delete, fmt, level, report, is_cancelled):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        staging = tempfile.mkdtemp(prefix="mc_tar_")
        tmp_archive = self.path + ".mc_tmp"
        try:
            report(0, 1, "Reading existing archive…")
            self.extract_all(staging, is_cancelled=is_cancelled)

            def is_deleted(rel: str) -> bool:
                rel = rel.replace(os.sep, "/")
                return any(rel == d or rel.startswith(d + "/") for d in delete)

            pairs = []
            for dp, _dirs, files in os.walk(staging):
                for fn in files:
                    fs = os.path.join(dp, fn)
                    rel = os.path.relpath(fs, staging).replace(os.sep, "/")
                    if not is_deleted(rel):
                        pairs.append((fs, rel))
            pairs.extend(add)
            TarHandler.create_tar(tmp_archive, pairs, fmt, level=level,
                                  report=report, is_cancelled=is_cancelled)
            os.replace(tmp_archive, self.path)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if os.path.exists(tmp_archive):
                os.remove(tmp_archive)

    # -- creating --------------------------------------------------------
    @classmethod
    def create(cls, dest_path, sources, level=CompressionLevel.NORMAL, password=None,
               encrypt_names=False, report=None, is_cancelled=None):
        # Default create() assumes plain .tar; the registry calls create_tar with
        # the concrete flavour for gz/bz2/xz.
        cls.create_tar(dest_path, sources, ArchiveFormat.TAR, level=level,
                       report=report, is_cancelled=is_cancelled)

    @classmethod
    def create_tar(cls, dest_path, sources, fmt: ArchiveFormat,
                   level=CompressionLevel.NORMAL, report=None, is_cancelled=None):
        report = report or _noop_progress
        is_cancelled = is_cancelled or _never_cancelled
        mode = _WRITE_MODE[fmt]
        kwargs = _write_kwargs(fmt, level)
        sources = list(sources)
        with tarfile.open(dest_path, mode, **kwargs) as tf:
            cls._add_members(tf, sources, report, is_cancelled)
