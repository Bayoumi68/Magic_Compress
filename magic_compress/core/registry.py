"""Format detection and handler/creation dispatch."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile

from .base import ArchiveError, ArchiveHandler
from .model import ArchiveFormat, CompressionLevel
from .rar_handler import RarHandler
from .sevenzip_handler import SevenZipHandler, volume_base
from .tar_handler import TarHandler
from .zip_handler import ZipHandler

# Magic-number signatures, longest/most-specific first.
_MAGIC: list[tuple[bytes, ArchiveFormat]] = [
    (b"7z\xbc\xaf\x27\x1c", ArchiveFormat.SEVENZIP),
    (b"Rar!\x1a\x07\x00", ArchiveFormat.RAR),        # RAR 1.5–4.x
    (b"Rar!\x1a\x07\x01\x00", ArchiveFormat.RAR),    # RAR 5.x
    (b"PK\x03\x04", ArchiveFormat.ZIP),
    (b"PK\x05\x06", ArchiveFormat.ZIP),              # empty zip
    (b"PK\x07\x08", ArchiveFormat.ZIP),              # spanned zip
]

# Extension fallback when magic is ambiguous (e.g. plain tar has no header magic).
_EXT: list[tuple[tuple[str, ...], ArchiveFormat]] = [
    ((".tar.gz", ".tgz"), ArchiveFormat.TAR_GZ),
    ((".tar.bz2", ".tbz2", ".tbz"), ArchiveFormat.TAR_BZ2),
    ((".tar.xz", ".txz"), ArchiveFormat.TAR_XZ),
    ((".tar",), ArchiveFormat.TAR),
    ((".7z",), ArchiveFormat.SEVENZIP),
    ((".rar",), ArchiveFormat.RAR),
    ((".zip", ".zipx"), ArchiveFormat.ZIP),
]

_HANDLERS: dict[ArchiveFormat, type[ArchiveHandler]] = {
    ArchiveFormat.ZIP: ZipHandler,
    ArchiveFormat.SEVENZIP: SevenZipHandler,
    ArchiveFormat.RAR: RarHandler,
    ArchiveFormat.TAR: TarHandler,
    ArchiveFormat.TAR_GZ: TarHandler,
    ArchiveFormat.TAR_BZ2: TarHandler,
    ArchiveFormat.TAR_XZ: TarHandler,
}


def detect_format(path: str) -> ArchiveFormat | None:
    """Best-effort format detection: magic bytes first, extension second."""
    # A 7z volume (name.7z.0002, …) — second and later parts have no 7z magic.
    if volume_base(path) is not None:
        return ArchiveFormat.SEVENZIP

    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        head = b""

    for sig, fmt in _MAGIC:
        if head.startswith(sig):
            # gzip/bz2/xz magic could front a compressed *tar* — but those carry
            # their own magic only via the compressor, so we resolve them below.
            return fmt

    # Compressed-tar magic (gzip/bz2/xz) — confirm it's really a tar.
    low = path.lower()
    for exts, fmt in _EXT:
        if low.endswith(exts):
            if fmt in (ArchiveFormat.TAR, ArchiveFormat.TAR_GZ, ArchiveFormat.TAR_BZ2, ArchiveFormat.TAR_XZ):
                if _is_tar(path):
                    return fmt
                continue
            return fmt

    # Last resort: probe with tarfile (handles odd extensions / no extension).
    if _is_tar(path):
        return ArchiveFormat.TAR
    return None


def _is_tar(path: str) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except (OSError, tarfile.TarError):
        return False


def open_archive(path: str) -> ArchiveHandler:
    """Return a handler for *path*, or raise ArchiveError if unrecognised."""
    if not os.path.isfile(path):
        raise ArchiveError(f"Not a file: {path}")
    fmt = detect_format(path)
    if fmt is None:
        raise ArchiveError(f"Unrecognised or unsupported archive format: {os.path.basename(path)}")
    return _HANDLERS[fmt](path)


def handler_for_format(fmt: ArchiveFormat, path: str) -> ArchiveHandler:
    return _HANDLERS[fmt](path)


def create_archive(
    fmt: ArchiveFormat,
    dest_path: str,
    sources,
    level: CompressionLevel = CompressionLevel.NORMAL,
    password: str | None = None,
    encrypt_names: bool = False,
    volume_size: int | None = None,
    report=None,
    is_cancelled=None,
) -> None:
    """Create a new archive of *fmt* from (fs_path, arcname) *sources*.

    *volume_size* (bytes) splits the output into multiple volumes; only 7z
    supports it and it is ignored for other formats.
    """
    if fmt == ArchiveFormat.ZIP:
        ZipHandler.create(dest_path, sources, level=level, password=password,
                          encrypt_names=encrypt_names, report=report, is_cancelled=is_cancelled)
    elif fmt == ArchiveFormat.SEVENZIP:
        SevenZipHandler.create(dest_path, sources, level=level, password=password,
                               encrypt_names=encrypt_names, volume_size=volume_size,
                               report=report, is_cancelled=is_cancelled)
    elif fmt in (ArchiveFormat.TAR, ArchiveFormat.TAR_GZ, ArchiveFormat.TAR_BZ2, ArchiveFormat.TAR_XZ):
        TarHandler.create_tar(dest_path, sources, fmt, level=level,
                              report=report, is_cancelled=is_cancelled)
    else:
        raise ArchiveError(f"Cannot create {fmt.label} archives.")


def repackage(
    src_path: str,
    dest_fmt: ArchiveFormat,
    dest_path: str,
    *,
    level: CompressionLevel = CompressionLevel.NORMAL,
    dest_password: str | None = None,
    encrypt_names: bool = False,
    volume_size: int | None = None,
    src_password: str | None = None,
    report=None,
    is_cancelled=None,
) -> None:
    """Convert an existing archive to *dest_fmt*: extract it, then recreate.

    Works across every readable format, so e.g. a RAR can be repackaged as a
    ZIP or 7z. The staging directory is always cleaned up.
    """
    handler = open_archive(src_path)
    staging = tempfile.mkdtemp(prefix="mc_conv_")
    try:
        if report:
            report(0, 1, "Extracting source archive…")
        handler.extract_all(staging, password=src_password, report=report, is_cancelled=is_cancelled)

        pairs: list[tuple[str, str]] = []
        for dirpath, dirnames, filenames in os.walk(staging):
            for fn in filenames:
                fs = os.path.join(dirpath, fn)
                rel = os.path.relpath(fs, staging).replace(os.sep, "/")
                pairs.append((fs, rel))
            if not filenames and not dirnames:  # preserve empty directories
                rel = os.path.relpath(dirpath, staging).replace(os.sep, "/")
                if rel != ".":
                    pairs.append((dirpath, rel + "/"))

        create_archive(dest_fmt, dest_path, pairs, level=level, password=dest_password,
                       encrypt_names=encrypt_names, volume_size=volume_size,
                       report=report, is_cancelled=is_cancelled)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def default_extension(fmt: ArchiveFormat | str) -> str:
    # Qt stores str-subclass enums as plain strings, so accept either form.
    if not isinstance(fmt, ArchiveFormat):
        fmt = ArchiveFormat(fmt)
    return "." + fmt.value
