"""
Post-hoc confidence check — runs AFTER an answer has already fully
streamed to the client. Small prompt, small output (just a float and a
bool), so it stays cheap even though it's an extra call per query. This
is the Phase 2 trade: one extra small call, in exchange for the main
answer streaming as real tokens instead of arriving all at once.
"""

from agents.schemas import ConfidenceAssessment
from models.model_pool import ModelCandidate, make_chat_model
from observability import trace_config

_PROMPT = (
    "You just answered an employee's question. Rate your own answer.\n\n"
    "Question: {query!r}\n"
    "Answer given: {answer!r}\n\n"
    "confidence: how sure you are this answer is correct and complete (0-1).\n"
    "needs_escalation: true only if this genuinely needs a stronger "
    "reasoning model to do better — not just because you're being modest."
)


async def assess_confidence(query: str, answer: str, candidate: ModelCandidate) -> ConfidenceAssessment:
    # method="function_calling" is explicit and load-bearing, not
    # cosmetic: LangChain's default structured-output mode for
    # ChatOpenAI is `json_schema`, and Groq's llama-3.3-70b-versatile
    # rejects that with a 400 ("does not support response format
    # json_schema"). Caught live via a Langfuse trace — every single
    # confidence check was failing silently (our own error handling
    # treated it as "assume confident, don't escalate"), so
    # confidence-based escalation had never actually fired since
    # switching to Groq as the default tier. function_calling uses
    # ordinary tool-calling to get the same structured result, which
    # this model already handles fine (it's how the agent's own real
    # tools work).
    checker = make_chat_model(candidate, temperature=0).with_structured_output(
        ConfidenceAssessment, method="function_calling"
    )
    return await checker.ainvoke(_PROMPT.format(query=query, answer=answer), config=trace_config())
