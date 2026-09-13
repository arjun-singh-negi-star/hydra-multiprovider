"""
Model pool: default (fast) tier + escalation tier, each candidate tied to
its own provider now — three providers, not one:

  - Groq         : OpenAI-compatible (https://api.groq.com/openai/v1),
                   LPU hardware, ~300+ tok/s. Chosen specifically to fix
                   the "openrouter/free is slow" latency problem measured
                   live in Phase 2 (8s+ time-to-first-token).
  - OpenRouter   : OpenAI-compatible, used only for DeepSeek V4 access —
                   no separate DeepSeek account needed.
  - Google AI Studio : OpenAI-compatible endpoint, verified live against
                   Google's own docs (https://generativelanguage.googleapis.com/v1beta/openai/,
                   their own example uses model="gemini-3.6-flash"). Used
                   as the escalation tier's fallback-of-last-resort.

Every provider still speaks the OpenAI wire format, so make_chat_model()
below never needs a provider-specific SDK — only the api_key/base_url pair
changes per candidate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar

from langchain_openai import ChatOpenAI

from config import settings

logger = logging.getLogger("hydra.model_pool")

T = TypeVar("T")


class Tier(str, Enum):
    DEFAULT = "default"
    ESCALATION = "escalation"


PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": settings.groq_api_key,
    },
    "openrouter": {
        "base_url": settings.openrouter_base_url,
        "api_key": settings.openrouter_api_key,
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": settings.google_api_key,
    },
}


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    tier: Tier
    provider: str  # key into PROVIDERS
    note: str = ""


# Default tier: Groq for speed. Was a single candidate; now two, after
# watching llama-3.3-70b-versatile — the only candidate here before —
# get fully decommissioned by Groq (deprecated 17 Jun 2026, removed by
# Aug 2026) with zero impact to us at the time it happened, only
# surfacing as a live 404 weeks later. Confirmed live: main.py's
# fully-exhausted-DEFAULT_POOL handling correctly auto-escalated when
# this hit in production, so the app never actually broke — but every
# query paid escalation-tier latency/cost until this was caught. A
# second candidate here buys a same-tier retry before paying that cost.
DEFAULT_POOL: list[ModelCandidate] = [
    ModelCandidate("openai/gpt-oss-120b", Tier.DEFAULT, "groq", "Groq's own migration target for the retired Llama 3.3 70B"),
    ModelCandidate("openai/gpt-oss-20b", Tier.DEFAULT, "groq", "smaller backup, same family"),
]

# Escalation tier: DeepSeek V4 (via OpenRouter) tried first, Gemini 3.6
# Flash as the fallback if BOTH DeepSeek candidates fail.
ESCALATION_POOL: list[ModelCandidate] = [
    ModelCandidate("deepseek/deepseek-v4-flash", Tier.ESCALATION, "openrouter", "cheap escalation, MIT-licensed, tried first"),
    ModelCandidate("deepseek/deepseek-v4-pro", Tier.ESCALATION, "openrouter", "hardest reasoning, tried second"),
    ModelCandidate("gemini-3.6-flash", Tier.ESCALATION, "google", "fallback if both DeepSeek candidates fail"),
]


async def run_with_fallback(
    pool: list[ModelCandidate], fn: Callable[[ModelCandidate], Awaitable[T]]
) -> tuple[T, str]:
    """Try each candidate in `pool`, in order, until one succeeds.

    `fn` receives the full ModelCandidate now (not just a model_id string)
    since building the right client needs provider info too. Returns
    (result, model_id_that_succeeded).
    """
    last_err: Exception | None = None
    for candidate in pool:
        try:
            result = await fn(candidate)
            return result, candidate.model_id
        except Exception as exc:  # noqa: BLE001 — intentionally broad: this IS the fallback loop
            logger.warning(
                "model %s (provider=%s) failed (%s), trying next candidate",
                candidate.model_id, candidate.provider, exc,
            )
            last_err = exc
    raise RuntimeError(f"all {len(pool)} candidates in pool exhausted: {last_err}")


def make_chat_model(candidate: ModelCandidate, temperature: float = 0.2) -> ChatOpenAI:
    provider = PROVIDERS.get(candidate.provider)
    if provider is None:
        raise RuntimeError(f"unknown provider '{candidate.provider}' for candidate {candidate.model_id}")
    if not provider["api_key"]:
        raise RuntimeError(
            f"no API key configured for provider '{candidate.provider}' "
            f"(candidate {candidate.model_id}) — check your .env"
        )
    return ChatOpenAI(
        model=candidate.model_id,
        api_key=provider["api_key"],
        base_url=provider["base_url"],
        temperature=temperature,
        max_tokens=1024,
    )
