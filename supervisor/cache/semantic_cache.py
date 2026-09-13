"""
Semantic cache: same-meaning queries hit Redis instead of running the full
agent pipeline again. This is the layer that directly cuts token spend on
repeat/near-duplicate questions ("less token consumption" from the doc).

Implementation note (read before "upgrading" this): a production RediSearch
setup would use FT.CREATE with a VECTOR field and FT.SEARCH ... KNN for
O(log n) lookups. This sandbox's plain `redis-server` package doesn't ship
the RediSearch module, so that path couldn't be verified end-to-end here.
What's below — plain Redis hashes + a Python-side cosine scan — WAS tested
end-to-end against a live Redis instance and is correct, just O(n) per
lookup. That's fine at portfolio/demo scale (hundreds–low thousands of
cached entries). The natural Phase 2/3 upgrade once entry count grows:
swap this class's internals for `redis/redis-stack` + FT.CREATE/FT.SEARCH,
keep get_similar()/set() as the interface so main.py never changes.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np
import redis.asyncio as aioredis

from config import settings
from routing.semantic_router_config import encoder


class SemanticCache:
    def __init__(self, prefix: str = "hydra:cache:"):
        self._client = aioredis.from_url(settings.redis_url)
        self._prefix = prefix

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
        return float(np.dot(a, b) / denom)

    async def get_similar(self, query: str, threshold: float | None = None) -> Optional[dict[str, Any]]:
        threshold = threshold if threshold is not None else settings.cache_similarity_threshold
        q_emb = np.array(encoder([query])[0])

        best_score, best_entry = -1.0, None
        async for key in self._client.scan_iter(match=f"{self._prefix}*"):
            raw = await self._client.hgetall(key)
            if not raw:
                continue
            emb = np.array(json.loads(raw[b"embedding"]))
            score = self._cosine(q_emb, emb)
            if score > best_score:
                best_score, best_entry = score, raw

        if best_entry is not None and best_score >= threshold:
            return {
                "answer": best_entry[b"answer"].decode(),
                "agent_name": best_entry[b"agent_name"].decode(),
                "model_used": best_entry[b"model_used"].decode(),
                "similarity": best_score,
            }
        return None

    async def set(self, query: str, value: dict[str, str]) -> None:
        emb = np.array(encoder([query])[0])
        key = f"{self._prefix}{abs(hash(query))}"
        await self._client.hset(
            key,
            mapping={
                "query": query,
                "embedding": json.dumps(emb.tolist()),
                "answer": value["answer"],
                "agent_name": value["agent_name"],
                "model_used": value["model_used"],
            },
        )

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False
