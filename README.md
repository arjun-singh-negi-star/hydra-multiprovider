# HYDRA — Enterprise AI Operating System

One gateway, N specialist agents. An employee sends one query; HYDRA
decides which specialist agent (or agents) handles it, on the cheapest
model that can answer it correctly, and streams the answer back
token-by-token.

## Status — Phase 1 + Phase 2 done, both verified live; Phase 3b (Kubernetes) now verified live too

The full HYDRA vision (15 agents, Kubernetes + KEDA, Langfuse, eval
harness, OAuth2/RBAC, cost dashboard...) is a 6-8 week build — the original
design doc says so itself, and it's right. So this repo grows as a
**vertical slice**: one real path through the whole architecture first
(Phase 1), then widened and deepened (Phase 2), rather than one giant
batch of untested boilerplate.

**What's real and working here:**
- Shared Protobuf contract (`proto/`), lints clean with `buf lint`
- Gateway in TypeScript/Bun, speaking Connect + gRPC + gRPC-Web simultaneously (`gateway/`)
- Supervisor in Python, pure gRPC, running the full cascade (`supervisor/`):
  cache → semantic router (0 LLM calls) → LLM router (only if ambiguous) →
  stream the agent's answer on the free model tier → small follow-up
  confidence check → escalate + re-stream on a stronger model only if
  that check says it's unsure
- **Real token-by-token streaming** (Phase 2) — the answer streams as the
  model generates it, not chunked after the fact. Escalation now streams
  *twice* (default tier, then escalation tier) with an `EscalationNotice`
  transition in between, since once tokens reach the client they can't be
  un-sent — see "Streaming vs. structured output" below for why this
  needed a real design change, not just a flag flip.
- **5 of the eventual 15 agents** (Phase 2): DevOps/SRE, Customer Support,
  HR/Recruiting, Finance/Expense, Code Review — each its own LangGraph agent
