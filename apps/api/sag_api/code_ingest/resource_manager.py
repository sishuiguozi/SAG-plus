"""Versioned, resumable Tree-sitter parser resource management."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4
from typing import Protocol

from sag_api.schemas.tree_sitter import TreeSitterResourceState, TreeSitterResourceStatus

TREE_SITTER_LANGUAGE_PACK_VERSION = "1.13.7"
TREE_SITTER_ESTIMATED_BYTES = 360 * 1024 * 1024


class LanguagePackAdapter(Protocol):
    version: str
    estimated_total_bytes: int

    def manifest_languages(self) -> tuple[str, ...]: ...

    def downloaded_languages(self, target_dir: Path) -> set[str]: ...

    async def download(self, language: str, target_dir: Path) -> None: ...

    def activate(self, target_dir: Path) -> None: ...


_SUBPROCESS_SCRIPT = """
import json
import sys
import time
import tree_sitter_language_pack as pack

target, operation, language = sys.argv[1:4]
# tree-sitter-language-pack 1.13.x: configure + prefetch is the reliable path.
# plain download() can return success without a loadable cache entry.
pack.configure(pack.PackConfig(cache_dir=target, languages=[]))
if operation == "download":
    pack.prefetch([language])
elif operation == "download_many":
    names = [part for part in language.split(",") if part]
    if names:
        pack.prefetch(names)
print(json.dumps(sorted(pack.downloaded_languages())))
"""


class InstalledLanguagePackAdapter:
    """Keep staging downloads outside the main process's global pack config."""

    version = TREE_SITTER_LANGUAGE_PACK_VERSION
    estimated_total_bytes = TREE_SITTER_ESTIMATED_BYTES

    @staticmethod
    def manifest_languages() -> tuple[str, ...]:
        import tree_sitter_language_pack as pack

        return tuple(sorted(pack.manifest_languages()))

    @staticmethod
    def _query(target_dir: Path, operation: str, language: str = "-") -> set[str]:
        completed = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(target_dir), operation, language],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"Tree-sitter resource command failed ({operation}): {detail or completed.returncode}"
            )
        output = completed.stdout.strip().splitlines()
        return set(json.loads(output[-1])) if output else set()

    def downloaded_languages(self, target_dir: Path) -> set[str]:
        if not target_dir.exists():
            return set()
        return self._query(target_dir, "status")

    async def download(self, language: str, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        before = self.downloaded_languages(target_dir)
        await asyncio.to_thread(self._query, target_dir, "download", language)
        after = self.downloaded_languages(target_dir)
        if language not in after and len(after) <= len(before):
            raise RuntimeError(f"Tree-sitter language download produced no cache entry: {language}")

    async def download_many(self, languages: list[str], target_dir: Path) -> None:
        if not languages:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._query, target_dir, "download_many", ",".join(languages))

    @staticmethod
    def activate(target_dir: Path) -> None:
        import tree_sitter_language_pack as pack

        pack.configure(pack.PackConfig(cache_dir=str(target_dir), languages=[]))
        # Ensure already-downloaded grammars are loadable in-process.
        try:
            names = pack.downloaded_languages()
            if names:
                pack.prefetch(names)
        except Exception:
            pack.init(pack.PackConfig(cache_dir=str(target_dir), languages=[]))


