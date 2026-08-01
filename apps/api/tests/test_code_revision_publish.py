from __future__ import annotations

import os
from pathlib import Path

import pytest


class FakeQueue:
    def __init__(self):
        self.ids: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.ids.append(job_id)


@pytest.mark.asyncio
async def test_stage_code_replacement_keeps_old_file_and_sets_pending(tmp_path: Path, monkeypatch):
    from sag_api.db.models import Document, Job, Source
    from sag_api.enums import DocumentStatus, JobType
    from sag_api.services import document_service as ds

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = 0
            self._doc = None
            self._job = None

        def add(self, obj):
            self.added.append(obj)
            if isinstance(obj, Document):
                self._doc = obj
            if isinstance(obj, Job):
                self._job = obj

        async def commit(self):
            self.committed += 1

        async def refresh(self, obj):
            return None

        async def get(self, model, ident):
            if model is Document:
                return self._doc
            if model is Job:
                return self._job
            return None

        async def execute(self, *args, **kwargs):
            return None

        async def scalar(self, *args, **kwargs):
            # First call in stage looks up existing by relative path.
            if self._doc is None:
                # create path through create_document_from_upload mock below
                return None
            return self._doc

    session = FakeSession()
    source = Source(id="src1", name="s", sag_source_config_id="cfg", document_count=1, chunk_count=3, event_count=1)
    queue = FakeQueue()

    # existing ready document
    live = tmp_path / "live.py"
    live.write_text("old = 1\n", encoding="utf-8")
    existing = Document(
        id="doc1",
        source_id="src1",
        filename="live.py",
        content_type="text/x-python",
        size_bytes=7,
        storage_path=str(live),
        status=DocumentStatus.READY,
        chunk_count=3,
        event_count=1,
        progress=100,
        sag_source_id="old-sag",
        relative_path="pkg/live.py",
        content_sha256="a" * 64,
        code_language="python",
    )
    session._doc = existing

    async def fake_find(session_, source_id, relative_path):
        return existing

    monkeypatch.setattr(ds, "find_document_by_relative_path", fake_find)
    monkeypatch.setattr(ds, "_ensure_ingest_disk", lambda: None)

    new_bytes = b"new = 2\n"
    doc, job = await ds.stage_code_document_upload(
        session,
        source,
        filename="live.py",
        content_type="text/x-python",
        data=new_bytes,
        upload_dir=str(tmp_path),
        job_queue=queue,
        relative_path="pkg/live.py",
        code_language="python",
    )

    assert doc is existing
    assert existing.status == DocumentStatus.PENDING
    assert existing.storage_path == str(live)
    assert existing.content_sha256 == "a" * 64  # old hash remains authoritative
    assert job.type == JobType.PROCESS_DOCUMENT
    repl = job.payload["code_replacement"]
    assert os.path.exists(repl["pending_path"])
    assert open(repl["pending_path"], "rb").read() == new_bytes
    assert repl["old"]["sag_source_id"] == "old-sag"
    assert queue.ids == [job.id]


@pytest.mark.asyncio
async def test_publish_code_replacement_swaps_file_and_enqueues_cleanup(tmp_path: Path):
    from sag_api.db.models import Document, Source
    from sag_api.enums import DocumentStatus, JobType
    from sag_api.services.document_service import publish_code_replacement

    live = tmp_path / "live.py"
    live.write_text("old\n", encoding="utf-8")
    pending = tmp_path / "pending.py"
    pending.write_text("new\n", encoding="utf-8")

    document = Document(
        id="doc1",
        source_id="src1",
        filename="live.py",
        content_type="text/x-python",
        size_bytes=4,
        storage_path=str(live),
        status=DocumentStatus.PENDING,
        chunk_count=3,
        event_count=1,
        progress=0,
        sag_source_id="old-sag",
        relative_path="pkg/live.py",
        content_sha256="a" * 64,
        code_language="python",
        token_usage=10,
    )
    source = Source(id="src1", name="s", sag_source_config_id="cfg", chunk_count=3, event_count=1)
    queue = FakeQueue()
    added = []

    class FakeSession:
        def add(self, obj):
            added.append(obj)

        async def commit(self):
            return None

        async def refresh(self, obj):
            return None

        async def execute(self, *args, **kwargs):
            return None

    session = FakeSession()
    replacement = {
        "pending_path": str(pending),
        "new_content_sha256": "b" * 64,
        "new_code_language": "python",
        "new_size_bytes": 4,
        "relative_path": "pkg/live.py",
        "old": {
            "storage_path": str(live),
            "content_sha256": "a" * 64,
            "code_language": "python",
            "size_bytes": 4,
            "chunk_count": 3,
            "event_count": 1,
            "sag_source_id": "old-sag",
            "status": "ready",
            "filename": "live.py",
            "token_usage": 10,
        },
    }

    cleanup = await publish_code_replacement(
        session,
        document,
        source,
        replacement=replacement,
        outcome_source_id="new-sag",
        outcome_chunk_count=5,
        outcome_event_count=2,
        outcome_token_usage=20,
        job_queue=queue,
    )

    assert live.read_text(encoding="utf-8") == "new\n"
    assert not pending.exists()
    assert document.content_sha256 == "b" * 64
    assert document.sag_source_id == "new-sag"
    assert document.chunk_count == 5
    assert document.event_count == 2
    assert document.status == DocumentStatus.READY
    assert cleanup is not None
    assert cleanup.type == JobType.CLEANUP_DOCUMENT_REVISION
    assert cleanup.payload["old_sag_source_id"] == "old-sag"
    assert queue.ids == [cleanup.id]


@pytest.mark.asyncio
async def test_rollback_code_replacement_deletes_pending_and_restores_old(tmp_path: Path):
    from sag_api.db.models import Document
    from sag_api.enums import DocumentStatus
    from sag_api.services.document_service import rollback_code_replacement

    live = tmp_path / "live.py"
    live.write_text("old\n", encoding="utf-8")
    pending = tmp_path / "pending.py"
    pending.write_text("new\n", encoding="utf-8")
    document = Document(
        id="doc1",
        source_id="src1",
        filename="live.py",
        content_type="text/x-python",
        size_bytes=99,
        storage_path=str(live),
        status=DocumentStatus.PENDING,
        chunk_count=0,
        event_count=0,
        progress=0,
        sag_source_id=None,
        relative_path="pkg/live.py",
        content_sha256=None,
        code_language=None,
        token_usage=0,
    )
    replacement = {
        "pending_path": str(pending),
        "old": {
            "storage_path": str(live),
            "content_sha256": "a" * 64,
            "code_language": "python",
            "size_bytes": 4,
            "chunk_count": 3,
            "event_count": 1,
            "sag_source_id": "old-sag",
            "status": "ready",
            "filename": "live.py",
            "token_usage": 10,
        },
    }
    await rollback_code_replacement(document, replacement)
    assert not pending.exists()
    assert document.content_sha256 == "a" * 64
    assert document.sag_source_id == "old-sag"
    assert document.status == DocumentStatus.READY
    assert document.chunk_count == 3
