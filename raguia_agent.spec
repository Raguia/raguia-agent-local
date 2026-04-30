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

import re
import os
import sys
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
ADMIN_SWITCH_FILENAME = os.environ.get("RAGUIA_ADMIN_SWITCH_FILENAME", ".raguia-admin.json").strip()
if not ADMIN_SWITCH_FILENAME or "/" in ADMIN_SWITCH_FILENAME or "\\" in ADMIN_SWITCH_FILENAME:
    ADMIN_SWITCH_FILENAME = ".raguia-admin.json"

# Assets additionnels a embarquer (format Analysis.datas: (src, dest_dir))
extra_datas = []
ASSETS_DIR = AGENT_ROOT / "assets"
icons_dir = ASSETS_DIR / "icons"
if ASSETS_DIR.exists():
    for src, dest in [
        (ASSETS_DIR / "logo_agent-local.png", "assets"),
        (icons_dir / "raguia-agent.png", "assets/icons"),
        (icons_dir / "raguia-agent.ico", "assets/icons"),
        (icons_dir / "raguia-agent.icns", "assets/icons"),
        # Optionnel: active le switch cache PROD/DEV dans le tray.
        (ASSETS_DIR / ADMIN_SWITCH_FILENAME, "assets"),
        # Optionnel: permet d'indiquer le nom du fichier admin a runtime.
        (ASSETS_DIR / ".raguia-admin-name.txt", "assets"),
    ]:
        if src.exists():
            extra_datas.append((str(src), dest))

a = Analysis(
    [ENTRY_POINT],
    pathex=[str(AGENT_ROOT)],
    binaries=[],
    datas=extra_datas,
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
        # SSL / certifi — bundle CA pour HTTPS (important en mode one-file Windows)
        "certifi",
        # divers
        "yaml",
        "_yaml",
        "zipfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["rth_certifi.py"],
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

# Collect icon files for each platform (png source copied to .ico/.icns during CI or manually)
windows_icon = str(icons_dir / "raguia-agent.ico")
mac_icon = str(icons_dir / "raguia-agent.icns")
linux_icon = str(icons_dir / "raguia-agent.png")

if is_win:
    # ------------------------------------------------------------------ Windows
    # Single-file .exe : tout embarqué, pas de console (--windowed)
    # Use the .ico file if available
    if (AGENT_ROOT / "assets" / "icons" / "raguia-agent.ico").exists():
        _exe_common["icon"] = str(AGENT_ROOT / "assets" / "icons" / "raguia-agent.ico")
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
    # Use the .icns file if available
    if (AGENT_ROOT / "assets" / "icons" / "raguia-agent.icns").exists():
        _exe_common["icon"] = str(AGENT_ROOT / "assets" / "icons" / "raguia-agent.icns")
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
        icon=_exe_common.get("icon"),
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
    # For Linux we'll keep the PNG next to the binary and optionally set icon
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="raguia-agent",
        **_exe_common,
    )
