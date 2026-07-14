#!/usr/bin/env python
"""Build and (optionally) publish a Magic Compress release.

Usage (run from the project root):

    python scripts/release.py            # build exe + installer + setup zip
    python scripts/release.py --release  # ...and publish a GitHub release

Pipeline:
  1. PyInstaller  ->  dist/MagicCompress.exe
  2. Inno Setup   ->  dist/MagicCompress-Setup.exe
  3. zip          ->  dist/MagicCompress-Setup.zip
  4. --release    ->  gh release for v<version> with the zip attached

The setup zip is DELIBERATELY named without a version. GitHub's
`releases/latest/download/MagicCompress-Setup.zip` link only stays stable if the
asset keeps this exact name — so do not add a version to it.

Requirements: pyinstaller, Inno Setup (ISCC.exe), and — for --release — the
GitHub CLI `gh` authenticated with repo scope.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
SETUP_EXE = os.path.join(DIST, "MagicCompress-Setup.exe")
SETUP_ZIP = os.path.join(DIST, "MagicCompress-Setup.zip")  # version-less on purpose
REPO = "Bayoumi68/Magic_Compress"
LATEST_LINK = f"https://github.com/{REPO}/releases/latest/download/MagicCompress-Setup.zip"

_ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def app_version() -> str:
    sys.path.insert(0, ROOT)
    from magic_compress import __version__
    return __version__


def run(cmd: list[str]) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def find_iscc() -> str:
    for candidate in _ISCC_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    sys.exit("ISCC.exe (Inno Setup 6) not found — install it or add ISCC to PATH.")


def build_exe() -> None:
    run([sys.executable, "-m", "PyInstaller", "MagicCompress.spec", "--noconfirm", "--clean"])


def build_installer(version: str) -> None:
    # Stamp the installer with the code version (single source of truth).
    run([find_iscc(), f"/DAppVersion={version}", os.path.join("installer", "MagicCompress.iss")])
    if not os.path.exists(SETUP_EXE):
        sys.exit(f"Installer build did not produce {SETUP_EXE}")


def build_zip() -> None:
    if os.path.exists(SETUP_ZIP):
        os.remove(SETUP_ZIP)
    with zipfile.ZipFile(SETUP_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SETUP_EXE, "MagicCompress-Setup.exe")
        zf.write(os.path.join(ROOT, "README.md"), "README.md")
    print(f"wrote {SETUP_ZIP} ({os.path.getsize(SETUP_ZIP):,} bytes)", flush=True)


def _release_exists(tag: str) -> bool:
    result = subprocess.run(["gh", "release", "view", tag], cwd=ROOT,
                            capture_output=True, text=True)
    return result.returncode == 0


def cut_release(version: str) -> None:
    tag = f"v{version}"
    if _release_exists(tag):
        print(f"release {tag} exists — replacing its setup zip", flush=True)
        run(["gh", "release", "upload", tag, SETUP_ZIP, "--clobber"])
    else:
        notes = (
            "Magic Compress installer.\n\n"
            "Download **MagicCompress-Setup.zip** below, unzip it, and run "
            "`MagicCompress-Setup.exe`.\n\n"
            f"Permanent link (always the newest release):\n{LATEST_LINK}"
        )
        run(["gh", "release", "create", tag, SETUP_ZIP,
             "--title", f"Magic Compress {version}", "--notes", notes])
    print(f"Download link: {LATEST_LINK}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and optionally publish a release.")
    parser.add_argument("--release", action="store_true",
                        help="publish a GitHub release for the current version")
    args = parser.parse_args()

    version = app_version()
    print(f"=== Magic Compress {version} ===", flush=True)
    build_exe()
    build_installer(version)
    build_zip()
    if args.release:
        cut_release(version)
    else:
        print(f"Built. Re-run with --release to publish, or attach {SETUP_ZIP} to a release.",
              flush=True)


if __name__ == "__main__":
    main()
