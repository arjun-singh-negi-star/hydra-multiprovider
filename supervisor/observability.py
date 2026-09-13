"""
Optional Langfuse tracing. If LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY
aren't set, langfuse_handler stays None and trace_config() below returns
whatever config it was given unchanged — the system runs identically
without tracing rather than crashing because someone hasn't set up
Langfuse yet.

Wired into every LangChain/LangGraph call (agents/streaming.py,
agents/confidence.py, routing/llm_router.py) via trace_config(), so a
single query produces one connected trace across routing -> agent ->
escalation instead of disconnected calls.

Known gap, not implemented here: attaching our own session_id/user_id
(from SubmitQueryRequest) to traces. Verified live during setup that the
straightforward `config={"metadata": {"langfuse_session_id": ...}}`
approach has an open, documented bug specifically with LangGraph as of
the SDK version pinned here (Langfuse GitHub discussions #8125, #11117,
issue #7183) — session/user metadata only reliably attaches to a child
span, not the parent trace. The current recommended fix is
`langfuse.propagate_attributes()` as a context manager, but wiring that
through our streaming generator functions is nontrivial enough that it
deserves its own pass rather than shipping unverified. Traces work and
are genuinely useful without this; it's a "who ran this query" filter,
not core functionality.
"""

import logging

from config import settings

# Safe even though main.py also calls this: logging.basicConfig() is a
# no-op if a handler is already configured, so whichever module runs
# first "wins". Needed here specifically because observability.py gets
# imported (via agents/confidence.py) before main.py reaches its own
# basicConfig() call — without this, the INFO logs below were silently
# dropped by Python's default handler-of-last-resort (WARNING+ only),
# which is exactly what happened: `docker compose logs | grep langfuse`
# came back completely empty even though nothing was actually broken.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hydra.observability")

langfuse_handler = None

if settings.langfuse_public_key and settings.langfuse_secret_key:
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            debug=True,  # verbose SDK-internal logging while diagnosing why traces weren't arriving
        )
        langfuse_handler = CallbackHandler()
        logger.info("Langfuse tracing enabled (%s)", settings.langfuse_host)
    except Exception as exc:  # noqa: BLE001 — tracing must never break the app
        logger.warning("Langfuse failed to initialize (%s), continuing without tracing", exc)
else:
    logger.info("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set, tracing disabled")


def trace_config(base_config: dict | None = None) -> dict:
    """Merge Langfuse's callback into a LangChain/LangGraph config dict.
    Safe to call unconditionally, whether or not tracing is enabled."""
    config = dict(base_config or {})
    if langfuse_handler is not None:
        config["callbacks"] = [*config.get("callbacks", []), langfuse_handler]
    return config


def flush() -> None:
    """Force-send any buffered trace data. The Langfuse SDK batches
    exports (it's OpenTelemetry-based under the hood) — fine for a short
    script that exits and triggers a final flush, but the supervisor is a
    long-running server, so nothing else ever tells it "send now". Call
    this once per request (see main.py) rather than relying on however
    long the batch processor's own timer happens to be."""
    if langfuse_handler is None:
        return
    try:
        from langfuse import get_client
        get_client().flush()
        logger.info("Langfuse flush() returned without raising")
    except Exception as exc:  # noqa: BLE001 — tracing must never break the app
        logger.warning("Langfuse flush failed (%s)", exc, exc_info=True)
