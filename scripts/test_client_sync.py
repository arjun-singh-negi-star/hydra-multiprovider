"""
Fallback smoke-test client -- identical to test_client.py, but uses plain
synchronous grpc instead of grpc.aio.

Only needed if test_client.py's async version still crashes on Windows
(exit code -1073741819 / 0xC0000005) even after forcing SelectorEventLoop.
The classic synchronous grpc Python bindings don't touch asyncio/Proactor
at all, so they sidestep that whole category of Windows instability --
useful here purely as a diagnostic: if THIS also crashes, the bug is
somewhere lower than the event loop (a real grpcio/Windows/network-stack
issue), not just an asyncio interaction.

Usage:
    python scripts/test_client_sync.py "the production pod keeps crash looping"
"""

import sys
import time
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent.parent / "supervisor" / "gen"))
from hydra.v1 import query_pb2, query_pb2_grpc  # noqa: E402


def main():
    query = " ".join(sys.argv[1:]) or "the production pod keeps crash looping, help"
    gateway_addr = "localhost:8080"

    print(f"-> {gateway_addr}  query={query!r}\n")

    with grpc.insecure_channel(gateway_addr) as channel:
        stub = query_pb2_grpc.QueryServiceStub(channel)
        request = query_pb2.SubmitQueryRequest(query=query, session_id="test-session", user_id="test-user")

        start = time.monotonic()
        first_token_at = None
        for chunk in stub.SubmitQuery(request):
            which = chunk.WhichOneof("payload")
            if which == "token":
                if first_token_at is None:
                    first_token_at = time.monotonic() - start
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
            elif which == "escalating":
                print(f"\n\n[escalating -- {chunk.escalating.reason}]\n")
                first_token_at = None
            elif which == "error":
                print(f"\n[ERROR] {chunk.error.code}: {chunk.error.message}")


if __name__ == "__main__":
    main()
