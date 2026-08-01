import asyncio
from pathlib import Path
import sys

import httpx
import pytest

from sag_api.sag.local_model_manager import LocalModelManager, MODEL_CATALOG


def test_model_catalog_exposes_the_five_supported_variants():
    assert tuple(MODEL_CATALOG) == (
        "bge-m3-Q4_K_M.gguf",
        "bge-m3-Q5_K_M.gguf",
        "bge-m3-Q6_K.gguf",
        "bge-m3-Q8_0.gguf",
        "bge-m3-FP16.gguf",
    )


def test_status_ignores_partial_downloads(tmp_path: Path):
    manager = LocalModelManager(tmp_path)
    (tmp_path / "bge-m3-Q8_0.gguf.part").write_bytes(b"partial")

    row = manager.status()["models"][3]

    assert row["file_name"] == "bge-m3-Q8_0.gguf"
    assert row["status"] == "missing"


@pytest.mark.asyncio
async def test_rejects_unknown_model_files(tmp_path: Path):
    manager = LocalModelManager(tmp_path)

    with pytest.raises(ValueError, match="Unsupported local embedding model"):
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

    assert not (tmp_path / f"{file_name}.part").exists()


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
            assert status.json()["models"][3]["file_name"] == "bge-m3-Q8_0.gguf"

            unsupported = await client.post(
                "/api/v1/system/local-models/download",
                headers=headers,
                json={"files": ["unknown.gguf"]},
            )
            assert unsupported.status_code == 422


@pytest.mark.asyncio
async def test_local_embedding_health_check_runs_a_real_client_call(monkeypatch: pytest.MonkeyPatch):
    from sag_api.api.v1 import system
    from sag_api.core.config import settings
    from sag_api.main import app

    class ReadyModelManager:
        def status(self):
            return {
                "backend": {"status": "ready", "error": None},
                "models": [
                    {
                        "file_name": settings.embedding_local_model_file,
                        "status": "ready",
                    }
                ],
            }

    class FakeLocalClient:
        model_path = "test.gguf"

        async def generate(self, text: str) -> list[float]:
            assert text == "SAG-plus local embedding health check"
            return [0.1, 0.2, 0.3]

        async def batch_generate(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

        def warmup(self) -> None:
            return None

    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(system, "_get_local_model_manager", lambda: ReadyModelManager())
    monkeypatch.setattr("sag_api.sag.embedding_backend._local_client", lambda: FakeLocalClient())

    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                assert (await client.post("/api/v1/system/local-models/test")).status_code == 401
                registration = await client.post(
                    "/api/v1/auth/register",
                    json={"email": "local-health@t.com", "password": "password123"},
                )
                headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

                response = await client.post("/api/v1/system/local-models/test", headers=headers)
    finally:
        from sag_api.sag.embedding_backend import uninstall_embedding_backend

        uninstall_embedding_backend()

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model_file"] == settings.embedding_local_model_file
    assert response.json()["dimensions"] == 3
    assert response.json()["elapsed_ms"] >= 0
