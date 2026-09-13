"""Finance / Expense agent. Ships with one illustrative mock tool for
Phase 2 — swap `check_expense_policy` for a real Expensify/Ramp/SAP
Concur API call when this becomes agent #4 of the full 15."""

from agents.base import build_agent

SYSTEM_PROMPT = (
    "You are HYDRA's Finance/Expense agent. You help with expense "
    "reports, reimbursement policies, and budget questions. Be precise "
    "about dollar amounts and policy limits — don't guess a number if "
    "you're not sure, look it up with the tool instead. Flag amounts "
    "above standard approval limits, potential fraud, or budget "
    "overruns as needing manager review."
)


def check_expense_policy(category: str) -> str:
    """Look up the reimbursement policy/limit for an expense category."""
    # Mock for Phase 2 — real version queries Expensify/Ramp/SAP Concur.
    return f"{category}: reimbursable up to $150/day without pre-approval, receipts required above $25."


def build_finance_agent(candidate):
    return build_agent(candidate, SYSTEM_PROMPT, tools=[check_expense_policy])
