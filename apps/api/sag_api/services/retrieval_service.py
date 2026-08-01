"""Shared bounded retrieval, reranking, and evidence-grounded search answers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from sag_api.core.config import settings
from sag_api.core.llm_call_context import llm_call_scope
from sag_api.core.logging import get_logger
from sag_api.sag import RetrievedSection, SearchOutcome

log = get_logger("retrieval")
_local_reranker_singleton: Any | None = None


class SearchSource(Protocol):
    id: str
    name: str
    sag_source_config_id: str


EventScoreMap = dict[tuple[str, str], float]


_QUERY_NOISE = (
    "知识库",
    "资料库",
    "资料中",
    "文档中",
    "告诉我",
    "帮我查",
    "搜索",
    "查询",
    "请问",
    "关于",
    "最新",
    "最近",
    "动态",
    "消息",
    "新闻",
    "内容",
    "资料",
    "一下",
    "是什么",
    "有哪些",
    "有什么",
)
_BOILERPLATE = (
    "新浪首页",
    "权利保护声明",
    "阅读排行榜",
    "评论排行榜",
    "点击加载更多",
    "免责声明",
)
_CITATION_RE = re.compile(r"\[(\d+)]")


def _normalized(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9\u3400-\u9fff]+", value.lower()))


def query_terms(query: str) -> list[str]:
    """Extract a small, deterministic lexical signal without pretending to segment Chinese."""

    cleaned = query.strip().lower()
    for phrase in _QUERY_NOISE:
        cleaned = cleaned.replace(phrase, " ")
    candidates = re.findall(
        r"[a-z0-9][a-z0-9_.+-]{1,31}|[\u3400-\u9fff]{2,16}",
        cleaned,
    )
    terms: list[str] = []
    for candidate in candidates:
        value = candidate.strip()
        if value and not value.isdigit() and value not in terms:
            terms.append(value)
    return terms[:4]


def _section_key(section: RetrievedSection) -> tuple[str, str]:
    source = (section.source_config_id or section.source_id or "").strip()
    chunk = (section.chunk_id or "").strip()
    if chunk:
        return source, chunk
    fingerprint = _normalized(f"{section.heading}\n{section.content}")[:240]
    return source, fingerprint


def _lexical_relevance(query: str, section: RetrievedSection) -> float:
    heading = _normalized(section.heading)
    content = _normalized(section.content)
    text = f"{heading}{content}"
    if not text:
        return 0.0

    terms = [_normalized(term) for term in query_terms(query)]
    terms = [term for term in terms if term]
    cleaned_query = query
    for phrase in _QUERY_NOISE:
        cleaned_query = cleaned_query.replace(phrase, " ")
    phrase = _normalized(cleaned_query)

    score = 0.0
    if phrase and len(phrase) >= 2 and phrase in text:
        score += 0.55
        if phrase in heading:
            score += 0.2
    if terms:
        matched = sum(term in text for term in terms)
        heading_matched = sum(term in heading for term in terms)
        score += 0.35 * matched / len(terms)
        score += 0.15 * heading_matched / len(terms)
    return min(1.0, score)


def _is_boilerplate(section: RetrievedSection) -> bool:
    text = f"{section.heading}\n{section.content}"
    return sum(marker in text for marker in _BOILERPLATE) >= 2


@dataclass(frozen=True, slots=True)
class RerankResult:
    sections: list[RetrievedSection]
    candidate_count: int
    relevant_count: int
    filtered_count: int
    lexical_count: int


def rerank_sections(
    query: str,
    semantic: list[RetrievedSection],
    *,
    lexical: list[RetrievedSection] | None = None,
    limit: int,
) -> RerankResult:
    """Hybrid rerank with an explicit relevance gate before anything reaches an answer."""

    lexical = lexical or []
    exact_keys = {_section_key(section) for section in lexical}
    merged: dict[tuple[str, str], tuple[RetrievedSection, int]] = {}
    for index, section in enumerate([*semantic, *lexical]):
        key = _section_key(section)
        if not key[1]:
            continue
        previous = merged.get(key)
        if previous is None:
            merged[key] = (section, index)
            continue
        previous_section, previous_index = previous
        chosen = section if len(section.content.strip()) > len(previous_section.content.strip()) else previous_section
        merged[key] = (
            chosen.model_copy(update={"score": max(float(previous_section.score), float(section.score))}),
            min(previous_index, index),
        )

    candidates = list(merged.items())
    if not candidates:
        return RerankResult([], 0, 0, 0, len(lexical))

    raw_scores = [max(0.0, float(item[1][0].score or 0.0)) for item in candidates]
    top_raw = max(raw_scores, default=0.0)
    semantic_floor = max(0.35, top_raw * 0.68)
    denominator = max(1, len(candidates) - 1)
    lexical_scores = {key: _lexical_relevance(query, section) for key, (section, _index) in candidates}
    has_lexical_signal = any(key in exact_keys or score >= 0.2 for key, score in lexical_scores.items())
    ranked: list[tuple[float, float, int, RetrievedSection]] = []

    for position, (key, (section, original_index)) in enumerate(candidates):
        raw = max(0.0, min(1.0, float(section.score or 0.0)))
        lexical_score = lexical_scores[key]
        exact = key in exact_keys
        if _is_boilerplate(section) and not exact and lexical_score < 0.35:
            continue
        rank_score = 1.0 - position / denominator
        combined = min(
            1.0,
            raw * 0.5 + rank_score * 0.2 + lexical_score * 0.3 + (0.15 if exact else 0.0),
        )
        if has_lexical_signal:
            relevant = exact or lexical_score >= 0.2
        else:
            relevant = raw >= semantic_floor
        if not relevant:
            continue
        ranked.append((combined, raw, original_index, section))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], _section_key(item[3])))
    selected = [
        section.model_copy(update={"score": round(score, 6), "rank": index})
        for index, (score, _raw, _original, section) in enumerate(ranked[: max(1, limit)])
    ]
    return RerankResult(
        sections=selected,
        candidate_count=len(candidates),
        relevant_count=len(ranked),
        filtered_count=len(candidates) - len(ranked),
        lexical_count=len(lexical),
    )


async def _lexical_sections(
    engine_manager: Any,
    sources: list[SearchSource],
    query: str,
) -> list[RetrievedSection]:
    grep_chunks = getattr(engine_manager, "grep_chunks", None)
    terms = query_terms(query)
    if not terms:
        return []
    # BM25 独立召回（LanceDB FTS）：专有名词/编号/精确短语优先，失败回退 grep
    if settings.lancedb_fts_enabled and callable(grep_chunks):
        try:
            from sag_api.sag.lancedb_fts import fts_search

            # LanceDB FTS 的索引构建与查询均为同步 I/O；放入 worker thread，避免首次
            # 懒建索引或慢盘查询阻塞 FastAPI 的事件循环。
            rows = await asyncio.to_thread(
                fts_search,
                [source.sag_source_config_id for source in sources],
                query,
                limit=max(4, int(settings.search_top_k or 8)),
            )
            if rows:
                return [
                    RetrievedSection(
                        chunk_id=row.get("chunk_id"),
                        heading=row.get("heading") or "",
                        content=row.get("content") or "",
                        score=min(1.0, max(0.0, float(row.get("_score") or 0.0) / 2.0)),
                        rank=index,
                        source_config_id=row.get("source_config_id"),
                    )
                    for index, row in enumerate(rows)
                ]
        except Exception:  # noqa: BLE001 - 回退 grep 通道
            pass
    if not callable(grep_chunks):
        return []

    semaphore = asyncio.Semaphore(max(1, settings.search_source_concurrency))

    async def one(source: SearchSource, term: str) -> list[RetrievedSection]:
        async with semaphore:
            try:
                rows = await grep_chunks(
                    source.sag_source_config_id,
                    term,
                    source=source,
                    limit=2,
                )
            except Exception:  # noqa: BLE001
                return []
        return [
            RetrievedSection(
                chunk_id=row.get("chunk_id"),
                heading=row.get("heading") or "",
                content=row.get("snippet") or "",
                score=max(0.8, 1.0 - index * 0.02),
                rank=index,
                source_config_id=source.sag_source_config_id,
            )
            for index, row in enumerate(rows)
        ]

    groups = await asyncio.gather(*(one(source, term) for source in sources for term in terms))
    return [section for group in groups for section in group]


async def _llm_rerank(
    query: str,
    sections: list[RetrievedSection],
    *,
    llm: Any,
    limit: int,
) -> list[RetrievedSection]:
    """可选的 LLM 重排（A3，默认关闭）：一次性让模型按相关度输出候选顺序。"""
    if len(sections) <= 1:
        return sections[:limit]
    numbered = [
        f"[{index}] {section.heading or '相关资料'}\n{section.content.strip()[:300]}"
        for index, section in enumerate(sections, 1)
    ]
    messages = [
        {
            "role": "system",
            "content": "你是检索结果重排器。只输出候选编号（最相关在前），用英文逗号分隔，不要解释。",
        },
        {
            "role": "user",
            "content": f"问题：{query}\n\n候选：\n" + "\n\n".join(numbered),
        },
    ]
    try:
        with llm_call_scope("rerank"):
            raw = await llm.complete(messages)
    except Exception:  # noqa: BLE001 - LLM 重排失败回退原顺序
        return sections[:limit]
    if not raw:
        return sections[:limit]
    ordered: list[RetrievedSection] = []
    seen: set[int] = set()
    for token in re.findall(r"\d+", raw):
        idx = int(token) - 1
        if 0 <= idx < len(sections) and idx not in seen:
            seen.add(idx)
            ordered.append(sections[idx])
    for index, section in enumerate(sections):
        if index not in seen:
            ordered.append(section)
    return ordered[:limit]


async def _api_rerank(
    query: str,
    sections: list[RetrievedSection],
    *,
    client: Any,
    limit: int,
) -> list[RetrievedSection]:
    """Score the existing fused order; API failures must never fail search."""
    if len(sections) <= 1:
        return sections[:limit]
    try:
        scores = await client.rank(
            query,
            [section.content.strip() for section in sections],
            limit=min(limit, len(sections)),
        )
    except Exception as error:  # noqa: BLE001 - external rerank is best effort
        log.warning("Rerank API failed; using fused order: %s", error)
        return sections[:limit]
    if len(scores) != len(sections):
        log.warning("Rerank API returned %s scores for %s sections", len(scores), len(sections))
        return sections[:limit]
    return [
        section
        for _, section in sorted(
            enumerate(sections),
            key=lambda item: scores[item[0]],
            reverse=True,
        )
    ][:limit]


def _local_reranker() -> Any:
    """Return the selected native GGUF reranker, recreating it after a setting change."""
    from sag_api.sag.local_reranker import LocalReranker

    global _local_reranker_singleton
    model_path = (
        Path(settings.data_dir).resolve().parent
        / "models"
        / "reranker"
        / settings.search_local_rerank_model_file
    )
    desired = LocalReranker(
        str(model_path),
        n_ctx=settings.embedding_local_n_ctx,
        n_threads=settings.embedding_local_n_threads or None,
    )
    if (
        _local_reranker_singleton is None
        or _local_reranker_singleton.fingerprint != desired.fingerprint
    ):
        _local_reranker_singleton = desired
    return _local_reranker_singleton


async def _local_rerank(
    query: str,
    sections: list[RetrievedSection],
    *,
    reranker: Any,
    limit: int,
) -> list[RetrievedSection]:
    """Run a native cross-encoder without ever substituting a chat completion."""
    if len(sections) <= 1:
        return sections[:limit]
    try:
        scores = await asyncio.to_thread(
            reranker.rank,
            query,
            [section.content.strip() for section in sections],
        )
    except Exception as error:  # noqa: BLE001 - local runtime is best effort
        log.warning("Local reranker failed; using fused order: %s", error)
        return sections[:limit]
    if len(scores) != len(sections):
        log.warning("Local reranker returned %s scores for %s sections", len(scores), len(sections))
        return sections[:limit]
    return [
        section
        for _, section in sorted(
            enumerate(sections),
            key=lambda item: scores[item[0]],
            reverse=True,
        )
    ][:limit]


async def retrieve_relevant_sections(
    engine_manager: Any,
    sources: list[SearchSource],
    query: str,
    *,
    strategy: str | None = None,
    top_k: int | None = None,
    llm: Any | None = None,
) -> SearchOutcome:
    """One retrieval contract for search UI and the Agent's search_context tool."""

    requested_limit = max(1, min(int(top_k or settings.search_top_k), 50))
    candidate_limit = min(50, max(requested_limit * 3, requested_limit + 8))
    targets = [(source.sag_source_config_id, source) for source in sources]
    outcome, lexical = await asyncio.gather(
        engine_manager.search_many(
            targets,
            query,
            strategy=strategy,
            top_k=candidate_limit,
        ),
        _lexical_sections(engine_manager, sources, query),
    )
    reranked = rerank_sections(
        query,
        outcome.sections,
        lexical=lexical,
        limit=requested_limit,
    )
    final_sections = reranked.sections
    rerank_mode = settings.effective_search_rerank_mode
    if rerank_mode == "local":
        final_sections = await _local_rerank(
            query,
            reranked.sections,
            reranker=_local_reranker(),
            limit=requested_limit,
        )
    elif rerank_mode == "api" and all(
        (
            settings.search_rerank_api_url,
            settings.search_rerank_api_key,
            settings.search_rerank_api_model,
        )
    ):
        from sag_api.sag.rerank_api_client import RerankAPIClient

        final_sections = await _api_rerank(
            query,
            reranked.sections,
            client=RerankAPIClient(
                url=settings.search_rerank_api_url,
                api_key=settings.search_rerank_api_key,
                model=settings.search_rerank_api_model,
                instruction=settings.search_rerank_api_instruction,
                timeout_ms=settings.search_rerank_api_timeout_ms,
            ),
            limit=requested_limit,
        )
    elif (
        rerank_mode == "llm"
        and llm is not None
        and getattr(llm, "configured", False)
    ):
        final_sections = await _llm_rerank(query, reranked.sections, llm=llm, limit=requested_limit)
    # A4：父子分块检索增强——命中子块时用父块内容替换（覆盖词法+语义全通道，
    # 增量安全：旧数据无 parent 标记时原样返回）。
    try:
        from sag_api.sag.parent_child import enrich_parent_context

        final_sections = await enrich_parent_context(final_sections)
    except Exception:  # noqa: BLE001 - enrich 失败不影响检索
        pass

    stats = {
        **outcome.stats,
        "requested_top_k": requested_limit,
        "candidate_top_k": candidate_limit,
        "candidates": reranked.candidate_count,
        "relevant": reranked.relevant_count,
        "filtered_irrelevant": reranked.filtered_count,
        "lexical_candidates": reranked.lexical_count,
        "has_more": reranked.relevant_count > len(reranked.sections),
    }
    return SearchOutcome(
        query=outcome.query or query,
        sections=final_sections,
        stats=stats,
    )


