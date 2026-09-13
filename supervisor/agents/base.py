"""
Every agent is its own LangGraph state machine, per the design doc ("har
agent apna alag LangGraph state-machine"). This factory is the one place
that wiring lives — devops_agent.py / support_agent.py / hr_agent.py only
supply a system prompt + tools, everything else is identical across all
of them.

Phase 2 change: no `response_format` here anymore. Verified live —
setting it forces the model's final turn through tool-calling (to fit the
schema), which means the answer text arrives as JSON tool-call arguments,
not natural-language content deltas. Streaming that gives the client
fragments of broken JSON, not readable prose. Confidence/escalation is now
a separate, small follow-up call after the plain-text answer has already
streamed (see agents/confidence.py) — costs one extra small call, buys
real token-by-token streaming of the actual answer.

Uses `langchain.agents.create_agent` — the LangChain 1.0 entrypoint that
replaced `langgraph.prebuilt.create_react_agent` (verified: the old path
still runs but its state class is marked deprecated in favor of this one).
"""

from langchain.agents import create_agent

from models.model_pool import ModelCandidate, make_chat_model

# Appended to every agent's own system prompt. Caught live: given a query
# missing info a tool needed (no employee ID provided), llama-3.3-70b-versatile
# either invented a tool that was never given to it, or looped for the
# full recursion limit instead of just asking. This instruction is the
# fix, applied once here rather than copy-pasted into all 5 agent prompts.
_SHARED_GUARDRAILS = (
    " If you don't have enough information to use one of your tools (like "
    "a missing ID), ask the user for it directly instead of guessing or "
    "retrying repeatedly. Only use the tools you've actually been given — "
    "never call a tool that wasn't provided to you, even if one would be "
    "convenient."
)


def build_agent(candidate: ModelCandidate, system_prompt: str, tools: list):
    return create_agent(
        model=make_chat_model(candidate),
        tools=tools,
        system_prompt=system_prompt + _SHARED_GUARDRAILS,
    )
