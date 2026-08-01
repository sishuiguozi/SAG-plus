from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sag_maintenance_guard import find_sag_runtime_processes


def _now_sqlite() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")


def _load_payload(raw: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dump_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _backup_sqlite(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-before-vector-job-recover-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, backup_dir / f"{backup_path.name}{suffix}")
    return backup_path


def _running_jobs(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        select id, source_config_id, payload_json, attempts, started_at, updated_at, error
        from vector_write_jobs
        where status in ('running', 'writing')
        order by started_at, created_at, id
        """
    ).fetchall()


def _summarize(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total_refs = 0
    sources: dict[str, int] = {}
    jobs: list[dict[str, Any]] = []
    for row in rows:
        payload = _load_payload(row["payload_json"])
        event_ids = [value for value in payload.get("event_ids", []) if value]
        total_refs += len(event_ids)
        source_config_id = str(row["source_config_id"] or "")
        sources[source_config_id] = sources.get(source_config_id, 0) + 1
        jobs.append(
            {
                "id": row["id"],
                "source_config_id": source_config_id,
                "event_refs": len(event_ids),
                "attempts": row["attempts"],
                "started_at": row["started_at"],
                "updated_at": row["updated_at"],
                "error": row["error"],
            }
        )
    return {
        "running_jobs": len(rows),
        "running_event_refs": total_refs,
        "sources": sources,
        "jobs": jobs,
    }


def _recover(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> int:
    now = _now_sqlite()
    changed = 0
    for row in rows:
        payload = _load_payload(row["payload_json"])
        payload["recovered_from_running_at"] = now
        payload["recovered_from_running_reason"] = "stale_runtime_recovery"
        cursor = con.execute(
            """
            update vector_write_jobs
            set status = 'retry',
                updated_at = ?,
                started_at = null,
                next_run_at = null,
                error = ?,
                payload_json = ?
            where id = ? and status in ('running', 'writing')
            """,
            (
                now,
                "Recovered stale running vector write job; queued for retry.",
                _dump_payload(payload),
                row["id"],
            ),
        )
        changed += cursor.rowcount
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover stale running vector write jobs.")
    parser.add_argument("--metadata-db", required=True, help="Path to SAG metadata sag.db")
    parser.add_argument("--execute", action="store_true", help="Apply recovery. Default is dry-run.")
    parser.add_argument("--backup-dir", help="Directory for pre-execute SQLite backup")
    parser.add_argument(
        "--allow-runtime-processes",
        action="store_true",
        help="Allow recovery even if a SAG API/runtime process appears active.",
    )
    parser.add_argument("--json-out", help="Write report JSON to this file")
    args = parser.parse_args()

    db_path = Path(args.metadata_db)
    runtime_processes = find_sag_runtime_processes()
    if runtime_processes and not args.allow_runtime_processes:
        raise SystemExit(
            "refuse recovery: SAG API/runtime process appears active; stop API first "
            "or pass --allow-runtime-processes for a verified stale state"
        )

    con = sqlite3.connect(db_path, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=60000")
        rows = _running_jobs(con)
        result: dict[str, Any] = {
            "mode": "execute" if args.execute else "dry-run",
            "metadata_db": str(db_path),
            "runtime_processes": runtime_processes,
            "before": _summarize(rows),
        }
        if args.execute:
            if not args.backup_dir:
                raise SystemExit("--backup-dir is required with --execute")
            backup_path = _backup_sqlite(db_path, Path(args.backup_dir))
            result["backup_path"] = str(backup_path)
            with con:
                result["recovered_jobs"] = _recover(con, rows)
            result["after"] = _summarize(_running_jobs(con))
    finally:
        con.close()

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
