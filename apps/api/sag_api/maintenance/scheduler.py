"""LanceDB 自动维护的调度与状态查询（SAG-OPT-803）。

复用 apps/api/scripts 中既有的维护逻辑（auto_maintenance / optimize /
cleanup / guard），本模块只负责：
- 读取设置页保存的维护计划（启用、频率、是否允许清理旧版本）；
- 应用启动早期判断“是否到期”，到期才执行；
- 设置页“立即维护”写入 pending 标记，下次启动强制执行；
- 向设置页提供状态（上次维护、下次到期、各表碎片/占用、任务计划命令）。

维护必须在 API 写入器未运行时执行（LanceDB 文件占用），因此执行点是
应用启动早期、引擎初始化之前；入库队列忙时由维护脚本自动拒绝（不改动）。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sag_api.maintenance.audit_sag_storage import _lancedb_summary
from sag_api.maintenance.auto_maintenance import run as run_auto_maintenance
from sag_api.maintenance.sag_maintenance_guard import find_sag_runtime_processes

log = logging.getLogger("sag.maintenance.scheduler")


def data_root_of(data_dir: str | Path) -> Path:
    """数据根目录 = engine 目录的父级（默认 E:/sag/.data）。"""
    return Path(data_dir).resolve().parent


def maintenance_root(data_dir: str | Path) -> Path:
    return data_root_of(data_dir) / "maintenance"


def state_file(data_dir: str | Path) -> Path:
    return maintenance_root(data_dir) / "auto-maintenance-state.json"


def pending_file(data_dir: str | Path) -> Path:
    return maintenance_root(data_dir) / "startup-pending.json"


def reports_dir(data_dir: str | Path) -> Path:
    return data_root_of(data_dir) / "reports"


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def last_success_at(data_dir: str | Path) -> datetime | None:
    state = _load_json(state_file(data_dir), {})
    raw = state.get("last_success_at") if isinstance(state, dict) else None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def has_pending(data_dir: str | Path) -> bool:
    return pending_file(data_dir).exists()


def request_startup_maintenance(data_dir: str | Path) -> Path:
    """设置页“立即维护”：写 pending 标记，下次启动时强制执行。"""
    target = pending_file(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"requested_at": datetime.now(UTC).isoformat(timespec="seconds")}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def clear_pending(data_dir: str | Path) -> None:
    try:
        pending_file(data_dir).unlink()
    except OSError:
        pass


def _sqlite_path_from_url(url: str, *, base: Path) -> Path | None:
    for marker in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(marker):
            raw = url[len(marker):]
            break
    else:
        return None
    if not raw or raw == ":memory:":
        return None
    path = Path(raw)
    return path if path.is_absolute() else base / path


def meta_db_path(settings: Any) -> Path:
    """从 database_url 解析元数据库磁盘路径（默认 {data_root}/sag.db）。"""
    base = Path(settings.data_dir).resolve()
    resolved = _sqlite_path_from_url(settings.database_url, base=base)
    if resolved is not None:
        return resolved
    return data_root_of(base) / "sag.db"


def maintenance_status(settings: Any) -> dict[str, Any]:
    """设置页状态：计划 + 上次/下次 + 各表占用与触发原因。"""
    data_dir = Path(settings.data_dir)
    last = last_success_at(data_dir)
    now = datetime.now(UTC)
    interval = timedelta(days=int(settings.lancedb_maintenance_interval_days or 7))
    next_due = (last + interval) if last is not None else None
    due = (
        has_pending(data_dir)
        or (
            bool(settings.lancedb_maintenance_enabled)
            and (last is None or now - last >= interval)
        )
    )

    summary = _lancedb_summary(data_dir / "lancedb")
    tables: dict[str, Any] = {}
    triggers: list[str] = []
    for name, t in sorted((summary.get("tables") or {}).items()):
        fragments = int(t.get("fragments") or 0)
        active = int(t.get("active_total_bytes") or 0)
        directory = int(t.get("directory_bytes") or 0)
        ratio = round(directory / active, 3) if active else 0.0
        reason = "ok"
        if fragments >= 500:
            reason = f"fragments={fragments} >= 500"
        elif ratio >= 2.5:
            reason = f"ratio={ratio} >= 2.5"
        elif int(t.get("latest_version") or 0) >= 500:
            reason = f"version_delta={t.get('latest_version')} >= 500"
        if reason != "ok":
            triggers.append(name)
        tables[name] = {
            "rows": t.get("rows"),
            "fragments": fragments,
            "directory_bytes": directory,
            "active_bytes": active,
            "ratio": ratio,
            "latest_version": t.get("latest_version"),
            "reason": reason,
        }

    active_processes = [
        {
            "pid": item["pid"],
            "name": item.get("name") or "",
            "command_line": item["command_line"],
        }
        for item in find_sag_runtime_processes()
    ]

    python = sys.executable or "python"
    scripts_entry = str(Path(__file__).resolve().parents[2] / "scripts" / "auto_maintenance.py")
    base_args = [
        f"--metadata-db {meta_db_path(settings).as_posix()}",
        f"--data-dir {data_dir.as_posix()}",
        f"--state {state_file(data_dir).as_posix()}",
        f"--reports-dir {reports_dir(data_dir).as_posix()}",
    ]
    if settings.lancedb_maintenance_delete_unverified:
        base_args.append("--delete-unverified")
    base_args.append("--force")
    task_command = f"{python} {scripts_entry} " + " ".join(base_args)

    # 打包环境：任务计划直接调用 sag-api.exe 的维护模式
    task_command_packaged = None
    if getattr(sys, "frozen", False):
        packaged_args = [
            f"--metadata-db {meta_db_path(settings).as_posix()}",
            f"--data-dir {data_dir.as_posix()}",
        ]
        if settings.lancedb_maintenance_delete_unverified:
            packaged_args.append("--delete-unverified")
        packaged_args.append("--force")
        task_command_packaged = f"{python} --maintenance-once " + " ".join(packaged_args)

    return {
        "enabled": bool(settings.lancedb_maintenance_enabled),
        "interval_days": int(settings.lancedb_maintenance_interval_days or 7),
        "delete_unverified": bool(settings.lancedb_maintenance_delete_unverified),
        "last_success_at": last.isoformat(timespec="seconds") if last else None,
        "next_due_at": next_due.isoformat(timespec="seconds") if next_due else None,
        "due_now": bool(due),
        "pending_restart": has_pending(data_dir),
        "active_processes": active_processes,
        "lancedb_dir": str(data_dir / "lancedb"),
        "tables": tables,
        "triggered_tables": triggers,
        "backup_hint": str(data_root_of(data_dir) / "backups"),
        "task_command": task_command,
        "task_command_packaged": task_command_packaged,
    }


def run_maintenance(settings: Any, *, force: bool = False) -> dict[str, Any]:
    """执行一次维护（复用 auto_maintenance.run）。返回执行摘要，不抛异常。"""
    data_dir = Path(settings.data_dir)
    args = [
        "--metadata-db", str(meta_db_path(settings)),
        "--data-dir", str(data_dir),
        "--state", str(state_file(data_dir)),
        "--reports-dir", str(reports_dir(data_dir)),
    ]
    if settings.lancedb_maintenance_delete_unverified:
        args.append("--delete-unverified")
    if force:
        args.append("--force")
    started = datetime.now(UTC)
    try:
        code = int(run_auto_maintenance(args))
        ok = code == 0
    except Exception as error:  # noqa: BLE001 - 维护失败不能阻塞 API 启动
        log.warning("启动维护失败：%s", error)
        return {
            "ok": False,
            "forced": force,
            "exit_code": -1,
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "error": str(error),
        }
    return {
        "ok": ok,
        "forced": force,
        "exit_code": code,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def run_startup_maintenance_if_due(settings: Any) -> dict[str, Any] | None:
    """应用启动早期调用：pending 或到期时执行维护；否则返回 None。

    - pending（设置页“立即维护”）优先，且强制执行；
    - 否则启用且距上次成功维护 >= 间隔才执行；
    - 入库队列忙 / 磁盘不足 / 进程检测失败时由维护脚本自身拒绝，
      这里只记录结果，绝不让维护失败阻塞 API 启动。
    """
    data_dir = Path(settings.data_dir)
    pending = has_pending(data_dir)
    if pending:
        clear_pending(data_dir)
    elif not settings.lancedb_maintenance_enabled:
        return None
    else:
        last = last_success_at(data_dir)
        if last is not None:
            interval = timedelta(days=int(settings.lancedb_maintenance_interval_days or 7))
            if datetime.now(UTC) - last < interval:
                return None
    result = run_maintenance(settings, force=pending)
    if not result.get("ok"):
        log.warning("启动维护未成功：%s", result)
    return result
