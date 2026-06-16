#!/usr/bin/env python3
"""Synchronise project.version depuis RELEASE_TAG (vX.Y.Z -> X.Y.Z)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _normalize_tag(tag: str) -> str:
    raw = (tag or "").strip()
    if raw.startswith("refs/tags/"):
        raw = raw[len("refs/tags/") :]
    if raw.startswith("v"):
        raw = raw[1:]
    return raw


def main() -> int:
    tag = (os.environ.get("RELEASE_TAG") or "").strip()
    if not tag:
        print("RELEASE_TAG vide: aucune mise a jour de version.")
        return 0

    version = _normalize_tag(tag)
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(
            f"Tag release invalide: {tag!r} (attendu: vX.Y.Z ou X.Y.Z)",
            file=sys.stderr,
        )
        return 1

    pyproject = Path("pyproject.toml")
    if not pyproject.is_file():
        print("pyproject.toml introuvable", file=sys.stderr)
        return 1

    text = pyproject.read_text(encoding="utf-8")
    out, n = re.subn(
        r'(?m)^(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{version}"',
        text,
        count=1,
    )
    if n != 1:
        print(
            "Impossible de mettre a jour project.version dans pyproject.toml",
            file=sys.stderr,
        )
        return 1

    pyproject.write_text(out, encoding="utf-8")
    print(f"Version synchronisee depuis tag: {tag} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
