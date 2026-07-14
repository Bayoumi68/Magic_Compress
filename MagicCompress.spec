# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Magic Compress.

Build a standalone Windows executable:

    pip install pyinstaller
    pyinstaller MagicCompress.spec

The result is dist/MagicCompress.exe (one self-contained file, no Python
install required on the target machine).
"""

from PyInstaller.utils.hooks import collect_submodules

# The 7z/zip codec stack pulls in several compiled helper modules that are
# imported lazily, so make sure PyInstaller sees all of them.
hiddenimports = []
for mod in ("py7zr", "multivolumefile", "pyzipper", "rarfile",
            "inflate64", "pybcj", "pyppmd", "brotli", "Cryptodome"):
    hiddenimports += collect_submodules(mod)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/icon.png", "assets"),
        ("assets/logo.png", "assets"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt modules we never use — dropping them keeps the binary lean.
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtBluetooth",
        "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# One-DIR build (not one-file): MagicCompress.exe is the actual app process that
# owns the window, so Windows' Restart Manager (used by the installer) can close
# it gracefully during upgrades. A one-file build hides the window in a child
# process, which the Restart Manager cannot close.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MagicCompress",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # GUI app — no console window
    disable_windowed_traceback=False,
    icon="assets/MagicCompress.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MagicCompress",
)
