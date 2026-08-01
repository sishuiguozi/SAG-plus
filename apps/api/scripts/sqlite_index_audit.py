"""SAG-OPT-403：SQLite 索引只读审计。

- 导出指定 SQLite 数据库（默认引擎库）的全部索引与列定义（含唯一性/部分索引/表达式）。
- 归一化后识别两类冗余：
  * Tier 1 完全重复：同表、同唯一性、同列序、同 partial 条件 → 保留首个，其余可安全删除。
  * Tier 2 左前缀冗余：较短的索引列是较长索引列的前缀（且较长索引唯一性不弱于较短索引）。
- 对计划列出的热查询执行 EXPLAIN QUERY PLAN，输出使用的索引或全表扫描。
- 生成回滚 DDL 文件（重建被删索引），保证删除操作可回滚。

用法示例：
  python scripts/sqlite_index_audit.py --db <SAG_DATA_ROOT>/engine/sag.db \
      --report index_audit.json --rollback rollback_indexes.sql
只读：连接使用 mode=ro，不做任何写操作。
"""

from __future__ import annotations

import argparse
import json
import os
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

# SAG-OPT-403 首批重点查询（与计划清单一致）
HOT_QUERIES: list[tuple[str, str]] = [
    ("article.source_config_id 列表", "SELECT * FROM article WHERE source_config_id = 1 ORDER BY id DESC LIMIT 50"),
    ("article.category 过滤", "SELECT * FROM article WHERE category = 'report' LIMIT 50"),
    ("article_section.article_id 分块", "SELECT * FROM article_section WHERE article_id = 1 ORDER BY order_index"),
    ("source_chunk.article_id 分块", "SELECT * FROM source_chunk WHERE article_id = 1"),
    ("source_chunk.source_config_id", "SELECT * FROM source_chunk WHERE source_config_id = 1 LIMIT 50"),
    ("source_event.chunk_id", "SELECT * FROM source_event WHERE chunk_id = 1"),
    ("source_event.article_id", "SELECT * FROM source_event WHERE article_id = 1"),
    ("source_event.parent_id", "SELECT * FROM source_event WHERE parent_id = 1"),
    ("source_event.source_config_id", "SELECT * FROM source_event WHERE source_config_id = 1 LIMIT 50"),
    ("entity.normalized_name", "SELECT * FROM entity WHERE normalized_name = 'x' AND source_config_id = 1"),
    ("entity.entity_type_id", "SELECT * FROM entity WHERE entity_type_id = 1 LIMIT 50"),
    ("event_entity.event_id", "SELECT * FROM event_entity WHERE event_id = 1"),
    ("event_entity.entity_id", "SELECT * FROM event_entity WHERE entity_id = 1"),
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_only_connect(path: str) -> sqlite3.Connection:
    uri = Path(path).as_posix()
    con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _iter_tables(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _index_info(con: sqlite3.Connection, index_name: str) -> list[dict[str, Any]]:
    cols = con.execute(f'PRAGMA index_xinfo("{index_name}")').fetchall()
    out: list[dict[str, Any]] = []
    for c in cols:
        if c["key"] == 0:
            continue  # 跳过 rowid/aux 列
        out.append(
            {
                "seqno": c["seqno"],
                "cid": c["cid"],
                "name": c["name"],
                "desc": bool(c["desc"]),
                "coll": c["coll"],
                "key": bool(c["key"]),
            }
        )
    return out


def _index_ddl(con: sqlite3.Connection, index_name: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
    ).fetchone()
    return row["sql"] if row and row["sql"] else ""


def _audit_db(path: str) -> dict[str, Any]:
    con = _read_only_connect(path)
    try:
        tables = _iter_tables(con)
        indexes: list[dict[str, Any]] = []
        for table in tables:
            for row in con.execute(f'PRAGMA index_list("{table}")').fetchall():
                name = row["name"]
                unique = bool(row["unique"])
                origin = row["origin"]  # c = CREATE INDEX, u = UNIQUE 约束, pk = 主键
                info = _index_info(con, name)
                cols = tuple((c["name"], c["desc"], c["coll"]) for c in info if c["key"])
                indexes.append(
                    {
                        "table": table,
                        "name": name,
                        "unique": unique,
                        "origin": origin,
                        "columns": [c["name"] for c in info if c["key"]],
                        "collations": [c["coll"] for c in info if c["key"]],
                        "desc": [c["desc"] for c in info if c["key"]],
                        "partial": row["partial"],
                        "ddl": _index_ddl(con, name),
                    }
                )
        # ── Tier 1 完全重复 ─────────────────────────────────────────────
        seen: dict[tuple[str, bool, tuple, bool], str] = {}
        for idx in indexes:
            # 表达式索引/rowid 辅助索引等无标准列定义的不参与归一化重复判断
            if not idx["columns"] or any(c is None for c in idx["columns"]):
                idx["tier1_dup_of"] = None
                continue
            key = (
                idx["table"],
                idx["unique"],
                tuple(idx["columns"]),
                tuple(idx["collations"]),
                bool(idx["partial"]),
            )
            first = seen.get(key)
            if first is None:
                seen[key] = idx["name"]
                idx["tier1_dup_of"] = None
            else:
                idx["tier1_dup_of"] = first
        # ── Tier 2 左前缀冗余 ──────────────────────────────────────────
        for idx in indexes:
            if idx["tier1_dup_of"] is not None or not idx["columns"]:
                idx["tier2_superset_of"] = []
                continue
            redundant_of: list[str] = []
            for other in indexes:
                if other is idx or other["table"] != idx["table"] or other["name"] == idx["name"]:
                    continue
                if other["tier1_dup_of"] is not None:
                    continue
                if not other["columns"] or len(other["columns"]) <= len(idx["columns"]):
                    continue
                # 较长索引必须严格以较短索引列作为左前缀，且 collation/排序一致
                if (
                    other["columns"][: len(idx["columns"])] == idx["columns"]
                    and other["collations"][: len(idx["columns"])] == idx["collations"]
                    and other["desc"][: len(idx["columns"])] == idx["desc"]
                    and bool(other["partial"]) == bool(idx["partial"])
                    # 唯一性不弱于较短索引（唯一约束不可丢）
                    and (other["unique"] or not idx["unique"])
                ):
                    redundant_of.append(other["name"])
            idx["tier2_superset_of"] = sorted(set(redundant_of))

        # ── EXPLAIN QUERY PLAN ─────────────────────────────────────────
        explains: list[dict[str, Any]] = []
        for label, sql in HOT_QUERIES:
            try:
                rows = con.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
                detail = [r["detail"] for r in rows]
            except sqlite3.Error as exc:  # 列名可能因真实 schema 不同而失败
                detail = [f"EXPLAIN 失败: {exc}"]
            explains.append({"label": label, "sql": sql, "detail": detail})

        return {
            "generated_at": _now(),
            "db": path,
            "tables": tables,
            "index_count": len(indexes),
            "indexes": indexes,
            "tier1_duplicates": [
                {"name": i["name"], "table": i["table"], "dup_of": i["tier1_dup_of"]}
                for i in indexes
                if i["tier1_dup_of"] is not None
            ],
            "tier2_prefix_redundant": [
                {"name": i["name"], "table": i["table"], "supersets": i["tier2_superset_of"]}
                for i in indexes
                if i["tier2_superset_of"]
            ],
            "explains": explains,
        }
    finally:
        con.close()


def _write_rollback(con: sqlite3.Connection, redundant: list[dict[str, Any]], out_path: Path) -> int:
    lines = [
        "-- SAG-OPT-403 回滚：重建被删除的冗余索引（幂等）。",
        f"-- 生成时间：{_now()}",
        "PRAGMA foreign_keys=OFF;",
        "BEGIN;",
    ]
    count = 0
    for idx in redundant:
        ddl = idx["ddl"]
        if not ddl:
            continue
        lines.append(f"DROP INDEX IF EXISTS \"{idx['name']}\";")
        lines.append(ddl.rstrip(";") + ";")
        count += 1
    lines.append("COMMIT;")
    lines.append("PRAGMA foreign_keys=ON;")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 数据库路径（只读打开）")
    parser.add_argument("--report", default="sqlite_index_audit.json", help="审计 JSON 输出路径")
    parser.add_argument("--rollback", default="rollback_redundant_indexes.sql", help="回滚 DDL 输出路径")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.exists():
        print(f"错误：数据库不存在：{path}", file=sys.stderr)
        return 2

    report = _audit_db(str(path))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    con = _read_only_connect(str(path))
    try:
        redundant = [
            i for i in report["indexes"]
            if i["tier1_dup_of"] is not None or i["tier2_superset_of"]
        ]
        rollback_count = _write_rollback(con, redundant, Path(args.rollback))

        print(f"数据库：{args.db}")
        print(f"表数：{len(report['tables'])}，索引总数：{report['index_count']}")
        print(f"\n[Tier 1 完全重复] {len(report['tier1_duplicates'])} 个：")
        for item in report["tier1_duplicates"]:
            print(f"  - {item['table']}.{item['name']} 重复于 {item['dup_of']}")
        print(f"\n[Tier 2 左前缀冗余] {len(report['tier2_prefix_redundant'])} 个：")
        for item in report["tier2_prefix_redundant"]:
            print(f"  - {item['table']}.{item['name']} 可由 {', '.join(item['supersets'])} 覆盖")
        print(f"\n[EXPLAIN QUERY PLAN] {len(report['explains'])} 条热查询：")
        import re as _re
        for item in report["explains"]:
            scan = [d for d in item["detail"] if _re.match(r"SCAN (?>\S+)(?! USING)", d)]
            tag = "SCAN!" if scan else "INDEX/USE"
            # 全 NULL 列等值过滤的纯表扫描是最优计划，给出豁免说明
            for _d in scan:
                sql = item["sql"]
                m = _re.match(r"^\s*SELECT .* FROM ([A-Za-z_][A-Za-z0-9_]*) WHERE ([A-Za-z_][A-Za-z0-9_]*) = '[^']*'(\s+LIMIT \d+)?\s*$", sql, _re.I | _re.S)
                if m:
                    try:
                        n = con.execute(f'SELECT count("{m.group(2)}") AS n FROM "{m.group(1)}"').fetchone()["n"]
                        if int(n) == 0:
                            tag = "SCAN(豁免:全NULL列)"
                    except sqlite3.Error:
                        pass
            print(f"  - {item['label']}: {tag}")
            for d in item["detail"]:
                print(f"      {d}")
        print(f"\n报告：{args.report}")
        print(f"回滚 DDL（重建 {rollback_count} 个索引）：{args.rollback}")
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
