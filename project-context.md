# HYDRA — Project Handoff Document

> Read this whole document before touching code. It's the distilled state of a long,
> heavily-iterated build. Where it says something was "verified live," treat that as
> load-bearing — it means a real bug was found and fixed on real hardware, not a
> theoretical concern.

## 1. Project Overview

**HYDRA** is a portfolio project: an "Enterprise AI Operating System" — a single
gateway that employees send natural-language queries to, which automatically routes
each query to one of several specialist AI agents (DevOps/SRE, Customer Support,
HR/Recruiting, Finance/Expense, Code Review), answers it on a fast/cheap model by
default, and transparently escalates to a stronger model only when the answer needs
it.

**Core pitch:** replace 10-15 separate AI tool subscriptions (one per department)
with one gateway, one architecture, cost-optimized by routing simple queries to free
models and reserving expensive models for genuinely hard ones.

**Target user:** the person building this is an AI/agentic engineer using it as a
portfolio piece for senior/staff-level job applications (mentioned target comp range
$400-450k). The build emphasizes things that read as "senior signals" in an
interview: measured trade-offs, real verified bugs and fixes, resilience design,
observability, honest documentation of limitations — not just "it works."

**Original spec** (paraphrased from the person's own words): "I want gRPC + semantic
router for speed, a small/fast default model, with fallback to stronger models
(DeepSeek-V4-Pro, Gemini) for complex queries, plus Redis, Docker, and Kubernetes."

**How this actually shipped, and why it differs slightly from the original ask:**
mid-project the person got real API keys for **Groq** (extremely fast inference
hardware) and switched the default tier to Groq specifically to fix a measured
latency problem (free-tier OpenRouter models were taking 8-30+ seconds; Groq brought
that to ~1-3s). This was a deliberate, data-driven pivot, not scope drift — see
Section 6.

## 2. Tech Stack & Architecture

### Languages / frameworks
- **Gateway**: TypeScript, Bun runtime, **Connect-RPC v2** (`@connectrpc/connect`,
  `@connectrpc/connect-node`, `@bufbuild/protobuf` v2.x)
- **Supervisor**: Python 3.12, pure gRPC (`grpcio`), **LangGraph** (`langgraph`),
  **LangChain 1.0** (`langchain`, `langchain-openai`)
- **Routing**: `semantic-router` (aurelio-labs), `fastembed` encoder (local ONNX,
  no API key)
- **Cache**: Redis (plain `redis:7-alpine`, not Redis Stack)
- **Observability**: Langfuse Cloud (SDK v4.x, OpenTelemetry-based under the hood)
- **Infra**: Docker Compose (working, tested) + Kubernetes manifests (written,
  **not yet tested** — see Section 5)

### The contract: one proto, two protocols, two languages
`proto/hydra/v1/query.proto` defines `QueryService` with one streaming RPC
(`SubmitQuery`) and one health RPC (`Health`). The **same** generated types serve:
- **Client ↔ Gateway**: Connect protocol (browser-native fetch; also speaks gRPC and
  gRPC-Web on the same port automatically — Connect-ES's whole selling point)
- **Gateway ↔ Supervisor**: pure gRPC

Payoff proven live: adding a new message (`EscalationNotice`) and oneof case to the
proto required **zero gateway code changes** — the gateway is a thin proxy that just
forwards whatever chunk type comes through.

### The cascade (the actual core logic, in `supervisor/main.py`)
```
1. Redis semantic cache check (embed query, cosine-compare against cached queries)
   → hit: replay stored answer, ~100ms, done
2. Semantic router (embedding similarity, ZERO LLM calls) picks an agent
   → if no route clears confidence threshold (0.6): fall through to...
3. LLM router: a small structured-output call (schema-constrained via Literal type
   so it CANNOT hallucinate a nonexistent agent name) picks the agent
4. Stream the chosen agent's answer on DEFAULT_POOL (Groq), real token-by-token
   streaming via astream_events()
5. Separate, small "confidence check" call (NOT the same call that produced the
   answer — see Section 6 for why) rates the answer 0-1 and flags needs_escalation
6. If confidence < threshold (0.70) or needs_escalation: send an EscalationNotice,
   re-stream the answer on ESCALATION_POOL (DeepSeek → Gemini)
7. Cache the final answer, send final metadata (agent, model, latency, cached,
   escalated, route_method, route_confidence)
```

### Provider architecture — 3 providers, one abstraction
`supervisor/models/model_pool.py` defines a `ModelCandidate(model_id, tier,
provider, note)` and a `PROVIDERS` dict mapping provider name → `{base_url,
api_key}`. Every provider speaks the OpenAI wire format, so `make_chat_model()`
always returns a `ChatOpenAI` regardless of provider — only base_url/api_key change.

```python
DEFAULT_POOL = [
    ModelCandidate("openai/gpt-oss-120b", DEFAULT, "groq", "..."),
    ModelCandidate("openai/gpt-oss-20b",  DEFAULT, "groq", "backup"),
]
ESCALATION_POOL = [
    ModelCandidate("deepseek/deepseek-v4-flash", ESCALATION, "openrouter", "tried first"),
    ModelCandidate("deepseek/deepseek-v4-pro",   ESCALATION, "openrouter", "tried second"),
    ModelCandidate("gemini-3.6-flash",           ESCALATION, "google", "last resort"),
]
```

`run_with_fallback(pool, fn)` tries each candidate in order until one succeeds.

### Key architectural decisions (with the "why" — useful for interview framing)

- **Confidence check is a separate call from the answer**, not folded into
  `response_format`. Verified live: forcing structured output via
  `response_format`/`json_schema` routes the model through tool-calling, which traps
  the answer text inside JSON tool-call arguments — you get broken JSON fragments
  when you try to stream it, not prose. Trade: one extra small LLM call per query,
  in exchange for the main answer actually streaming token-by-token.
- **`method="function_calling"` is explicit** on every `.with_structured_output()`
  call. Verified live: LangChain's *default* structured-output mode for `ChatOpenAI`
  is `json_schema`, and Groq's models reject that with a 400 ("does not support
  response format json_schema"). This was silently breaking confidence-based
  escalation for a long stretch — the failure was swallowed by error handling that
  assumed "confidence check failed → assume confident, don't escalate."
- **Schema-constrained routing.** The LLM router's output schema uses a dynamic
  `Literal` of real agent names, so it's a Pydantic validation error — not a silent
  bad route — if the model tries to invent a nonexistent agent.
- **DEFAULT_POOL exhaustion auto-escalates** rather than erroring out. If every
  DEFAULT_POOL candidate fails *before sending any token* (mid-stream failures are
  handled differently — see below), `main.py` treats this as a last-resort
  escalation to ESCALATION_POOL instead of returning an error while two working
  providers sit idle. Verified live twice: once via a deliberately-broken API key,
  once for real when Groq deprecated a model out from under the app in production.
- **Streaming retry semantics are asymmetric.** Once a token has reached the
  client, you can't un-send it — so a mid-stream failure ends the response with an
  error (no silent retry on a different model). A failure *before* any token is
  sent is safe to retry on the next pool candidate. This distinction is
  load-bearing logic in `main.py`'s `_stream_tier` helper, not an incidental detail.
- **Shared guardrails on every agent's system prompt** (`agents/base.py`): "if you
  don't have enough info to use a tool, ask — don't guess or invent a tool you
  weren't given." Added after live evidence (via a Langfuse trace) that a model
  hallucinated a nonexistent tool name and, in another case, looped for the full
  default 25-step LangGraph recursion limit (38+ seconds) rather than asking a
  clarifying question.
- **`RECURSION_LIMIT = 10`**, set explicitly (LangGraph's default is 25) — same
  incident as above; a stuck agent should fail in a few seconds, not 38.
- **Groq specifically for the default tier**, chosen for raw speed (LPU hardware).
  OpenRouter kept only for DeepSeek access (avoids needing a separate DeepSeek
  account). Google AI Studio used directly for Gemini. This is 3 providers behind
  one clean abstraction, not 3 SDKs scattered through the codebase.
- **Langfuse Cloud, not self-hosted.** Current self-hosted Langfuse (v3) is a
  6-service stack (ClickHouse, Postgres, Redis, MinIO, web, worker). Given repeated,
  real Docker Desktop resource pressure on the dev machine this session (see
  Section 6), adding 6 more containers was assessed as a real reliability risk, not
  hypothetical. Documented as a deliberate trade-off in the README, not an oversight.
- **Docker/K8s health checks call the app's own `Health` gRPC RPC** (`healthcheck.py`
  script, reused verbatim as both the Docker `HEALTHCHECK` and the K8s
  readiness/liveness probe `exec` command). This replaced a fragile pattern of
  manually guessing sleep durations before the app was actually ready (the semantic
  router's embedding model takes 18-40+ seconds to download on first boot, and
  "container started" ≠ "app accepting connections" — this mismatch caused many
  confusing `ECONNREFUSED` errors before the healthcheck was added).

## 3. File/Folder Structure

```
hydra/
├── README.md                       # Architecture, setup, "lessons from real hardware", roadmap
├── project-context.md              # This file
├── .env.example                    # Template for every required env var
├── .gitignore
├── docker-compose.yml              # gateway + supervisor + redis
├── buf.yaml / buf.gen.yaml         # Proto lint + TypeScript codegen config
│
├── proto/hydra/v1/query.proto      # Shared gRPC/Connect contract (source of truth)
│
├── gateway/                        # TypeScript + Bun + Connect-RPC
│   ├── package.json / tsconfig.json / Dockerfile
│   └── src/
│       ├── index.ts                # Entry point
│       ├── server.ts               # HTTP/2 server (Connect + gRPC + gRPC-Web)
│       ├── connect.ts              # Router — thin proxy to Supervisor
│       ├── supervisor-client.ts    # gRPC client to the Python Supervisor
│       ├── config.ts
│       └── gen/                    # Generated from proto via `buf generate` (committed)
│
├── supervisor/                     # Python — the actual orchestration/"brain"
│   ├── main.py                     # gRPC server + full cascade (see Section 2)
│   ├── config.py                   # All settings, read from env vars
│   ├── observability.py            # Langfuse tracing — optional, graceful no-op if unset
│   ├── healthcheck.py              # Calls the service's own Health RPC; used by Docker + K8s
│   ├── requirements.txt / Dockerfile
│   ├── models/
│   │   └── model_pool.py           # ModelCandidate, PROVIDERS, DEFAULT_POOL, ESCALATION_POOL,
│   │                                # run_with_fallback(), make_chat_model()
│   ├── routing/
│   │   ├── semantic_router_config.py   # Route/utterance definitions for all 5 agents
│   │   └── llm_router.py               # Schema-constrained LLM fallback routing
│   ├── cache/
│   │   └── semantic_cache.py       # Redis-backed semantic cache (Python-side cosine scan)
│   ├── agents/
│   │   ├── base.py                 # build_agent() factory + shared guardrail text
│   │   ├── schemas.py              # ConfidenceAssessment pydantic model
│   │   ├── streaming.py            # stream_agent_answer() via astream_events()
│   │   ├── confidence.py           # assess_confidence() — the separate post-hoc call
│   │   ├── registry.py             # AGENT_BUILDERS: route name -> builder function
│   │   ├── devops_agent.py         # + check_service_status mock tool
│   │   ├── support_agent.py        # + lookup_refund_policy mock tool
│   │   ├── hr_agent.py             # + lookup_leave_balance mock tool
│   │   ├── finance_agent.py        # + check_expense_policy mock tool
│   │   └── code_review_agent.py    # + check_pr_status mock tool
│   └── gen/                        # Generated Python grpc stubs (committed)
│
├── eval/
│   ├── golden_dataset.py           # 15 cases, 3 per agent, with inline notes on known edge cases
│   └── run_eval.py                 # Regression runner — hits the real gateway, checks routing +
│                                    # leak-patterns + escalation, prints a pass/fail summary
│
├── infra/k8s/                      # NEW, UNTESTED — see Section 5
│   ├── 00-namespace.yaml
│   ├── 01-redis.yaml
│   ├── 02-gateway-config.yaml
│   ├── 03-supervisor.yaml
│   └── 04-gateway.yaml
│
└── scripts/
    └── test_client.py              # Manual CLI test client; prints first-token vs total
                                     # latency to prove real streaming, handles escalating/
                                     # error/final chunks
```

**Not yet created:** `infra/terraform/`, `observability/` (Prometheus/Grafana
configs — Langfuse covers LLM-specific tracing but not infra-level metrics), any
CI config, any auth/RBAC code.

## 4. Features Completed (verified working on real hardware, not just logic-tested)

- Full gRPC/Connect-RPC gateway↔supervisor pipeline, real token streaming
  (confirmed via measured first-token-vs-total-latency gaps, e.g. 1.7s first token
  vs 8.3s total — proves genuine progressive delivery, not chunked-after-the-fact)
- Semantic routing across 5 agents with a calibrated confidence threshold (0.6,
  derived from real measured scores: exact match ~0.71, paraphrase ~0.70, unrelated
  query ~0.47 — NOT guessed)
- LLM-fallback routing for ambiguous queries, schema-constrained against hallucinated
  agent names (verified: a fake agent name raises a Pydantic `ValidationError`)
- 5 working agents (DevOps/SRE, Customer Support, HR/Recruiting, Finance/Expense,
  Code Review), each a real LangGraph agent with one working mock tool
- Redis semantic cache (verified hit + miss against a live Redis instance)
- 3-provider model routing (Groq default, OpenRouter + Google escalation) with
  automatic fallback within a tier and automatic escalation if the whole default
  tier is down
- Confidence-based escalation, live-verified (see Section 6 bug list — this took
  multiple real fixes to actually work)
- Docker healthcheck reusing the app's own Health RPC — eliminated a whole category
  of race-condition bugs from guessing startup timing
- Eval harness: 15-case golden dataset, checks routing accuracy + a literal
  regex check for a specific historical bug (`needs_escalation=` leaking into
  answer text) + prints escalation/model/latency per case; exit code is CI-gate-ready
- Langfuse tracing wired into all 3 LLM call sites via a single `trace_config()`
  helper; confirmed working end-to-end (traces visible in the Langfuse dashboard,
  full call tree: routing → agent → tool call → confidence check)
- Kubernetes manifests **written** for all 3 services (see Section 5 — not yet
  applied/tested)

### Real bugs found and fixed this session (all found via live testing, not code review)
1. **`needs_escalation=true` leaking into answer text.** Cause: a Phase-2 refactor
   removed `response_format` from agents (to enable streaming) but left "Set
   needs_escalation=true for X" instructions in every agent's system prompt — the
   model tried to literally comply by writing it as text. Fix: rewrote all 5 system
   prompts to describe severity in natural language instead of referencing a field
   that no longer exists for the model to set.
2. **Tool hallucination + 25-step recursion loop.** An under-specified query (no
   employee ID given) caused Llama-via-Groq to either invent a tool that was never
   provided, or loop for the full default recursion limit. Fix: shared guardrail
   text on every agent ("ask, don't guess/invent") + explicit `RECURSION_LIMIT=10`.
3. **Confidence check silently 400ing on every single call.** Groq's models reject
   LangChain's default `json_schema` structured-output mode. Because the failure
   was caught and treated as "assume confident," confidence-based escalation had
   never actually fired since Groq became the default — this was discovered via a
   Langfuse trace, not by symptom-chasing. Fix: explicit `method="function_calling"`
   on both `.with_structured_output()` call sites (`agents/confidence.py`,
   `routing/llm_router.py`).
4. **Groq deprecated `llama-3.3-70b-versatile`** (deprecated 17 Jun 2026, fully
   removed by ~Aug 2026) mid-project, with zero code-side warning — it just started
   404ing in production weeks after being verified working. Fix: migrated
   `DEFAULT_POOL` to `openai/gpt-oss-120b` (Groq's own documented migration target)
   with `openai/gpt-oss-20b` as a second backup candidate (previously only one
   candidate existed — the DEFAULT_POOL-exhaustion-escalates design absorbed this
   failure gracefully in production, but every query was paying escalation-tier
   cost/latency until the model swap was caught).
5. **Langfuse traces never arriving (401, then never showing up at all).** Two
   separate causes: (a) API key auth failure — fixed by regenerating a fresh key
   pair; (b) the SDK batches/exports asynchronously (OTel-based) and the supervisor
   is a long-running process with nothing else triggering a flush — fixed by adding
   an explicit `flush()` call wrapped in `try/finally` around the whole
   `SubmitQuery` RPC handler (see `observability.py` + `main.py`'s thin
   `SubmitQuery` wrapper around `_submit_query_impl`).

## 5. In Progress / Known Issues

- **Kubernetes manifests are written but UNTESTED.** They were just created this
  session; the person has Docker Desktop's built-in Kubernetes available
  (`kubectl config get-contexts` shows `docker-desktop` as current context) but has
  not yet run `kubectl apply -f infra/k8s/` or verified pods come up healthy. **This
  is the most likely immediate next step.** Known risk areas to check first: image
  availability (`imagePullPolicy: Never` assumes Docker Desktop's K8s shares the
  Docker image store — should hold true but hasn't been confirmed live), whether
  the LoadBalancer Service actually gets an `EXTERNAL-IP` of `localhost` on this
  machine (Docker Desktop usually handles this automatically; port-forward is the
  documented fallback if not), and whether the readiness probe's generous timing
  (headroom for the 18-40s embedding-model download) is actually sufficient.
- **Secret handling is simplified, not production-grade.** The K8s Secret
  (`hydra-secrets`) is created directly from a copy of `.env` (with `REDIS_URL`
  corrected for in-cluster DNS) via `kubectl create secret generic --from-env-file`.
  This puts non-secret config (thresholds, URLs) in the Secret alongside real API
  keys. Documented in the README as a known simplification worth splitting into a
  proper Secret+ConfigMap pair later.
- **One accepted, documented routing edge case**: a query containing the literal
  word "CI" ("why did our last deployment fail in CI") reliably misroutes to
  `code_review` instead of `devops_sre`, because `code_review`'s own training
  utterance literally contains "CI" too. Chased once via utterance tuning (which
  fixed 2 other cases but couldn't fully resolve this one without risking new
  regressions elsewhere — semantic routing tuning is inherently a shifting-boundary
  problem, not a fix-once-and-done one). Documented directly in
  `eval/golden_dataset.py`'s comments so it doesn't look like a mystery failure.
- **Semantic cache uses Python-side cosine similarity scan, not Redis Stack's
  native vector search (`FT.CREATE`/`FT.SEARCH` KNN).** Fine at portfolio/demo
  scale (hundreds of cached entries); documented in the README as the natural
  upgrade path if this needs to scale.
- **Langfuse traces aren't tagged with the app's own `session_id`/`user_id`** (from
  `SubmitQueryRequest`). Investigated: the straightforward approach
  (`config={"metadata": {"langfuse_session_id": ...}}`) has an open, documented SDK
  bug specifically with LangGraph (metadata attaches to a child span, not the
  parent trace). The current correct approach is Langfuse's
  `propagate_attributes()` context manager, not yet wired in — deferred rather than
  shipped half-verified.
- **KEDA autoscaling not started** — needs the KEDA operator installed via Helm
  first, then a `ScaledObject` watching Redis queue depth (or similar metric).
- **The confidence-check's actual calibration is still an open question.** It was
  completely broken (silently erroring) for a long stretch of this session (see bug
  #3 above); now that it's fixed (`method="function_calling"`), its real-world
  behavior — does it escalate at reasonable moments, is it well-calibrated, is it
  systematically over/under-confident — has not yet been observed across a range of
  genuinely hard queries. Worth deliberately testing with queries designed to need
  escalation, now that the mechanism actually works.

## 6. Conventions & Preferences

- **All conversational responses in this project were written in Hinglish**
  (Hindi-English code-mixed, Latin script) per explicit instruction — this document
  itself is in English since it's a technical reference artifact (matching the
  register of the actual README and code comments, which are English throughout).
  If continuing in conversation with the person, default back to Hinglish unless
  told otherwise.
- **"Verify, don't guess" is the operating principle of this entire project**,
  applied especially aggressively to anything that could have changed since a
  knowledge cutoff: model names/availability, library APIs, current best practices.
  This repeatedly mattered in practice — Connect-ES's v1→v2 API changed
  significantly, `langgraph.prebuilt.create_react_agent` is now superseded by
  `langchain.agents.create_agent`, `semantic-router`'s `RouteLayer` class was
  renamed `SemanticRouter`, LangChain's structured-output default silently doesn't
  work on Groq, and a "stable" model (`llama-3.3-70b-versatile`) was deprecated and
  removed mid-project. **Assume any specific model name, library version, or API
  signature needs a fresh check before being used in new code** — don't trust this
  document's specifics past their stated verification point without re-checking if
  meaningful time has passed.
- **Every fix ships with an inline code comment explaining the bug, how it was
  found, and why the fix works** — not just what changed. Continue this pattern;
  it's directly useful both for future debugging and as interview material.
- **The person works on Windows + PowerShell + VS Code + Docker Desktop.** All
  shell guidance must be PowerShell syntax, not bash. Common recurring friction
  points to anticipate:
  - New PowerShell terminals in VS Code sometimes open in the wrong working
    directory (one level above the actual project root) — always confirm with
    `pwd` before assuming context, or prefix commands with an explicit `cd`.
  - Docker Desktop's "Resource Saver" repeatedly put the whole stack to sleep
    mid-session on this machine; recommend disabling it, or always be ready to
    diagnose `connection refused` errors as "stack is asleep, not actually broken."
  - Windows Smart App Control has intermittently blocked the `grpc` package's
    native `cygrpc` DLL (`ImportError: DLL load failed... Application Control
    policy has blocked this file`) after fresh `pip install`s into new venvs. Fix:
    Windows Security → Virus & Threat Protection → Protection History → Allow on
    device; or disable Smart App Control entirely (irreversible without a fresh
    Windows install — a real trade-off, not a casual suggestion).
  - Blind `Start-Sleep`-based waits for container readiness are unreliable (the
    embedding-model download takes a variable 18-40+ seconds). Prefer polling
    `docker inspect --format='{{.State.Health.Status}}' <container>` in a bounded
    loop (with a max-tries cutoff — an earlier unbounded polling loop ran 190+
    iterations once when a build failed and the container never existed).
- **Delivery workflow**: Claude builds and verifies in its own sandbox (bash tool,
  can't reach most external APIs — verified this repeatedly by testing what's
  reachable: pypi/npm work, but `groq.com`, `langfuse.com`, `huggingface.co` etc.
  don't), then zips the project and delivers via `present_files`. **The person
  re-extracts and copies the *contents* of the `hydra/` folder into their **same,
  persistent working folder** each time** (not a fresh folder per delivery) — their
  `.env`, `venv/`, and `docker-compose.override.yml` (a personal Redis port remap,
  since a different unrelated project of theirs already uses port 6379) live only
  on their machine and are never part of what Claude ships. When the person has
  used a *fresh* folder for a new zip instead of reusing the working one, it has
  caused real problems (missing venv, missing port-override, confusing
  double-nested paths) — always confirm which folder is the "real" one before
  giving copy instructions.
- **Standard rebuild-and-test sequence** the person has done many times — reuse
  this exact shape when proposing next steps:
  ```powershell
  docker compose up -d --build supervisor   # or full `down` + `up -d --build` if
                                             # docker-compose.yml itself changed
  # then poll for healthy (bounded loop, not blind sleep)
  docker compose restart gateway            # only needed if supervisor's container
                                             # was recreated while gateway kept running
  docker exec hydra-redis-1 redis-cli FLUSHALL   # before testing, to avoid stale
                                                   # cached answers masking the test
  venv\Scripts\python.exe scripts/test_client.py "some query"   # let it run to
                                                                  # completion —
                                                                  # Ctrl+C mid-run has
                                                                  # repeatedly destroyed
                                                                  # diagnostic info
  ```
- **Zip packaging gotcha**: exclude `.git` directories with `-x "*/.git/*"`, **not**
  `-x "*.git*"` — the broader pattern also matches and silently drops `.gitignore`
  itself. This actually happened once; caught and fixed.
- **Security**: the person has pasted real API keys into chat/logs several times
  unintentionally (once via `docker compose config`'s resolved-env-vars output,
  which prints secrets in plaintext by design; once directly). Standing instruction:
  flag this immediately and clearly whenever it happens, recommend rotating the
  exposed key, and proactively suggest safer alternatives (e.g., `Select-String`
  filtered output, or boolean-only presence checks like
  `python -c "import os; print(bool(os.getenv('X')))"`) instead of ever printing a
  secret value.
- **Sandbox persistence**: Claude's own working sandbox has reset mid-session more
  than once (long conversation). The last successfully-packaged zip in
  `/mnt/user-data/outputs/` is the durable source of truth to restore from — always
  check there before concluding work was lost, and re-verify (grep for known
  fix-markers, syntax-check) after restoring rather than assuming the restore is
  current.
- **Interaction style**: the person generally wants direct action and concrete next
  steps rather than being asked to choose among options — except at natural
  checkpoints between major phases of work (e.g., "eval harness is solid now —
  README consolidation, or move to Phase 3b?"), where offering 2-3 concrete choices
  has worked well. When something breaks, they want the *specific, verified* root
  cause, not a plausible-sounding guess — this document's whole bug list exists
  because guesses were repeatedly wrong and had to be corrected by actually reading
  logs/traces.

## 7. Next Steps

**Immediate (in likely priority order):**
1. Test the Kubernetes manifests live: `kubectl apply -f infra/k8s/` (after
   creating the namespace + secret per the README's Kubernetes section), confirm
   all 3 pods reach `Running`/ready, confirm the gateway is reachable
   (LoadBalancer `EXTERNAL-IP` or `kubectl port-forward` fallback), run
   `scripts/test_client.py` against it end-to-end.
2. Once K8s is confirmed working, deliberately test a few queries designed to
   trigger confidence-based escalation (now that the `function_calling` fix makes
   this mechanism actually functional) to observe real calibration — this was
   never actually verified working end-to-end for genuinely hard queries.
3. Circle back and consolidate the README's "Lessons from running this on real
   hardware" section — a significant amount of real, valuable debugging history
   from this session (the Langfuse 401/flush bugs, the Groq deprecation, the
   `function_calling` structured-output bug) has been fixed in code but not yet
   written up in the README the way earlier lessons were. The person has
   deliberately deferred this multiple times ("we'll do the README at the end") —
   check whether "the end" has arrived, or keep deferring per their direction.

**Then, per the original roadmap (Phase 3b remainder / Phase 4):**
- KEDA autoscaling (install the KEDA operator via Helm, add a `ScaledObject`
  watching Redis queue depth or similar)
- Remaining 10 of the planned 15 agents (currently have 5: DevOps/SRE, Customer
  Support, HR/Recruiting, Finance/Expense, Code Review)
- Cost dashboard (per-query/per-agent/per-model token + $ breakdown)
- OpenTelemetry + Prometheus/Grafana for infra-level metrics (Langfuse only covers
  LLM-call-level tracing)
- Circuit breakers on external LLM calls (formalizing what `run_with_fallback`
  currently does informally)
- JWT/OAuth2 + RBAC (which department can access which agent)
- Terraform (currently only local Docker Desktop K8s — no real cluster/VPC
  provisioning exists)
- Ingress instead of a bare `LoadBalancer` Service
- Resource requests/limits on the K8s container specs (currently unset)
- Splitting the K8s Secret into a proper Secret (real secrets only) + ConfigMap
  (everything else) pair

**Open question worth raising with the person early in a new conversation:**
given how much has been built and fixed, is the goal still to complete all 15
agents and the full Phase 4 list, or has the portfolio/interview-prep goal shifted
based on what's already demonstrable? The current state (multi-provider resilience,
real bugs found via observability, a working eval harness, K8s in progress) is
already a strong, complete-feeling story — worth confirming scope before continuing
to add breadth.
