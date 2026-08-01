import asyncio
from pathlib import Path
import sys

import httpx
import pytest

from sag_api.sag.local_model_manager import LocalModelManager, MODEL_CATALOG


def test_model_catalog_exposes_the_three_supported_embedding_variants():
    assert tuple(MODEL_CATALOG) == (
        "bge-m3-Q8_0.gguf",
        "Qwen3-Embedding-0.6B-Q8_0.gguf",
        "Qwen3-Embedding-4B-Q8_0.gguf",
    )


def test_status_groups_embedding_and_reranker_models(tmp_path: Path):
    manager = LocalModelManager(tmp_path)

    status = manager.status()

    assert [row["file_name"] for row in status["embedding"]["models"]] == list(MODEL_CATALOG)
    assert [row["file_name"] for row in status["reranker"]["models"]] == [
        "bge-reranker-v2-m3-q8_0.gguf",
        "qwen3-reranker-0.6b-q8_0.gguf",
        "Qwen3-Reranker-4B-Q8_0.gguf",
    ]


def test_status_ignores_partial_downloads(tmp_path: Path):
    manager = LocalModelManager(tmp_path)
    partial = tmp_path / "embedding" / "bge-m3-Q8_0.gguf.part"
    partial.parent.mkdir()
    partial.write_bytes(b"partial")

    row = manager.status()["embedding"]["models"][0]

    assert row["file_name"] == "bge-m3-Q8_0.gguf"
    assert row["status"] == "missing"


def test_legacy_bge_q8_file_is_reported_as_ready(tmp_path: Path):
    manager = LocalModelManager(tmp_path)
    legacy = tmp_path / "bge-m3" / "bge-m3-Q8_0.gguf"
    legacy.parent.mkdir()
    legacy.write_bytes(b"existing-model")

    row = manager.status()["embedding"]["models"][0]

    assert row["status"] == "ready"
    assert row["model_path"] == str(legacy)


@pytest.mark.asyncio
async def test_rejects_unknown_model_files(tmp_path: Path):
    manager = LocalModelManager(tmp_path)

    with pytest.raises(ValueError, match="Unsupported local model"):
        await manager.download(["unknown.gguf"])


@pytest.mark.asyncio
async def test_reuses_an_existing_download_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = LocalModelManager(tmp_path)
    release = asyncio.Event()

    async def pending_download(_: str) -> None:
        await release.wait()

    monkeypatch.setattr(manager, "_download", pending_download)
    file_name = "bge-m3-Q8_0.gguf"

    await manager.download([file_name])
    first_task = manager._tasks[file_name]
    await manager.download([file_name, file_name])

    assert manager._tasks[file_name] is first_task
    release.set()
    await first_task


def test_deletes_partial_file_when_download_length_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class ShortResponse:
        headers = {"Content-Length": "4", "ETag": ""}

        def __init__(self) -> None:
            self._read = False

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, _: int) -> bytes:
            if self._read:
                return b""
            self._read = True
            return b"abc"

    manager = LocalModelManager(tmp_path)
    file_name = "bge-m3-Q8_0.gguf"
    manager._state[file_name] = {}
    monkeypatch.setattr("sag_api.sag.local_model_manager.urlopen", lambda *_args, **_kwargs: ShortResponse())

    with pytest.raises(RuntimeError, match="size verification"):
        manager._download_sync(file_name)

    assert not (tmp_path / "embedding" / f"{file_name}.part").exists()


@pytest.mark.asyncio
async def test_installs_llama_backend_in_the_running_python_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manager = LocalModelManager(tmp_path)
    installed = [False]
    command: list[str] = []

    monkeypatch.setattr(
        "sag_api.sag.local_model_manager.importlib.util.find_spec",
        lambda _: object() if installed[0] else None,
    )

    def fake_run(args: list[str], **_: object) -> None:
        command.extend(args)
        installed[0] = True

    monkeypatch.setattr("sag_api.sag.local_model_manager.subprocess.run", fake_run)

    await manager.install_backend()
    assert manager._backend_task is not None
    await manager._backend_task

    assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "llama-cpp-python>=0.3.34" in command
    assert manager.status()["backend"]["status"] == "ready"


