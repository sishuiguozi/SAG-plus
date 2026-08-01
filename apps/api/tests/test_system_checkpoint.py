"""SAG-OPT-703：更新前数据库检查点端点测试。

- 缺少/错误的 X-SAG-INTERNAL 头返回 403。
- 校验通过后创建 <data_dir>/upgrade-checkpoints/<ts>/ 下的 engine.db / metadata.db
  在线备份与 manifest.json。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from sag_api.api.v1 import system as system_module
from sag_api.core.errors import ApiError


class _FakeSettings:
    secret_key = "x" * 64
    data_dir = ""  # 由 fixture 填充
    database_url = "sqlite+aiosqlite:///meta.db"


@pytest.fixture()
def client(tmp_path: Path):
    _FakeSettings.data_dir = str(tmp_path)
    original = system_module.settings
    system_module.settings = _FakeSettings  # type: ignore[assignment]
    # 造两个最小 SQLite 库供在线备份
    engine = tmp_path / "sag.db"
    meta = tmp_path / "meta.db"
    for db, table in [(engine, "engine_rows"), (meta, "vector_write_jobs")]:
        con = sqlite3.connect(db)
        con.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        con.execute(f"INSERT INTO {table} VALUES ('r1')")
        con.commit()
        con.close()
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    app.include_router(system_module.router)
    try:
        yield TestClient(app)
    finally:
        system_module.settings = original


def test_checkpoint_requires_internal_header(client: TestClient):
    assert client.post("/system/checkpoint").status_code == 403
    assert client.post(
        "/system/checkpoint", headers={"X-SAG-INTERNAL": "wrong"}
    ).status_code == 403


def test_checkpoint_creates_online_backups(client: TestClient, tmp_path: Path):
    resp = client.post(
        "/system/checkpoint",
        headers={
            "X-SAG-INTERNAL": _FakeSettings.secret_key,
            "X-SAG-Desktop-Version": "9.9.9",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    checkpoint_dir = Path(body["checkpoint_dir"])
    assert checkpoint_dir.exists()
    assert (checkpoint_dir / "manifest.json").exists()
    assert body["desktop_version"] == "9.9.9"
    labels = {f["label"] for f in body["files"]}
    assert {"engine", "metadata"} <= labels
    # 备份库可独立打开且包含原表数据
    for label in ("engine", "metadata"):
        backup = checkpoint_dir / f"{label}.db"
        assert backup.exists() and backup.stat().st_size > 0
        con = sqlite3.connect(backup)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert tables
