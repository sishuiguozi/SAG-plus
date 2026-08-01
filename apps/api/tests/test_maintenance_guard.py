import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from sag_maintenance_guard import (  # noqa: E402
    ensure_directory_writable,
    ensure_min_free_space,
    find_sag_runtime_processes,
    maintenance_guard,
)


def _init_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table vector_write_jobs (status text)")
        con.execute("insert into vector_write_jobs (status) values ('running')")
        con.commit()
    finally:
        con.close()


def test_maintenance_guard_refuses_running_vector_queue(tmp_path: Path):
    db_path = tmp_path / "sag.db"
    _init_db(db_path)

    with pytest.raises(RuntimeError, match="running=1"):
        with maintenance_guard(
            db_path,
            lease_name="test-maintenance",
            purpose="test",
        ):
            raise AssertionError("guard should refuse before entering")


def test_maintenance_guard_refuses_writing_vector_queue(tmp_path: Path):
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute("create table vector_write_jobs (status text)")
        con.execute("insert into vector_write_jobs (status) values ('writing')")
        con.commit()
    finally:
        con.close()

    with pytest.raises(RuntimeError, match="running=1"):
        with maintenance_guard(
            db_path,
            lease_name="test-maintenance",
            purpose="test",
        ):
            raise AssertionError("guard should refuse before entering")


def test_maintenance_guard_can_allow_verified_stale_running_queue(tmp_path: Path):
    db_path = tmp_path / "sag.db"
    _init_db(db_path)

    with maintenance_guard(
        db_path,
        lease_name="test-maintenance",
        purpose="test",
        allow_running_queue=True,
    ):
        con = sqlite3.connect(db_path)
        try:
            assert con.execute("select count(*) from maintenance_leases").fetchone()[0] == 1
        finally:
            con.close()

    con = sqlite3.connect(db_path)
    try:
        assert con.execute("select count(*) from maintenance_leases").fetchone()[0] == 0
    finally:
        con.close()


def test_find_sag_runtime_processes_detects_api_and_ignores_maintenance_script():
    matches = find_sag_runtime_processes(
        [
            {
                "pid": 10,
                "command_line": "python -m uvicorn sag_api.main:app --host 127.0.0.1",
            },
            {
                "pid": 11,
                "command_line": "python apps/api/scripts/optimize_lancedb_table.py --table event_vectors",
            },
            {
                "pid": 12,
                "command_line": "node apps/web/node_modules/.bin/next dev --port 3000",
            },
        ]
    )

    assert [item["pid"] for item in matches] == [10]


def test_maintenance_guard_refuses_live_runtime_process(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute("create table vector_write_jobs (status text)")
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(
        "sag_api.maintenance.sag_maintenance_guard.find_sag_runtime_processes",
        lambda: [{"pid": 10, "command_line": "python -m uvicorn sag_api.main:app"}],
    )

    with pytest.raises(RuntimeError, match="runtime process appears active"):
        with maintenance_guard(
            db_path,
            lease_name="test-maintenance",
            purpose="test",
            require_runtime_stopped=True,
        ):
            raise AssertionError("guard should refuse before entering")


def test_maintenance_guard_refuses_insufficient_free_space(tmp_path: Path):
    db_path = tmp_path / "sag.db"
    con = sqlite3.connect(db_path)
    try:
        con.execute("create table vector_write_jobs (status text)")
        con.commit()
    finally:
        con.close()

    with pytest.raises(RuntimeError, match="insufficient disk free space"):
        with maintenance_guard(
            db_path,
            lease_name="test-maintenance",
            purpose="test",
            min_free_bytes=10**30,
            free_space_path=tmp_path,
        ):
            raise AssertionError("guard should refuse before entering")


def test_free_space_and_writable_helpers_accept_normal_tmpdir(tmp_path: Path):
    ensure_min_free_space(tmp_path, min_free_bytes=1)
    ensure_directory_writable(tmp_path / "reports")
