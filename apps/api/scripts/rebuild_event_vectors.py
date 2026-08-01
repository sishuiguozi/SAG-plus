from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_environment() -> None:
    # Keep zleap-sag from interpreting an inherited DEBUG=release as a config
    # value. The desktop launcher uses the same guard.
    os.environ.setdefault("DEBUG", "false")


def _build_event_document(event: Any, title_vec: list[float], content_vec: list[float]) -> dict[str, Any]:
    entity_ids = [
        assoc.entity_id
        for assoc in getattr(event, "event_associations", []) or []
        if getattr(assoc, "entity_id", None)
    ]
    extra_fields: dict[str, Any] = {}
    if getattr(event, "extra_data", None) and "tags" in event.extra_data:
        extra_fields["tags"] = event.extra_data["tags"]
    if getattr(event, "category", None):
        extra_fields["category"] = event.category
    if getattr(event, "keywords", None):
        extra_fields["keywords"] = event.keywords
    return {
        "id": event.id,
        "event_id": event.id,
        "source_config_id": event.source_config_id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "title": event.title or "",
        "summary": event.summary or "",
        "content": event.content or "",
        "title_vector": title_vec,
        "content_vector": content_vec,
        "entity_ids": entity_ids,
        "start_time": event.start_time.isoformat() if event.start_time else None,
        "end_time": event.end_time.isoformat() if event.end_time else None,
        "created_time": event.created_time.isoformat() if event.created_time else None,
        **extra_fields,
    }


async def _count_events(source_config_id: str | None) -> int:
    from zleap.sag.db import SourceEvent, get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        filters = [SourceEvent.status.is_(None) | (SourceEvent.status != "DELETED")]
        if source_config_id:
            filters.append(SourceEvent.source_config_id == source_config_id)
        return int(await session.scalar(select(func.count()).select_from(SourceEvent).where(*filters)) or 0)


async def _iter_event_batches(source_config_id: str | None, batch_size: int):
    from zleap.sag.db import SourceEvent, get_session_factory
    from zleap.sag.db.models import EventEntity

    sf = get_session_factory()
    last_id = ""
    while True:
        async with sf() as session:
            filters = [
                SourceEvent.id > last_id,
                SourceEvent.status.is_(None) | (SourceEvent.status != "DELETED"),
            ]
            if source_config_id:
                filters.append(SourceEvent.source_config_id == source_config_id)
            rows = (
                (
                    await session.execute(
                        select(SourceEvent)
                        .where(*filters)
                        .options(selectinload(SourceEvent.event_associations).selectinload(EventEntity.entity))
                        .order_by(SourceEvent.id.asc())
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )
            for event in rows:
                # Eager-touch fields before the session closes.
                _ = event.event_associations
                _ = event.created_time
                _ = event.updated_time
            session.expunge_all()
        if not rows:
            break
        yield rows
        last_id = rows[-1].id


async def rebuild(args: argparse.Namespace) -> int:
    _configure_environment()

    from sag_api.core.config import settings as app_settings
    from sag_api.core.db import init_db
    from sag_api.sag.config_builder import build_engine_config
    from zleap.sag.core.ai.factory import get_embedding_client
    from zleap.sag.core.storage.client import get_es_client
    from zleap.sag.engine import DataEngine
    from zleap.sag.utils.batch import batch_index_to_es

    engine = DataEngine(build_engine_config(app_settings), source_config_id="rebuild-event-vectors")
    await engine.start()
    await init_db()

    total = await _count_events(args.source_config_id)
    scope = args.source_config_id or "ALL"
    print(f"event_vectors rebuild scope={scope} total_events={total}")
    if not args.execute:
        print("dry-run only. Add --execute to generate embeddings and write event_vectors.")
        return 0
    if total == 0:
        return 0

    embedding_client = await get_embedding_client(scenario="general")
    es_client = get_es_client()
    if not await es_client.ping():
        print("vector store ping failed", file=sys.stderr)
        return 2

    processed = indexed = failed_embedding = failed_index = 0
    started = time.perf_counter()
    try:
        async for events in _iter_event_batches(args.source_config_id, args.batch_size):
            documents: list[dict[str, Any]] = []
            for i in range(0, len(events), args.embedding_batch_size):
                batch = events[i : i + args.embedding_batch_size]
                try:
                    title_vectors = await embedding_client.batch_generate([event.title or "" for event in batch])
                    content_vectors = await embedding_client.batch_generate(
                        [
                            f"{event.title or ''}\n\n{(event.content or '')[:args.embedding_max_length]}"
                            for event in batch
                        ]
                    )
                    documents.extend(
                        _build_event_document(event, title_vec, content_vec)
                        for event, title_vec, content_vec in zip(batch, title_vectors, content_vectors)
                    )
                except Exception as error:  # noqa: BLE001
                    print(f"embedding batch failed, fallback single: {error}", file=sys.stderr)
                    for event in batch:
                        try:
                            title_vec = await embedding_client.generate(event.title or "")
                            content_vec = await embedding_client.generate(
                                f"{event.title or ''}\n\n{(event.content or '')[:args.embedding_max_length]}"
                            )
                            documents.append(_build_event_document(event, title_vec, content_vec))
                        except Exception as single_error:  # noqa: BLE001
                            failed_embedding += 1
                            print(f"embedding failed event={event.id}: {single_error}", file=sys.stderr)

            batch_indexed = 0
            batch_failed_index = 0
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for document in documents:
                grouped[str(document.get("source_config_id") or "")].append(document)
            for routing, scoped_documents in grouped.items():
                result = await batch_index_to_es(
                    documents=scoped_documents,
                    es_client=es_client,
                    index_name="event_vectors",
                    batch_size=args.index_batch_size,
                    routing=routing or None,
                )
                batch_indexed += int(result.get("indexed") or 0)
                batch_failed_index += int(result.get("failed") or 0)
            processed += len(events)
            indexed += batch_indexed
            failed_index += batch_failed_index
            elapsed = time.perf_counter() - started
            print(
                f"progress {processed}/{total} indexed={indexed} "
                f"embedding_failed={failed_embedding} index_failed={failed_index} elapsed={elapsed:.1f}s",
                flush=True,
            )
            if failed_index and args.stop_on_index_failure:
                print("stopping because index failures were reported", file=sys.stderr)
                return 3
    finally:
        client = getattr(es_client, "client", None)
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        await engine.aclose()

    print(
        f"done total={total} processed={processed} indexed={indexed} "
        f"embedding_failed={failed_embedding} index_failed={failed_index}"
    )
    return 0 if failed_embedding == 0 and failed_index == 0 else 4


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild zleap-sag event_vectors from source_event rows.")
    parser.add_argument("--execute", action="store_true", help="Actually write event_vectors. Omit for dry-run.")
    parser.add_argument("--source-config-id", help="Optional zleap source_config_id scope.")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--embedding-batch-size", type=int, default=10)
    parser.add_argument("--index-batch-size", type=int, default=50)
    parser.add_argument("--embedding-max-length", type=int, default=500)
    parser.add_argument("--stop-on-index-failure", action="store_true")
    raise SystemExit(asyncio.run(rebuild(parser.parse_args())))


if __name__ == "__main__":
    main()
