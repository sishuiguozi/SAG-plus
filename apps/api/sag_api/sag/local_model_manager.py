"""On-demand local embedding model downloads for the settings UI."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from sag_api.sag.local_model_catalog import ModelKind, ModelSpec, get_model_spec, specs_for

# Kept for callers that previously rendered the embedding picker directly.
MODEL_CATALOG = {spec.file_name: spec.label for spec in specs_for(ModelKind.EMBEDDING)}

_CUDA_RANK_RUNTIME_WHEEL = (
    "https://github.com/JamePeng/llama-cpp-python/releases/download/"
    "v0.3.45-cu131-win-20260801/"
    "llama_cpp_python-0.3.45%2Bcu131-cp312-cp312-win_amd64.whl"
)


class LocalModelManager:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._backend_task: asyncio.Task[None] | None = None
        self._backend_state: dict[str, Any] = {"status": "missing", "error": None}
        self._reranker_backend_task: asyncio.Task[None] | None = None
        self._reranker_backend_state: dict[str, Any] = {"status": "missing", "error": None}

    def _model_path(self, spec: ModelSpec) -> Path:
        return self.model_dir / spec.relative_dir / spec.file_name

    def _existing_model_path(self, spec: ModelSpec) -> Path:
        preferred = self._model_path(spec)
        if preferred.is_file():
            return preferred
        if spec.kind is ModelKind.EMBEDDING and spec.file_name == "bge-m3-Q8_0.gguf":
            legacy = self.model_dir / "bge-m3" / spec.file_name
            if legacy.is_file():
                return legacy
        return preferred

    def _model_status(self, spec: ModelSpec) -> dict[str, Any]:
        path = self._existing_model_path(spec)
        state = self._state.get(spec.file_name, {})
        exists = path.is_file()
        return {
                "file_name": spec.file_name,
                "label": spec.label,
                "kind": spec.kind,
                "runtime": spec.runtime,
                "dimensions": spec.dimensions,
                "size_mb": spec.size_mb,
                "status": "ready" if exists else state.get("status", "missing"),
                "downloaded_bytes": path.stat().st_size if exists else state.get("downloaded_bytes", 0),
                "total_bytes": path.stat().st_size if exists else state.get("total_bytes"),
                "progress": 100 if exists else state.get("progress", 0),
                "error": state.get("error"),
                "model_path": str(path),
            }

    def status(self) -> dict[str, Any]:
        embedding_models = [self._model_status(spec) for spec in specs_for(ModelKind.EMBEDDING)]
        reranker_models = [self._model_status(spec) for spec in specs_for(ModelKind.RERANKER)]
        backend_installed = importlib.util.find_spec("llama_cpp") is not None
        backend = {
            "status": "ready" if backend_installed else self._backend_state["status"],
            "error": None if backend_installed else self._backend_state["error"],
        }
        # ``find_spec("parent.child")`` raises when ``parent`` itself is absent.
        # Probe in two stages so an untouched installation still returns a useful
        # status payload for the settings page.
        native_reranker_installed = self._native_reranker_installed()
        native_reranker = {
            "status": "ready" if native_reranker_installed else self._reranker_backend_state["status"],
            "error": None if native_reranker_installed else self._reranker_backend_state["error"] or (
                "Native GGUF reranking requires a llama-cpp-python build with LlamaEmbedding pooling=rank support"
            ),
        }
        return {
            "embedding": {"backend": backend, "models": embedding_models},
            "reranker": {"backends": {"llama_cpp_rank": native_reranker}, "models": reranker_models},
            # Legacy shape retained for the existing local-embedding endpoint/UI.
            "backend_installed": backend_installed,
            "backend": backend,
            "models": embedding_models,
        }

    @staticmethod
    def _native_reranker_installed() -> bool:
        """Check the optional native rank adapter without importing the heavy library."""
        if importlib.util.find_spec("llama_cpp") is None:
            return False
        return importlib.util.find_spec("llama_cpp.llama_embedding") is not None

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

    async def install_reranker_backend(self) -> dict[str, Any]:
        """Install the opt-in native GGUF rank runtime in the API environment."""
        if self._native_reranker_installed():
            return self.status()
        if self._reranker_backend_task is None:
            self._reranker_backend_state = {"status": "installing", "error": None}
            self._reranker_backend_task = asyncio.create_task(self._install_reranker_backend())
        return self.status()

    async def _install_reranker_backend(self) -> None:
        try:
            await asyncio.to_thread(self._install_reranker_backend_sync)
            self._reranker_backend_state = {"status": "ready", "error": None}
        except Exception as exc:  # noqa: BLE001
            self._reranker_backend_state = {"status": "failed", "error": str(exc)}
        finally:
            self._reranker_backend_task = None

    @staticmethod
    def _install_reranker_backend_sync() -> None:
        # The upstream wheel exposes embedding but not the native ``pooling=rank``
        # interface.  This explicit, opt-in source build adds that interface for
        # BGE/Qwen reranker GGUFs; it can require CMake and a C++ compiler.
        # Prefer the fork's Windows CUDA wheel where it can be used.  It avoids a
        # multi-gigabyte Visual C++ build-tool install and enables GPU ranking.
        # CPU-only machines retain the explicit source-build fallback below.
        if sys.platform == "win32" and sys.version_info[:2] == (3, 12) and shutil.which("nvidia-smi"):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    "--no-cache-dir",
                    _CUDA_RANK_RUNTIME_WHEEL,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return

        runtime_scripts = str(Path(sys.executable).resolve().parent)
        build_env = os.environ.copy()
        build_env["PATH"] = runtime_scripts + os.pathsep + build_env.get("PATH", "")
        # llama.cpp 的子模块中包含超长文件名。只对本次 pip/Git 子进程启用
        # Windows long paths，避免修改用户的全局 Git 配置。
        build_env["GIT_CONFIG_COUNT"] = "1"
        build_env["GIT_CONFIG_KEY_0"] = "core.longpaths"
        build_env["GIT_CONFIG_VALUE_0"] = "true"
        # CMake has a Windows wheel and is installed into the same venv first;
        # this keeps the optional source build self-contained for desktop users.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "cmake>=3.27",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=build_env,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--force-reinstall",
                "--no-cache-dir",
                "llama-cpp-python @ git+https://github.com/JamePeng/llama-cpp-python.git",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=build_env,
        )

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
        unknown = [name for name in files if get_model_spec(name) is None]
        if unknown:
            raise ValueError(f"Unsupported local model: {unknown[0]}")
        for name in dict.fromkeys(files):
            spec = get_model_spec(name)
            assert spec is not None
            if self._existing_model_path(spec).is_file() or name in self._tasks:
                continue
            self._state[name] = {
                "status": "downloading",
                "downloaded_bytes": 0,
                "total_bytes": None,
                "progress": 0,
                "error": None,
            }
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
        spec = get_model_spec(file_name)
        if spec is None:
            raise ValueError(f"Unsupported local model: {file_name}")
        target = self._model_path(spec)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(f"{target.suffix}.part")
        request = Request(f"{spec.source_url}?download=true", headers={"User-Agent": "SAG-plus"})
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
                self._state[file_name].update(
                    downloaded_bytes=written,
                    progress=round(written * 100 / total, 1) if total else 0,
                )
        if total is not None and written != total:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Model download size verification failed")
        if len(etag) == 64 and digest.hexdigest() != etag:
            partial.unlink(missing_ok=True)
            raise RuntimeError("Model download checksum verification failed")
        partial.replace(target)
        self._state[file_name].update(status="ready", downloaded_bytes=written, total_bytes=written, progress=100)
