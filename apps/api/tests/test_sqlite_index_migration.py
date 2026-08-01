"""SAG-OPT-403：冗余索引迁移检测逻辑测试。

覆盖：
- Tier1 完全重复仅限同表同列；跨表同列不算重复。
- Tier2 左前缀冗余仅限同表；唯一性约束不可丢；partial 条件必须一致。
- 纯表扫描正则不误报 ``SCAN 表 USING INDEX``。
- 全 NULL 列的等值过滤豁免逻辑。
"""

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


def _connect(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "idx.db")
    con.row_factory = sqlite3.Row
    return con


def test_tier1_duplicate_same_table_only(tmp_path: Path):
    migrate = _load_script_module("migrate_redundant_indexes_t1", "scripts/migrate_redundant_indexes.py")
    con = _connect(tmp_path)
    try:
        con.executescript(
            """
            CREATE TABLE t1 (a TEXT, b TEXT);
            CREATE TABLE t2 (a TEXT, b TEXT);
            CREATE INDEX ix_t1_ab ON t1 (a, b);
            CREATE INDEX ix_t1_ab_dup ON t1 (a, b);   -- 同表重复
            CREATE INDEX ix_t2_ab ON t2 (a, b);       -- 跨表同列，不是重复
            """
        )
        dropped = migrate._collect_redundant(con, tier1=True, tier2=False)
    finally:
        con.close()
    # PRAGMA index_list 按创建倒序返回，被删的是"两个重复之一"，跨表同列不参与
    assert len(dropped) == 1
    assert dropped[0]["table"] == "t1"
    assert dropped[0]["name"] in ("ix_t1_ab", "ix_t1_ab_dup")
    assert "ix_t2_ab" not in [i["name"] for i in dropped]


def test_tier2_prefix_same_table_only_and_unique_respected(tmp_path: Path):
    migrate = _load_script_module("migrate_redundant_indexes_t2", "scripts/migrate_redundant_indexes.py")
    con = _connect(tmp_path)
    try:
        con.executescript(
            """
            CREATE TABLE t1 (a TEXT, b TEXT, c TEXT);
            CREATE TABLE t2 (a TEXT, b TEXT, c TEXT);
            CREATE INDEX ix_t1_ab ON t1 (a, b);       -- 被 t1.abc 覆盖
            CREATE INDEX ix_t1_abc ON t1 (a, b, c);
            CREATE INDEX ix_t2_ab ON t2 (a, b);       -- 被 t2.abc 覆盖
            CREATE INDEX ix_t2_abc ON t2 (a, b, c);
            CREATE TABLE t3 (a TEXT, b TEXT);
            CREATE UNIQUE INDEX uk_t3_ab ON t3 (a, b);
            CREATE INDEX ix_t3_a ON t3 (a);           -- 非唯一较短，可被唯一较长覆盖
            CREATE TABLE t4 (a TEXT, b TEXT);
            CREATE UNIQUE INDEX uk_t4_a ON t4 (a);    -- 唯一较短，不能被非唯一较长覆盖
            CREATE INDEX ix_t4_ab ON t4 (a, b);
            """
        )
        dropped = migrate._collect_redundant(con, tier1=False, tier2=True)
    finally:
        con.close()
    names = sorted(i["name"] for i in dropped)
    # ix_t1_ab 与 ix_t2_ab 是同表前缀冗余；ix_t3_a 可被唯一索引覆盖；uk_t4_a 必须保留
    assert names == ["ix_t1_ab", "ix_t2_ab", "ix_t3_a"]


def test_tier2_partial_condition_must_match(tmp_path: Path):
    migrate = _load_script_module("migrate_redundant_indexes_partial", "scripts/migrate_redundant_indexes.py")
    con = _connect(tmp_path)
    try:
        con.executescript(
            """
            CREATE TABLE t5 (a TEXT, b TEXT);
            CREATE INDEX ix_t5_ab ON t5 (a, b) WHERE a IS NOT NULL;
            CREATE INDEX ix_t5_a ON t5 (a) WHERE a IS NOT NULL;   -- partial 一致 → 覆盖
            CREATE INDEX ix_t5_a_nopart ON t5 (a);                -- partial 不同 → 不覆盖
            """
        )
        dropped = migrate._collect_redundant(con, tier1=False, tier2=True)
    finally:
        con.close()
    names = sorted(i["name"] for i in dropped)
    assert names == ["ix_t5_a"]


def test_plain_scan_regex_does_not_flag_index_scan(tmp_path: Path):
    migrate = _load_script_module("migrate_redundant_indexes_regex", "scripts/migrate_redundant_indexes.py")
    assert migrate._PLAIN_SCAN.match("SCAN article") is not None
    assert migrate._PLAIN_SCAN.match("SCAN source_chunk") is not None
    # 索引扫描不是纯表扫描
    assert migrate._PLAIN_SCAN.match("SCAN article USING INDEX sqlite_autoindex_article_1") is None
    assert migrate._PLAIN_SCAN.match("SEARCH article USING INDEX ix_article (a=?)") is None


def test_all_null_column_exemption(tmp_path: Path):
    migrate = _load_script_module("migrate_redundant_indexes_null", "scripts/migrate_redundant_indexes.py")
    con = _connect(tmp_path)
    try:
        con.executescript(
            """
            CREATE TABLE article (id TEXT PRIMARY KEY, category TEXT);
            INSERT INTO article (id, category) VALUES ('a1', NULL), ('a2', NULL);
            """
        )
        assert migrate._all_null_column(con, "article", "category") is True
        assert migrate._all_null_column(con, "article", "id") is False
        m = migrate._SIMPLE_EQ.match("SELECT * FROM article WHERE category = 'report' LIMIT 50")
        assert m is not None and m.group(1) == "article" and m.group(2) == "category"
        # 带 ORDER BY 的查询不属于单列等值形态，不可豁免
        m2 = migrate._SIMPLE_EQ.match(
            "SELECT * FROM article WHERE source_config_id = 1 ORDER BY id DESC LIMIT 50"
        )
        assert m2 is None
    finally:
        con.close()
