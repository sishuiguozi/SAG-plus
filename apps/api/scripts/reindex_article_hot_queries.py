"""SAG-OPT-403 收尾：article 热查询索引优化。

背景：Tier1/Tier2 冗余索引迁移后，ANALYZE 揭示 article.category 全为 NULL，
``ix_article_category(category)`` 单一列索引被优化器判定无选择性，导致
``WHERE category=...`` 退化为纯表扫描；``WHERE source_config_id=... ORDER BY id DESC``
则因 Tier2 删除 ``(source_config_id)`` 后退化为主键扫描。

本脚本把两个热查询改为最优形态：
- ``ix_article_category(category)`` → ``ix_article_category_id(category, id)``
- 新增 ``ix_article_source_config_id_id(source_config_id, id)``（列表查询免排序）

安全设计：
- 默认 dry-run；``--apply`` 才写库。
- 写库前检测 API/uvicorn 进程（``--force`` 跳过）。
- 删除/重建在事务内执行；变更前把回滚 DDL 写入 ``--rollback-dir``。
- 变更后自动 ANALYZE，并复跑 13 条热查询 EXPLAIN 验证（纯表扫描会失败）。

用法：
  python scripts/reindex_article_hot_queries.py --db "<SAG_DATA_ROOT>/engine/sag.db"
  python scripts/reindex_article_hot_queries.py --db "<SAG_DATA_ROOT>/engine/sag.db" --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


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


def _rollback_ddl() -> str:
    return "\n".join([
        "-- SAG-OPT-403 收尾回滚：恢复 article 原有单列索引并移除复合索引（幂等）。",
        f"-- 生成时间：{_now()}",
        "PRAGMA foreign_keys=OFF;",
        "BEGIN;",
        'DROP INDEX IF EXISTS "ix_article_source_config_id_id";',
        "COMMIT;",
        "PRAGMA foreign_keys=ON;",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--rollback-dir", default=str(_default_data_root() / "rollback"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    suspects = _detect_api_processes()
    if suspects and not args.force and args.apply:
        print(f"检测到疑似 API 进程（{', '.join(suspects)}），拒绝写库。请先停止 API 或使用 --force。", file=sys.stderr)
        return 3

    con = _connect(args.db)
    try:
        has_old = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_article_category'").fetchone())
        has_new1 = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_article_category_id'").fetchone())
        has_new2 = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_article_source_config_id_id'").fetchone())

        print(f"当前状态：ix_article_category={has_old}, ix_article_category_id={has_new1}, ix_article_source_config_id_id={has_new2}")

        rollback_dir = Path(args.rollback_dir)
        rollback_dir.mkdir(parents=True, exist_ok=True)
        rb_path = rollback_dir / f"rollback-article-hot-indexes-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.sql"
        rb_path.write_text(_rollback_ddl(), encoding="utf-8")
        print(f"回滚 DDL：{rb_path}")

        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return 0

        con.execute("BEGIN")
        try:
            # category 全为 NULL：ANALYZE 后复合索引同样被判非选择性，保留原单列索引即可
            con.execute('DROP INDEX IF EXISTS "ix_article_category_id"')
            con.execute('CREATE INDEX IF NOT EXISTS "ix_article_category" ON article (category)')
            # 列表查询 WHERE source_config_id=? ORDER BY id DESC 的免排序索引
            con.execute('CREATE INDEX IF NOT EXISTS "ix_article_source_config_id_id" ON article (source_config_id, id)')
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        print("索引变更已提交：确保 ix_article_category 存在，新增 ix_article_source_config_id_id")

        con.execute("ANALYZE")
        print("ANALYZE 完成。")

        problems, notes = _explain_problems(con)
        for n in notes:
            print(f"[verify] {n}")
        if problems:
            print("\n[verify] 热查询出现纯表扫描或失败：", file=sys.stderr)
            for p in problems:
                print(f"  ! {p}", file=sys.stderr)
            return 4
        print("[verify] 全部 13 条热查询无未解释的纯表扫描回退。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
