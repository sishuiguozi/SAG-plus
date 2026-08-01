"""On-demand local embedding model downloads for the settings UI."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

MODEL_REPOSITORY = "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/"
MODEL_CATALOG = {
    "bge-m3-Q4_K_M.gguf": "Low memory",
    "bge-m3-Q5_K_M.gguf": "Balanced",
    "bge-m3-Q6_K.gguf": "Higher precision",
    "bge-m3-Q8_0.gguf": "Recommended",
    "bge-m3-FP16.gguf": "Highest precision",
}


class LocalModelManager:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._backend_task: asyncio.Task[None] | None = None
        self._backend_state: dict[str, Any] = {"status": "missing", "error": None}

    def status(self) -> dict[str, Any]:
        models = []
        for file_name, label in MODEL_CATALOG.items():
            path = self.model_dir / file_name
            state = self._state.get(file_name, {})
            exists = path.is_file()
            models.append({
                "file_name": file_name,
                "label": label,
                "status": "ready" if exists else state.get("status", "missing"),
                "downloaded_bytes": path.stat().st_size if exists else state.get("downloaded_bytes", 0),
                "total_bytes": path.stat().st_size if exists else state.get("total_bytes"),
                "progress": 100 if exists else state.get("progress", 0),
                "error": state.get("error"),
                "model_path": str(path),
            })
        backend_installed = importlib.util.find_spec("llama_cpp") is not None
        backend = {
            "status": "ready" if backend_installed else self._backend_state["status"],
            "error": None if backend_installed else self._backend_state["error"],
        }
        return {"backend_installed": backend_installed, "backend": backend, "models": models}

    async def install_backend(self) -> dict[str, Any]:
        """Install llama-cpp-python into the Python environment running the API."""
        if importlib.util.find_spec("llama_cpp") is not None:
            return self.status()
        if self._backend_task is None:
            self._backend_state = {"status": "installing", "error": None}
            self._backend_task = asyncio.create_task(self._install_backend())
        return self.status()

    async def _install_backend(self) -> None:
        try:
            await asyncio.to_thread(self._install_backend_sync)
            self._backend_state = {"status": "ready", "error": None}
        except Exception as exc:  # noqa: BLE001
            self._backend_state = {"status": "failed", "error": str(exc)}
        finally:
            self._backend_task = None

    @staticmethod
    def _install_backend_sync() -> None:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "llama-cpp-python>=0.3.34",
                "--extra-index-url",
                "https://abetlen.github.io/llama-cpp-python/whl/cpu",
                "--only-binary",
                ":all:",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    async def download(self, files: list[str]) -> dict[str, Any]:
        unknown = [name for name in files if name not in MODEL_CATALOG]
        if unknown:
            raise ValueError(f"Unsupported local embedding model: {unknown[0]}")
        for name in dict.fromkeys(files):
            if (self.model_dir / name).is_file() or name in self._tasks:
                continue
            self._state[name] = {"status": "downloading", "downloaded_bytes": 0, "total_bytes": None, "progress": 0, "error": None}
            self._tasks[name] = asyncio.create_task(self._download(name))
        return self.status()

    async def _download(self, file_name: str) -> None:
        try:
            await asyncio.to_thread(self._download_sync, file_name)
        except Exception as exc:  # noqa: BLE001
            self._state[file_name].update(status="failed", error=str(exc))
        finally:
            self._tasks.pop(file_name, None)

    def _download_sync(self, file_name: str) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        target = self.model_dir / file_name
        partial = target.with_suffix(f"{target.suffix}.part")
        request = Request(f"{MODEL_REPOSITORY}{file_name}?download=true", headers={"User-Agent": "SAG-plus"})
        with urlopen(request, timeout=30) as response, partial.open("wb") as output:  # noqa: S310
            total = int(response.headers.get("Content-Length") or 0) or None
            etag = (response.headers.get("ETag") or "").strip('"')
            self._state[file_name]["total_bytes"] = total
            digest = hashlib.sha256()
            written = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                self._state[file_name].update(downloaded_bytes=written, progress=round(written * 100 / total, 1) if total else 0)
        if total is not None and written != total:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Model download size verification failed")
        if len(etag) == 64 and digest.hexdigest() != etag:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Model download checksum verification failed")
        partial.replace(target)
        self._state[file_name].update(status="ready", downloaded_bytes=written, total_bytes=written, progress=100)
