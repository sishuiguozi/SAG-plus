from __future__ import annotations

import argparse
import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import lancedb

from sag_api.maintenance.sag_maintenance_guard import maintenance_guard


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


def _snapshot(db: Any, lancedb_root: Path, table_name: str) -> dict[str, Any]:
    table = db.open_table(table_name)
    return {
        "rows": int(table.count_rows()),
        "version": table.version,
        "stats": table.stats(),
        "dir": _dir_stats(lancedb_root / f"{table_name}.lance"),
        "indices": table.list_indices(),
    }


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LanceDB optimize for one SAG table.")
    parser.add_argument("--data-dir", required=True, help="Engine data dir containing lancedb")
    parser.add_argument("--table", required=True, help="LanceDB table name")
    parser.add_argument("--report-dir", required=True, help="Directory for optimize report JSON")
    parser.add_argument(
        "--cleanup-older-than-seconds",
        type=int,
        default=0,
        help="Prune old versions older than this age. Default keeps only latest.",
    )
    parser.add_argument(
        "--delete-unverified",
        action="store_true",
        help="Only use when API/writer is stopped and backup is verified.",
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
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    lancedb_root = data_dir / "lancedb"
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    metadata_db = Path(args.metadata_db) if args.metadata_db else None
    with maintenance_guard(
        metadata_db,
        lease_name=args.lease_name,
        purpose=f"optimize_lancedb:{args.table}",
        ttl_seconds=args.lease_ttl_seconds,
        allow_running_queue=args.allow_running_queue,
        require_runtime_stopped=args.check_runtime_processes,
        min_free_bytes=int(args.min_free_gb * 1024**3),
        free_space_path=data_dir,
        writable_paths=[report_dir],
        metadata={"table": args.table, "data_dir": str(data_dir)},
    ):
        db = lancedb.connect(str(lancedb_root))
        before = _snapshot(db, lancedb_root, args.table)
        start = time.time()
        table = db.open_table(args.table)
        optimize_stats = table.optimize(
            cleanup_older_than=timedelta(seconds=args.cleanup_older_than_seconds),
            delete_unverified=args.delete_unverified,
        )
        elapsed = time.time() - start
        after = _snapshot(db, lancedb_root, args.table)

        ok = before["rows"] == after["rows"]
    report = {
        "table": args.table,
        "elapsed_seconds": elapsed,
        "optimize_stats": repr(optimize_stats),
        "before": before,
        "after": after,
        "ok": ok,
    }
    report_path = report_dir / f"optimize_{args.table}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "table": args.table,
                "ok": ok,
                "elapsed_seconds": round(elapsed, 2),
                "before_rows": before["rows"],
                "after_rows": after["rows"],
                "before_version": before["version"],
                "after_version": after["version"],
                "before_fragments": (before.get("stats", {}).get("fragment_stats") or {}).get(
                    "num_fragments"
                ),
                "after_fragments": (after.get("stats", {}).get("fragment_stats") or {}).get(
                    "num_fragments"
                ),
                "before_gb": round(before["dir"]["bytes"] / 1024**3, 3),
                "after_gb": round(after["dir"]["bytes"] / 1024**3, 3),
                "report": str(report_path),
                "optimize_stats": repr(optimize_stats),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(run())
