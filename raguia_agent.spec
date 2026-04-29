# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — Raguia Local Agent
#
# Usage (depuis raguia_local_agent/) :
#   pip install pyinstaller pillow pystray watchdog httpx pyyaml keyring
#   pip install -e ".[full]"
#   pyinstaller raguia_agent.spec
#
# Windows  → dist/raguia-agent.exe   (single-file, pas de console)
# macOS    → dist/raguia-agent.app   (bundle .app, LSUIElement=True = invisible dans le Dock)
# Linux    → dist/raguia-agent       (single-file)

import sys
import re
from pathlib import Path

block_cipher = None
AGENT_ROOT = Path(SPECPATH)  # répertoire du spec = raguia_local_agent/
ENTRY_POINT = str(AGENT_ROOT / "raguia_local_agent" / "__main__.py")

# Lire la version depuis pyproject.toml sans dépendance externe
_pyproject_text = (AGENT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
_ver_match = re.search(r'^version\s*=\s*"([^"]+)"', _pyproject_text, re.MULTILINE)
_VERSION = _ver_match.group(1) if _ver_match else "0.0.0"

is_win = sys.platform == "win32"
is_mac = sys.platform == "darwin"

a = Analysis(
    [ENTRY_POINT],
    pathex=[str(AGENT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pystray backends
        "pystray._darwin",
        "pystray._win32",
        "pystray._xorg",
        # Pillow
        "PIL._tkinter_finder",
        "PIL.Image",
        "PIL.ImageDraw",
        # keyring backends (détection dynamique au runtime)
        "keyring.backends",
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.fail",
        "keyring.backends.null",
        # watchdog observers (sélectionné au runtime selon l'OS)
        "watchdog.observers.fsevents",
        "watchdog.observers.winapi",
        "watchdog.observers.inotify",
        "watchdog.observers.kqueue",
        # tkinter (wizard + dialogs)
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.simpledialog",
        # divers
        "yaml",
        "_yaml",
        "zipfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tests", "test", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_exe_common = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

if is_win:
    # ------------------------------------------------------------------ Windows
    # Single-file .exe : tout embarqué, pas de console (--windowed)
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="raguia-agent",
        **_exe_common,
    )

elif is_mac:
    # ------------------------------------------------------------------ macOS
    # Bundle .app (windowed) : LSUIElement masque l'app du Dock et du switcher
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="raguia-agent",
        **_exe_common,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="raguia-agent",
    )
    app = BUNDLE(
        coll,
        name="raguia-agent.app",
        icon=None,
        bundle_identifier="fr.valentin-fiess.raguia.agent",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSAppleScriptEnabled": False,
            "CFBundleShortVersionString": _VERSION,
            "CFBundleVersion": _VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )

else:
    # ------------------------------------------------------------------ Linux
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="raguia-agent",
        **_exe_common,
    )
