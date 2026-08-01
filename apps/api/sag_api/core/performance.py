"""SAG-OPT-604：运行期请求性能指标（P50/P95/P99 + 慢请求日志）。

用有界环形缓冲记录最近 N 条请求耗时，提供：

- ``record(duration_ms, method, path, status, request_id)``：每个请求完成后调用。
- ``summary()``：P50/P95/P99、计数、最近慢请求（>= slow_threshold_ms）。
- 超过 ``slow_threshold_ms`` 的请求通过 logging 输出 WARN（含 request_id 便于追踪）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("sag.performance")

DEFAULT_WINDOW = 4096
DEFAULT_SLOW_MS = 2000.0


@dataclass
class RequestSample:
    ts: float
    duration_ms: float
    method: str
    path: str
    status: int
    request_id: str


@dataclass
class PerformanceRing:
    window: int = DEFAULT_WINDOW
    slow_threshold_ms: float = DEFAULT_SLOW_MS
    samples: deque[RequestSample] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, *, duration_ms: float, method: str, path: str, status: int, request_id: str) -> None:
        sample = RequestSample(
            ts=time.time(), duration_ms=duration_ms, method=method,
            path=path, status=status, request_id=request_id,
        )
        with self._lock:
            self.samples.append(sample)
            while len(self.samples) > self.window:
                self.samples.popleft()
        if duration_ms >= self.slow_threshold_ms:
            log.warning(
                "慢请求: %s %s status=%s 耗时=%.0fms request_id=%s",
                method, path, status, duration_ms, request_id,
            )

    def _percentile(self, values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(len(ordered) * p))
        return round(ordered[idx], 1)

    def summary(self, *, since_seconds: float | None = None) -> dict[str, Any]:
        with self._lock:
            samples = list(self.samples)
        if since_seconds is not None:
            cutoff = time.time() - since_seconds
            samples = [s for s in samples if s.ts >= cutoff]
        durations = [s.duration_ms for s in samples]
        slow = [s for s in samples if s.duration_ms >= self.slow_threshold_ms]
        slow_top = sorted(slow, key=lambda s: s.duration_ms, reverse=True)[:10]
        by_path: dict[str, list[float]] = {}
        for s in samples:
            by_path.setdefault(f"{s.method} {s.path}", []).append(s.duration_ms)
        path_stats = [
            {
                "route": route,
                "count": len(values),
                "p50_ms": self._percentile(values, 0.50),
                "p95_ms": self._percentile(values, 0.95),
                "p99_ms": self._percentile(values, 0.99),
                "max_ms": round(max(values), 1),
            }
            for route, values in sorted(by_path.items(), key=lambda kv: -max(kv[1]))[:20]
        ]
        return {
            "window": self.window,
            "slow_threshold_ms": self.slow_threshold_ms,
            "sample_count": len(durations),
            "p50_ms": self._percentile(durations, 0.50),
            "p95_ms": self._percentile(durations, 0.95),
            "p99_ms": self._percentile(durations, 0.99),
            "max_ms": round(max(durations), 1) if durations else 0.0,
            "slow_count": len(slow),
            "recent_slow": [
                {
                    "ts": s.ts,
                    "duration_ms": round(s.duration_ms, 1),
                    "method": s.method,
                    "path": s.path,
                    "status": s.status,
                    "request_id": s.request_id,
                }
                for s in slow_top
            ],
            "by_path": path_stats,
        }


# 进程级单例
performance_ring = PerformanceRing()


class PerformanceMiddleware:
    """HTTP 请求耗时采集中间件：记录到 PerformanceRing，慢请求打 WARN 日志。"""

    def __init__(self, app: Any, ring: PerformanceRing | None = None) -> None:
        self.app = app
        self.ring = ring or performance_ring

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status_holder: dict[str, int] = {"status": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            state = scope.get("state") or {}
            self.ring.record(
                duration_ms=duration_ms,
                method=str(scope.get("method", "")),
                path=str(scope.get("path", "")),
                status=status_holder["status"],
                request_id=str(state.get("request_id", "-")),
            )