class TreeSitterResourceManager:
    def __init__(
        self,
        root_dir: Path,
        *,
        adapter: LanguagePackAdapter | None = None,
    ) -> None:
        self.adapter = adapter or InstalledLanguagePackAdapter()
        self.root_dir = Path(root_dir)
        self.version_dir = self.root_dir / self.adapter.version
        self.active_dir = self.version_dir / "active"
        self.staging_dir = self.version_dir / "staging"
        self._checkpoint_path = self.version_dir / "download.json"
        self._task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._pause_requested = False
        self._state: TreeSitterResourceState | None = None
        self._error: str | None = None

    def _manifest(self) -> tuple[str, ...]:
        return tuple(self.adapter.manifest_languages())

    def _installed(self, target_dir: Path) -> set[str]:
        return self.adapter.downloaded_languages(target_dir)

    @staticmethod
    def _disk_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    def status(self) -> TreeSitterResourceStatus:
        manifest = set(self._manifest())
        active = self._installed(self.active_dir)
        staging = self._installed(self.staging_dir)
        visible = staging if self._state in {"downloading", "paused"} else active
        if self._state is not None:
            state = self._state
        elif active == manifest and manifest:
            state = "ready"
        elif active:
            state = "failed"
        else:
            state = "missing"
        error = self._error
        if state == "failed" and error is None and active != manifest:
            error = f"Parser cache is incomplete: {len(active)}/{len(manifest)} languages installed"
        disk_bytes = self._disk_bytes(self.version_dir)
        installed_count = len(visible & manifest)
        total_count = len(manifest)
        progress = round(installed_count * 100 / total_count) if total_count else 0
        return TreeSitterResourceStatus(
            version=self.adapter.version,
            state=state,
            installed_languages=installed_count,
            total_languages=total_count,
            downloaded_bytes=min(disk_bytes, self.adapter.estimated_total_bytes),
            total_bytes=self.adapter.estimated_total_bytes,
            disk_bytes=disk_bytes,
            progress=progress,
            error=error,
        )

    async def start_download(self) -> TreeSitterResourceStatus:
        async with self._operation_lock:
            if self._task is not None and not self._task.done():
                return self.status()
            # Clear stale in-memory failure once the on-disk pack is complete.
            manifest = set(self._manifest())
            active = self._installed(self.active_dir)
            staging = self._installed(self.staging_dir)
            if manifest and active == manifest:
                self._state = "ready"
                self._error = None
                self.activate_if_ready()
                self._cleanup_stale_trees()
                return self.status()
            # Complete staging with incomplete/missing active: promote without re-fetch.
            if manifest and staging == manifest:
                try:
                    self._promote_staging()
                    self._state = "ready"
                    self._error = None
                    self.activate_if_ready()
                    return self.status()
                except Exception as exc:  # noqa: BLE001
                    # Fall through to download task only if promote truly failed
                    # and languages are still missing.
                    if self._installed(self.active_dir) == manifest:
                        self._state = "ready"
                        self._error = None
                        return self.status()
                    self._error = str(exc)
            if self.status().state == "ready":
                return self.status()
            self._pause_requested = False
            self._error = None
            self._state = "downloading"
            self._task = asyncio.create_task(self._run_download())
        return self.status()

    async def pause(self) -> TreeSitterResourceStatus:
        self._pause_requested = True
        return self.status()

    async def resume(self) -> TreeSitterResourceStatus:
        return await self.start_download()

    async def repair(self) -> TreeSitterResourceStatus:
        async with self._operation_lock:
            if self._task is not None and not self._task.done():
                return self.status()
            manifest = set(self._manifest())
            if manifest and self._installed(self.active_dir) == manifest:
                self._state = "ready"
                self._error = None
                self.activate_if_ready()
                self._best_effort_rmtree(self.staging_dir)
                self._cleanup_stale_trees()
                return self.status()
            self.version_dir.mkdir(parents=True, exist_ok=True)
            if not self.staging_dir.exists() and self.active_dir.exists():
                shutil.copytree(self.active_dir, self.staging_dir)
            self._pause_requested = False
            self._error = None
            self._state = "downloading"
            self._task = asyncio.create_task(self._run_download())
        return self.status()

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            await task

    async def close(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def activate_if_ready(self) -> bool:
        manifest = set(self._manifest())
        if manifest and self._installed(self.active_dir) == manifest:
            activate = getattr(self.adapter, "activate", None)
            if activate is not None:
                activate(self.active_dir)
            return True
        return False

    def _seed_staging_from_active(self) -> None:
        """Reuse already-installed active grammars so ready packs are never re-fetched."""
        if not self.active_dir.exists():
            return
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        # Only copy files staging does not already have.
        for item in self.active_dir.iterdir():
            if not item.is_file():
                continue
            target = self.staging_dir / item.name
            if target.exists():
                continue
            try:
                shutil.copy2(item, target)
            except OSError:
                # Locked files can still be listed by the pack adapter from active;
                # missing copies are handled by pending-language download below.
                continue

    async def _run_download(self) -> None:
        try:
            self.version_dir.mkdir(parents=True, exist_ok=True)
            manifest = self._manifest()
            manifest_set = set(manifest)

            # If active is already complete, never re-download.
            if manifest_set and self._installed(self.active_dir) == manifest_set:
                self._state = "ready"
                self._error = None
                self._best_effort_rmtree(self.staging_dir)
                self._cleanup_stale_trees()
                self.activate_if_ready()
                return

            self.staging_dir.mkdir(parents=True, exist_ok=True)
            # Carry forward whatever is already installed in active/staging.
            self._seed_staging_from_active()
            installed = self._installed(self.staging_dir) | self._installed(self.active_dir)
            # Reflect seeded progress immediately in checkpoint/status.
            self._write_checkpoint(installed & manifest_set, len(manifest))
            pending = [language for language in manifest if language not in installed]
            if not pending:
                # Staging/active already cover the full manifest.
                if self._installed(self.staging_dir) != manifest_set:
                    # Ensure staging mirrors the complete set before promote.
                    self._seed_staging_from_active()
                if self._installed(self.staging_dir) != manifest_set and self._installed(self.active_dir) == manifest_set:
                    self._state = "ready"
                    self._error = None
                    self.activate_if_ready()
                    return
                self._promote_staging()
                self._state = "ready"
                self._error = None
                self.activate_if_ready()
                return
            batch_size = 8
            for offset in range(0, len(pending), batch_size):
                if self._pause_requested:
                    self._state = "paused"
                    return
                batch = pending[offset : offset + batch_size]
                download_many = getattr(self.adapter, "download_many", None)
                # Prefer small batches for throughput, but still honor pause between
                # languages when the adapter only supports one-by-one downloads.
                if callable(download_many) and not self._pause_requested:
                    await download_many(batch, self.staging_dir)
                    installed = self._installed(self.staging_dir)
                    self._write_checkpoint(installed, len(manifest))
                    if self._pause_requested:
                        self._state = "paused"
                        return
                else:
                    for language in batch:
                        if self._pause_requested:
                            self._state = "paused"
                            return
                        await self.adapter.download(language, self.staging_dir)
                        installed = self._installed(self.staging_dir)
                        self._write_checkpoint(installed, len(manifest))
            # Prefer staging completeness; fall back to active+staging union so a
            # ready active pack never fails verification after a no-op download.
            self._seed_staging_from_active()
            verified = self._installed(self.staging_dir)
            combined = verified | self._installed(self.active_dir)
            if verified != set(manifest) and combined == set(manifest) and self._installed(self.active_dir) == set(manifest):
                self._state = "ready"
                self._error = None
                self._best_effort_rmtree(self.staging_dir)
                self.activate_if_ready()
                return
            if verified != set(manifest):
                missing = sorted(set(manifest) - verified)
                raise RuntimeError(f"Parser verification failed; missing: {', '.join(missing[:8])}")
            self._promote_staging()
            self._state = "ready"
            self._error = None
            self.activate_if_ready()
        except asyncio.CancelledError:
            self._state = "paused"
            raise
        except Exception as exc:  # noqa: BLE001
            self._state = "failed"
            self._error = str(exc)
        finally:
            self._task = None

    def _write_checkpoint(self, installed: set[str], total: int) -> None:
        self.version_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.adapter.version,
            "installed_languages": sorted(installed),
            "total_languages": total,
        }
        self._checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _best_effort_rmtree(self, path: Path) -> None:
        """Remove a directory tree; on Windows locked DLLs, rename aside first."""
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            pass
        trash = path.with_name(f"{path.name}.trash-{uuid4().hex}")
        try:
            path.rename(trash)
        except OSError:
            # Still locked and unrenamable; leave it for a later cleanup pass.
            return
        try:
            shutil.rmtree(trash)
        except OSError:
            return

    def _cleanup_stale_trees(self) -> None:
        for child in self.version_dir.iterdir() if self.version_dir.exists() else []:
            name = child.name
            if name == "previous" or name.startswith("previous-") or ".trash-" in name:
                self._best_effort_rmtree(child)

    def _merge_tree(self, source: Path, destination: Path) -> None:
        """Copy/replace files from source into destination without deleting locked roots."""
        destination.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            rel = item.relative_to(source)
            target = destination / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
            shutil.copy2(item, tmp)
            tmp.replace(target)

    def _promote_staging(self) -> None:
        """Make staging the active pack without failing on Windows file locks.

        Language DLLs may stay mapped by the running API process. Deleting or
        replacing the live ``active`` / ``previous`` trees can raise WinError 5.
        If ``active`` is already complete we keep it; otherwise we rename with a
        unique backup name or fall back to merging files into ``active``.
        """
        if not self.staging_dir.exists():
            return

        manifest = set(self._manifest())
        staging_ok = self._installed(self.staging_dir) == manifest and bool(manifest)
        if not staging_ok:
            missing = sorted(manifest - self._installed(self.staging_dir))
            raise RuntimeError(
                "Parser verification failed before promote; missing: "
                + ", ".join(missing[:8])
            )

        active_ok = self._installed(self.active_dir) == manifest
        if active_ok:
            # Current process may already hold active DLLs open. Staging is only
            # a completed mirror, so keep active and drop staging best-effort.
            self._best_effort_rmtree(self.staging_dir)
            self._cleanup_stale_trees()
            return

        backup_dir = self.version_dir / f"previous-{time.time_ns()}-{uuid4().hex[:8]}"
        try:
            if self.active_dir.exists():
                self.active_dir.replace(backup_dir)
            self.staging_dir.replace(self.active_dir)
        except OSError:
            # Active tree is locked: copy staging contents over it instead.
            self._merge_tree(self.staging_dir, self.active_dir)
            if self._installed(self.active_dir) != manifest:
                raise
            self._best_effort_rmtree(self.staging_dir)
        else:
            self._best_effort_rmtree(backup_dir)

        self._cleanup_stale_trees()
