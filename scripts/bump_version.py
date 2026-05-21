#!/usr/bin/env python3
"""Incremente ``project.version`` dans pyproject.toml (semver patch).

Usage :
  python scripts/bump_version.py [ROOT]              # bump la version actuelle du fichier
  python scripts/bump_version.py [ROOT] --base X.Y.Z # impose la base (sync public apres rsync)

Utilisé par la CI qui synchronise vers raguia-agent-local : la base est la derniere
version publiée sur le depot public, pas celle du monorepo.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _parse_semver_parts(v: str) -> tuple[int, int, int]:
    """Comparaison simple pour choisir la plus grande version affichée (x.y.z)."""
    base = v.strip().split("+")[0].split("-")[0]
    nums: list[int] = []
    for seg in base.split("."):
        if seg.isdigit():
            nums.append(int(seg))
        else:
            break
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def max_semver(a: str, b: str) -> str:
    """Retourne la version ``a`` ou ``b`` la plus elevee."""
    pa, pb = _parse_semver_parts(a), _parse_semver_parts(b)
    return a if pa >= pb else b


def bump_patch(version: str) -> str:
    """Incremente le dernier segment numerique (0.2.0 -> 0.2.1, 0.2 -> 0.2.1)."""
    base = version.strip().split("+")[0].split("-")[0]
    nums: list[int] = []
    for seg in base.split("."):
        if seg.isdigit():
            nums.append(int(seg))
        else:
            break
    if len(nums) >= 3:
        nums[-1] += 1
        return ".".join(str(x) for x in nums[:3])
    if len(nums) == 2:
        return f"{nums[0]}.{nums[1]}.1"
    if len(nums) == 1:
        return f"{nums[0]}.0.1"
    return "0.0.1"


def extract_version(text: str) -> str | None:
    m = re.search(r"(?m)^version\s*=\s*\"([^\"]+)\"", text)
    return m.group(1) if m else None


def replace_version(text: str, new_ver: str) -> str:
    out, n = re.subn(
        r"(?m)^(version\s*=\s*)\"([^\"]+)\"",
        lambda m: f'{m.group(1)}"{new_ver}"',
        text,
        count=1,
    )
    if n != 1:
        raise ValueError(
            "Impossible de remplacer la ligne version = dans pyproject.toml"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump patch version in pyproject.toml")
    ap.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Repertoire contenant pyproject.toml (defaut : .)",
    )
    ap.add_argument(
        "--base",
        metavar="VERSION",
        default=None,
        help="Version de depart (sinon lue dans pyproject.toml)",
    )
    ap.add_argument(
        "--pub-before",
        metavar="VERSION",
        default=None,
        dest="pub_before",
        help="CI : version sur le depot public avant rsync ; fusionnee avec la version du pyproject apres rsync.",
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        print(f"Fichier introuvable : {pyproject}", file=sys.stderr)
        return 1

    text = pyproject.read_text(encoding="utf-8")
    cur_in_file = extract_version(text) or "0.0.0"
    if (args.pub_before or "").strip():
        merged = max_semver((args.pub_before or "").strip(), cur_in_file)
        base = merged
    else:
        base = (args.base or "").strip() or cur_in_file
    if not base:
        print(
            "Pas de version de base et pas de version dans le fichier.", file=sys.stderr
        )
        return 1
    new_ver = bump_patch(base)
    pyproject.write_text(replace_version(text, new_ver), encoding="utf-8")
    print(f"{base} -> {new_ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
