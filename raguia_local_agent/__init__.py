"""Agent de synchronisation locale RAGUIA → API portail Raguia."""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    """Canonique : ``project.version`` dans pyproject.toml à la racine du paquet."""
    try:
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        try:
            return pkg_version("raguia-local-agent")
        except PackageNotFoundError:
            pass
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
