"""
Golden dataset for regression-testing HYDRA's routing + agent pipeline.

Why this exists: Phase 2's streaming rewrite removed `response_format`
from agents/base.py but left "Set needs_escalation=true" instructions in
every agent's system prompt. Nothing automated caught it — it only
surfaced when a real model, given a real query, tried to literally follow
that now-meaningless instruction and leaked "needs_escalation=true" into
a live answer. This dataset (and the leak-pattern checks in run_eval.py)
exists specifically so the next version of that bug fails a test instead
of shipping.

15 cases, 3 per agent, mostly paraphrased rather than copied from the
routes' own training utterances (routing.semantic_router_config) — an
exact-utterance match is the easy case; paraphrases are the real test.

This is a starting set, not a finished one. Add a case here every time a
query gets misrouted or produces a bad answer in real use — that's the
actual point of a golden dataset: it should grow from real failures, not
just be written once and left alone.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenCase:
    query: str
    expected_agent: str


GOLDEN_DATASET: list[GoldenCase] = [
    # devops_sre
    GoldenCase("the checkout service keeps crashing every few minutes", "devops_sre"),
    GoldenCase("can you check if the payments API is healthy", "devops_sre"),
    # Known, accepted edge case: this query literally contains "CI", which
    # also appears verbatim in code_review's own training utterance ("why
    # is the CI check failing on my PR") — genuine real-world ambiguity
    # (a CI/deployment failure legitimately touches both domains), not a
    # bug. Chased once via utterance tuning without fully resolving it;
    # further tuning risked reintroducing the devops/customer_support
    # regression this dataset also caught. Left as a documented miss
    # rather than chased into whack-a-mole.
    GoldenCase("why did our last deployment fail in CI", "devops_sre"),
    # customer_support
    GoldenCase("a customer wants their money back for a broken product", "customer_support"),
    GoldenCase("how do I reset someone's account password", "customer_support"),
    GoldenCase("user is really frustrated their app won't stop crashing", "customer_support"),
    # hr_recruiting
    GoldenCase("book an interview slot for the senior engineer candidate", "hr_recruiting"),
    GoldenCase("how many vacation days does an employee have left", "hr_recruiting"),
    GoldenCase("what's our policy on maternity leave", "hr_recruiting"),
    # finance_expense
    GoldenCase("can I get reimbursed for my flight to the conference", "finance_expense"),
    GoldenCase("what's the daily limit for meal expenses", "finance_expense"),
    GoldenCase("I need approval for a new laptop purchase", "finance_expense"),
    # code_review
    GoldenCase("take a look at my latest pull request", "code_review"),
    GoldenCase("is this function following our coding standards", "code_review"),
    GoldenCase("what's blocking PR number 501 from merging", "code_review"),
]
