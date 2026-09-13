"""Central settings for the Supervisor. Everything is env-driven so the
same image runs unmodified in docker-compose today and a k8s ConfigMap
in Phase 3."""

import os


class Settings:
    def __init__(self) -> None:
        # gRPC server
        self.grpc_port = int(os.getenv("SUPERVISOR_GRPC_PORT", "50051"))

        # OpenRouter — still used for DeepSeek V4 access (no separate
        # DeepSeek account needed) in the escalation tier.
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
        self.openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

        # Groq — default (fast) tier. LPU hardware, ~300+ tok/s, chosen
        # specifically to fix the "free tier is slow" latency problem.
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")

        # Google AI Studio — direct Gemini access, used as the escalation
        # tier's fallback-of-last-resort if both DeepSeek candidates fail.
        self.google_api_key = os.getenv("GOOGLE_API_KEY", "")

        # Langfuse — optional LLM tracing. If either key is empty,
        # tracing is disabled and the system runs identically without it
        # (see observability.py).
        self.langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
        self.langfuse_host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        # Redis (semantic cache)
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.cache_similarity_threshold = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))

        # Routing
        self.route_confidence_threshold = float(os.getenv("ROUTE_CONFIDENCE_THRESHOLD", "0.6"))
        self.escalation_confidence_threshold = float(os.getenv("ESCALATION_CONFIDENCE_THRESHOLD", "0.70"))


settings = Settings()
