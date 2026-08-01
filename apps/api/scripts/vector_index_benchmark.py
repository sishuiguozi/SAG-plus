"""SAG-OPT-301/302/303：LanceDB 向量检索基准与 ANN 索引实验。

- baseline：从真实知识库按确定性步长采样查询向量，计算 exact KNN Top-10 作为召回基线；
  覆盖全库(full)、单知识库(kb，source_config_id 过滤)、单文档(doc，source_id 过滤)三类形态。
  输出基准夹具 JSON（只含 id/向量/距离/元数据，不含敏感原文，不放入仓库）。
- experiment：对指定 (表, 向量列) 创建 ANN 索引（HNSW-SQ / IVF-HNSW-SQ / HNSW-FLAT 可选），
  复跑基准查询，输出 Recall@10、P50/P95/P99 延迟、索引大小与构建耗时；
  --drop 可移除索引以便对比多种配置。
- scalar-index：为 source_config_id 等标量列创建 BTree 标量索引，验证过滤检索结果与延迟。

用法示例：
  python scripts/vector_index_benchmark.py baseline --uri "<SAG_DATA_ROOT>/engine/lancedb" \
      --out "<SAG_DATA_ROOT>/vector-benchmark/benchmark-v1.json"
  python scripts/vector_index_benchmark.py experiment --uri "<SAG_DATA_ROOT>/engine/lancedb" \
      --fixture "<SAG_DATA_ROOT>/vector-benchmark/benchmark-v1.json" --config hnsw_sq \
      --report "<SAG_DATA_ROOT>/vector-benchmark/report-hnsw-sq.json"
  python scripts/vector_index_benchmark.py scalar-index --uri "<SAG_DATA_ROOT>/engine/lancedb"
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lancedb
from lancedb.index import HnswSq, IvfHnswSq, HnswFlat


def _default_data_root() -> Path:
    """数据根目录：优先 SAG_DATA_ROOT 环境变量，否则 ~/.sag/.data。"""
    env = os.environ.get("SAG_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sag" / ".data"


URI_DEFAULT = str(_default_data_root() / "engine" / "lancedb")
BENCHMARK_DIR = _default_data_root() / "vector-benchmark"

# (表, 向量列, 优先级, 采样数, id列, 过滤列)
SPECS: list[dict[str, Any]] = [
    {"table": "event_vectors", "column": "title_vector", "priority": "P0", "n": 50, "id": "id", "scalar": "source_config_id", "doc": "source_id"},
    {"table": "event_vectors", "column": "content_vector", "priority": "P0", "n": 50, "id": "id", "scalar": "source_config_id", "doc": "source_id"},
    {"table": "source_chunks", "column": "heading_vector", "priority": "P0", "n": 40, "id": "id", "scalar": "source_config_id", "doc": "source_id"},
    {"table": "source_chunks", "column": "content_vector", "priority": "P0", "n": 40, "id": "id", "scalar": "source_config_id", "doc": "source_id"},
    {"table": "entity_vectors", "column": "vector", "priority": "P1", "n": 50, "id": "id", "scalar": "source_config_id", "doc": None},
    {"table": "event_entity_vectors", "column": "vector", "priority": "P1", "n": 50, "id": "id", "scalar": "source_config_id", "doc": None},
]

TOP_K = 10
SEED = 20260731


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sql_str(v: Any) -> str:
    return str(v).replace("'", "''")


def _scan_samples(tbl: Any, cols: list[str], n: int, vec_col: str) -> list[dict[str, Any]]:
    """单遍扫描，按确定性步长采样 n 行（id + 向量 + 过滤列），跳过向量为 NULL 的行。"""
    ds = tbl.to_lance()
    total = ds.count_rows()
    step = max(1, total // max(n * 2, 1))
    out: list[dict[str, Any]] = []
    seen = 0
    for batch in ds.scanner(columns=cols, batch_size=4096).to_batches():
        for row in batch.to_pylist():
            if len(out) >= n:
                break
            if row.get(vec_col) is None:
                seen += 1
                continue
            if (seen % step) == 0 and seen // step < n:
                out.append(row)
            seen += 1
        if len(out) >= n:
            break
    return out


def _exact_search(tbl: Any, vec: list[float], col: str, where: str | None, k: int = TOP_K) -> list[dict[str, Any]]:
    q = tbl.search(vec, vector_column_name=col).limit(k)
    if where:
        q = q.where(where)
    return q.to_list()


def _recall(exact_ids: list[str], ann_ids: list[str]) -> float:
    if not exact_ids:
        return 0.0
    hit = len(set(exact_ids) & set(ann_ids))
    return hit / len(exact_ids)


def _latency_stats(times: list[float]) -> dict[str, float]:
    if not times:
        return {}
    t = sorted(times)
    def pct(p: float) -> float:
        idx = min(len(t) - 1, int(len(t) * p))
        return round(t[idx] * 1000, 2)  # ms
    return {"p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "avg_ms": round(statistics.mean(t) * 1000, 2)}


def cmd_baseline(args: argparse.Namespace) -> int:
    db = lancedb.connect(args.uri, read_consistency_interval=None)
    queries: list[dict[str, Any]] = []
    qid = 0
    for spec in SPECS:
        if args.priority and spec["priority"] != args.priority:
            continue
        table, col, n = spec["table"], spec["column"], spec["n"]
        tbl = db.open_table(table)
        cols = [spec["id"], col, spec["scalar"]]
        if spec["doc"]:
            cols.append(spec["doc"])
        samples = _scan_samples(tbl, cols, n, col)
        print(f"[baseline] {table}.{col}: 采样 {len(samples)} 条（表共 {tbl.count_rows():,} 行）")
        for row in samples:
            vec = list(row[col])
            q = {
                "id": f"q{qid:04d}",
                "table": table,
                "column": col,
                "priority": spec["priority"],
                "kind": "full",
                "query_id": str(row[spec["id"]]),
                "query_vector": vec,
                "source_config_id": str(row[spec["scalar"]] or ""),
                "top10_ids": [],
                "top10_distances": [],
            }
            if spec["doc"]:
                q["source_id"] = str(row[spec["doc"]] or "")
            # exact 全库 Top-K
            hits = _exact_search(tbl, vec, col, None)
            q["top10_ids"] = [str(h["id"]) for h in hits]
            q["top10_distances"] = [float(h["_distance"]) for h in hits]
            queries.append(q)
            qid += 1
            # 子集：单知识库形态
            if qid % 4 == 0 and q["source_config_id"]:
                where = f"source_config_id = '{_sql_str(q['source_config_id'])}'"
                hits = _exact_search(tbl, vec, col, where)
                kb = {**q, "id": f"q{qid:04d}-kb", "kind": "kb", "query_vector": vec,
                      "top10_ids": [str(h["id"]) for h in hits], "top10_distances": [float(h["_distance"]) for h in hits]}
                queries.append(kb)
                qid += 1
            # 子集：单文档形态
            if qid % 8 == 0 and q.get("source_id"):
                where = f"source_id = '{_sql_str(q['source_id'])}'"
                hits = _exact_search(tbl, vec, col, where)
                doc = {**q, "id": f"q{qid:04d}-doc", "kind": "doc", "query_vector": vec,
                       "top10_ids": [str(h["id"]) for h in hits], "top10_distances": [float(h["_distance"]) for h in hits]}
                queries.append(doc)
                qid += 1

    out = {
        "version": 1,
        "created_at": _now(),
        "uri": args.uri,
        "top_k": TOP_K,
        "seed": SEED,
        "query_count": len(queries),
        "queries": queries,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    kinds = {}
    for q in queries:
        kinds[q["kind"]] = kinds.get(q["kind"], 0) + 1
    print(f"[baseline] 共 {len(queries)} 条查询：{kinds}")
    print(f"夹具：{args.out}")
    return 0


def _index_config(config: str):
    cfg = {
        "hnsw_sq": lambda: HnswSq(distance_type="cosine", m=32, ef_construction=300),
        "hnsw_flat": lambda: HnswFlat(distance_type="cosine", m=32, ef_construction=300),
        "ivf_hnsw_sq": lambda: IvfHnswSq(distance_type="cosine", num_partitions=16, m=32, ef_construction=300),
    }[config]
    return cfg()


def _index_name(spec: dict[str, Any], config: str) -> str:
    return f"ann_{spec['table']}_{spec['column'].replace('_vector', '')}_{config}"


def cmd_experiment(args: argparse.Namespace) -> int:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    db = lancedb.connect(args.uri, read_consistency_interval=None)
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for q in fixture["queries"]:
        by_key.setdefault((q["table"], q["column"]), []).append(q)

    report: dict[str, Any] = {
        "config": args.config,
        "created_at": _now(),
        "top_k": TOP_K,
        "results": [],
    }
    for spec in SPECS:
        if args.priority and spec["priority"] != args.priority:
            continue
        key = (spec["table"], spec["column"])
        qs = by_key.get(key)
        if not qs:
            continue
        tbl = db.open_table(spec["table"])
        name = _index_name(spec, args.config)
        existing = [i.name for i in tbl.list_indices()] if hasattr(tbl, "list_indices") else []
        dir_before = _dir_size(Path(args.uri) / f"{spec['table']}.lance")
        built = False
        if name not in existing:
            if not args.no_create:
                t0 = time.time()
                tbl.create_index(spec["column"], config=_index_config(args.config), name=name)
                build_s = round(time.time() - t0, 1)
                built = True
                print(f"[experiment] {spec['table']}.{spec['column']}: 创建索引 {name} 耗时 {build_s}s")
            else:
                print(f"[experiment] {spec['table']}.{spec['column']}: 索引 {name} 不存在且 --no-create，跳过")
                continue
        else:
            build_s = None
            print(f"[experiment] {spec['table']}.{spec['column']}: 复用已有索引 {name}")

        # exact 对照延迟（仅当本列尚未建索引时测量）
        full = [q for q in qs if q["kind"] == "full"]
        exact_lat: list[float] = []
        if built:
            for q in full:
                t0 = time.time()
                _exact_search(tbl, q["query_vector"], q["column"], None)
                exact_lat.append(time.time() - t0)

        # 运行 full 查询（全库 ANN）
        lat: list[float] = []
        recalls: list[float] = []
        for q in full:
            t0 = time.time()
            hits = _exact_search(tbl, q["query_vector"], q["column"], None)
            lat.append(time.time() - t0)
            ids = [str(h["id"]) for h in hits]
            recalls.append(_recall(q["top10_ids"], ids))
        # 过滤查询（kb/doc）——验证索引+预过滤
        filt_lat: list[float] = []
        filt_recalls: list[float] = []
        for q in qs:
            if q["kind"] == "full":
                continue
            where = None
            if q["kind"] == "kb" and q.get("source_config_id"):
                where = f"source_config_id = '{_sql_str(q['source_config_id'])}'"
            elif q["kind"] == "doc" and q.get("source_id"):
                where = f"source_id = '{_sql_str(q['source_id'])}'"
            if not where:
                continue
            t0 = time.time()
            hits = _exact_search(tbl, q["query_vector"], q["column"], where)
            filt_lat.append(time.time() - t0)
            ids = [str(h["id"]) for h in hits]
            filt_recalls.append(_recall(q["top10_ids"], ids))

        dir_after = _dir_size(Path(args.uri) / f"{spec['table']}.lance")
        res = {
            "table": spec["table"], "column": spec["column"], "priority": spec["priority"],
            "index_name": name, "built": built, "build_s": build_s,
            "rows": tbl.count_rows(),
            "query_count": len(full),
            "recall_at_10": round(statistics.mean(recalls), 4) if recalls else None,
            "latency": _latency_stats(lat),
            "exact_latency": _latency_stats(exact_lat),
            "filtered_query_count": len(filt_lat),
            "filtered_recall_at_10": round(statistics.mean(filt_recalls), 4) if filt_recalls else None,
            "filtered_latency": _latency_stats(filt_lat),
            "table_dir_mb_before": round(dir_before / 1e6, 1),
            "table_dir_mb_after": round(dir_after / 1e6, 1),
            "index_mb": round(max(0.0, dir_after - dir_before) / 1e6, 1),
        }
        report["results"].append(res)
        print(json.dumps(res, ensure_ascii=False))
        if args.drop:
            tbl.drop_index(name)
            print(f"[experiment] 已移除索引 {name}")

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{args.report}")
    return 0


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# SAG-OPT-303：为高频预过滤列创建 BTree 标量索引
SCALAR_COLUMNS: dict[str, list[str]] = {
    "event_vectors": ["source_config_id", "category", "source_id"],
    "source_chunks": ["source_config_id", "source_id"],
    "entity_vectors": ["source_config_id", "is_delete"],
    "event_entity_vectors": ["source_config_id", "is_delete"],
}


def cmd_scalar_index(args: argparse.Namespace) -> int:
    db = lancedb.connect(args.uri, read_consistency_interval=None)
    results = []
    for spec in SPECS:
        if args.priority and spec["priority"] != args.priority:
            continue
        tbl = db.open_table(spec["table"])
        for col in SCALAR_COLUMNS.get(spec["table"], [spec["scalar"]]):
            # 表里没有该列则跳过
            cols = {f.name for f in tbl.schema}
            if col not in cols:
                print(f"[scalar] {spec['table']}.{col}: 列不存在，跳过")
                continue
            existing = [i.name for i in tbl.list_indices()] if hasattr(tbl, "list_indices") else []
            name = f"scalar_{spec['table']}_{col}"
            created = False
            if name not in existing:
                if not args.no_create:
                    t0 = time.time()
                    tbl.create_scalar_index(col, index_type="BTREE", name=name)
                    created = True
                    print(f"[scalar] {spec['table']}.{col}: 创建标量索引 {name} 耗时 {time.time()-t0:.1f}s")
                else:
                    print(f"[scalar] {spec['table']}.{col}: 跳过（--no-create）")
                    continue
            else:
                print(f"[scalar] {spec['table']}.{col}: 复用 {name}")
            # 验证：对一条已有向量做该列预过滤检索，确认结果全部满足过滤条件
            sample = tbl.search().select([spec["column"], col]).limit(1).to_list()[0]
            val = sample[col]
            if val is None:
                print(f"[scalar] {spec['table']}.{col}: 样本值为 NULL，跳过验证")
                results.append({"table": spec["table"], "column": col, "scalar_index": name,
                                "created": created, "skipped": True})
                continue
            if isinstance(val, bool):
                where = f"{col} = {str(val).lower()}"
            else:
                where = f"{col} = '{_sql_str(val)}'"
            t0 = time.time()
            out = tbl.search(sample[spec["column"]], vector_column_name=spec["column"]) \
                      .where(where).limit(10).to_list()
            dt_ms = (time.time() - t0) * 1000
            ok = all(str(r[col]) == str(val) for r in out)
            results.append({"table": spec["table"], "column": col, "scalar_index": name, "created": created,
                            "sample_value": str(val)[:24], "hit_rows": len(out), "all_in_scope": ok,
                            "latency_ms": round(dt_ms, 2)})
            print(json.dumps(results[-1], ensure_ascii=False))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0




def cmd_verify_app(args: argparse.Namespace) -> int:
    """用应用补丁后的 LanceDBStore.vector_search 复跑基准，验证生产路径召回与延迟。"""
    import asyncio

    from sag_api.sag.lancedb_search_compat import install_zleap_sag_lancedb_ann_search_patch
    install_zleap_sag_lancedb_ann_search_patch()

    from zleap.sag.core.storage.lancedb_store import LanceDBStore

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    store = LanceDBStore(args.uri)

    async def run() -> dict[str, Any]:
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for q in fixture["queries"]:
            by_key.setdefault((q["table"], q["column"]), []).append(q)
        results: list[dict[str, Any]] = []
        for spec in SPECS:
            if args.priority and spec["priority"] != args.priority:
                continue
            qs = by_key.get((spec["table"], spec["column"]))
            if not qs:
                continue
            lat: list[float] = []
            recalls: list[float] = []
            filt_lat: list[float] = []
            filt_recalls: list[float] = []
            for q in qs:
                where = None
                if q["kind"] == "kb" and q.get("source_config_id"):
                    where = {"term": {"source_config_id": q["source_config_id"]}}
                elif q["kind"] == "doc" and q.get("source_id"):
                    where = {"term": {"source_id": q["source_id"]}}
                t0 = time.time()
                hits = await store.vector_search(
                    spec["table"], spec["column"], q["query_vector"], size=TOP_K, filter_query=where,
                )
                dt = time.time() - t0
                ids = [str(h["id"]) for h in hits]
                if q["kind"] == "full":
                    lat.append(dt)
                    recalls.append(_recall(q["top10_ids"], ids))
                else:
                    filt_lat.append(dt)
                    filt_recalls.append(_recall(q["top10_ids"], ids))
            results.append({
                "table": spec["table"], "column": spec["column"], "priority": spec["priority"],
                "rows": await (await store._open_table(spec["table"])).count_rows(),
                "query_count": len([q for q in qs if q["kind"] == "full"]),
                "recall_at_10": round(statistics.mean(recalls), 4) if recalls else None,
                "latency": _latency_stats(lat),
                "filtered_query_count": len(filt_lat),
                "filtered_recall_at_10": round(statistics.mean(filt_recalls), 4) if filt_recalls else None,
                "filtered_latency": _latency_stats(filt_lat),
            })
            print(json.dumps(results[-1], ensure_ascii=False))
        return {"created_at": _now(), "results": results}

    report = asyncio.run(run())
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{args.report}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("baseline", help="采集基准并计算 exact Top-K")
    pb.add_argument("--uri", default=URI_DEFAULT)
    pb.add_argument("--out", default=str(BENCHMARK_DIR / "benchmark-v1.json"))
    pb.add_argument("--priority", choices=["P0", "P1"], default=None)
    pb.set_defaults(func=cmd_baseline)

    pe = sub.add_parser("experiment", help="ANN 索引实验")
    pe.add_argument("--uri", default=URI_DEFAULT)
    pe.add_argument("--fixture", default=str(BENCHMARK_DIR / "benchmark-v1.json"))
    pe.add_argument("--report", default=str(BENCHMARK_DIR / "report.json"))
    pe.add_argument("--config", choices=["hnsw_sq", "hnsw_flat", "ivf_hnsw_sq"], default="hnsw_sq")
    pe.add_argument("--priority", choices=["P0", "P1"], default=None)
    pe.add_argument("--no-create", action="store_true", help="不创建索引，只测量已有索引")
    pe.add_argument("--drop", action="store_true", help="测量后移除索引")
    pe.set_defaults(func=cmd_experiment)

    pv = sub.add_parser("verify-app", help="用应用补丁后的检索路径验证")
    pv.add_argument("--uri", default=URI_DEFAULT)
    pv.add_argument("--fixture", default=str(BENCHMARK_DIR / "benchmark-v1.json"))
    pv.add_argument("--report", default=str(BENCHMARK_DIR / "verify-app.json"))
    pv.add_argument("--priority", choices=["P0", "P1"], default=None)
    pv.set_defaults(func=cmd_verify_app)

    ps = sub.add_parser("scalar-index", help="标量过滤索引")
    ps.add_argument("--uri", default=URI_DEFAULT)
    ps.add_argument("--priority", choices=["P0", "P1"], default=None)
    ps.add_argument("--no-create", action="store_true")
    ps.set_defaults(func=cmd_scalar_index)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
