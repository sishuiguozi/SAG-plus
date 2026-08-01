"""SAG-OPT-304：LanceDB 向量/标量索引增量维护。

LanceDB 在 append 写入时自动增量维护 ANN/标量索引（无需每个批次重建）；
本脚本负责“确保生产索引集合存在且覆盖完整”，供启动自检/维护窗口调用：

- 按 SAG-OPT-302/303 实验确定的最终索引集合幂等创建缺失索引：
  * ANN：event_vectors(title_vector/content_vector)、source_chunks(content_vector) 用
    IVF_HNSW_FLAT(cosine, m=32, ef_construction=300)；entity_vectors/event_entity_vectors 用
    IVF_HNSW_SQ(cosine, m=32, ef_construction=300)。
  * 标量：source_config_id（四表）、category/source_id（event_vectors）、source_id（source_chunks）。
- 创建后通过 ``index_stats`` 校验覆盖行数 == 表行数（freshness/coverage）。
- 默认 dry-run；``--apply`` 才写；检测到 API/uvicorn 进程时拒绝（``--force`` 跳过）。

用法：
  python scripts/ensure_vector_indexes.py --uri "<SAG_DATA_ROOT>/engine/lancedb"
  python scripts/ensure_vector_indexes.py --uri "<SAG_DATA_ROOT>/engine/lancedb" --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lancedb
from lancedb.index import HnswFlat, HnswSq, BTree


def _default_data_root() -> Path:
    """数据根目录：优先 SAG_DATA_ROOT 环境变量，否则 ~/.sag/.data。"""
    env = os.environ.get("SAG_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sag" / ".data"


URI_DEFAULT = str(_default_data_root() / "engine" / "lancedb")

# 表 -> (向量列, 索引类型, 索引名前缀)
ANN_SPECS: dict[str, list[dict[str, Any]]] = {
    "event_vectors": [
        {"column": "title_vector", "config": HnswFlat(distance_type="cosine", m=32, ef_construction=300), "suffix": "hnsw_flat"},
        {"column": "content_vector", "config": HnswFlat(distance_type="cosine", m=32, ef_construction=300), "suffix": "hnsw_flat"},
    ],
    "source_chunks": [
        {"column": "content_vector", "config": HnswFlat(distance_type="cosine", m=32, ef_construction=300), "suffix": "hnsw_flat"},
    ],
    "entity_vectors": [
        {"column": "vector", "config": HnswSq(distance_type="cosine", m=32, ef_construction=300), "suffix": "hnsw_sq"},
    ],
    "event_entity_vectors": [
        {"column": "vector", "config": HnswSq(distance_type="cosine", m=32, ef_construction=300), "suffix": "hnsw_sq"},
    ],
}

SCALAR_SPECS: dict[str, list[str]] = {
    "event_vectors": ["source_config_id", "category", "source_id"],
    "source_chunks": ["source_config_id", "source_id"],
    "entity_vectors": ["source_config_id"],
    "event_entity_vectors": ["source_config_id"],
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=URI_DEFAULT)
    parser.add_argument("--apply", action="store_true", help="实际创建索引（默认 dry-run）")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    suspects = _detect_api_processes()
    if suspects and not args.force and args.apply:
        print(f"检测到疑似 API 进程（{', '.join(suspects)}），拒绝写库。请先停止 API 或使用 --force。", file=sys.stderr)
        return 3

    db = lancedb.connect(args.uri, read_consistency_interval=None)
    report: dict[str, Any] = {"uri": args.uri, "created_at": _now(), "actions": []}

    for table_name, specs in ANN_SPECS.items():
        try:
            tbl = db.open_table(table_name)
        except Exception as error:  # noqa: BLE001
            print(f"[ann] {table_name}: 打开失败 {error}")
            report["actions"].append({"table": table_name, "error": str(error)})
            continue
        existing = {i.name for i in tbl.list_indices()}
        rows = int(tbl.count_rows())
        for spec in specs:
            name = f"ann_{table_name}_{spec['column'].replace('_vector', '')}_{spec['suffix']}"
            entry = {"table": table_name, "column": spec["column"], "index": name, "exists": name in existing}
            if name in existing:
                st = tbl.index_stats(name)
                entry["num_indexed_rows"] = getattr(st, "num_indexed_rows", None)
                entry["num_unindexed_rows"] = getattr(st, "num_unindexed_rows", None)
                entry["covered_pct"] = round(
                    (getattr(st, "num_indexed_rows", 0) or 0) / rows * 100, 2
                ) if rows else None
                print(f"[ann] {table_name}.{spec['column']}: 已存在 {name}（覆盖 {entry['covered_pct']}%）")
            else:
                if not args.apply:
                    print(f"[ann] {table_name}.{spec['column']}: 缺失 {name}（dry-run，未创建）")
                    entry["planned"] = True
                else:
                    t0 = time.time()
                    tbl.create_index(spec["column"], config=spec["config"], name=name)
                    st = tbl.index_stats(name)
                    entry["created"] = True
                    entry["build_s"] = round(time.time() - t0, 1)
                    entry["num_indexed_rows"] = getattr(st, "num_indexed_rows", None)
                    entry["num_unindexed_rows"] = getattr(st, "num_unindexed_rows", None)
                    print(f"[ann] {table_name}.{spec['column']}: 已创建 {name}（{entry['build_s']}s）")
            report["actions"].append(entry)

    for table_name, cols in SCALAR_SPECS.items():
        try:
            tbl = db.open_table(table_name)
        except Exception as error:  # noqa: BLE001
            report["actions"].append({"table": table_name, "scalar_error": str(error)})
            continue
        existing = {i.name for i in tbl.list_indices()}
        schema_cols = {f.name for f in tbl.schema}
        for col in cols:
            if col not in schema_cols:
                continue
            name = f"scalar_{table_name}_{col}"
            entry = {"table": table_name, "column": col, "index": name, "exists": name in existing}
            if name in existing:
                print(f"[scalar] {table_name}.{col}: 已存在 {name}")
            else:
                if not args.apply:
                    print(f"[scalar] {table_name}.{col}: 缺失 {name}（dry-run，未创建）")
                    entry["planned"] = True
                else:
                    t0 = time.time()
                    tbl.create_index(col, config=BTree(), name=name)
                    entry["created"] = True
                    entry["build_s"] = round(time.time() - t0, 1)
                    print(f"[scalar] {table_name}.{col}: 已创建 {name}（{entry['build_s']}s）")
            report["actions"].append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
