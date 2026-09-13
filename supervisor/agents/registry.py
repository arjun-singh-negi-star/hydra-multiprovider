"""
The one place that maps a route name (from semantic_router_config.py) to
the agent that actually handles it. Adding agent #4 through #15 means:
add a Route in routing/semantic_router_config.py, write an agents/x.py
with a build_x_agent(model_id) function, register it here. main.py never
needs to change.
"""

from agents.code_review_agent import build_code_review_agent
from agents.devops_agent import build_devops_agent
from agents.finance_agent import build_finance_agent
from agents.hr_agent import build_hr_agent
from agents.support_agent import build_support_agent

AGENT_BUILDERS = {
    "devops_sre": build_devops_agent,
    "customer_support": build_support_agent,
    "hr_recruiting": build_hr_agent,
    "finance_expense": build_finance_agent,
    "code_review": build_code_review_agent,
}
