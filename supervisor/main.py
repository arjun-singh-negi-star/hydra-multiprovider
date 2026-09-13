"""
Supervisor entrypoint: a pure-gRPC server implementing QueryService.

Phase 2 change from Phase 1: the answer now streams as real tokens as the
model generates them (Phase 1 waited for the full answer, then chunked it
word-by-word — real UX, but zero actual latency benefit). That meant
moving confidence/needs_escalation OUT of the same call that produces the
answer: verified live that forcing structured output via response_format
routes the answer through tool-calling, so the text arrives as JSON
tool-call arguments, not streamable prose. Confidence is now a small,
separate, cheap follow-up call after the answer has already streamed
(agents/confidence.py) — one extra small call, in exchange for real
token-by-token streaming of the actual answer.

Cascade:

    cache hit?  --yes--> return cached answer, ~5ms, $0
        |no
    semantic router (0 LLM calls)
        |low confidence
    LLM router (structured output, can't hallucinate an agent name)
        |
    stream the agent's answer on the DEFAULT (free) model pool
        |
    small follow-up confidence check on the answer just streamed
        |low confidence / needs_escalation
    send an "escalating" notice, re-stream on the ESCALATION model pool
        |
    write-through to cache (the final answer, escalated or not), send
    final metadata
"""

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent / "gen"))

from hydra.v1 import query_pb2, query_pb2_grpc  # noqa: E402

from agents.confidence import assess_confidence  # noqa: E402
from agents.registry import AGENT_BUILDERS  # noqa: E402
from agents.schemas import ConfidenceAssessment  # noqa: E402
from agents.streaming import stream_agent_answer  # noqa: E402
from cache.semantic_cache import SemanticCache  # noqa: E402
from config import settings  # noqa: E402
from models.model_pool import DEFAULT_POOL, ESCALATION_POOL, run_with_fallback  # noqa: E402
from observability import flush  # noqa: E402
from routing.llm_router import llm_decide_route  # noqa: E402
from routing.semantic_router_config import router as semantic_router  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hydra.supervisor")

cache = SemanticCache()


