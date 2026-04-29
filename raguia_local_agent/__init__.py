"""Agent de synchronisation locale RAGUIA → API portail Raguia."""

from __future__ import annotations

import os
import sys
import plistlib
from pathlib import Path


def _read_version() -> str:
    """Canonique : ``project.version`` dans pyproject.toml à la racine du paquet."""
    env_version = (os.environ.get("RAGUIA_AGENT_VERSION") or "").strip()
    if env_version:
        return env_version

    try:
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        try:
            return pkg_version("raguia-local-agent")
        except PackageNotFoundError:
            pass
    except Exception:
        pass

    # Mode binaire (PyInstaller) : essayer les metadonnees du bundle macOS.
    try:
        if getattr(sys, "frozen", False) and sys.platform == "darwin":
            exe = Path(sys.executable).resolve()
            info_plist = exe.parent.parent / "Info.plist"
            if info_plist.is_file():
                with info_plist.open("rb") as f:
                    data = plistlib.load(f) or {}
                v = str(data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or "").strip()
                if v:
                    return v
    except Exception:
        pass

    try:
        import tomllib

        pp = Path(__file__).resolve().parents[1] / "pyproject.toml"
        if pp.is_file():
            with pp.open("rb") as f:
                data = tomllib.load(f)
            v = (data.get("project") or {}).get("version")
            if v:
                return str(v).strip()
    except Exception:
        pass
    return "0.0.0"


__version__ = _read_version()
