"""
Regression runner: sends every query in golden_dataset.py through the
real gateway (the same path an actual client uses — Client -> Gateway ->
Supervisor -> Agent -> Model), then checks routing correctness and a few
content sanity rules.

Requires the stack running first:
    docker compose up -d

Usage:
    python eval/run_eval.py

Exit code 0 if everything passes, 1 if anything fails — safe to wire into
a CI regression gate later (see README roadmap).

Note on cost/time: 15 real LLM calls against the default (Groq) tier.
Fast and effectively free on Groq's free tier, but not zero — this is a
real integration test, not a mock.

Note on caching: a second run right after the first will show `cached`
in the metadata for repeated queries — that's expected, not a bug, and
routing/content checks still run against the (identical) cached answer.
"""

import asyncio
import re
import sys
import time
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent.parent / "supervisor" / "gen"))
from hydra.v1 import query_pb2, query_pb2_grpc  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from golden_dataset import GOLDEN_DATASET  # noqa: E402

GATEWAY_ADDR = "localhost:8080"

# Patterns that should never appear in a real answer. Each one is a
# regression this harness exists to catch, not a hypothetical — the first
# is the exact bug found live (see golden_dataset.py docstring).
LEAK_PATTERNS = [
    re.compile(r"needs_escalation\s*=", re.IGNORECASE),
    re.compile(r"\bconfidence\s*=\s*[\d.]+", re.IGNORECASE),
    re.compile(r"\bAgentResponse\b"),
]


async def run_one(stub, case):
    request = query_pb2.SubmitQueryRequest(query=case.query, session_id="eval", user_id="eval")
    answer = ""
    meta = None
    error = None
    start = time.monotonic()
    try:
        async for chunk in stub.SubmitQuery(request):
            which = chunk.WhichOneof("payload")
            if which == "token":
                answer += chunk.token
            elif which == "final":
                meta = chunk.final
            elif which == "error":
                error = f"{chunk.error.code}: {chunk.error.message}"
    except grpc.aio.AioRpcError as exc:
        error = str(exc.details()) if exc.details() else str(exc)
    elapsed_ms = (time.monotonic() - start) * 1000

    failures = []
    if error:
        failures.append(f"request errored: {error}")
    if not error and not answer.strip():
        failures.append("answer was empty")
    for pattern in LEAK_PATTERNS:
        if pattern.search(answer):
            failures.append(f"answer contains leaked pattern: {pattern.pattern!r}")
    if meta is not None and meta.agent_name != case.expected_agent:
        failures.append(f"routed to {meta.agent_name!r}, expected {case.expected_agent!r}")

    return {
        "case": case,
        "passed": not failures,
        "failures": failures,
        "agent": meta.agent_name if meta else None,
        "model": meta.model_used if meta else None,
        "escalated": meta.escalated if meta else None,
        "route_method": meta.route_method if meta else None,
        "cached": meta.cached if meta else None,
        "elapsed_ms": elapsed_ms,
    }


async def main():
    print(f"-> running {len(GOLDEN_DATASET)} golden cases against {GATEWAY_ADDR}\n")

    async with grpc.aio.insecure_channel(GATEWAY_ADDR) as channel:
        stub = query_pb2_grpc.QueryServiceStub(channel)
        results = []
        for i, case in enumerate(GOLDEN_DATASET, 1):
            result = await run_one(stub, case)
            results.append(result)
            status = "PASS" if result["passed"] else "FAIL"
            cached_tag = " [cached]" if result["cached"] else ""
            route_tag = f" via={result['route_method']}" if result["route_method"] else ""
            esc_tag = f" ESCALATED->{result['model']}" if result["escalated"] else ""
            print(f"[{i:2d}/{len(GOLDEN_DATASET)}] {status}  {case.expected_agent:18s} "
                  f"{case.query[:48]!r:52s} {result['elapsed_ms']:6.0f}ms{route_tag}{cached_tag}{esc_tag}")
            for f in result["failures"]:
                print(f"           -> {f}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    routing_correct = sum(1 for r in results if r["agent"] == r["case"].expected_agent)
    avg_latency = sum(r["elapsed_ms"] for r in results) / total if total else 0
    escalations = sum(1 for r in results if r["escalated"])

    print()
    print(f"=== {passed}/{total} passed | routing accuracy {routing_correct}/{total} | "
          f"avg latency {avg_latency:.0f}ms | escalated {escalations}/{total} ===")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
