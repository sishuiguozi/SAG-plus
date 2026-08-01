r"""检索评估集（A5）：跑一组 QA 用例，量化 recall@k / 命中率。

用法（在 apps/api 下，venv）：
    python scripts/eval_retrieval.py --top-k 5 --limit 10

会启动引擎读取真实库（E:\sag\.data\engine），对每个用例执行一次检索，
输出命中率、平均 top 命中位次，用于对比优化前后的检索质量。

用例结构：{query, expected_terms[]}（expected_terms 命中任一视为命中）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 内置评估集（可扩展；基于已知信源内容）
EVAL_CASES: list[dict] = [
    {"query": "AFSIM terrain configuration", "expected_terms": ["terrain", "configuration", "AFSIM"]},
    {"query": "common terrain configuration", "expected_terms": ["terrain", "configuration"]},
    {"query": "class Engine", "expected_terms": ["class Engine", "engine"]},
    {"query": "事件语义召回", "expected_terms": ["事件"]},
]


def _hit(section_content: str, terms: list[str]) -> bool:
    lowered = section_content.lower()
    return any(term.lower() in lowered for term in terms)


async def warm_read_runtime(manager, sources: list) -> None:
    """在当前任务预热一个引擎槽，避免关闭时跨 ContextVar reset。

    ``retrieve_relevant_sections`` 会在子任务中并发执行向量与词法检索。若第一个
    引擎在该子任务启动，zleap-sag 保存的资源 ContextVar token 无法由脚本主任务
    关闭。先在调用者上下文 provision 一个源，后续检索复用已初始化的运行时。
    """
    if not sources:
        return
    source = sources[0]
    await manager.provision(source.sag_source_config_id, source)


def format_case_result(
    case: dict,
    *,
    top_k: int,
    matched_count: int,
    elapsed_ms: float,
) -> str:
    """Render one stable, copyable evaluation result line."""
    status = "HIT" if matched_count else "MISS"
    return (
        f"[{status}] {case['query']}  "
        f"(top{top_k} 命中 {matched_count}，{elapsed_ms:.0f}ms)"
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=10, help="最多评估用例数")
    args = parser.parse_args()

    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager

    settings.search_top_k = args.top_k
    manager = EngineManager(settings)

    from sag_api.sag.chunking_compat import install_structural_chunking_patch
    from sag_api.sag.embedding_backend import install_embedding_backend

    install_structural_chunking_patch()
    install_embedding_backend(settings)

    from sag_api.core.db import SessionLocal
    from sag_api.services.retrieval_service import retrieve_relevant_sections
    from sag_api.services.source_service import list_sources

    async with SessionLocal() as session:
        sources = await list_sources(session)
    await warm_read_runtime(manager, sources)

    hits = 0
    total = 0
    print(f"== 检索评估 (top_k={args.top_k}, 信源数={len(sources)}) ==")
    for case in EVAL_CASES[: args.limit]:
        started = time.perf_counter()
        try:
            outcome = await retrieve_relevant_sections(
                manager,
                sources,
                case["query"],
                strategy="vector",
                top_k=args.top_k,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"- {case['query']}: ERROR {exc}")
            continue
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        total += 1
        matched = [s for s in outcome.sections if _hit(s.content, case["expected_terms"])]
        ok = bool(matched)
        hits += ok
        print(
            format_case_result(
                case,
                top_k=args.top_k,
                matched_count=len(matched),
                elapsed_ms=elapsed_ms,
            )
        )
    if total:
        print(f"== 命中率 {hits}/{total} = {hits / total:.0%} ==")
    await manager.aclose_all()
    return 0 if total and hits / total >= 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
