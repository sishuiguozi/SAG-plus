from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def _load_script_module(module_name: str, relative_path: str):
    script_path = Path(__file__).resolve().parents[1] / relative_path
    scripts_dir = str(script_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vector_queue_audit_reports_record_counts_and_versions(tmp_path: Path):
    audit = _load_script_module("audit_sag_storage_test", "scripts/audit_sag_storage.py")
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table vector_write_jobs (
                id text primary key,
                source_config_id text,
                status text,
                payload_json text,
                record_count integer,
                embedding_version text
            )
            """
        )
        con.executemany(
            """
            insert into vector_write_jobs (id, source_config_id, status, payload_json, record_count, embedding_version)
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                ("job-1", "source-a", "queued", '{"event_ids":["e1","e2"]}', 2, "default"),
                ("job-2", "source-a", "running", '{"event_ids":["e2","e3","e4"]}', 3, "default"),
                ("job-4", "source-a", "retry", '{"event_ids":["e5"]}', 1, "default"),
                ("job-5", "source-a", "writing", '{"event_ids":["e6"]}', 1, "default"),
                ("job-3", "source-b", "failed", '{"event_ids":["e9"]}', 1, "v2"),
            ],
        )
        con.commit()
    finally:
        con.close()

    summary = audit._vector_queue_summary(db_path)

    assert summary["status_counts"] == {"failed": 1, "queued": 1, "retry": 1, "running": 1, "writing": 1}
    assert summary["status_record_counts"] == {"failed": 1, "queued": 2, "retry": 1, "running": 3, "writing": 1}
    assert summary["active_jobs"] == 4
    assert summary["active_records"] == 7
    assert summary["active_event_refs"] == 7
    assert summary["active_unique_event_refs"] == 6
    assert summary["active_duplicate_event_refs"] == 1
    assert summary["top_sources_by_active_records"][0] == ("source-a", 7)
    assert dict(summary["active_records_by_embedding_version"]) == {"default": 7}


def test_recover_vector_write_jobs_moves_writing_and_running_to_retry(tmp_path: Path):
    recover = _load_script_module("recover_vector_write_jobs_test", "scripts/recover_vector_write_jobs.py")
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            create table vector_write_jobs (
                id text primary key,
                source_config_id text,
                status text,
                payload_json text,
                attempts integer,
                created_at text,
                started_at text,
                updated_at text,
                next_run_at text,
                error text
            )
            """
        )
        con.executemany(
            """
            insert into vector_write_jobs
                (id, source_config_id, status, payload_json, attempts, created_at, started_at, updated_at, next_run_at, error)
            values (?, 'source-a', ?, '{"event_ids":["e1"]}', 1, '2026-07-30 00:00:00', '2026-07-30 00:00:00',
                    '2026-07-30 00:00:00', null, null)
            """,
            [
                ("job-running", "running"),
                ("job-writing", "writing"),
            ],
        )
        con.commit()

        rows = recover._running_jobs(con)
        changed = recover._recover(con, rows)
        con.commit()
        statuses = {
            row["id"]: row["status"]
            for row in con.execute("select id, status from vector_write_jobs order by id").fetchall()
        }
    finally:
        con.close()

    assert changed == 2
    assert statuses == {"job-running": "retry", "job-writing": "retry"}


def test_consolidate_vector_jobs_populates_v2_fields(tmp_path: Path):
    consolidate = _load_script_module("consolidate_vector_jobs_test", "scripts/consolidate_vector_jobs.py")
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            create table vector_write_jobs (
                id text primary key,
                created_at text,
                updated_at text,
                kind text,
                status text,
                source_config_id text,
                payload_json text,
                attempts integer,
                max_attempts integer,
                next_run_at text,
                lease_owner text,
                lease_expires_at text,
                embedding_version text default 'default',
                parent_batch_id text,
                superseded_by text,
                record_count integer default 0,
                started_at text,
                finished_at text,
                error text
            )
            """
        )
        con.executemany(
            """
            insert into vector_write_jobs
                (id, created_at, updated_at, kind, status, source_config_id, payload_json, attempts, max_attempts,
                 next_run_at, lease_owner, lease_expires_at, embedding_version, parent_batch_id, superseded_by,
                 record_count, started_at, finished_at, error)
            values (?, '2026-07-30 00:00:00', '2026-07-30 00:00:00', 'event_sync', ?, ?, ?, 0, 8,
                    null, null, null, ?, null, null, ?, null, null, null)
            """,
            [
                ("job-old-1", "queued", "source-a", '{"event_ids":["e1","e2"],"embedding_version":"v2"}', "v2", 2),
                ("job-old-2", "queued", "source-a", '{"event_ids":["e2","e3"],"embedding_version":"v2"}', "v2", 2),
            ],
        )
        con.commit()

        rows = con.execute(
            """
            select id, status, source_config_id, payload_json, max_attempts
            from vector_write_jobs
            where status in ('queued', 'running') and kind = 'event_sync'
            order by source_config_id, created_at, id
            """
        ).fetchall()
        plan = consolidate.build_plan(rows, batch_size=2)
        result = consolidate._execute_plan(con, plan)
        con.commit()

        new_rows = con.execute(
            """
            select status, embedding_version, parent_batch_id, record_count, payload_json
            from vector_write_jobs
            where parent_batch_id = ?
            order by id
            """,
            (result["consolidation_group_id"],),
        ).fetchall()
        old_rows = con.execute(
            """
            select status, superseded_by
            from vector_write_jobs
            where id in ('job-old-1', 'job-old-2')
            order by id
            """
        ).fetchall()
    finally:
        con.close()

    assert len(new_rows) == 2
    assert {row["status"] for row in new_rows} == {"queued"}
    assert {row["embedding_version"] for row in new_rows} == {"v2"}
    assert {row["parent_batch_id"] for row in new_rows} == {result["consolidation_group_id"]}
    assert sorted(row["record_count"] for row in new_rows) == [1, 2]
    assert {row["status"] for row in old_rows} == {"succeeded"}
    assert {row["superseded_by"] for row in old_rows} == {result["consolidation_group_id"]}


