"""DevOps / SRE agent. Ships with one illustrative mock tool for Phase 1 —
swap `check_service_status` for real PagerDuty/Datadog/kubectl API calls
when this becomes agent #1 of the full 15."""

from agents.base import build_agent

SYSTEM_PROMPT = (
    "You are HYDRA's DevOps/SRE agent. You help engineers with deployments, "
    "incidents, Kubernetes, and CI/CD pipelines. Be precise and give "
    "actionable steps, not vague advice. Treat live production outages and "
    "irreversible infra changes (e.g. deleting a database, force-pushing "
    "to main) with appropriate urgency in your tone."
)


def check_service_status(service_name: str) -> str:
    """Look up the current health/status of an internal service."""
    # Mock for Phase 1 — real version calls your Datadog/Prometheus API.
    return f"{service_name}: status=healthy, latency_p99=142ms, last_deploy=2h ago"


def build_devops_agent(candidate):
    return build_agent(candidate, SYSTEM_PROMPT, tools=[check_service_status])
