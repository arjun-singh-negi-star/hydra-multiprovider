"""
Fallback routing for the "ambiguous case": queries where no semantic-router
route cleared its confidence threshold. Uses structured output constrained
to a Literal of real agent names, so this step *cannot* hallucinate a
non-existent agent — an invalid value is a Pydantic validation error, not a
silently-wrong route. (Verified: passing a made-up agent name raises
ValidationError rather than constructing the object.)
"""

from typing import Literal

from pydantic import BaseModel, Field

from models.model_pool import DEFAULT_POOL, make_chat_model
from observability import trace_config
from routing.semantic_router_config import AGENT_ROUTES

_AGENT_NAMES: tuple[str, ...] = tuple(r.name for r in AGENT_ROUTES)
AgentName = Literal[_AGENT_NAMES]  # type: ignore[valid-type]


class RouteDecision(BaseModel):
    agent_name: AgentName = Field(description="which specialist agent should handle this query")
    reasoning: str = Field(description="one short sentence explaining the choice")


# Routing is cheap classification work, so it rides the same fast default
# tier (Groq) as simple-query answers — only the *agent's actual answer*
# escalates, per the cascade design. Uses DEFAULT_POOL[0] directly rather
# than run_with_fallback: if the default tier is down, the semantic router
# has almost certainly already picked a confident route anyway (this path
# only runs when it hasn't), so a fallback loop here isn't worth the
# complexity yet.
#
# method="function_calling" is explicit for the same reason as
# agents/confidence.py: LangChain's default structured-output mode
# (json_schema) 400s on Groq's llama-3.3-70b-versatile.
_router_model = make_chat_model(DEFAULT_POOL[0], temperature=0).with_structured_output(
    RouteDecision, method="function_calling"
)


async def llm_decide_route(query: str) -> RouteDecision:
    return await _router_model.ainvoke(
        f"Classify this employee query into exactly one agent category. "
        f"Available agents: {', '.join(_AGENT_NAMES)}.\n\nQuery: {query!r}",
        config=trace_config(),
    )
