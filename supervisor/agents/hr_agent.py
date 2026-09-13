"""HR / Recruiting agent. Ships with one illustrative mock tool for
Phase 1 — swap `lookup_leave_balance` for a real Workday/BambooHR API
call when this becomes agent #3 of the full 15."""

from agents.base import build_agent

SYSTEM_PROMPT = (
    "You are HYDRA's HR/Recruiting agent. You help with leave balances, "
    "payroll questions, interview scheduling, and onboarding. Never share "
    "one employee's personal data with another employee. For termination, "
    "legal disputes, or compensation negotiation topics, note that these "
    "typically need a human HR rep, not just an automated answer."
)


def lookup_leave_balance(employee_id: str) -> str:
    """Look up an employee's remaining leave balance."""
    # Mock for Phase 1 — real version queries your HRIS (Workday/BambooHR).
    return f"{employee_id}: 12 days annual leave remaining, 4 sick days remaining."


def build_hr_agent(candidate):
    return build_agent(candidate, SYSTEM_PROMPT, tools=[lookup_leave_balance])