def test_consolidate_vector_jobs_syncs_record_items(tmp_path: Path):
    consolidate = _load_script_module("consolidate_vector_jobs_items", "scripts/consolidate_vector_jobs.py")
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            create table vector_write_jobs (
                id text primary key,
                created_at text,
                updated_at text,
                kind text,
                status text,
                source_config_id text,
                payload_json text,
                attempts integer,
                max_attempts integer,
                next_run_at text,
                lease_owner text,
                lease_expires_at text,
                embedding_version text default 'default',
                parent_batch_id text,
                superseded_by text,
                record_count integer default 0,
                started_at text,
                finished_at text,
                error text
            )
            """
        )
        con.execute(
            """
            create table vector_write_items (
                id text primary key,
                created_at text,
                updated_at text,
                job_id text,
                table_name text,
                record_id text,
                embedding_version text,
                source_config_id text,
                status text,
                attempts integer,
                next_run_at text,
                last_error text,
                lease_owner text,
                lease_expires_at text,
                payload_json text
            )
            """
        )
        con.execute(
            """
            create unique index uq_vector_write_items_active
            on vector_write_items (table_name, record_id, embedding_version)
            where status in ('queued','embedding','ready_to_write','writing','retry')
            """
        )
        con.executemany(
            """
            insert into vector_write_jobs
                (id, created_at, updated_at, kind, status, source_config_id, payload_json, attempts, max_attempts,
                 next_run_at, lease_owner, lease_expires_at, embedding_version, parent_batch_id, superseded_by,
                 record_count, started_at, finished_at, error)
            values (?, '2026-07-30 00:00:00', '2026-07-30 00:00:00', 'event_sync', ?, ?, ?, 0, 8,
                    null, null, null, ?, null, null, ?, null, null, null)
            """,
            [
                ("job-old-1", "queued", "source-a", '{"event_ids":["e1","e2"],"embedding_version":"v2"}', "v2", 2),
                ("job-old-2", "queued", "source-a", '{"event_ids":["e3"],"embedding_version":"v2"}', "v2", 1),
                ("job-running", "running", "source-a", '{"event_ids":["e9"],"embedding_version":"v2"}', "v2", 1),
            ],
        )
        con.executemany(
            """
            insert into vector_write_items
                (id, created_at, updated_at, job_id, table_name, record_id, embedding_version,
                 source_config_id, status, attempts, next_run_at, last_error, lease_owner,
                 lease_expires_at, payload_json)
            values (?, '2026-07-30 00:00:00', '2026-07-30 00:00:00', ?, 'event_vectors', ?, 'v2',
                    'source-a', 'queued', 0, null, null, null, null, '{"kind":"event_sync"}')
            """,
            [
                ("item-1", "job-old-1", "e1"),
                ("item-2", "job-old-1", "e2"),
                ("item-3", "job-old-2", "e3"),
                ("item-9", "job-running", "e9"),
            ],
        )
        con.commit()

        rows = con.execute(
            """
            select id, status, source_config_id, payload_json, max_attempts
            from vector_write_jobs
            where status in ('queued', 'running') and kind = 'event_sync'
            order by source_config_id, created_at, id
            """
        ).fetchall()
        plan = consolidate.build_plan(rows, batch_size=200)
        result = consolidate._execute_plan(con, plan)
        con.commit()

        new_job_id = con.execute(
            """
            select id from vector_write_jobs
            where parent_batch_id = ?
            order by id
            """,
            (result["consolidation_group_id"],),
        ).fetchone()["id"]
        new_items = con.execute(
            """
            select record_id, status, job_id
            from vector_write_items
            where job_id = ? and status = 'queued'
            order by record_id
            """,
            (new_job_id,),
        ).fetchall()
        old_items = con.execute(
            """
            select record_id, status, last_error
            from vector_write_items
            where job_id in ('job-old-1', 'job-old-2')
            order by record_id
            """,
        ).fetchall()
        running_items = con.execute(
            """
            select record_id, status, job_id
            from vector_write_items
            where record_id = 'e9'
            """,
        ).fetchall()
    finally:
        con.close()

    assert result["inserted_jobs"] == 1
    assert result["registered_items"] == 3
    assert result["superseded_items"] == 3
    assert [row["record_id"] for row in new_items] == ["e1", "e2", "e3"]
    assert {row["status"] for row in new_items} == {"queued"}
    assert {row["record_id"] for row in old_items} == {"e1", "e2", "e3"}
    assert all(row["status"] == "failed" for row in old_items)
    assert all(row["last_error"] == "superseded by consolidation" for row in old_items)
    # running 任务及其明细不受影响
    assert len(running_items) == 1
    assert running_items[0]["status"] == "queued"
    assert running_items[0]["job_id"] == "job-running"


def test_consolidate_vector_jobs_without_items_table(tmp_path: Path):
    consolidate = _load_script_module("consolidate_vector_jobs_no_items", "scripts/consolidate_vector_jobs.py")
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            create table vector_write_jobs (
                id text primary key,
                created_at text,
                updated_at text,
                kind text,
                status text,
                source_config_id text,
                payload_json text,
                attempts integer,
                max_attempts integer,
                next_run_at text,
                lease_owner text,
                lease_expires_at text,
                embedding_version text default 'default',
                parent_batch_id text,
                superseded_by text,
                record_count integer default 0,
                started_at text,
                finished_at text,
                error text
            )
            """
        )
        con.execute(
            """
            insert into vector_write_jobs
                (id, created_at, updated_at, kind, status, source_config_id, payload_json, attempts, max_attempts,
                 next_run_at, lease_owner, lease_expires_at, embedding_version, parent_batch_id, superseded_by,
                 record_count, started_at, finished_at, error)
            values ('job-old-1', '2026-07-30 00:00:00', '2026-07-30 00:00:00', 'event_sync', 'queued', 'source-a',
                    '{"event_ids":["e1","e2"],"embedding_version":"v2"}', 0, 8, null, null, null,
                    'v2', null, null, 2, null, null, null)
            """
        )
        con.commit()

        rows = con.execute(
            """
            select id, status, source_config_id, payload_json, max_attempts
            from vector_write_jobs
            where status in ('queued', 'running') and kind = 'event_sync'
            """
        ).fetchall()
        plan = consolidate.build_plan(rows, batch_size=200)
        result = consolidate._execute_plan(con, plan)
        con.commit()
        table_exists = consolidate._items_table_exists(con)
    finally:
        con.close()

    assert result["inserted_jobs"] == 1
    assert result["registered_items"] == 0
    assert result["superseded_items"] == 0
    assert table_exists is False
