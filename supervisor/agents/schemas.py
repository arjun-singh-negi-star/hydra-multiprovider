from pydantic import BaseModel, Field


class ConfidenceAssessment(BaseModel):
    """A small, cheap judgment made AFTER an answer has already streamed to
    the client — deliberately NOT folded into the same call that produces
    the answer text. Folding it in (Phase 1's approach, via
    response_format) forces structured output through tool-calling, which
    traps the answer inside a tool-call's JSON arguments instead of
    streamable prose — verified live: raw streamed chunks came out as
    broken JSON fragments, not readable text. See agents/base.py."""

    confidence: float = Field(ge=0, le=1, description="how confident this answer is, 0-1")
    needs_escalation: bool = Field(description="true if this genuinely needs a stronger reasoning model")