async def recall_event_scores(
    engine_manager: Any,
    query: str,
    sources_by_config: dict[str, SearchSource],
    *,
    limit: int | None = None,
) -> EventScoreMap:
    """Best-effort direct event recall shared by Search and the Agent.

    Chunks remain the traceable evidence path.  Event recall supplies the
    semantic result layer (title + summary) so a long document does not lose
    its extracted events merely because the best matching chunks are located
    elsewhere in the document.
    """

    search = getattr(engine_manager, "search_event_scores", None)
    if not callable(search):
        return {}
    try:
        result = await search(query, sources_by_config, limit=limit)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        log.warning("事项向量召回失败，继续使用原文块结果：%s", error)
        return {}
    if not isinstance(result, dict):
        return {}

    scores: EventScoreMap = {}
    for raw_key, raw_score in result.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 2:
            continue
        source_config_id = str(raw_key[0] or "").strip()
        event_id = str(raw_key[1] or "").strip()
        if not source_config_id or not event_id:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        scores[(source_config_id, event_id)] = max(0.0, min(1.0, score))
    return scores


def _best_excerpt(query: str, section: RetrievedSection, limit: int = 260) -> str:
    content = re.sub(r"\s+", " ", section.content).strip()
    if not content:
        return section.heading.strip()
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])", content) if part.strip()]
    terms = [_normalized(term) for term in query_terms(query)]
    best = max(
        sentences or [content],
        key=lambda sentence: sum(term in _normalized(sentence) for term in terms),
    )
    return best[:limit] + ("…" if len(best) > limit else "")


