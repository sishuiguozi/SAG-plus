"""SAG-OPT-604：运行期性能指标测试。

- PerformanceRing 记录、分位数、慢请求列表正确。
- PerformanceMiddleware 会记录 HTTP 请求（通过独立 mini app 验证）。
- /api/v1/system/metrics 需要认证并返回统计。
"""

from __future__ import annotations

import httpx
import pytest


def test_performance_ring_summary():
    from sag_api.core.performance import PerformanceRing

    ring = PerformanceRing(window=128, slow_threshold_ms=50.0)
    for i in range(1, 101):
        ring.record(duration_ms=float(i), method="GET", path="/api/v1/test",
                    status=200, request_id=f"r{i}")
    summary = ring.summary()
    assert summary["sample_count"] == 100
    assert summary["p50_ms"] >= 50.0  # 1..100 的 P50 ≈ 50
    assert summary["p95_ms"] >= 95.0
    assert summary["p99_ms"] >= 99.0
    assert summary["slow_count"] == 51  # >= 50ms 的有 50..100 共 51 条
    assert summary["recent_slow"][0]["duration_ms"] == 100.0
    assert summary["by_path"][0]["route"] == "GET /api/v1/test"

    # 窗口裁剪
    for _ in range(200):
        ring.record(duration_ms=1.0, method="GET", path="/x", status=200, request_id="w")
    assert ring.summary()["sample_count"] == 128


def test_performance_middleware_records_requests():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sag_api.core.performance import PerformanceMiddleware, PerformanceRing

    ring = PerformanceRing(window=64, slow_threshold_ms=0.1)  # 全部视为慢
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(PerformanceMiddleware, ring=ring)
    client = TestClient(app)
    client.get("/ping")
    client.get("/ping")
    summary = ring.summary()
    assert summary["sample_count"] == 2
    assert summary["by_path"][0]["route"] == "GET /ping"
    assert summary["slow_count"] == 2


@pytest.mark.asyncio
async def test_metrics_endpoint_requires_auth():
    from sag_api.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            # 未认证 → 401
            assert (await c.get("/api/v1/system/metrics")).status_code == 401
            # 注册用户后 → 200 且含统计字段
            tok = (
                await c.post(
                    "/api/v1/auth/register",
                    json={"email": "perf@x.com", "password": "password123"},
                )
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {tok}"}
            body = (await c.get("/api/v1/system/metrics", headers=headers)).json()
            assert "p50_ms" in body and "sample_count" in body and "by_path" in body
            assert "slow_threshold_ms" in body