class QueryServicer(query_pb2_grpc.QueryServiceServicer):
    async def _submit_query_impl(self, request, context):
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        query = request.query
        logger.info("[%s] query=%r", request_id, query)

        # 1. Redis semantic cache — bypasses the entire pipeline on a hit.
        cached = await cache.get_similar(query)
        if cached is not None:
            logger.info("[%s] cache hit (similarity=%.3f)", request_id, cached["similarity"])
            for word in cached["answer"].split(" "):
                yield query_pb2.SubmitQueryResponse(request_id=request_id, token=word + " ")
            yield query_pb2.SubmitQueryResponse(
                request_id=request_id,
                final=query_pb2.QueryMetadata(
                    agent_name=cached["agent_name"],
                    model_used=cached["model_used"],
                    cached=True,
                    escalated=False,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    route_confidence=cached["similarity"],
                    route_method="cache",
                ),
            )
            return

        # 2. Semantic router — zero LLM calls, pure embedding similarity.
        choice = semantic_router(query)
        route_method = "semantic_router"
        confidence = choice.similarity_score or 0.0

        # 3. Fall through to a real LLM decision only when the router isn't sure.
        if choice.name is None:
            logger.info("[%s] semantic router unsure, falling back to LLM router", request_id)
            decision = await llm_decide_route(query)
            agent_name = decision.agent_name
            route_method = "llm_fallback"
            confidence = 1.0  # schema-constrained — a valid agent_name is guaranteed
        else:
            agent_name = choice.name

        logger.info("[%s] routed -> %s (via %s, confidence=%.3f)", request_id, agent_name, route_method, confidence)
        build_agent = AGENT_BUILDERS[agent_name]

        async def _stream_tier(pool, tier_name, result_holder):
            """Yields SubmitQueryResponse token chunks (and, for mid-stream
            failures only, an error chunk). On success, fills
            result_holder with answer/assessment/model_used before
            returning.

            Retry semantics: once a token has reached the client we can't
            un-send it, so falling back to the next pool CANDIDATE only
            happens for failures BEFORE the first token goes out — that
            part is unchanged. What's new: if the whole POOL is exhausted
            without ever sending a token, this no longer unilaterally
            yields an error and ends the response — it sets
            result_holder["exhausted"] and lets the caller (SubmitQuery)
            decide what to do next. Known gap this closes: with a
            single-candidate DEFAULT_POOL (Groq), a total Groq outage used
            to hard-fail the whole request even though the escalation
            tier (OpenRouter/DeepSeek, Google/Gemini) was sitting right
            there, unused.
            """
            last_err = None
            for candidate in pool:
                sent_any = False
                parts: list[str] = []
                try:
                    agent = build_agent(candidate)
                    async for tok in stream_agent_answer(agent, query):
                        sent_any = True
                        parts.append(tok)
                        yield query_pb2.SubmitQueryResponse(request_id=request_id, token=tok)
                except Exception as exc:  # noqa: BLE001 — fallback loop, deliberately broad
                    logger.warning(
                        "[%s] %s tier model %s failed (%s)", request_id, tier_name, candidate.model_id, exc
                    )
                    last_err = exc
                    if sent_any:
                        yield query_pb2.SubmitQueryResponse(
                            request_id=request_id,
                            error=query_pb2.QueryError(code="STREAM_INTERRUPTED", message=str(exc)),
                        )
                        result_holder["fatal"] = True
                        return
                    continue  # nothing shown yet — safe to try the next candidate

                # Streaming succeeded. The confidence check is best-effort:
                # if IT fails, we still have a perfectly good answer
                # already streamed — don't throw that away over a broken
                # follow-up call, just assume it didn't need escalation.
                answer_text = "".join(parts)
                try:
                    assessment, _ = await run_with_fallback(
                        DEFAULT_POOL, lambda cand: assess_confidence(query, answer_text, cand)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] confidence check failed (%s), assuming no escalation", request_id, exc)
                    assessment = ConfidenceAssessment(confidence=1.0, needs_escalation=False)

                result_holder["answer"] = answer_text
                result_holder["assessment"] = assessment
                result_holder["model_used"] = candidate.model_id
                return

            # Pool exhausted without ever sending a token. Deliberately NOT
            # yielding an error here — the caller decides whether there's
            # somewhere else to try first.
            result_holder["exhausted"] = True
            result_holder["last_err"] = last_err

        async def _finalize(answer_text: str, model_used: str, escalated: bool):
            latency_ms = (time.perf_counter() - start) * 1000
            await cache.set(query, {"answer": answer_text, "agent_name": agent_name, "model_used": model_used})
            logger.info(
                "[%s] done in %.0fms (model=%s, escalated=%s)", request_id, latency_ms, model_used, escalated
            )
            return query_pb2.SubmitQueryResponse(
                request_id=request_id,
                final=query_pb2.QueryMetadata(
                    agent_name=agent_name,
                    model_used=model_used,
                    cached=False,
                    escalated=escalated,
                    latency_ms=latency_ms,
                    route_confidence=confidence,
                    route_method=route_method,
                ),
            )

        # 4. Stream the default (free) tier.
        default_result: dict = {}
        async for chunk in _stream_tier(DEFAULT_POOL, "default", default_result):
            yield chunk

        if default_result.get("fatal"):
            return  # mid-stream failure already surfaced as an error chunk

        if "answer" not in default_result:
            # Default tier is completely unavailable (e.g. Groq itself is
            # down) — rather than end the response on a hard error while
            # the escalation tier sits unused, treat this as an automatic
            # last-resort escalation.
            logger.warning(
                "[%s] default tier fully exhausted (%s), escalating as a last resort",
                request_id, default_result.get("last_err"),
            )
            yield query_pb2.SubmitQueryResponse(
                request_id=request_id,
                escalating=query_pb2.EscalationNotice(reason="default tier unavailable"),
            )

            escalation_result: dict = {}
            async for chunk in _stream_tier(ESCALATION_POOL, "escalation", escalation_result):
                yield chunk

            if escalation_result.get("fatal"):
                return  # error chunk already sent

            if "answer" not in escalation_result:
                yield query_pb2.SubmitQueryResponse(
                    request_id=request_id,
                    error=query_pb2.QueryError(
                        code="ALL_TIERS_EXHAUSTED",
                        message=str(escalation_result.get("last_err") or default_result.get("last_err")),
                    ),
                )
                return

            yield await _finalize(escalation_result["answer"], escalation_result["model_used"], True)
            return

        answer_text = default_result["answer"]
        assessment = default_result["assessment"]
        model_used = default_result["model_used"]
        escalated = False

        # 5. Escalate only if the agent's own follow-up check flags it.
        if assessment.confidence < settings.escalation_confidence_threshold or assessment.needs_escalation:
            reason = (
                "agent flagged this as needing stronger reasoning"
                if assessment.needs_escalation
                else f"confidence {assessment.confidence:.2f} below threshold"
            )
            logger.info("[%s] escalating (%s)", request_id, reason)
            yield query_pb2.SubmitQueryResponse(
                request_id=request_id,
                escalating=query_pb2.EscalationNotice(reason=reason),
            )

            escalation_result: dict = {}
            async for chunk in _stream_tier(ESCALATION_POOL, "escalation", escalation_result):
                yield chunk

            if escalation_result.get("fatal"):
                return  # error chunk already sent

            if "answer" in escalation_result:
                answer_text = escalation_result["answer"]
                model_used = escalation_result["model_used"]
                escalated = True
            # else: escalation tier failed entirely (cleanly, no tokens
            # sent) — keep the default-tier answer already streamed
            # rather than leaving the user with nothing.

        yield await _finalize(answer_text, model_used, escalated)

    async def SubmitQuery(self, request, context):
        """Thin wrapper: guarantees flush() runs after every request,
        whatever path _submit_query_impl took to get there (cache hit,
        normal answer, escalation, or an error partway through). finally
        on an async generator runs once it's fully consumed or closed —
        grpc.aio always fully consumes this as part of serving the
        streaming RPC, so this fires exactly once per request."""
        try:
            async for chunk in self._submit_query_impl(request, context):
                yield chunk
        finally:
            flush()

    async def Health(self, request, context):
        deps = {"redis": await cache.ping()}
        status = "ok" if all(deps.values()) else "degraded"
        return query_pb2.HealthResponse(status=status, dependencies=deps)


async def serve():
    server = grpc.aio.server()
    query_pb2_grpc.add_QueryServiceServicer_to_server(QueryServicer(), server)
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    logger.info("supervisor listening on :%d", settings.grpc_port)
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