def fallback_search_answer(query: str, sections: list[RetrievedSection]) -> str:
    if not sections:
        return ""
    lines = [f"- {_best_excerpt(query, section)} [{index}]" for index, section in enumerate(sections[:4], 1)]
    return "根据与问题直接相关的证据：\n" + "\n".join(lines)


def _validated_answer(answer: str, section_count: int) -> str | None:
    text = answer.strip()
    if not text:
        return None
    references = [int(value) for value in _CITATION_RE.findall(text)]
    if not references or any(value < 1 or value > section_count for value in references):
        return None
    return text


@dataclass(frozen=True, slots=True)
class SearchAnswerUpdate:
    kind: Literal["delta", "completed"]
    text: str


def _search_answer_messages(
    query: str,
    sections: list[RetrievedSection],
) -> tuple[list[dict[str, str]], int]:
    evidence_blocks: list[str] = []
    used = 0
    for index, section in enumerate(sections, 1):
        block = f"[{index}] {section.heading or '相关资料'}\n{section.content.strip()}"
        remaining = 12000 - used
        if remaining <= 0:
            break
        block = block[:remaining]
        evidence_blocks.append(block)
        used += len(block)
    return (
        [
            {
                "role": "system",
                "content": (
                    "你是检索结果回答器。只回答用户提出的具体问题，不要概括候选集合。"
                    "只能使用给定证据；忽略与问题无关的内容。每个事实性结论必须标注"
                    "对应的 [编号]，编号只能来自证据。证据不足时明确说明不足，不得补充"
                    "常识或猜测。回答简洁、直接。"
                ),
            },
            {
                "role": "user",
                "content": (f"问题：{query}\n\n已通过相关性重排的证据：\n" + "\n\n".join(evidence_blocks)),
            },
        ],
        len(evidence_blocks),
    )


