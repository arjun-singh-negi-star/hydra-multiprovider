"""
Zero-LLM-call routing layer. Encode the query once, cosine-compare against
each agent's example utterances — this is the "0 token cost" step from the
design doc. Only when NO route clears its threshold do we spend an LLM
call on routing (see routing/llm_router.py).

Phase 2 ships 5 of the eventual 15 agents (matches the design doc's own
Week 3-4 target of "4-5 agents"). Adding agent #6 is: write a Route with
~6-8 realistic utterances, add it to AGENT_ROUTES, register a builder in
agents/registry.py. Nothing else in the system needs to change — that
decoupling is the actual point of the router/agent split.
"""

from semantic_router import Route, SemanticRouter
from semantic_router.encoders import FastEmbedEncoder

from config import settings

devops_sre = Route(
    name="devops_sre",
    score_threshold=settings.route_confidence_threshold,
    utterances=[
        "the production server is down",
        "kubernetes pod is crash looping",
        "the backend service keeps crashing under heavy load",
        "can you restart the deployment",
        "the deployment pipeline failed to release to production",
        "check disk usage on the staging box",
        "rollback the last release",
        "the API is returning 500 errors",
        "database connections are maxed out",
        "is the payments service healthy right now",
    ],
)

customer_support = Route(
    name="customer_support",
    score_threshold=settings.route_confidence_threshold,
    utterances=[
        "a customer is asking for a refund",
        "how do I reset a user's password",
        "customer says the app is crashing on login",
        "a frustrated user says the app keeps crashing",
        "escalate this support ticket",
        "what is our refund policy",
        "user can't access their account",
        "customer is unhappy with response time",
    ],
)

hr_recruiting = Route(
    name="hr_recruiting",
    score_threshold=settings.route_confidence_threshold,
    utterances=[
        "schedule an interview for the backend role",
        "how many leave days do I have left",
        "update my payroll bank details",
        "onboard a new employee starting Monday",
        "what is our parental leave policy",
        "reject this candidate's application",
        "post a new job opening",
    ],
)

finance_expense = Route(
    name="finance_expense",
    score_threshold=settings.route_confidence_threshold,
    utterances=[
        "can I expense this client dinner",
        "what's the reimbursement policy for travel",
        "submit an expense report for my conference trip",
        "how much can I spend on a hotel per night",
        "my expense report was rejected",
        "what's our budget for the marketing campaign",
        "request approval for a software purchase",
    ],
)

code_review = Route(
    name="code_review",
    score_threshold=settings.route_confidence_threshold,
    utterances=[
        "can you review this pull request",
        "is this code following best practices",
        "why is the CI check failing on my PR",
        "suggest improvements for this function",
        "check this code for security issues",
        "what's the status of PR 342",
        "should I use a mutex here or a channel",
    ],
)

AGENT_ROUTES = [devops_sre, customer_support, hr_recruiting, finance_expense, code_review]

# Runs locally via a small ONNX model (fastembed) — no API key, no
# per-query cost, and no torch/transformers dependency (that combo alone
# is 1-2GB+ and directly fights this project's own "fast K8s cold start"
# goal). First run downloads the ONNX model once (~80MB) and caches it in
# `cache_dir` (defaults to a local .fastembed_cache folder).
encoder = FastEmbedEncoder()

router = SemanticRouter(
    encoder=encoder,
    routes=AGENT_ROUTES,
    auto_sync="local",
)