- Redis-backed semantic cache
- `docker-compose.yml` to run the whole thing locally
- **Kubernetes (Phase 3b)** — `infra/k8s/` manifests deployed and verified
  live end-to-end on Docker Desktop's Kubernetes (redis + supervisor +
  gateway all `1/1 Running`, `test_client.py` got a real streamed answer
  through the gateway → supervisor path). See the
  [Kubernetes section](#kubernetes-phase-3b) for the real-hardware gotchas
  hit along the way (Docker Desktop's Kubernetes now runs on a kind
  backend with its own known image-sharing bug, plus a Windows-specific
  `grpc.aio` crash in the test client) — none of these were guessable from
  the manifests alone, only from actually running it.

**Not yet built** (see [Roadmap](#roadmap) below): the other 10 agents,
KEDA autoscaling + Terraform, Langfuse tracing, eval harness, cost dashboard,
OpenTelemetry/Prometheus/Grafana, JWT/OAuth2+RBAC.

## Architecture

```
Browser/client
      │  Connect protocol (plain fetch, browser-native)
      ▼
┌─────────────┐   pure gRPC (HTTP/2, protobuf binary)   ┌──────────────────┐
│   Gateway    │ ───────────────────────────────────────▶│    Supervisor    │
│ (TS + Bun)   │◀─────────────────────────────────────── │    (Python)      │
└─────────────┘        streamed SubmitQueryResponse       └─────┬────────────┘
  thin proxy:                                                    │
  terminates public                          ┌─────────────────┬─┴───────────────┐
  protocols, forwards                        ▼                 ▼                 ▼
  to Supervisor                         Redis cache      Semantic Router     LLM Router
                                       (embedding sim.)   (0 LLM calls)    (structured output,
                                                                            ambiguous case only)
                                                                 │
                                                                 ▼
                                                     ┌───────────────────────┐
                                                     │  Agent (LangGraph)      │
                                                     │  1 of 5, free-tier      │
                                                     │  model — streams real   │
                                                     │  tokens as generated    │
                                                     └───────────┬────────────┘
                                                                 │  answer fully streamed
                                                                 ▼
                                                     ┌───────────────────────┐
                                                     │  Confidence check       │
                                                     │  (small follow-up call) │
                                                     └───────────┬────────────┘
                                                    low confidence /│\ confident enough
                                                    needs_escalation│ send `final` metadata
                                                                 ▼
                                        EscalationNotice → re-stream on
                                        escalation tier (gemini-3.6-flash /
                                        deepseek-v4) → `final` metadata
```

Why the confidence check is a separate step and not folded into the same
call as the answer: `response_format` forces structured output through
tool-calling, which traps the answer text inside JSON tool-call arguments
— streaming that gives broken JSON fragments, not prose (verified live).
Trading one small extra call for real token streaming was the Phase 2 call.

One `.proto` file generates both the TypeScript client/server (gateway)
and the Python client/server (supervisor) — that's the actual payoff of
picking Connect/gRPC over a REST+OpenAPI setup here. Proof it holds up:
adding the `EscalationNotice` message for Phase 2 needed zero gateway
code changes, since the thin proxy just forwards whatever chunk type
comes through.

## Full project structure

```
hydra/
├── proto/hydra/v1/query.proto     # shared contract — source of truth
├── buf.yaml / buf.gen.yaml        # proto lint + codegen config
│
├── gateway/                       # TypeScript + Bun + Connect-RPC
│   ├── src/
│   │   ├── index.ts               # entrypoint
│   │   ├── server.ts              # http2 server (Connect+gRPC+gRPC-Web)
│   │   ├── connect.ts             # router: proxies to Supervisor
│   │   ├── supervisor-client.ts   # gRPC client -> Python supervisor
│   │   ├── config.ts
│   │   └── gen/                   # generated from proto (committed, see note below)
│   ├── package.json / tsconfig.json / Dockerfile
│
├── supervisor/                    # Python + gRPC + LangGraph
│   ├── main.py                    # gRPC server + the full cascade
│   ├── config.py
│   ├── models/model_pool.py       # default(free)/escalation pools + fallback
│   ├── routing/
│   │   ├── semantic_router_config.py   # Route definitions (0-LLM-call routing)
│   │   └── llm_router.py               # structured-output fallback routing
│   ├── cache/semantic_cache.py    # Redis semantic cache
│   ├── agents/
│   │   ├── schemas.py             # ConfidenceAssessment schema
│   │   ├── base.py                # agent factory (plain text, no response_format)
│   │   ├── streaming.py           # streams the agent's final turn, token by token
│   │   ├── confidence.py          # small post-hoc confidence/escalation check
│   │   ├── devops_agent.py / support_agent.py / hr_agent.py
│   │   ├── finance_agent.py / code_review_agent.py
│   │   └── registry.py            # route name -> agent builder
│   ├── gen/                       # generated from proto (committed)
│   ├── requirements.txt / Dockerfile
│
├── scripts/test_client.py         # CLI smoke-test — talks to the gateway
├── docker-compose.yml
├── .env.example
└── README.md   (you are here)

# Phase 3-4 additions (not yet present):
├── agents/{10 more}/              # remaining specialist agents
├── infra/k8s/                     # Deployments, KEDA ScaledObjects
├── infra/terraform/               # cluster/VPC
├── observability/                 # Langfuse, OTel, Prometheus/Grafana configs
├── eval/                          # golden-dataset regression harness
└── auth/                          # JWT/OAuth2 + RBAC middleware
```

## Prerequisites

- [Bun](https://bun.sh) 1.x (gateway)
- Python 3.12+ (supervisor)
- Docker + Docker Compose (easiest way to run everything together)
- A free [OpenRouter](https://openrouter.ai/keys) API key

## Quickstart (Docker Compose)

```bash
cp .env.example .env
# edit .env, paste your OPENROUTER_API_KEY

docker compose up --build
```

This starts Redis, the Supervisor (`:50051`), and the Gateway (`:8080`).
First boot downloads a small ONNX embedding model for the semantic
router (~80MB, one-time, cached after).

Then, in another terminal:

```bash
python scripts/test_client.py "the production pod keeps crash looping"
```

You should see the answer stream in token-by-token as the model actually
generates it (not all at once), followed by metadata showing which agent
handled it, which model answered, and whether it escalated.

Try a near-duplicate of the same query right after — it should come back
in a few ms with `cached: true` (streaming timing only shows up on a
fresh, uncached query, since a cache hit just replays a stored string).

If port `6379`, `8080`, or `50051` is already taken on your machine,
don't edit `docker-compose.yml` directly — create a
`docker-compose.override.yml` instead (Compose merges it in
automatically, and it's gitignored so it stays a personal fix):

```yaml
services:
  redis:
    ports: !override
      - "6380:6379"   # or whatever's actually free on your machine
```

The `!override` tag matters — without it, Compose *appends* to the
`ports` list instead of replacing it, and you'll still have the original
(conflicting) port bound alongside the new one.

## Local dev without Docker

```bash
# terminal 1 — supervisor
cd supervisor
pip install -r requirements.txt --break-system-packages
export OPENROUTER_API_KEY=sk-or-...
python main.py

# terminal 2 — gateway
cd gateway
bun install
bun run dev

# terminal 3
python scripts/test_client.py "how many leave days do I have left"
```

## Eval harness

```bash
docker compose up -d      # stack must be running
python eval/run_eval.py
```

Sends 15 golden-dataset queries (3 per agent) through the real gateway,
checks routing correctness and a few content sanity rules — including a
literal check for `needs_escalation=` appearing in an answer, which is
exactly the bug this harness was built in response to (see
`eval/golden_dataset.py` for the full story). Exit code is non-zero on
any failure, so this is CI-gate-ready as-is.

Grow this file whenever a query gets misrouted or produces a bad answer
in real use — that's what makes it a golden dataset instead of a one-time
test.

## Langfuse tracing (optional)

Every LLM call in the cascade — routing, the agent's answer, the
confidence check, any escalation — can trace to
[Langfuse](https://langfuse.com) so a single query becomes one connected,
inspectable trace instead of scattered log lines. Entirely optional: with
no keys set, `supervisor/observability.py` is a no-op and nothing else
in the system changes.

**Setup:**
```bash
# 1. Free account at https://cloud.langfuse.com -> Settings -> API Keys
# 2. Add to .env:
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

docker compose up -d --build supervisor
```

Run any query, then check the Langfuse dashboard — you should see the
full call tree: semantic router → (LLM router, if it fell through) →
agent → confidence check → escalation (if triggered), with token counts
and latency at every step. This is exactly the visibility that would
have made this session's two real bugs (a hallucinated tool call, a
25-step recursion loop) obvious from a trace instead of a 38-second
hang and a stack trace pasted into chat.

**Why Langfuse Cloud, not self-hosted:** current self-hosted Langfuse
(v3) is a 6-service stack — ClickHouse, Postgres, Redis, MinIO, plus its
own web and worker services. Added to this repo's existing 3 containers,
on a machine that spent a good chunk of this session fighting Docker
Desktop's Resource Saver sleeping mid-task, that's a real reliability
risk, not a hypothetical one. The free hosted tier gets the same tracing
with zero new local containers. Self-hosting v2 (Postgres-only, no
ClickHouse) is a reasonable path later if it's specifically worth
demonstrating.

**Known gap:** traces aren't tagged with our own `session_id`/`user_id`
from `SubmitQueryRequest` yet. Verified live: the straightforward
`config={"metadata": {"langfuse_session_id": ...}}` approach has an open,
documented bug with LangGraph specifically (metadata attaches to a child
span, not the parent trace). The current fix is Langfuse's
`propagate_attributes()` context manager — not wired in yet since it
needs its own verification pass through the streaming generator
functions rather than shipping unverified. See
`supervisor/observability.py` for the full note.

## Regenerating proto code

Generated code is committed for zero-friction clone-and-run, but treat it
as build output — regenerate after any `.proto` change:

```bash
# TypeScript (gateway) — needs the buf CLI + protoc-gen-es
npm install -g @bufbuild/buf @bufbuild/protoc-gen-es
buf generate

# Python (supervisor)
pip install grpcio-tools --break-system-packages
python -m grpc_tools.protoc -I proto \
  --python_out=supervisor/gen --grpc_python_out=supervisor/gen --pyi_out=supervisor/gen \
  proto/hydra/v1/query.proto
```

## What was actually verified while building this (and what wasn't)

Being upfront about this because copy-pasting untested glue code for a
distributed system is worse than useless. Everything below was really run,
in a sandboxed container, not just written from memory:

| Piece | Verified how |
|---|---|
| `query.proto` | `buf lint` — clean pass |
| Gateway TypeScript | `tsc --noEmit` against the real generated Connect-ES v2 code — clean pass |
| gRPC server-streaming (async generator handler) | Real `grpc.aio` server + client, in-process, using the real generated Python stubs |
| Model pool fallback/retry | Unit-tested with a deliberately-flaky fake async function |
| Hallucination-proof routing schema | Confirmed a fake agent name raises a Pydantic `ValidationError` instead of constructing |
| Semantic cache | Ran against a real local Redis instance — cache hit/miss both confirmed |
| `SemanticRouter` confidence threshold | Confirmed against the real `semantic-router` library (with a network-free fake encoder implementing the same interface FastEmbedEncoder uses) — both the "confident" and "falls through" cases |
| `create_agent(...)` structured output (Phase 1 approach) | Fully simulated a 2-turn tool-calling exchange with a scripted fake `BaseChatModel` — confirmed `result["structured_response"]` is a real populated object. **Superseded in Phase 2** — see below. |
| Plain-text token streaming (`astream_events`, Phase 2) | Simulated a real tool-call turn followed by a plain-text final turn with a scripted streaming fake model — confirmed tool-call chunks (empty `content`, populated `tool_call_chunks`) are correctly distinguished from real answer token deltas |
| Escalation/retry control flow (Phase 2, `_stream_tier` in `main.py`) | 4 scenarios run directly against the real orchestration logic: confident answer (no escalation), low confidence (escalates + re-streams), mid-stream failure (errors out, does *not* silently retry with a different model after tokens were already shown), pre-stream failure (cleanly falls back to the next pool candidate) |
| New `EscalationNotice` wire message | Full request/response cycle over a real `grpc.aio` server + client using the regenerated stubs — confirmed the token → escalating → token → final sequence round-trips correctly |
| All 5 agents (Phase 2) | Each one built into a real compiled LangGraph graph and inspected — confirmed no construction-time errors after adding Finance/Expense and Code Review |

**Not verified end-to-end** (this sandbox's network allowlist doesn't
reach `openrouter.ai` or `huggingface.co`/fastembed's CDN): an actual live
OpenRouter completion call, and the actual embedding-model download on
first run. Both are standard, well-documented behavior of libraries that
otherwise checked out completely — but they're the first thing to sanity
check on your machine (`docker compose up` and watch the logs) since your
environment can reach the real internet.

## Lessons from running this on real hardware

Everything in the codebase was verified in a sandbox with no access to
OpenRouter or the embedding-model CDN. Running it for real (Windows +
Docker Desktop) surfaced things the sandbox couldn't catch — logic bugs,
calibration, and eventually the actual proof the Phase 2 streaming rewrite
was for real.

**Phase 1 findings:**

- **`docker-compose.yml` hardcoded the three threshold env vars** instead
  of reading them from `.env` (`ROUTE_CONFIDENCE_THRESHOLD: "0.75"` instead
  of `"${ROUTE_CONFIDENCE_THRESHOLD:-0.6}"`). Editing `.env` silently did
  nothing until this was fixed — a good reminder that "reads from env" and
  "hardcoded with the right-looking value" look identical until you
  actually change the value and watch nothing happen.
- **No `max_tokens` on the OpenRouter client** — some free-tier models
  fall back to a small provider-side default and truncate mid-answer
  (caught live: an answer cut off mid shell-command). Fixed with an
  explicit `max_tokens=1024` in `models/model_pool.py`.
- **`ROUTE_CONFIDENCE_THRESHOLD=0.75` was calibrated against a fake
  encoder, not the real `FastEmbedEncoder`.** Real scores, measured live:
  an *exact* utterance match scored **0.71**, a genuine paraphrase ("the
  checkout service keeps restarting" vs. the trained utterance "kubernetes
  pod is crash looping") scored **0.70**, and a completely unrelated query
  (a movie recommendation) scored **0.47**. `0.75` meant even exact
  matches fell through to the LLM router every time — not wrong, just
  needlessly slow/costly. `0.6` sits cleanly between the relevant cluster
  (~0.70) and the irrelevant case (0.47); shipped as the new default.
- Docker Desktop's Resource Saver can pause the WSL2 VM after idle time,
  which shows up as `connection refused` or `failed to connect to the
  docker API` — not a code bug, just restart the stack
  (`docker compose up -d`, detached so one terminal can both run the stack
  and send test queries).

**Phase 2 findings:**

- **`docker compose config` prints fully resolved env vars — including
  secrets — in plaintext.** Useful for debugging the merge/override
  behavior below, but a real reminder to never paste that output
  somewhere public without redacting first.
- **Compose merges list-type fields (like `ports`) by default, it doesn't
  replace them.** A `docker-compose.override.yml` that just re-declares
  `redis.ports` ends up with *both* the original and the new port bound —
  still conflicts. Fix: the `!override` YAML merge-key tells Compose to
  replace the list instead of appending to it.
  `docker compose config` (which prints the fully-merged, effective
  config) is the fast way to confirm which behavior you actually got.
- **The real, decisive proof the Phase 2 streaming rewrite works**:
  measuring time-to-first-token vs. total latency on a fresh (uncached)
  query. A buffered/fake-streaming implementation would show both numbers
  converge (everything arrives at once, right at the end). What actually
  showed up: first token at **8.3s**, last token at **33.6s** — a 25-second
  window where the answer kept arriving progressively. That gap is the
  proof; a screenshot of nicely-formatted text streaming by itself proves
  nothing, since chunked-after-the-fact output (Phase 1's approach) looks
  identical in a terminal.
- **An agent's own wording can collide with the system's vocabulary.**
  A DevOps agent describing a production outage said "I need to escalate
  this" (meaning: notify the on-call team) in the same response where
  `metadata.escalated = False` (meaning: this query did not get re-run on
  a stronger model). Not a bug — two different, legitimate uses of the
  same word — but a real UX rough edge worth a prompt tweak (e.g. "notify
  the on-call team" instead of "escalate") before this reaches an actual
  user who'd reasonably read that as a contradiction.

The general lesson, worth saying out loud in an interview: **sandbox
verification catches logic bugs; it doesn't catch calibration, and it
can't prove timing claims.** A threshold, a token limit, an env-var wiring
bug — none of these throw an exception, so nothing in a test suite flags
them. And "does this actually stream" is a claim about *when* bytes
arrive, which no amount of reading the code proves — only a clock does.
That gap between "verified in a sandbox" and "proven on real
infrastructure" is exactly what Phase 3's eval harness (golden dataset +
CI regression gate) exists to close systematically, instead of one
manually-run query at a time.

## Design decisions worth defending in an interview

- **`openrouter/free` as primary, not a hardcoded model id.** Verified
  live on 2026-07-27: OpenRouter delisted 7 free endpoints in 9 days,
  including the exact two the original research doc suggested. A
  health-checked pool with the auto-router first is the only version of
  "free tier" that survives contact with reality.
- **Escalation on a small, separate follow-up check, not folded into the
  answer call.** Phase 1 got this for free (the same structured-output
  call carried `confidence`/`needs_escalation`), but that only works
  because structured output rides on tool-calling — which traps the
  answer text inside JSON args and breaks real streaming (verified live).
  Phase 2 trades one extra small call for the answer actually streaming
  as it's generated. Worth having this trade-off ready if asked "why not
  just stream the structured output" — the honest answer is "tried it,
  it streams broken JSON, not prose."
- **Schema-constrained routing.** `RouteDecision.agent_name` is a
  `Literal` of real agent names — the LLM router physically cannot return
  an agent that doesn't exist; it's a validation error, not a runtime
  surprise.
- **One proto, two languages, two protocols.** Same `QueryService`
  definition serves Connect/gRPC/gRPC-Web to the browser and pure gRPC
  internally — no separate REST layer to keep in sync. Proof this paid
  off: adding the `EscalationNotice` message and oneof case in Phase 2
  required zero gateway code changes — the thin-proxy design meant it
  just forwarded the new chunk type transparently.
- **`langchain.agents.create_agent`, not `langgraph.prebuilt.create_react_agent`.**
  The latter still works but is being superseded — worth knowing this
  distinction cold if asked "why LangGraph and not LangChain agents,"
  since the honest answer as of mid-2026 is "they merged."
- **Streaming retry is not the same as request retry.** The default/
  escalation model pools still fall back to the next candidate on
  failure, but only if that failure happens *before* the first token
  reaches the client. Once tokens are out, you can't un-send them, so a
  mid-stream failure ends the response with an error instead of silently
  swapping models. This is a real constraint of streaming systems, not a
  corner cut — good to name explicitly if asked about it.

### Known failure mode to have an answer for

**Adversarial misrouting**: a query that looks simple to the semantic
router/classifier but actually needs real reasoning gets routed to a cheap
model and comes back confidently wrong. The `confidence`/`needs_escalation`
self-report is a mitigation, not a fix — a model can be wrong AND
confident. Phase 3's eval harness (golden dataset + CI regression gate) is
the real answer here; mention that you know this gap exists even before
you've built the harness that closes it.

## Kubernetes (Phase 3b)

Uses Docker Desktop's built-in Kubernetes (no separate kind/minikube
install needed on your end). **Verified live**, including the one
assumption that turned out wrong on real hardware: this cluster does
*not* reliably share `docker compose build`'s image store the way older
Docker Desktop Kubernetes versions did — see "If pods show
`ErrImageNeverPull`" below for why and the fix (`scripts/test-k8s.ps1`
now handles it automatically).

**Recommended: `scripts/test-k8s.ps1`.** Runs every step below in order,
plus three fixes to the original copy-paste sequence found by reasoning
through it line-by-line before the first live run (each explained inline
in the script):
1. The `REDIS_URL` replace below only fires on an exact literal match of
   the default value — if `.env`'s `REDIS_URL` was ever adjusted to match
   your local `docker-compose.override.yml` port remap, this silently
   no-ops and ships a `REDIS_URL` into the Secret that resolves to nothing
   inside the pod. The script matches on the key instead of the value.
2. `kubectl create namespace hydra` followed by `kubectl apply -f
   infra/k8s/` (which also creates the namespace, via
   `00-namespace.yaml`) prints a harmless but noisy "missing
   last-applied-configuration annotation" warning the first time apply
   touches it. The script applies `00-namespace.yaml` directly instead.
3. `kubectl get pods -n hydra -w` is a blind, indefinite watch. The script
   polls with a bounded ceiling and a real Ready-condition check on all 3
   pods, so a broken deploy fails loudly instead of hanging the terminal.

```powershell
.\scripts\test-k8s.ps1              # full run: build, deploy, wait, test
.\scripts\test-k8s.ps1 -SkipBuild   # images already built
.\scripts\test-k8s.ps1 -Teardown    # delete the hydra namespace for a clean re-run
```

**Manual steps** (what the script above automates — useful for
understanding or debugging a specific stage):

**1. Confirm the cluster is up:**
```powershell
kubectl config get-contexts   # look for docker-desktop, marked current
kubectl get nodes
```

**2. Build the images** (if you haven't already via docker-compose):
```powershell
docker compose build
```

**3. Create the secret** — reuses your existing `.env`, with `REDIS_URL`
corrected to the in-cluster DNS name (K8s resolves Service names the
same way docker-compose resolves service names, but `localhost` from
`.env` doesn't mean anything inside a pod). Match on the *key*, not a
literal value — see fix #1 above:
```powershell
$lines = Get-Content .env
$lines = if ($lines -match '^REDIS_URL=') {
    $lines -replace '^REDIS_URL=.*$', 'REDIS_URL=redis://redis:6379'
} else {
    $lines + 'REDIS_URL=redis://redis:6379'
}
$lines | Set-Content .env.k8s
kubectl apply -f infra/k8s/00-namespace.yaml
kubectl create secret generic hydra-secrets --from-env-file=.env.k8s -n hydra
Remove-Item .env.k8s   # don't leave a decrypted copy of secrets on disk
```

**4. Apply everything:**
```powershell
kubectl apply -f infra/k8s/
kubectl get pods -n hydra -w
```
Wait for all 3 pods to show `1/1 Running` — first boot still downloads
the FastEmbed model (18-40s+, same as docker-compose), the readiness
probe (`healthcheck.py`, reused as-is from the Docker setup) is
configured with enough headroom for that.

**5. Test:**
```powershell
kubectl get service gateway -n hydra   # EXTERNAL-IP should show localhost
venv\Scripts\python.exe scripts/test_client.py "the checkout service is down"
```
If `EXTERNAL-IP` stays `<pending>`, port-forward instead:
```powershell
kubectl port-forward -n hydra service/gateway 8080:8080
```

**If pods show `ErrImageNeverPull`:** confirmed on real hardware during
the first live run. The tempting first theory — `docker compose build`
now builds through the Buildx Bake backend and, with Docker Desktop's
containerd image store enabled, attaches a default provenance
attestation, wrapping the image in a multi-manifest index — turned out
to be a red herring: rebuilding with `BUILDX_NO_DEFAULT_ATTESTATIONS=1`
produced a confirmed-plain image (no attestation/manifest-list lines in
the build log) and pods **still** failed with `ErrImageNeverPull`. The
actual cause: Docker Desktop's Kubernetes has run on a kind-managed
cluster (kubectl context `docker-desktop`, but the underlying kind
cluster is literally named `desktop`) since v4.38, and there's a
long-standing, still-open Docker Desktop bug
([desktop-feedback#190](https://github.com/docker/desktop-feedback/issues/190))
where its built-in image-sharing path for that cluster is unreliable —
`docker images` shows the image fine on the host, but the node's own
containerd never actually gets it.

`scripts/test-k8s.ps1` now handles this automatically (Step 1.5): if the
`kind` CLI is installed, it loads both images directly into the node's
containerd via `kind load docker-image`, bypassing Docker Desktop's
flaky sharing path entirely. If you don't have `kind` yet:
```powershell
winget install Kubernetes.kind   # then open a NEW PowerShell window (PATH)
kind load docker-image hydra-gateway:latest --name desktop
kind load docker-image hydra-supervisor:latest --name desktop
```
(Note: `docker buildx imagetools inspect` looks like the obvious way to
check an image's manifest type, but it's registry-only — against a
purely local, never-pushed image it just fails with a misleading `pull
access denied` against Docker Hub.)

**Known simplification:** the Secret holds both real secrets (API keys)
and plain config (thresholds, URLs) together, since it's sourced
directly from `.env` for convenience. A stricter setup splits these —
actual secrets in a Secret, everything else in a ConfigMap — worth doing
before this is anything more than a local demo.

**Not yet done:** KEDA autoscaling (needs the KEDA operator installed via
Helm first, then a `ScaledObject` watching Redis queue depth — see
Roadmap), Ingress instead of a bare LoadBalancer, resource
requests/limits on the containers, and Terraform for anything beyond a
local single-node cluster.

## Roadmap

**Phase 2 — done:** 5 agents total (DevOps/SRE, Customer Support,
HR/Recruiting, Finance/Expense, Code Review), real token-level streaming
via `astream_events()` with a separate post-hoc confidence check.

**Phase 3 (weeks 5-6):** Docker → Kubernetes (`kind`/`minikube` first),
Helm charts, KEDA autoscaling on Redis queue depth, Langfuse tracing,
eval harness with a golden dataset + CI regression gate, upgrade the
semantic cache to real `redis/redis-stack` + `FT.CREATE`/`FT.SEARCH` KNN.

**Phase 4 (weeks 7-8):** remaining agents up to 15, cost dashboard,
OpenTelemetry + Prometheus/Grafana, circuit breakers on every external LLM
call, JWT/OAuth2 + RBAC, README polish + architecture diagram + demo video,
deploy to Fly.io/GKE free tier.
