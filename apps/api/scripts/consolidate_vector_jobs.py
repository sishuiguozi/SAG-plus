from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = ("queued", "retry", "running", "writing")
MUTABLE_PENDING_STATUSES = ("queued", "retry")
INFLIGHT_STATUSES = ("running", "writing")
ITEM_ACTIVE_STATUSES = ("queued", "embedding", "ready_to_write", "writing", "retry")
_ITEM_INSERT_BATCH = 500


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


def _unique(values: list[Any], *, exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "")
        if not item or item in excluded or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _most_common_int(payloads: list[dict[str, Any]], key: str, default: int) -> int:
    values: list[int] = []
    for payload in payloads:
        try:
            values.append(int(payload.get(key) or default))
        except (TypeError, ValueError):
            pass
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def _most_common_bool(payloads: list[dict[str, Any]], key: str, default: bool) -> bool:
    values = [bool(payload.get(key, default)) for payload in payloads]
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def _most_common_str(payloads: list[dict[str, Any]], key: str, default: str) -> str:
    values = [str(payload.get(key) or default) for payload in payloads if payload.get(key) or default]
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def build_plan(rows: list[sqlite3.Row], *, batch_size: int) -> dict[str, Any]:
    running_by_source: dict[str, set[str]] = defaultdict(set)
    queued_by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for row in rows:
        source_config_id = str(row["source_config_id"] or "")
        payload = _load_payload(row["payload_json"])
        event_ids = _unique(payload.get("event_ids") or [])
        if row["status"] in INFLIGHT_STATUSES:
            running_by_source[source_config_id].update(event_ids)
        elif row["status"] in MUTABLE_PENDING_STATUSES:
            queued_by_source[source_config_id].append(row)

    groups: list[dict[str, Any]] = []
    total_old_jobs = 0
    total_old_refs = 0
    total_unique_refs = 0
    total_new_jobs = 0

    for source_config_id, source_rows in sorted(queued_by_source.items()):
        payloads = [_load_payload(row["payload_json"]) for row in source_rows]
        old_refs: list[str] = []
        for payload in payloads:
            old_refs.extend(payload.get("event_ids") or [])
        unique_ids = _unique(old_refs, exclude=running_by_source[source_config_id])
        batches = _chunks(unique_ids, batch_size)
        duplicate_refs = max(0, len([x for x in old_refs if x]) - len(_unique(old_refs)))
        groups.append(
            {
                "source_config_id": source_config_id,
                "old_job_ids": [row["id"] for row in source_rows],
                "old_jobs": len(source_rows),
                "old_event_refs": len([x for x in old_refs if x]),
                "unique_event_refs": len(unique_ids),
                "duplicate_event_refs": duplicate_refs,
                "running_event_refs_excluded": len(running_by_source[source_config_id]),
                "new_jobs": len(batches),
                "batches": batches,
                "payload_defaults": {
                    "embedding_batch_size": _most_common_int(payloads, "embedding_batch_size", 10),
                    "index_batch_size": _most_common_int(payloads, "index_batch_size", 50),
                    "embedding_max_length": _most_common_int(payloads, "embedding_max_length", 500),
                    "enable_entity_vector_sync": _most_common_bool(
                        payloads, "enable_entity_vector_sync", True
                    ),
                    "enable_event_entity_vector_sync": _most_common_bool(
                        payloads, "enable_event_entity_vector_sync", True
                    ),
                },
                "embedding_version": _most_common_str(payloads, "embedding_version", "default"),
                "max_attempts": max([int(row["max_attempts"] or 8) for row in source_rows] or [8]),
            }
        )
        total_old_jobs += len(source_rows)
        total_old_refs += len([x for x in old_refs if x])
        total_unique_refs += len(unique_ids)
        total_new_jobs += len(batches)

    return {
        "batch_size": batch_size,
        "queued_old_jobs": total_old_jobs,
        "queued_old_event_refs": total_old_refs,
        "queued_unique_event_refs": total_unique_refs,
        "new_jobs": total_new_jobs,
        "groups": groups,
    }


def summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_size": plan["batch_size"],
        "queued_old_jobs": plan["queued_old_jobs"],
        "queued_old_event_refs": plan["queued_old_event_refs"],
        "queued_unique_event_refs": plan["queued_unique_event_refs"],
        "new_jobs": plan["new_jobs"],
        "groups": [
            {
                "source_config_id": group["source_config_id"],
                "old_jobs": group["old_jobs"],
                "old_event_refs": group["old_event_refs"],
                "unique_event_refs": group["unique_event_refs"],
                "duplicate_event_refs": group["duplicate_event_refs"],
                "running_event_refs_excluded": group["running_event_refs_excluded"],
                "new_jobs": group["new_jobs"],
                "batch_sizes": [len(batch) for batch in group["batches"]],
                "payload_defaults": group["payload_defaults"],
                "max_attempts": group["max_attempts"],
            }
            for group in plan["groups"]
        ],
    }


def _items_table_exists(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'vector_write_items'"
    ).fetchone()
    return row is not None


def _supersede_job_items(con: sqlite3.Connection, old_job_id: str, now: str) -> int:
    """Close out active record items of a superseded job as failed."""
    cursor = con.execute(
        """
        update vector_write_items
        set status = 'failed',
            last_error = 'superseded by consolidation',
            updated_at = ?,
            lease_owner = null,
            lease_expires_at = null
        where job_id = ? and status in ({item_statuses})
        """.format(item_statuses=", ".join(f"'{s}'" for s in ITEM_ACTIVE_STATUSES)),
        (now, old_job_id),
    )
    return cursor.rowcount


