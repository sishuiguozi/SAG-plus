from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def _now() -> datetime:
    return datetime.now(UTC)


def _to_sqlite(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(sep=" ")


def _parse_sqlite(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def ensure_maintenance_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists maintenance_leases (
            name text primary key,
            owner text not null,
            purpose text not null,
            acquired_at datetime not null,
            heartbeat_at datetime not null,
            expires_at datetime not null,
            metadata_json text not null default '{}'
        )
        """
    )


def active_vector_running_count(con: sqlite3.Connection) -> int:
    row = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = 'vector_write_jobs'"
    ).fetchone()
    if row is None:
        return 0
    return int(
        con.execute(
            "select count(*) from vector_write_jobs where status in ('running', 'writing')"
        ).fetchone()[0]
    )


def _process_rows() -> list[dict[str, Any]]:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Select-Object ProcessId,Name,CommandLine | "
                        "ConvertTo-Json -Compress"
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except Exception:
            return []
        try:
            raw = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            return []
        if isinstance(raw, dict):
            raw = [raw]
        rows: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            rows.append(
                {
                    "pid": int(item.get("ProcessId") or 0),
                    "name": str(item.get("Name") or ""),
                    "command_line": str(item.get("CommandLine") or ""),
                }
            )
        return rows

    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return []
    rows = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command_line = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            pid = 0
        rows.append({"pid": pid, "command_line": command_line})
    return rows


def find_sag_runtime_processes(
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return likely live SAG API / writer processes.

    The vector writer runs inside the SAG API lifespan, so detecting API
    process command lines is the practical process-level safety gate. This is a
    guardrail, not an authorization source; DB queue state remains authoritative.
    """

    current_pid = os.getpid()
    # 只把真正的 Python / sag-api 运行器当作“活动 API”。Windows 上 cmd/pwsh
    # 等壳进程的命令行里也可能包含 uvicorn 字样，但不是 API 本体，不拦截。
    _RUNNER_NAMES = {
        "python.exe", "python", "python3.exe", "python3",
        "sag-api.exe", "sag-api",
    }
    matches: list[dict[str, Any]] = []
    for row in rows if rows is not None else _process_rows():
        pid = int(row.get("pid") or 0)
        if pid == current_pid:
            continue
        name = str(row.get("name") or "")
        if name and name.lower() not in _RUNNER_NAMES:
            continue
        command_line = str(row.get("command_line") or "")
        normalized = command_line.replace("\\", "/").lower()
        if not normalized:
            continue
        is_api = (
            ("uvicorn" in normalized and "sag_api" in normalized)
            or "sag_api.main" in normalized
            or "sag_api.desktop" in normalized
            or "sag_api/desktop.py" in normalized
            or "sag-api.exe" in normalized
        )
        is_maintenance_or_test = (
            "sag_maintenance_guard" in normalized
            or "cleanup_lancedb_old_versions.py" in normalized
            or "optimize_lancedb_table.py" in normalized
            or "consolidate_vector_jobs.py" in normalized
            or "pytest" in normalized
        )
        if is_api and not is_maintenance_or_test:
            matches.append({"pid": pid, "command_line": command_line})
    return matches


def _disk_free_bytes(path: Path) -> int:
    target = path if path.exists() else path.parent
    return int(shutil.disk_usage(target).free)


def ensure_min_free_space(path: Path, *, min_free_bytes: int) -> None:
    free_bytes = _disk_free_bytes(path)
    if free_bytes < min_free_bytes:
        raise RuntimeError(
            "refuse maintenance: insufficient disk free space "
            f"path={path} free_gb={free_bytes / 1024**3:.2f} "
            f"required_gb={min_free_bytes / 1024**3:.2f}"
        )


def ensure_directory_writable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".sag-maintenance-write-test-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"refuse maintenance: directory is not writable path={path}") from error
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def acquire_maintenance_lease(
    con: sqlite3.Connection,
    *,
    name: str,
    purpose: str,
    ttl_seconds: int,
    metadata: dict[str, Any] | None = None,
) -> str:
    ensure_maintenance_table(con)
    now = _now()
    now_text = _to_sqlite(now)
    expires_at = _to_sqlite(now + timedelta(seconds=ttl_seconds))
    owner = f"{socket.gethostname()}:{uuid.uuid4().hex}"
    current = con.execute(
        "select owner, purpose, expires_at from maintenance_leases where name = ?",
        (name,),
    ).fetchone()
    if current is not None:
        current_expires_at = _parse_sqlite(current[2])
        if current_expires_at is not None and current_expires_at > now:
            raise RuntimeError(
                "maintenance lease is active: "
                f"name={name} owner={current[0]} purpose={current[1]} "
                f"expires_at={current[2]}"
            )
    con.execute(
        """
        insert into maintenance_leases
            (name, owner, purpose, acquired_at, heartbeat_at, expires_at, metadata_json)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(name) do update set
            owner = excluded.owner,
            purpose = excluded.purpose,
            acquired_at = excluded.acquired_at,
            heartbeat_at = excluded.heartbeat_at,
            expires_at = excluded.expires_at,
            metadata_json = excluded.metadata_json
        """,
        (
            name,
            owner,
            purpose,
            now_text,
            now_text,
            expires_at,
            json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return owner


def release_maintenance_lease(
    con: sqlite3.Connection,
    *,
    name: str,
    owner: str,
) -> None:
    con.execute(
        "delete from maintenance_leases where name = ? and owner = ?",
        (name, owner),
    )


@contextmanager
def maintenance_guard(
    metadata_db: Path | None,
    *,
    lease_name: str,
    purpose: str,
    ttl_seconds: int = 3600,
    allow_running_queue: bool = False,
    require_runtime_stopped: bool = False,
    min_free_bytes: int | None = None,
    free_space_path: Path | None = None,
    writable_paths: list[Path] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    if metadata_db is None:
        yield
        return
    con = sqlite3.connect(metadata_db, timeout=60)
    owner: str | None = None
    try:
        con.execute("PRAGMA busy_timeout=60000")
        if not allow_running_queue:
            running = active_vector_running_count(con)
            if running:
                raise RuntimeError(
                    f"refuse maintenance: vector_write_jobs has running={running}; "
                    "stop/recover the writer first, or pass --allow-running-queue for a verified stale state"
                )
        if require_runtime_stopped:
            processes = find_sag_runtime_processes()
            if processes:
                preview = "; ".join(
                    f"pid={item['pid']} cmd={item['command_line'][:180]}"
                    for item in processes[:5]
                )
                raise RuntimeError(
                    "refuse maintenance: SAG API/runtime process appears active: "
                    f"{preview}"
                )
        if min_free_bytes is not None:
            ensure_min_free_space(free_space_path or metadata_db, min_free_bytes=min_free_bytes)
        for path in writable_paths or []:
            ensure_directory_writable(path)
        with con:
            owner = acquire_maintenance_lease(
                con,
                name=lease_name,
                purpose=purpose,
                ttl_seconds=ttl_seconds,
                metadata=metadata,
            )
        yield
    finally:
        if owner is not None:
            with con:
                release_maintenance_lease(con, name=lease_name, owner=owner)
        con.close()
