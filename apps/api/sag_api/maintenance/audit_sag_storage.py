from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

DEFAULT_ENGINE_TABLES = (
    "article",
    "article_section",
    "source_chunk",
    "source_event",
    "entity",
    "event_entity",
)
DEFAULT_META_TABLES = (
    "sources",
    "documents",
    "jobs",
    "vector_write_jobs",
    "vector_write_items",
)
LANCE_TABLES = (
    "event_vectors",
    "source_chunks",
    "entity_vectors",
    "event_entity_vectors",
)
VECTOR_QUEUE_ACTIVE_STATUSES = ("queued", "retry", "running", "writing")
VECTOR_ITEM_ACTIVE_STATUSES = ("queued", "embedding", "ready_to_write", "writing", "retry")


def _bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for root, _, files in os.walk(path):
        for name in files:
            total += _bytes(Path(root) / name)
    return total


def _disk_usage(path: Path) -> dict[str, int | str]:
    target = path if path.exists() else path.parent
    usage = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _resolve_relative(path: Path, *, base: Path) -> Path:
    if path.is_absolute():
        return path
    candidates = [
        (Path.cwd() / path),
        (base / path),
        (base.parent.parent / path),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _sqlite_path_from_url(value: str, *, base: Path) -> Path | None:
    if not value.startswith("sqlite"):
        return None
    parsed = urlparse(value)
    raw_path = unquote(parsed.path or "")
    if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    if not raw_path:
        return None
    return _resolve_relative(Path(raw_path), base=base)


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count_table(con: sqlite3.Connection, table: str) -> int | None:
    if not _table_exists(con, table):
        return None
    return int(con.execute(f'select count(*) from "{table}"').fetchone()[0])


def _sqlite_status_counts(con: sqlite3.Connection, table: str) -> dict[str, int]:
    if not _table_exists(con, table):
        return {}
    columns = [row[1] for row in con.execute(f'pragma table_info("{table}")').fetchall()]
    if "status" not in columns:
        return {}
    rows = con.execute(
        f'select coalesce(status, "<null>") as status, count(*) from "{table}" group by status'
    ).fetchall()
    return {str(status): int(count) for status, count in rows}


def _sqlite_rows(db_path: Path, tables: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": _bytes(db_path),
        "wal_bytes": _bytes(Path(str(db_path) + "-wal")),
        "tables": {},
        "status_counts": {},
    }
    if not db_path.exists():
        return result
    con = sqlite3.connect(db_path, timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        for table in tables:
            count = _count_table(con, table)
            if count is not None:
                result["tables"][table] = count
                status_counts = _sqlite_status_counts(con, table)
                if status_counts:
                    result["status_counts"][table] = status_counts
    finally:
        con.close()
    return result


def _vector_queue_summary(meta_db: Path) -> dict[str, Any]:
    if not meta_db.exists():
        return {"exists": False}
    con = sqlite3.connect(meta_db, timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        if not _table_exists(con, "vector_write_jobs"):
            return {"exists": False}
        columns = {str(row[1]) for row in con.execute("pragma table_info('vector_write_jobs')").fetchall()}
        has_kind = "kind" in columns
        has_record_count = "record_count" in columns
        has_embedding_version = "embedding_version" in columns
        if has_record_count:
            status_rows = con.execute(
                """
                select status, count(*), coalesce(sum(coalesce(record_count, 0)), 0)
                from vector_write_jobs
                group by status
                """
            ).fetchall()
        else:
            status_rows = con.execute(
                "select status, count(*) from vector_write_jobs group by status"
            ).fetchall()
        active_rows = con.execute(
            """
            select source_config_id, status, payload_json
            {kind_sql}
            {record_count_sql}
            {embedding_version_sql}
            from vector_write_jobs
            where status in ({active_statuses})
            """
            .format(
                kind_sql=", kind" if has_kind else "",
                record_count_sql=", record_count" if has_record_count else "",
                embedding_version_sql=", embedding_version" if has_embedding_version else "",
                active_statuses=", ".join(f"'{status}'" for status in VECTOR_QUEUE_ACTIVE_STATUSES),
            )
        ).fetchall()
        if _table_exists(con, "vector_write_items"):
            item_status_counts = {
                str(row[0]): int(row[1])
                for row in con.execute(
                    "select status, count(*) from vector_write_items group by status"
                ).fetchall()
            }
            item_active_rows = con.execute(
                "select table_name, count(*) from vector_write_items "
                f"where status in ({', '.join(repr(s) for s in VECTOR_ITEM_ACTIVE_STATUSES)}) "
                "group by table_name"
            ).fetchall()
            items_summary = {
                "exists": True,
                "status_counts": item_status_counts,
                "active_records": sum(int(row[1]) for row in item_active_rows),
                "active_records_by_table": {
                    str(row[0]): int(row[1]) for row in item_active_rows
                },
            }
    finally:
        con.close()

    status_counts: dict[str, int] = {}
    status_record_counts: dict[str, int] = {}
    for row in status_rows:
        status = str(row[0])
        status_counts[status] = int(row[1])
        if has_record_count:
            status_record_counts[status] = int(row[2] or 0)
    refs = 0
    active_records = 0
    jobs_with_payload = 0
    active_ref_counter: Counter[str] = Counter()
    jobs_by_source: Counter[str] = Counter()
    records_by_source: Counter[str] = Counter()
    records_by_kind: Counter[str] = Counter()
    records_by_embedding_version: Counter[str] = Counter()
    for row in active_rows:
        source_config_id = row[0]
        status = row[1]
        payload_json = row[2]
        offset = 3
        kind = str(row[offset] or "unknown") if has_kind else "unknown"
        offset += 1 if has_kind else 0
        record_count = int(row[offset] or 0) if has_record_count else 0
        offset += 1 if has_record_count else 0
        embedding_version = str(row[offset] or "default") if has_embedding_version else "default"
        del status
        jobs_by_source[str(source_config_id or "")] += 1
        try:
            payload = json.loads(payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        event_ids = [str(value) for value in payload.get("event_ids", []) if value]
        if event_ids:
            jobs_with_payload += 1
        refs += len(event_ids)
        resolved_record_count = record_count or len(event_ids)
        active_records += resolved_record_count
        records_by_source[str(source_config_id or "")] += resolved_record_count
        records_by_kind[kind] += resolved_record_count
        records_by_embedding_version[embedding_version] += resolved_record_count
        active_ref_counter.update(event_ids)

    duplicate_refs = sum(count - 1 for count in active_ref_counter.values() if count > 1)
    items_summary: dict[str, Any] = {"exists": False}
    return {
        "exists": True,
        "status_counts": status_counts,
        "status_record_counts": status_record_counts,
        "active_jobs": len(active_rows),
        "active_records": active_records,
        "active_event_refs": refs,
        "active_unique_event_refs": len(active_ref_counter),
        "active_duplicate_event_refs": duplicate_refs,
        "jobs_with_event_payload": jobs_with_payload,
        "top_sources_by_active_jobs": jobs_by_source.most_common(10),
        "top_sources_by_active_records": records_by_source.most_common(10),
        "active_records_by_kind": records_by_kind,
        "active_records_by_embedding_version": records_by_embedding_version,
        "items": items_summary,
    }


def _lance_table_summary(db: Any, table_name: str, table_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "exists": False,
        "path": str(table_dir),
        "directory_bytes": _dir_bytes(table_dir),
    }
    try:
        table = db.open_table(table_name)
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
        return result

    result["exists"] = True
    try:
        result["schema_columns"] = [field.name for field in table.schema]
    except Exception as error:  # noqa: BLE001
        result["schema_error"] = str(error)
    try:
        result["rows"] = int(table.count_rows())
    except Exception as error:  # noqa: BLE001
        result["rows_error"] = str(error)
    try:
        stats = table.stats()
        result["stats"] = stats
        fragment_stats = stats.get("fragment_stats") if isinstance(stats, dict) else None
        if isinstance(fragment_stats, dict):
            result["fragments"] = fragment_stats.get("num_fragments")
            result["small_fragments"] = fragment_stats.get("num_small_fragments")
        if isinstance(stats, dict):
            result["active_total_bytes"] = stats.get("total_bytes")
            result["num_indices"] = stats.get("num_indices")
    except Exception as error:  # noqa: BLE001
        result["stats_error"] = str(error)
    result["latest_version"] = getattr(table, "version", None)
    try:
        indices = list(table.list_indices())
        result["indices"] = [
            {
                "name": getattr(i, "name", None) or str(i),
                "index_type": getattr(i, "index_type", None) or getattr(i, "type", None),
            }
            for i in indices
        ]
        result["index_summary"] = {}
        for i in indices:
            index_name = getattr(i, "name", None) or str(i)
            try:
                st = table.index_stats(index_name)
                result["index_summary"][index_name] = {
                    "index_type": getattr(st, "index_type", None),
                    "distance_type": getattr(st, "distance_type", None),
                    "num_indexed_rows": getattr(st, "num_indexed_rows", None),
                    "num_unindexed_rows": getattr(st, "num_unindexed_rows", None),
                    "num_indices": getattr(st, "num_indices", None),
                }
            except Exception as error:  # noqa: BLE001
                result["index_summary"][index_name] = {"error": str(error)}
    except Exception as error:  # noqa: BLE001
        result["indices_error"] = str(error)
    return result


def _lancedb_summary(lancedb_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(lancedb_dir),
        "exists": lancedb_dir.exists(),
        "directory_bytes": _dir_bytes(lancedb_dir),
        "tables": {},
    }
    if not lancedb_dir.exists():
        return result
    try:
        import lancedb

        db = lancedb.connect(str(lancedb_dir))
        table_response = db.list_tables()
        available_tables = getattr(table_response, "tables", table_response)
        available = {str(name) for name in available_tables}
        result["available_tables"] = sorted(available)
        for table_name in LANCE_TABLES:
            table_dir = lancedb_dir / f"{table_name}.lance"
            if table_name in available or table_dir.exists():
                result["tables"][table_name] = _lance_table_summary(db, table_name, table_dir)
    except Exception as error:  # noqa: BLE001
        result["error"] = str(error)
    return result


def _human_bytes(value: int | float | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def _print_text(report: dict[str, Any]) -> None:
    print(f"SAG storage audit @ {report['generated_at']}")
    print(f"engine data: {report['paths']['data_dir']}")
    print(f"metadata db: {report['paths']['metadata_db']}")
    disk = report["disk"]
    print(f"disk free: {_human_bytes(disk['free_bytes'])} / {_human_bytes(disk['total_bytes'])}")
    print()

    for label in ("metadata_sqlite", "engine_sqlite"):
        db = report[label]
        print(f"{label}: {_human_bytes(db['size_bytes'])} wal={_human_bytes(db['wal_bytes'])}")
        for table, count in db["tables"].items():
            print(f"  {table}: {count}")
        if db["status_counts"]:
            print("  status:")
            for table, counts in db["status_counts"].items():
                print(f"    {table}: {counts}")
        print()

    queue = report["vector_queue"]
    print("vector queue:")
    print(f"  status: {queue.get('status_counts', {})}")
    if queue.get("status_record_counts"):
        print(f"  status records: {queue.get('status_record_counts', {})}")
    print(
        "  active records/refs: "
        f"{queue.get('active_records', 0)} / "
        f"{queue.get('active_unique_event_refs', 0)}/{queue.get('active_event_refs', 0)} "
        f"duplicate={queue.get('active_duplicate_event_refs', 0)}"
    )
    if queue.get("active_records_by_embedding_version"):
        print(f"  by embedding version: {dict(queue.get('active_records_by_embedding_version', {}))}")
    if queue.get("active_records_by_kind"):
        print(f"  by kind: {dict(queue.get('active_records_by_kind', {}))}")
    items = queue.get("items") or {}
    if items.get("exists"):
        print(f"  item status: {items.get('status_counts', {})}")
        print(
            "  item active records: "
            f"{items.get('active_records', 0)} "
            f"by_table={dict(items.get('active_records_by_table', {}))}"
        )
    print()

    lance = report["lancedb"]
    print(f"LanceDB: {_human_bytes(lance['directory_bytes'])}")
    for name, table in lance.get("tables", {}).items():
        print(
            f"  {name}: rows={table.get('rows', '-')} "
            f"dir={_human_bytes(table.get('directory_bytes'))} "
            f"active={_human_bytes(table.get('active_total_bytes'))} "
            f"fragments={table.get('fragments', '-')} "
            f"small={table.get('small_fragments', '-')} "
            f"version={table.get('latest_version', '-')}"
        )
        for index_name, ist in (table.get("index_summary") or {}).items():
            covered = ist.get("num_indexed_rows")
            total = table.get("rows")
            pct = (
                f"{covered / total * 100:.1f}%"
                if isinstance(covered, int) and isinstance(total, int) and total
                else "-"
            )
            print(f"      index {index_name}: type={ist.get('index_type', '-')} "
                  f"distance={ist.get('distance_type', '-')} covered={covered} "
                  f"unindexed={ist.get('num_unindexed_rows', '-')} ({pct})")
    print()


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    script_base = Path(__file__).resolve().parents[1]
    try:
        from sag_api.core.config import settings

        configured_data_dir = Path(settings.data_dir)
        configured_meta_db = _sqlite_path_from_url(settings.database_url, base=script_base)
    except Exception:
        configured_data_dir = Path("./.data/engine")
        configured_meta_db = Path("./.data/sag.db")

    data_dir = _resolve_relative(Path(args.data_dir or configured_data_dir), base=script_base)
    metadata_db = _resolve_relative(Path(args.metadata_db or configured_meta_db), base=script_base)
    engine_db = _resolve_relative(Path(args.engine_db or data_dir / "sag.db"), base=script_base)
    lancedb_dir = _resolve_relative(Path(args.lancedb_dir or data_dir / "lancedb"), base=script_base)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "paths": {
            "data_dir": str(data_dir),
            "metadata_db": str(metadata_db),
            "engine_db": str(engine_db),
            "lancedb_dir": str(lancedb_dir),
        },
        "disk": _disk_usage(data_dir),
        "metadata_sqlite": _sqlite_rows(metadata_db, DEFAULT_META_TABLES),
        "engine_sqlite": _sqlite_rows(engine_db, DEFAULT_ENGINE_TABLES),
        "vector_queue": _vector_queue_summary(metadata_db),
        "lancedb": _lancedb_summary(lancedb_dir),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SAG storage audit.")
    parser.add_argument("--data-dir", help="zleap-sag engine data directory")
    parser.add_argument("--metadata-db", help="SAG metadata sqlite database path")
    parser.add_argument("--engine-db", help="zleap-sag engine sqlite database path")
    parser.add_argument("--lancedb-dir", help="LanceDB directory path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    report = build_report(args)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
