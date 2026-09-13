"""Code Review agent. Ships with one illustrative mock tool for
Phase 2 — swap `check_pr_status` for a real GitHub/GitLab API call
when this becomes agent #5 of the full 15."""

from agents.base import build_agent

SYSTEM_PROMPT = (
    "You are HYDRA's Code Review agent. You help engineers with PR "
    "reviews, code quality questions, and best practices. Be specific "
    "and reference concrete issues, not generic advice. Flag security "
    "vulnerabilities, data loss risk, or architecture-level decisions "
    "as needing senior/team review."
)


def check_pr_status(pr_number: str) -> str:
    """Look up the current CI/review status of a pull request."""
    # Mock for Phase 2 — real version queries the GitHub/GitLab API.
    return f"PR #{pr_number}: CI passing, 2 approvals, 1 change requested (unresolved comment on error handling)."


def build_code_review_agent(candidate):
    return build_agent(candidate, SYSTEM_PROMPT, tools=[check_pr_status])
