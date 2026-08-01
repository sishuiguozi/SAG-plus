"""SAG-OPT-803：LanceDB 自动维护调度器。

触发条件（任一满足即进入维护，--force 强制）：
- 单表 fragment 数 >= --min-fragments（默认 500）
- 单表版本增量（距上次成功维护）>= --min-version-delta（默认 500）
- 单表目录占用 / 有效字节 >= --max-ratio（默认 2.5）
- 距上次成功维护 >= --min-interval-hours（默认 24）且系统空闲

执行（在 ``lancedb-maintenance`` 独占租约 + 队列空闲 + 运行进程检测 + 磁盘门禁下）：
- optimize：压缩碎片并裁剪旧版本（``--delete-unverified`` 仅在确认已有可打开备份后显式传入）
- cleanup_old_versions：低负载窗口清理旧版本（同上）

状态与报告：
- ``--state`` 记录上次成功维护时间与每表版本/行数；中断后重新评估即可继续。
- 每次维护写 ``--reports-dir/auto-maintenance-<ts>.json``，保留最近 30 份。

用法：
  python scripts/auto_maintenance.py --metadata-db E:/sag/.data/sag.db --data-dir E:/sag/.data/engine
  # 确认备份可打开后再允许删除旧版本：
  python scripts/auto_maintenance.py ... --delete-unverified
  # 只评估不执行：
  python scripts/auto_maintenance.py ... --noop
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from audit_sag_storage import _lancedb_summary
from sag_maintenance_guard import maintenance_guard

STATE_DEFAULT = "E:/sag/.data/maintenance/auto-maintenance-state.json"
REPORTS_DEFAULT = "E:/sag/.data/reports"
KEEP_REPORTS = 30

_VECTOR_ACTIVE = ("queued", "running", "writing", "retry")
_JOB_ACTIVE = ("RUNNING", "EXTRACTING", "PENDING", "QUEUED")


def _now() -> datetime:
    return datetime.now(UTC)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tables": {}, "last_success_at": None, "last_report": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tables": {}, "last_success_at": None, "last_report": None}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _queue_idle(meta_db: Path) -> tuple[bool, dict[str, int]]:
    counts: dict[str, int] = {}
    if not meta_db.exists():
        return True, counts
    try:
        con = sqlite3.connect(f"file:{meta_db.as_posix()}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            has_vec = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_write_jobs'"
            ).fetchone()
            if has_vec:
                for r in con.execute(
                    "SELECT status, count(*) n FROM vector_write_jobs "
                    "WHERE status IN (%s) GROUP BY status" % ",".join("?" * len(_VECTOR_ACTIVE)),
                    _VECTOR_ACTIVE,
                ).fetchall():
                    counts[f"vector:{r['status']}"] = r["n"]
            has_jobs = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if has_jobs:
                for r in con.execute(
                    "SELECT status, count(*) n FROM jobs WHERE status IN (%s) GROUP BY status"
                    % ",".join("?" * len(_JOB_ACTIVE)),
                    _JOB_ACTIVE,
                ).fetchall():
                    counts[f"job:{r['status']}"] = r["n"]
        finally:
            con.close()
    except sqlite3.Error as error:
        counts["error"] = str(error)
    return (not counts), counts


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _evaluate_triggers(
    summary: dict[str, Any],
    state: dict[str, Any],
    *,
    min_fragments: int,
    min_version_delta: int,
    max_ratio: float,
    force: bool,
) -> list[dict[str, Any]]:
    """按碎片/版本增量/占用比/force 计算需要维护的表。"""
    triggers: list[dict[str, Any]] = []
    for table_name, t in sorted(summary.get("tables", {}).items()):
        fragments = int(t.get("fragments") or 0)
        active = int(t.get("active_total_bytes") or 0)
        dir_bytes = int(t.get("directory_bytes") or 0)
        ratio = (dir_bytes / active) if active else 0.0
        last_version = int(t.get("latest_version") or 0)
        prev = int(state.get("tables", {}).get(table_name, {}).get("latest_version") or 0)
        version_delta = (last_version - prev) if prev else last_version
        entry = {
            "table": table_name, "rows": t.get("rows"), "fragments": fragments,
            "dir_bytes": dir_bytes, "active_bytes": active, "ratio": round(ratio, 3),
            "latest_version": last_version, "version_delta": version_delta,
        }
        if fragments >= min_fragments:
            entry["reason"] = f"fragments={fragments} >= {min_fragments}"
        elif version_delta >= min_version_delta:
            entry["reason"] = f"version_delta={version_delta} >= {min_version_delta}"
        elif ratio >= max_ratio:
            entry["reason"] = f"ratio={ratio:.2f} >= {max_ratio}"
        elif force:
            entry["reason"] = "forced"
        if entry.get("reason"):
            triggers.append(entry)
    return triggers


def _run_script(script: str, args: list[str]) -> int:
    scripts_dir = Path(__file__).resolve().parent
    cmd = [sys.executable, str(scripts_dir / script), *args]
    print(f"[auto-maintenance] exec: {' '.join(cmd)}")
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-db", required=True, help="SAG metadata SQLite 路径")
    parser.add_argument("--data-dir", required=True, help="引擎数据目录（含 lancedb 与 sag.db）")
    parser.add_argument("--reports-dir", default=REPORTS_DEFAULT)
    parser.add_argument("--state", default=STATE_DEFAULT)
    parser.add_argument("--min-fragments", type=int, default=500)
    parser.add_argument("--min-version-delta", type=int, default=500)
    parser.add_argument("--max-ratio", type=float, default=2.5)
    parser.add_argument("--min-interval-hours", type=float, default=24.0)
    parser.add_argument("--older-than-seconds", type=int, default=7 * 24 * 3600)
    parser.add_argument("--min-free-gb", type=float, default=30.0)
    parser.add_argument("--delete-unverified", action="store_true",
                        help="确认已有可打开备份后，允许清理未验证的旧版本")
    parser.add_argument("--force", action="store_true", help="无视触发条件强制执行")
    parser.add_argument("--noop", action="store_true", help="只评估不执行")
    args = parser.parse_args()

    meta_db = Path(args.metadata_db)
    data_dir = Path(args.data_dir)
    state = _load_state(Path(args.state))
    idle, queue_counts = _queue_idle(meta_db)
    now = _now()

    summary = _lancedb_summary(data_dir / "lancedb")
    triggers = _evaluate_triggers(
        summary, state,
        min_fragments=args.min_fragments,
        min_version_delta=args.min_version_delta,
        max_ratio=args.max_ratio,
        force=args.force,
    )

    last_run = _parse_dt(state.get("last_success_at"))
    time_trigger = (
        last_run is not None
        and (now - last_run).total_seconds() >= args.min_interval_hours * 3600
        and idle
    ) or (last_run is None and idle and not args.noop)
    if time_trigger and not triggers:
        print("[auto-maintenance] 距离上次成功维护超过阈值且系统空闲，执行全表维护")
        triggers = [
            {"table": n, **{k: t.get(k) for k in ("rows", "fragments", "dir_bytes",
                                                  "active_bytes", "latest_version")},
             "reason": "idle window", "ratio": 0.0, "version_delta": 0}
            for n, t in sorted(summary.get("tables", {}).items())
        ]

    print(f"[auto-maintenance] idle={idle} queue={queue_counts}")
    print(f"[auto-maintenance] 触发表：{[t['table'] for t in triggers] or '无'}")

    if args.noop or not triggers or not idle:
        action = "evaluated_only" if args.noop else ("deferred_queue_busy" if not idle else "no_maintenance_needed")
        report_eval = {
            "generated_at": now.isoformat(timespec="seconds"),
            "idle": idle, "queue_counts": queue_counts,
            "triggers": triggers,
            "action": action,
        }
        Path(args.reports_dir).mkdir(parents=True, exist_ok=True)
        (Path(args.reports_dir) / "auto-maintenance-eval.json").write_text(
            json.dumps(report_eval, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report_eval, ensure_ascii=False, indent=2))
        return 0

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    # 调度器级互斥（子脚本各自持 lancedb-maintenance 租约串行执行）
    with maintenance_guard(
        meta_db, lease_name="auto-maintenance-scheduler", purpose="auto_maintenance_scheduler",
        ttl_seconds=4 * 3600, allow_running_queue=False, require_runtime_stopped=True,
        min_free_bytes=int(args.min_free_gb * 1024**3), free_space_path=data_dir,
        writable_paths=[reports_dir, data_dir / "maintenance"],
    ):
        step_results: list[dict[str, Any]] = []
        ok_all = True
        for entry in triggers:
            table = entry["table"]
            base = [
                "--data-dir", str(data_dir), "--table", table,
                "--metadata-db", str(meta_db), "--check-runtime-processes",
                "--min-free-gb", str(args.min_free_gb),
            ]
            if args.delete_unverified:
                base.append("--delete-unverified")
            rc1 = _run_script("optimize_lancedb_table.py", [
                *base, "--report-dir", str(reports_dir),
                "--cleanup-older-than-seconds", str(args.older_than_seconds),
            ])
            rc2 = _run_script("cleanup_lancedb_old_versions.py", [
                *base, "--backup-root", str(reports_dir),
                "--older-than-seconds", str(args.older_than_seconds),
            ])
            ok = rc1 == 0 and rc2 == 0
            ok_all = ok_all and ok
            step_results.append({"table": table, "optimize_rc": rc1, "cleanup_rc": rc2, "ok": ok})

        # 刷新状态与报告
        fresh = _lancedb_summary(data_dir / "lancedb")
        tables_state: dict[str, Any] = {}
        for name, t in sorted(fresh.get("tables", {}).items()):
            tables_state[name] = {
                "latest_version": int(t.get("latest_version") or 0),
                "rows": t.get("rows"),
                "fragments": int(t.get("fragments") or 0),
            }
        report = {
            "generated_at": now.isoformat(timespec="seconds"),
            "ok": ok_all,
            "idle": idle,
            "steps": step_results,
            "delete_unverified": args.delete_unverified,
            "tables_after": tables_state,
        }
        report_path = reports_dir / f"auto-maintenance-{now.strftime('%Y%m%d-%H%M%S')}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if ok_all:
            state["last_success_at"] = now.isoformat(timespec="seconds")
            state["last_report"] = str(report_path)
        state["tables"] = tables_state
        _save_state(Path(args.state), state)
        # 保留最近 KEEP_REPORTS 份报告
        old_reports = sorted(reports_dir.glob("auto-maintenance-*.json"))
        for stale in old_reports[:-KEEP_REPORTS]:
            try:
                stale.unlink()
            except OSError:
                pass
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