async def synthesize_search_answer(
    query: str,
    sections: list[RetrievedSection],
    *,
    llm: Any | None,
) -> str:
    """Answer the actual question from selected evidence; never summarize the raw candidate pool."""

    fallback = fallback_search_answer(query, sections)
    if not sections or llm is None or not getattr(llm, "configured", False):
        return fallback

    messages, evidence_count = _search_answer_messages(query, sections)
    try:
        answer = await llm.complete(messages)
    except Exception as error:  # noqa: BLE001
        log.warning("搜索答案生成失败，回退证据摘要：%s", error)
        return fallback
    return _validated_answer(answer, evidence_count) or fallback


async def stream_synthesize_search_answer(
    query: str,
    sections: list[RetrievedSection],
    *,
    llm: Any | None,
) -> AsyncIterator[SearchAnswerUpdate]:
    """Yield true provider deltas followed by one citation-validated answer."""

    fallback = fallback_search_answer(query, sections)
    if not sections or llm is None or not getattr(llm, "configured", False):
        yield SearchAnswerUpdate(kind="completed", text=fallback)
        return

    messages, evidence_count = _search_answer_messages(query, sections)
    parts: list[str] = []
    try:
        async for delta in llm.stream_complete(messages):
            if not delta:
                continue
            parts.append(delta)
            yield SearchAnswerUpdate(kind="delta", text=delta)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001
        log.warning("搜索答案流生成失败，回退证据摘要：%s", error)
        yield SearchAnswerUpdate(kind="completed", text=fallback)
        return

    answer = _validated_answer("".join(parts), evidence_count) or fallback
    yield SearchAnswerUpdate(kind="completed", text=answer)
