"""
Docker HEALTHCHECK script. Calls the supervisor's own Health RPC —
if it responds at all, the gRPC server is up and accepting connections.
Runs inside the container, so it reuses the same generated stubs main.py
uses.

This exists because "container started" and "app is ready to accept
connections" are different things — the supervisor's first boot downloads
a ~80MB embedding model before it starts listening (18-38s observed), and
gateway trying to connect before that finishes was a repeat source of
confusing ECONNREFUSED errors. With this wired into docker-compose.yml
(gateway's depends_on: condition: service_healthy), Compose now waits for
a real "yes, I'm listening" signal instead of everyone guessing sleep
durations.
"""

import sys
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent / "gen"))
from hydra.v1 import query_pb2, query_pb2_grpc  # noqa: E402


def main() -> int:
    try:
        with grpc.insecure_channel("localhost:50051") as channel:
            stub = query_pb2_grpc.QueryServiceStub(channel)
            stub.Health(query_pb2.HealthRequest(), timeout=3)
            return 0  # got any response at all -> server is up and answering
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
