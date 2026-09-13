"""Customer Support agent. Ships with one illustrative mock tool for
Phase 1 — swap `lookup_refund_policy` for a real Zendesk/Intercom/CRM
lookup when this becomes agent #2 of the full 15."""

from agents.base import build_agent

SYSTEM_PROMPT = (
    "You are HYDRA's Customer Support agent. You help with account issues, "
    "refunds, and troubleshooting. Be empathetic and concise. Treat refund "
    "amounts above policy limits, legal threats, or a visibly upset "
    "customer with extra care."
)


def lookup_refund_policy(product: str) -> str:
    """Look up the refund policy for a given product."""
    # Mock for Phase 1 — real version queries your billing/CRM system.
    return f"{product}: 30-day money-back guarantee, no questions asked under $500."


def build_support_agent(candidate):
    return build_agent(candidate, SYSTEM_PROMPT, tools=[lookup_refund_policy])
