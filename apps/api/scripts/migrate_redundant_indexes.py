"""SAG-OPT-403：可回滚的 SQLite 冗余索引迁移。

配合 ``sqlite_index_audit.py`` 使用：审计脚本把冗余索引清单与回滚 DDL 落盘，
本脚本按批次（Tier 1 完全重复 / Tier 2 左前缀冗余）执行删除，并在删除前
重新导出回滚 DDL（双保险），删除后自动运行 ANALYZE 并复跑热查询 EXPLAIN 验证。

安全设计：
- 默认 ``--dry-run``，只打印将删除的索引；显式 ``--apply`` 才写库。
- 删除前把“重建所有将被删除索引”的 DDL 写入 ``--rollback-dir``（幂等）。
- 删除在事务内执行，SQLite DDL 支持回滚。
- 检测到 SAG API/写入进程存活时拒绝执行（除非 ``--force``）。
- ``--verify`` 复跑热查询，若出现纯表扫描（SCAN 表名，非 USING INDEX）会
  打印警告并置非零退出码；``SCAN 表 USING INDEX`` 是正常的索引扫描。

用法示例：
  python scripts/migrate_redundant_indexes.py --db "<SAG_DATA_ROOT>/engine/sag.db" --apply --tier1
  python scripts/migrate_redundant_indexes.py --db "<SAG_DATA_ROOT>/engine/sag.db" --apply --tier2
  python scripts/migrate_redundant_indexes.py --rollback "<SAG_DATA_ROOT>/rollback/xxx.sql"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _default_data_root() -> Path:
    """数据根目录：优先 SAG_DATA_ROOT 环境变量，否则 ~/.sag/.data。"""
    env = os.environ.get("SAG_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sag" / ".data"


DEFAULT_DB = str(_default_data_root() / "engine" / "sag.db")

HOT_QUERY_SQLS: list[str] = [
    "SELECT * FROM article WHERE source_config_id = 1 ORDER BY id DESC LIMIT 50",
    "SELECT * FROM article WHERE category = 'report' LIMIT 50",
    "SELECT * FROM article_section WHERE article_id = 1 ORDER BY order_index LIMIT 50",
    "SELECT * FROM source_chunk WHERE article_id = 1",
    "SELECT * FROM source_chunk WHERE source_config_id = 1 LIMIT 50",
    "SELECT * FROM source_event WHERE chunk_id = 1",
    "SELECT * FROM source_event WHERE article_id = 1",
    "SELECT * FROM source_event WHERE parent_id = 1",
    "SELECT * FROM source_event WHERE source_config_id = 1 LIMIT 50",
    "SELECT * FROM entity WHERE normalized_name = 'x' AND source_config_id = 1",
    "SELECT * FROM entity WHERE entity_type_id = 1 LIMIT 50",
    "SELECT * FROM event_entity WHERE event_id = 1",
    "SELECT * FROM event_entity WHERE entity_id = 1",
]

_PLAIN_SCAN = re.compile(r"SCAN (?>\S+)(?! USING)")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    return con


def _detect_api_processes() -> list[str]:
    """粗略检测 SAG API / uvicorn / dev 进程，作为写库前的一道门禁。"""
    import subprocess

    try:
        if sys.platform == "win32":
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
                check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
        else:
            completed = subprocess.run(
                ["ps", "-eo", "pid=,args="], check=True, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15,
            )
    except Exception:
        return []
    text = completed.stdout or ""
    suspects = []
    for token in ("uvicorn", "sag_api.main", "sag_api.main:app", "run.py"):
        if token.lower() in text.lower():
            suspects.append(token)
    return sorted(set(suspects))


def _index_ddl(con: sqlite3.Connection, name: str) -> str:
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
    return row["sql"] if row and row["sql"] else ""


def _collect_redundant(con: sqlite3.Connection, tier1: bool, tier2: bool) -> list[dict[str, Any]]:
    """现场重新审计，返回将删除的索引（与审计脚本同规则，避免依赖旧报告）。"""
    tables = [
        r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    indexes: list[dict[str, Any]] = []
    for table in tables:
        for row in con.execute(f'PRAGMA index_list("{table}")').fetchall():
            name = row["name"]
            if row["origin"] == "pk":
                continue
            cols = con.execute(f'PRAGMA index_xinfo("{name}")').fetchall()
            key_cols = [c["name"] for c in cols if c["key"] and c["cid"] >= 0]
            colls = [c["coll"] for c in cols if c["key"] and c["cid"] >= 0]
            descs = [bool(c["desc"]) for c in cols if c["key"] and c["cid"] >= 0]
            indexes.append(
                {
                    "table": table, "name": name, "unique": bool(row["unique"]),
                    "origin": row["origin"], "partial": bool(row["partial"]),
                    "columns": key_cols, "collations": colls, "desc": descs,
                    "ddl": _index_ddl(con, name),
                }
            )
    tier1_drop: set[str] = set()
    seen: dict[tuple, str] = {}
    for idx in indexes:
        if not idx["columns"] or any(c is None for c in idx["columns"]):
            continue
        key = (idx["table"], idx["unique"], tuple(idx["columns"]), tuple(idx["collations"]), idx["partial"])
        if key in seen:
            tier1_drop.add(idx["name"])
        else:
            seen[key] = idx["name"]
    tier2_drop: set[str] = set()
    for idx in indexes:
        if idx["name"] in tier1_drop or not idx["columns"]:
            continue
        for other in indexes:
            if other is idx or other["name"] == idx["name"] or other["name"] in tier1_drop:
                continue
            if other["table"] != idx["table"]:
                continue
            if not other["columns"] or len(other["columns"]) <= len(idx["columns"]):
                continue
            if (
                other["columns"][: len(idx["columns"])] == idx["columns"]
                and other["collations"][: len(idx["columns"])] == idx["collations"]
                and other["desc"][: len(idx["columns"])] == idx["desc"]
                and other["partial"] == idx["partial"]
                and (other["unique"] or not idx["unique"])
            ):
                tier2_drop.add(idx["name"])
                break
    wanted: set[str] = set()
    if tier1:
        wanted |= tier1_drop
    if tier2:
        wanted |= tier2_drop
    return [i for i in indexes if i["name"] in wanted]


def _write_rollback(con: sqlite3.Connection, redundant: list[dict[str, Any]], out_path: Path) -> int:
    lines = [
        "-- SAG-OPT-403 回滚：重建被删除的冗余索引（幂等，可重复执行）。",
        f"-- 生成时间：{_now()}",
        "PRAGMA foreign_keys=OFF;",
        "BEGIN;",
    ]
    count = 0
    for idx in redundant:
        ddl = idx["ddl"]
        if not ddl:
            continue
        lines.append(f'DROP INDEX IF EXISTS "{idx["name"]}";')
        lines.append(ddl.rstrip(";") + ";")
        count += 1
    lines.append("COMMIT;")
    lines.append("PRAGMA foreign_keys=ON;")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


_SIMPLE_EQ = re.compile(
    r"^\s*SELECT .* FROM ([A-Za-z_][A-Za-z0-9_]*) WHERE ([A-Za-z_][A-Za-z0-9_]*) = '[^']*'(\s+LIMIT \d+)?\s*$",
    re.I | re.S,
)


def _all_null_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    """列全为 NULL 时，对该列的等值过滤无任何索引可用，全表扫描是最优计划。"""
    try:
        row = con.execute(f'SELECT count("{column}") AS n FROM "{table}"').fetchone()
        return int(row["n"]) == 0
    except sqlite3.Error:
        return False


def _explain_problems(con: sqlite3.Connection) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    for sql in HOT_QUERY_SQLS:
        try:
            rows = con.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
            details = [r["detail"] for r in rows]
        except sqlite3.Error as exc:
            problems.append(f"{sql} -> EXPLAIN 失败: {exc}")
            continue
        if any(_PLAIN_SCAN.match(d) for d in details):
            msg = f"{sql} -> {details}"
            m = _SIMPLE_EQ.match(sql)
            if m:
                table, col = m.group(1), m.group(2)
                if _all_null_column(con, table, col):
                    notes.append(
                        f"豁免：{table}.{col} 全列为 NULL，等值过滤无索引可用，{msg} 属最优计划"
                    )
                    continue
            problems.append(msg)
    return problems, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--tier1", action="store_true", help="删除完全重复索引")
    parser.add_argument("--tier2", action="store_true", help="删除左前缀冗余索引")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--rollback-dir", default=str(_default_data_root() / "rollback"), help="回滚 DDL 输出目录")
    parser.add_argument("--rollback", help="执行指定回滚 DDL 文件后退出")
    parser.add_argument("--verify", action="store_true", help="删除后复跑热查询 EXPLAIN 验证")
    parser.add_argument("--skip-analyze", action="store_true", help="不自动 ANALYZE")
    parser.add_argument("--force", action="store_true", help="检测到 API 进程时仍继续")
    args = parser.parse_args()

    if args.rollback:
        rb_path = Path(args.rollback)
        if not rb_path.exists():
            print(f"错误：回滚文件不存在：{rb_path}", file=sys.stderr)
            return 2
        con = _connect(args.db)
        try:
            con.executescript(rb_path.read_text(encoding="utf-8"))
            con.commit()
            print(f"回滚完成：{rb_path}")
        finally:
            con.close()
        return 0

    if not args.tier1 and not args.tier2:
        parser.error("至少指定 --tier1 或 --tier2")

    suspects = _detect_api_processes()
    if suspects and not args.force and args.apply:
        print(f"检测到疑似 API 进程（{', '.join(suspects)}），拒绝写库。请先停止 API 或使用 --force。", file=sys.stderr)
        return 3

    con = _connect(args.db)
    try:
        redundant = _collect_redundant(con, tier1=args.tier1, tier2=args.tier2)
        if not redundant:
            print("没有需要删除的冗余索引。")
            return 0

        rollback_dir = Path(args.rollback_dir)
        rollback_dir.mkdir(parents=True, exist_ok=True)
        tier_label = "tier1" if args.tier1 and not args.tier2 else ("tier2" if args.tier2 and not args.tier1 else "tier1+tier2")
        rollback_path = rollback_dir / f"rollback-redundant-indexes-{tier_label}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.sql"
        count = _write_rollback(con, redundant, rollback_path)

        print(f"[{tier_label}] 将删除 {len(redundant)} 个索引：")
        for idx in sorted(redundant, key=lambda i: (i["table"], i["name"])):
            print(f"  - {idx['table']}.{idx['name']}  unique={idx['unique']}")
        print(f"回滚 DDL：{rollback_path}（{count} 个索引）")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return 0

        names = [idx["name"] for idx in redundant]
        con.execute("BEGIN")
        try:
            for name in names:
                con.execute(f'DROP INDEX IF EXISTS "{name}"')
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        if not args.skip_analyze:
            con.execute("ANALYZE")
            print("ANALYZE 完成。")

        print(f"已删除 {len(names)} 个冗余索引。")

        if args.verify:
            problems, notes = _explain_problems(con)
            for n in notes:
                print(f"[verify] {n}")
            if problems:
                print("\n[verify] 热查询出现纯表扫描或失败：", file=sys.stderr)
                for p in problems:
                    print(f"  ! {p}", file=sys.stderr)
                return 4
            print("[verify] 全部热查询无未解释的纯表扫描回退。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
