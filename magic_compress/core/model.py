"""Format-agnostic data types shared across all archive handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ArchiveFormat(str, Enum):
    """Archive formats Magic Compress knows about.

    The value doubles as the canonical file extension (without a leading dot).
    """

    ZIP = "zip"
    SEVENZIP = "7z"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"
    RAR = "rar"

    @property
    def label(self) -> str:
        return {
            ArchiveFormat.ZIP: "ZIP",
            ArchiveFormat.SEVENZIP: "7-Zip",
            ArchiveFormat.TAR: "TAR",
            ArchiveFormat.TAR_GZ: "TAR + GZip",
            ArchiveFormat.TAR_BZ2: "TAR + BZip2",
            ArchiveFormat.TAR_XZ: "TAR + XZ",
            ArchiveFormat.RAR: "RAR",
        }[self]


# Formats the user may *create*. RAR is deliberately absent — creating .rar
# archives requires WinRAR's proprietary, non-redistributable encoder.
CREATABLE_FORMATS: tuple[ArchiveFormat, ...] = (
    ArchiveFormat.ZIP,
    ArchiveFormat.SEVENZIP,
    ArchiveFormat.TAR,
    ArchiveFormat.TAR_GZ,
    ArchiveFormat.TAR_BZ2,
    ArchiveFormat.TAR_XZ,
)


# ---------------------------------------------------------------------------
# Compression levels
#
# Every format has its own numeric scale, so we expose one friendly 6-step
# scale to the UI and translate it per codec at write time.
# ---------------------------------------------------------------------------

class CompressionLevel(int, Enum):
    STORE = 0
    FASTEST = 1
    FAST = 2
    NORMAL = 3
    GOOD = 4
    MAXIMUM = 5

    @property
    def label(self) -> str:
        return {
            0: "Store (no compression)",
            1: "Fastest",
            2: "Fast",
            3: "Normal",
            4: "Good",
            5: "Maximum",
        }[self.value]


# Map the friendly level onto each codec's own numeric range.
_DEFLATE = {0: 0, 1: 1, 2: 3, 3: 6, 4: 8, 5: 9}   # zip / gzip: 0..9
_LZMA = {0: 0, 1: 1, 2: 3, 3: 6, 4: 7, 5: 9}       # 7z / xz preset: 0..9
_BZIP2 = {0: 1, 1: 1, 2: 3, 3: 6, 4: 8, 5: 9}      # bzip2: 1..9 (no store)


def deflate_level(level: CompressionLevel) -> int:
    return _DEFLATE[int(level)]


def lzma_preset(level: CompressionLevel) -> int:
    return _LZMA[int(level)]


def bzip2_level(level: CompressionLevel) -> int:
    return _BZIP2[int(level)]


@dataclass(slots=True)
class ArchiveEntry:
    """A single member inside an archive.

    Paths are always stored POSIX-style ("/" separators, no leading slash) so
    the UI can build a uniform tree regardless of the source format.
    """

    path: str
    is_dir: bool
    size: int = 0                       # uncompressed bytes
    compressed_size: int | None = None  # packed bytes, if the format reports it
    modified: datetime | None = None
    crc: str | None = None
    encrypted: bool = False

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def parent(self) -> str:
        stripped = self.path.rstrip("/")
        return stripped.rsplit("/", 1)[0] if "/" in stripped else ""

    @property
    def ratio(self) -> float | None:
        """Fraction saved (0.0–1.0), or None when it can't be computed."""
        if self.is_dir or not self.size or self.compressed_size is None:
            return None
        if self.compressed_size > self.size:
            return 0.0
        return 1.0 - (self.compressed_size / self.size)