def _register_job_items(
    con: sqlite3.Connection,
    *,
    job_id: str,
    table_name: str,
    source_config_id: str,
    embedding_version: str,
    record_ids: list[str],
    now: str,
) -> int:
    """Insert record-level items for a consolidated job (idempotent)."""
    if not record_ids:
        return 0
    payload = '{"kind":"event_sync"}'
    rows = [
        (
            uuid.uuid4().hex,
            now,
            now,
            job_id,
            table_name,
            record_id,
            embedding_version,
            source_config_id,
            "queued",
            0,
            None,
            None,
            None,
            None,
            payload,
        )
        for record_id in record_ids
    ]
    inserted = 0
    for offset in range(0, len(rows), _ITEM_INSERT_BATCH):
        chunk = rows[offset : offset + _ITEM_INSERT_BATCH]
        cursor = con.executemany(
            """
            insert or ignore into vector_write_items
                (id, created_at, updated_at, job_id, table_name, record_id,
                 embedding_version, source_config_id, status, attempts,
                 next_run_at, last_error, lease_owner, lease_expires_at, payload_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk,
        )
        inserted += cursor.rowcount
    return inserted


def _backup_sqlite(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}-before-vector-job-consolidate-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, backup_dir / f"{backup_path.name}{suffix}")
    return backup_path


def _execute_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    now = _now_sqlite()
    group_id = uuid.uuid4().hex
    inserted = 0
    superseded = 0
    registered_items = 0
    superseded_items = 0
    created_job_ids: list[str] = []
    columns = {str(row[1]) for row in con.execute("pragma table_info('vector_write_jobs')").fetchall()}
    has_v2_columns = {
        "embedding_version",
        "parent_batch_id",
        "superseded_by",
        "record_count",
    }.issubset(columns)
    has_items_table = _items_table_exists(con)

    for group in plan["groups"]:
        source_config_id = group["source_config_id"]
        # 先收尾旧任务（jobs -> succeeded + 明细 -> failed），释放 active
        # 唯一键，再注册新任务明细，避免 partial unique index 冲突。
        for old_job_id in group["old_job_ids"]:
            raw = con.execute(
                "select payload_json from vector_write_jobs where id = ? and status in ('queued', 'retry')",
                (old_job_id,),
            ).fetchone()
            if raw is None:
                continue
            payload = _load_payload(raw["payload_json"])
            payload.update(
                {
                    "superseded": True,
                    "superseded_at": now,
                    "superseded_by_group_id": group_id,
                    "superseded_by_job_count": group["new_jobs"],
                    "superseded_reason": "consolidated_vector_jobs",
                }
            )
            if has_v2_columns:
                cursor = con.execute(
                    """
                    update vector_write_jobs
                    set status = 'succeeded',
                        updated_at = ?,
                        finished_at = ?,
                        next_run_at = null,
                        lease_owner = null,
                        lease_expires_at = null,
                        superseded_by = ?,
                        error = null,
                        payload_json = ?
                    where id = ? and status in ('queued', 'retry')
                    """,
                    (now, now, group_id, _dump_payload(payload), old_job_id),
                )
            else:
                cursor = con.execute(
                    """
                    update vector_write_jobs
                    set status = 'succeeded',
                        updated_at = ?,
                        finished_at = ?,
                        next_run_at = null,
                        error = null,
                        payload_json = ?
                    where id = ? and status in ('queued', 'retry')
                    """,
                    (now, now, _dump_payload(payload), old_job_id),
                )
            superseded += cursor.rowcount
            if has_items_table:
                superseded_items += _supersede_job_items(con, old_job_id, now)
        for index, batch in enumerate(group["batches"]):
            job_id = uuid.uuid4().hex
            created_job_ids.append(job_id)
            payload = {
                **group["payload_defaults"],
                "event_ids": batch,
                "chunk_ids": ["consolidated-vector-jobs"],
                "reason": "consolidated_vector_jobs",
                "consolidation_group_id": group_id,
                "consolidation_batch_index": index,
                "consolidation_batch_count": group["new_jobs"],
                "parent_job_count": group["old_jobs"],
                "embedding_version": group["embedding_version"],
            }
            if has_v2_columns:
                con.execute(
                    """
                    insert into vector_write_jobs
                        (id, created_at, updated_at, kind, status, source_config_id,
                         payload_json, attempts, max_attempts, next_run_at,
                         lease_owner, lease_expires_at, embedding_version, parent_batch_id,
                         superseded_by, record_count, started_at, finished_at, error)
                    values (?, ?, ?, 'event_sync', 'queued', ?, ?, 0, ?, null,
                            null, null, ?, ?, null, ?, null, null, null)
                    """,
                    (
                        job_id,
                        now,
                        now,
                        source_config_id,
                        _dump_payload(payload),
                        int(group["max_attempts"] or 8),
                        group["embedding_version"],
                        group_id,
                        len(batch),
                    ),
                )
            else:
                con.execute(
                    """
                    insert into vector_write_jobs
                        (id, created_at, updated_at, kind, status, source_config_id,
                         payload_json, attempts, max_attempts, next_run_at,
                         started_at, finished_at, error)
                    values (?, ?, ?, 'event_sync', 'queued', ?, ?, 0, ?, null, null, null, null)
                    """,
                    (
                        job_id,
                        now,
                        now,
                        source_config_id,
                        _dump_payload(payload),
                        int(group["max_attempts"] or 8),
                    ),
                )
            inserted += 1
            if has_items_table:
                registered_items += _register_job_items(
                    con,
                    job_id=job_id,
                    table_name="event_vectors",
                    source_config_id=source_config_id,
                    embedding_version=group["embedding_version"],
                    record_ids=batch,
                    now=now,
                )


    return {
        "consolidation_group_id": group_id,
        "inserted_jobs": inserted,
        "created_job_count": len(created_job_ids),
        "superseded_attempted": sum(len(group["old_job_ids"]) for group in plan["groups"]),
        "superseded_jobs": superseded,
        "registered_items": registered_items,
        "superseded_items": superseded_items,
        "total_changes": con.total_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolidate tiny queued vector write jobs.")
    parser.add_argument("--metadata-db", required=True, help="Path to SAG metadata sag.db")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--execute", action="store_true", help="Apply changes. Default is dry-run.")
    parser.add_argument("--backup-dir", help="Directory for pre-execute SQLite backup")
    parser.add_argument("--json-out", help="Write full plan/result JSON to this file")
    args = parser.parse_args()

    db_path = Path(args.metadata_db)
    con = sqlite3.connect(db_path, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=60000")
        rows = con.execute(
            """
            select id, status, source_config_id, payload_json, max_attempts
            from vector_write_jobs
            where status in ('queued', 'retry', 'running', 'writing') and kind = 'event_sync'
            order by source_config_id, created_at, id
            """
        ).fetchall()
        plan = build_plan(rows, batch_size=args.batch_size)
        result: dict[str, Any] = {
            "mode": "execute" if args.execute else "dry-run",
            "metadata_db": str(db_path),
            "plan": summarize_plan(plan),
        }
        if args.execute:
            if not args.backup_dir:
                raise SystemExit("--backup-dir is required with --execute")
            backup_path = _backup_sqlite(db_path, Path(args.backup_dir))
            result["backup_path"] = str(backup_path)
            with con:
                result["execution"] = _execute_plan(con, plan)
    finally:
        con.close()

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
