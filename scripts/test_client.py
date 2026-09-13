"""
Quick smoke-test client. Talks to the GATEWAY (not the supervisor
directly) using plain gRPC — proving the full path: this client -> gateway
(Connect/gRPC/gRPC-Web server) -> supervisor (pure gRPC) -> agent -> model.

Usage:
    python scripts/test_client.py "the production pod keeps crash looping"

A browser client would hit the same gateway with the Connect protocol
(plain fetch, no code-gen client required) instead — this script just uses
gRPC because it's the simplest thing to drive from Python.
"""

import asyncio
import sys
import time
from pathlib import Path

import grpc

# Windows-only: grpc.aio's native (C-core) implementation has a long history
# of instability under the default ProactorEventLoop on Windows (see e.g.
# grpc/grpc#23617) -- symptoms range from silent hangs to a hard native
# crash (exit code -1073741819 / 0xC0000005, STATUS_ACCESS_VIOLATION) with
# no Python traceback at all, since the fault is below the interpreter.
# SelectorEventLoop is the standard, well-documented workaround; it doesn't
# affect Linux/Mac, where ProactorEventLoop doesn't exist.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# If a native crash still happens, faulthandler prints the actual faulting
# stack frame instead of nothing -- worth knowing exactly which grpc.aio
# call it was, rather than guessing from a bare exit code again.
import faulthandler
faulthandler.enable()

sys.path.insert(0, str(Path(__file__).parent.parent / "supervisor" / "gen"))
from hydra.v1 import query_pb2, query_pb2_grpc  # noqa: E402


async def main():
    query = " ".join(sys.argv[1:]) or "the production pod keeps crash looping, help"
    gateway_addr = "localhost:8080"

    print(f"-> {gateway_addr}  query={query!r}\n")

    async with grpc.aio.insecure_channel(gateway_addr) as channel:
        stub = query_pb2_grpc.QueryServiceStub(channel)
        request = query_pb2.SubmitQueryRequest(query=query, session_id="test-session", user_id="test-user")

        start = time.monotonic()
        first_token_at = None
        answer = ""
        async for chunk in stub.SubmitQuery(request):
            which = chunk.WhichOneof("payload")
            if which == "token":
                if first_token_at is None:
                    first_token_at = time.monotonic() - start
                answer += chunk.token
                print(chunk.token, end="", flush=True)
            elif which == "final":
                meta = chunk.final
                print("\n\n--- metadata ---")
                print(f"agent:       {meta.agent_name}")
                print(f"model:       {meta.model_used}")
                print(f"cached:      {meta.cached}")
                print(f"escalated:   {meta.escalated}")
                print(f"route via:   {meta.route_method} (confidence={meta.route_confidence:.2f})")
                print(f"latency:     {meta.latency_ms:.0f}ms")
                if first_token_at is not None:
                    gap = meta.latency_ms - (first_token_at * 1000)
                    print(f"first token: {first_token_at * 1000:.0f}ms  (answer kept arriving for {gap:.0f}ms after that -> real streaming)")
            elif which == "escalating":
                print(f"\n\n[escalating — {chunk.escalating.reason}]\n")
                answer = ""  # the escalation tier re-streams a fresh answer, replacing this one
                first_token_at = None  # re-measure for the escalation tier's own stream
            elif which == "error":
                print(f"\n[ERROR] {chunk.error.code}: {chunk.error.message}")


if __name__ == "__main__":
    asyncio.run(main())
