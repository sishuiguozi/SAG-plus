"""Client for Qwen/Cohere/Jina/vLLM compatible rerank endpoints."""

from __future__ import annotations

import math
from typing import Any

import httpx


class RerankAPIClient:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        instruction: str | None = None,
        timeout_ms: int = 30_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self.instruction = instruction
        self.timeout = timeout_ms / 1000
        self.transport = transport

    async def rank(self, query: str, documents: list[str], *, limit: int) -> list[float]:
        if not documents:
            return []
        payload: dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": min(limit, len(documents)),
        }
        if self.instruction:
            payload["instruct"] = self.instruction
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    self.url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Rerank API request failed") from exc

        scores: list[float | None] = [None] * len(documents)
        results = body.get("results") if isinstance(body, dict) else None
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                score = item.get("relevance_score")
                if not isinstance(index, int) or not (0 <= index < len(documents)):
                    continue
                if isinstance(score, bool):
                    continue
                try:
                    numeric = float(score)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(numeric):
                    scores[index] = numeric
        if not any(score is not None for score in scores):
            raise RuntimeError("Rerank API returned no valid relevance scores")
        return [score if score is not None else float("-inf") for score in scores]
