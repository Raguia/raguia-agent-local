#!/usr/bin/env python3
"""Prepare a release asset and write its SHA256 checksum file."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Source file path")
    parser.add_argument("--target", required=True, help="Target file path")
    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)

    if not source.exists():
        raise SystemExit(f"Expected file not found: {source}")

    source_resolved = source.resolve()
    target_resolved = target.resolve() if target.exists() else None
    if target_resolved != source_resolved:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    digest = _sha256(target)
    checksum_file = target.with_name(f"{target.name}.sha256")
    checksum_file.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
