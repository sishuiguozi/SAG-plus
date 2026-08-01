from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_cleanup_skips_when_hash_mismatch():
    from sag_api.enums import JobType
    from sag_api.jobs.tasks import cleanup_document_revision

    calls = []

    class FakeSession:
        async def get(self, model, ident):
            if model.__name__ == "Document":
                return SimpleNamespace(
                    id="doc1",
                    source_id="src1",
                    content_sha256="c" * 64,  # different from expected
                    sag_source_id="new-sag",
                )
            return SimpleNamespace(id="src1", sag_source_config_id="cfg")

        async def commit(self):
            return None

    class FakeEngine:
        async def delete_document_data(self, *args, **kwargs):
            calls.append((args, kwargs))

    job = SimpleNamespace(
        id="j1",
        document_id="doc1",
        source_id="src1",
        payload={
            "document_id": "doc1",
            "source_id": "src1",
            "old_sag_source_id": "old-sag",
            "new_content_sha256": "b" * 64,
        },
        progress=0,
        type=JobType.CLEANUP_DOCUMENT_REVISION,
    )
    await cleanup_document_revision(FakeSession(), job, engine_manager=FakeEngine())
    assert calls == []
    assert job.progress == 1.0


@pytest.mark.asyncio
async def test_cleanup_deletes_old_revision_when_hash_matches():
    from sag_api.enums import JobType
    from sag_api.jobs.tasks import cleanup_document_revision

    calls = []

    class FakeSession:
        async def get(self, model, ident):
            name = getattr(model, "__name__", "")
            if name == "Document":
                return SimpleNamespace(
                    id="doc1",
                    source_id="src1",
                    content_sha256="b" * 64,
                    sag_source_id="new-sag",
                )
            return SimpleNamespace(id="src1", sag_source_config_id="cfg")

        async def commit(self):
            return None

    class FakeEngine:
        async def delete_document_data(self, source_config_id, document_source_id, *, source=None):
            calls.append((source_config_id, document_source_id, getattr(source, "id", None)))

    job = SimpleNamespace(
        id="j1",
        document_id="doc1",
        source_id="src1",
        payload={
            "document_id": "doc1",
            "source_id": "src1",
            "old_sag_source_id": "old-sag",
            "new_content_sha256": "b" * 64,
        },
        progress=0,
        type=JobType.CLEANUP_DOCUMENT_REVISION,
    )
    await cleanup_document_revision(FakeSession(), job, engine_manager=FakeEngine())
    assert calls == [("cfg", "old-sag", "src1")]
    assert job.progress == 1.0