@pytest.mark.asyncio
async def test_local_model_endpoints_require_auth_and_return_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from sag_api.api.v1 import system
    from sag_api.main import app

    monkeypatch.setattr(system, "_local_model_manager", LocalModelManager(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            denied = await client.get("/api/v1/system/local-models")
            assert denied.status_code == 401

            registration = await client.post(
                "/api/v1/auth/register",
                json={"email": "local-models@t.com", "password": "password123"},
            )
            assert registration.status_code == 201, registration.text
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

            status = await client.get("/api/v1/system/local-models", headers=headers)
            assert status.status_code == 200
            assert status.json()["embedding"]["models"][0]["file_name"] == "bge-m3-Q8_0.gguf"

            unsupported = await client.post(
                "/api/v1/system/local-models/download",
                headers=headers,
                json={"files": ["unknown.gguf"]},
            )
            assert unsupported.status_code == 422


@pytest.mark.asyncio
async def test_local_embedding_health_check_uses_unsaved_draft_values(monkeypatch: pytest.MonkeyPatch):
    from sag_api.api.v1 import system
    from sag_api.main import app

    class ReadyModelManager:
        def status(self):
            return {
                "backend": {"status": "ready", "error": None},
                "models": [
                    {
                        "file_name": "bge-m3-Q8_0.gguf",
                        "status": "ready",
                        "model_path": "draft-q6.gguf",
                    },
                    {
                        "file_name": "unknown.gguf",
                        "status": "ready",
                        "model_path": "not-in-catalog.gguf",
                    },
                ],
            }

    class FakeLocalClient:
        def __init__(self, model_path: str, *, n_ctx: int, n_threads: int | None) -> None:
            assert model_path == "draft-q6.gguf"
            assert n_ctx == 4096
            assert n_threads == 6

        async def generate(self, text: str) -> list[float]:
            assert text == "SAG-plus local embedding health check"
            return [0.1, 0.2, 0.3]

        async def close(self) -> None:
            return None

    monkeypatch.setattr(system, "_get_local_model_manager", lambda: ReadyModelManager())
    monkeypatch.setattr("sag_api.sag.embedding_backend.LocalEmbeddingClient", FakeLocalClient)

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            request_body = {
                "model_file": "bge-m3-Q8_0.gguf",
                "n_ctx": 4096,
                "n_threads": 6,
            }
            assert (
                await client.post("/api/v1/system/local-models/test", json=request_body)
            ).status_code == 401
            registration = await client.post(
                "/api/v1/auth/register",
                json={"email": "local-health@t.com", "password": "password123"},
            )
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

            response = await client.post(
                "/api/v1/system/local-models/test", headers=headers, json=request_body
            )
            unsupported = await client.post(
                "/api/v1/system/local-models/test",
                headers=headers,
                json={**request_body, "model_file": "unknown.gguf"},
            )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model_file"] == "bge-m3-Q8_0.gguf"
    assert response.json()["dimensions"] == 3
    assert response.json()["elapsed_ms"] >= 0
    assert unsupported.status_code == 422
    assert unsupported.json() == {
        "error": {
            "code": "validation_error",
            "message": "Unsupported local embedding model",
        }
    }


@pytest.mark.asyncio
async def test_local_embedding_health_check_serializes_and_closes_temporary_clients(
    monkeypatch: pytest.MonkeyPatch,
):
    from sag_api.api.v1 import system

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    clients = []

    class FakeLocalClient:
        def __init__(self, _model_path: str, *, n_ctx: int, n_threads: int | None) -> None:
            self.index = len(clients)
            self.n_ctx = n_ctx
            self.n_threads = n_threads
            self.closed = False
            clients.append(self)

        async def generate(self, _text: str) -> list[float]:
            if self.index == 0:
                first_started.set()
                await release_first.wait()
                return [0.1, 0.2, 0.3]
            if self.index == 1:
                second_started.set()
                return [0.4, 0.5, 0.6]
            raise RuntimeError("simulated local inference failure")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("sag_api.sag.embedding_backend.LocalEmbeddingClient", FakeLocalClient)

    first_request = asyncio.create_task(
        system._generate_local_embedding_test("draft-q6.gguf", n_ctx=4096, n_threads=6)
    )
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second_request = asyncio.create_task(
        system._generate_local_embedding_test("draft-q6.gguf", n_ctx=4096, n_threads=6)
    )
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(second_started.wait(), timeout=0.05)
    finally:
        release_first.set()

    first_vector, second_vector = await asyncio.wait_for(
        asyncio.gather(first_request, second_request), timeout=1
    )
    with pytest.raises(RuntimeError, match="simulated local inference failure"):
        await asyncio.wait_for(
            system._generate_local_embedding_test("draft-q6.gguf", n_ctx=512, n_threads=None),
            timeout=1,
        )

    assert first_vector == [0.1, 0.2, 0.3]
    assert second_vector == [0.4, 0.5, 0.6]
    assert len(clients) == 3
    assert all(client.closed for client in clients)
