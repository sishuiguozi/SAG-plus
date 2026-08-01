from __future__ import annotations

import argparse
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import lancedb

from sag_maintenance_guard import maintenance_guard


def _dir_stats(path: Path) -> dict[str, int]:
    total_bytes = 0
    total_files = 0
    for base, _, names in os.walk(path):
        for name in names:
            total_files += 1
            try:
                total_bytes += (Path(base) / name).stat().st_size
            except OSError:
                pass
    return {"files": total_files, "bytes": total_bytes}


def _table_snapshot(db: Any, root: Path, table_name: str) -> dict[str, Any]:
    table = db.open_table(table_name)
    table_dir = root / f"{table_name}.lance"
    return {
        "rows": int(table.count_rows()),
        "version": table.version,
        "stats": table.stats(),
        "dir": _dir_stats(table_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely remove old LanceDB table versions after an external backup."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Engine data dir that contains the lancedb directory, e.g. E:\\sag\\.data\\engine",
    )
    parser.add_argument("--table", required=True, help="LanceDB table name")
    parser.add_argument(
        "--backup-root",
        required=True,
        help="Backup/report directory where cleanup_<table>.json will be written",
    )
    parser.add_argument(
        "--older-than-seconds",
        type=int,
        default=0,
        help="Age threshold for old versions. Default cleans all old versions.",
    )
    parser.add_argument(
        "--delete-unverified",
        action="store_true",
        help="Allow cleanup of unverified old versions. Use only after backup validation.",
    )
    parser.add_argument("--metadata-db", help="SAG metadata sqlite database path for maintenance lock")
    parser.add_argument("--lease-name", default="lancedb-maintenance")
    parser.add_argument("--lease-ttl-seconds", type=int, default=7200)
    parser.add_argument(
        "--allow-running-queue",
        action="store_true",
        help="Allow maintenance even if vector_write_jobs has running rows. Use only for verified stale state.",
    )
    parser.add_argument(
        "--check-runtime-processes",
        action="store_true",
        help="Refuse maintenance if a SAG API/runtime process appears active.",
    )
    parser.add_argument(
        "--min-free-gb",
        type=float,
        default=30.0,
        help="Refuse maintenance if the data drive has less free space than this.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    lancedb_root = data_dir / "lancedb"
    backup_root = Path(args.backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)

    metadata_db = Path(args.metadata_db) if args.metadata_db else None
    with maintenance_guard(
        metadata_db,
        lease_name=args.lease_name,
        purpose=f"cleanup_old_versions:{args.table}",
        ttl_seconds=args.lease_ttl_seconds,
        allow_running_queue=args.allow_running_queue,
        require_runtime_stopped=args.check_runtime_processes,
        min_free_bytes=int(args.min_free_gb * 1024**3),
        free_space_path=data_dir,
        writable_paths=[backup_root],
        metadata={"table": args.table, "data_dir": str(data_dir)},
    ):
        db = lancedb.connect(str(lancedb_root))
        before = _table_snapshot(db, lancedb_root, args.table)

        start = time.time()
        table = db.open_table(args.table)
        cleanup_stats = table.cleanup_old_versions(
            older_than=timedelta(seconds=args.older_than_seconds),
            delete_unverified=args.delete_unverified,
        )
        elapsed = time.time() - start

        after = _table_snapshot(db, lancedb_root, args.table)
        ok = before["rows"] == after["rows"] and before["version"] == after["version"]

    report = {
        "table": args.table,
        "elapsed_seconds": elapsed,
        "cleanup_stats": repr(cleanup_stats),
        "before": before,
        "after": after,
        "ok": ok,
    }
    report_path = backup_root / f"cleanup_{args.table}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    summary = {
        "table": args.table,
        "ok": ok,
        "elapsed_seconds": round(elapsed, 2),
        "before_gb": round(before["dir"]["bytes"] / 1024**3, 3),
        "after_gb": round(after["dir"]["bytes"] / 1024**3, 3),
        "before_files": before["dir"]["files"],
        "after_files": after["dir"]["files"],
        "rows": after["rows"],
        "version": after["version"],
        "report": str(report_path),
        "cleanup_stats": repr(cleanup_stats),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
