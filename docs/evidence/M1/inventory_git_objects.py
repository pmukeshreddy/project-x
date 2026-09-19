"""Inventory a Git object directory as ordinary files without invoking Git."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("git_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = (args.git_dir / "objects").resolve(strict=True)
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        body = path.read_bytes()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    payload = {
        "object_file_count": len(records),
        "object_file_bytes": sum(item["size"] for item in records),
        "inventory_sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "objects": records,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
