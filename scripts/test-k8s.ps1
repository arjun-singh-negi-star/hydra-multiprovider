<#
.SYNOPSIS
    First live test of infra/k8s/ against Docker Desktop's built-in Kubernetes.

.DESCRIPTION
    Wraps the README's "Kubernetes (Phase 3b)" steps end-to-end. Written
    because the manifests had genuinely never been applied before -- three
    real fragilities in the documented copy-paste sequence were caught by
    reasoning through it line-by-line before this first live run (see the
    inline comments at each fix: REDIS_URL, namespace, secret pre-check,
    bounded polling). None of these were guessed; each traces back to a
    specific line in the manifests, config.py, or docker-compose.yml.

    Run from the hydra/ project root.

.PARAMETER SkipBuild
    Skip `docker compose build` (use if images are already fresh).

.PARAMETER Teardown
    Delete the hydra namespace (and everything in it) and exit. Use this
    for a clean re-run instead of layering apply on top of a half-broken
    previous attempt.

.PARAMETER MaxPollTries / PollIntervalSeconds
    Bounded readiness poll, not a blind `kubectl get pods -w`. Default
    ceiling: 60 x 5s = 5 minutes, well past the 18-40s worst-case FastEmbed
    download the supervisor's readiness probe already budgets for.
    Bounded on purpose -- an earlier unbounded polling loop elsewhere in
    this project once ran 190+ iterations against a container that had
    failed to build and would never exist. Same lesson, applied here.

.EXAMPLE
    .\scripts\test-k8s.ps1
    .\scripts\test-k8s.ps1 -SkipBuild
    .\scripts\test-k8s.ps1 -Teardown
#>

[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$Teardown,
    [int]$MaxPollTries = 60,
    [int]$PollIntervalSeconds = 5
)

$ErrorActionPreference = "Stop"
$ExpectedPods = 3   # redis + supervisor + gateway -- see infra/k8s/*.yaml

# New PowerShell tabs in VS Code sometimes open a level above the actual
# project root -- confirm before anything else, per project convention,
# instead of failing confusingly five steps later.
if (-not (Test-Path ".env") -or -not (Test-Path "infra/k8s")) {
    Write-Error "Run this from the hydra/ project root -- .env and infra/k8s/ not found in $(Get-Location). Check 'pwd' / cd into the right folder."
    exit 1
}

if ($Teardown) {
    Write-Host "Deleting namespace 'hydra' (this deletes everything in it)..." -ForegroundColor Yellow
    kubectl delete namespace hydra --ignore-not-found
    exit 0
}

# --- Step 0: confirm cluster context ---
Write-Host "`n== Step 0: cluster context ==" -ForegroundColor Cyan
$ctx = kubectl config current-context
if ($ctx -ne "docker-desktop") {
    Write-Warning "Current context is '$ctx', not 'docker-desktop'. infra/k8s/03-supervisor.yaml and 04-gateway.yaml both set imagePullPolicy: Never, which assumes Docker Desktop's K8s shares the local image store with 'docker compose build'. On any other cluster this will fail with ErrImageNeverPull."
}
kubectl get nodes

# --- Step 1: build images ---
if (-not $SkipBuild) {
    Write-Host "`n== Step 1: docker compose build ==" -ForegroundColor Cyan
    docker compose build
} else {
    Write-Host "`n== Step 1: skipped (-SkipBuild) ==" -ForegroundColor DarkGray
}

# --- Step 1.5: load images directly into the cluster's own image store ---
# Confirmed necessary on real hardware, not just theorized: disabling
# default build attestations (see git history / README) produced a plain
# image but pods STILL hit ErrImageNeverPull. Docker Desktop's Kubernetes
# has run on a kind-managed cluster (context "docker-desktop", but the
# underlying kind cluster is literally named "desktop" -- confirmed by
# the fix below actually working) since v4.38, and there's a long-standing,
# still-open Docker Desktop bug (github.com/docker/desktop-feedback#190)
# where its built-in image-sharing/registry-proxy path for that cluster
# is unreliable. `kind load docker-image` sidesteps that flaky path
# entirely -- it injects the image straight into the node's own
# containerd content store via `ctr images import`, independent of
# whatever the built-in sharing mechanism is failing at.
$kindCli = Get-Command kind -ErrorAction SilentlyContinue
if ($kindCli) {
    Write-Host "`n== Step 1.5: loading images into the 'desktop' kind cluster ==" -ForegroundColor Cyan
    kind load docker-image hydra-gateway:latest --name desktop
    kind load docker-image hydra-supervisor:latest --name desktop
} else {
    Write-Warning "kind CLI not found -- can't pre-load images into the cluster's own image store, which real-world testing shows Docker Desktop's built-in sharing doesn't reliably do on its own. Install it (winget install Kubernetes.kind, then open a NEW PowerShell window) and re-run with -SkipBuild if pods below show ErrImageNeverPull."
}

# --- Step 2: build .env.k8s ---
Write-Host "`n== Step 2: building .env.k8s ==" -ForegroundColor Cyan
#
# FIX 1 (REDIS_URL): the README's original one-liner was
#   -replace 'REDIS_URL=redis://localhost:6379', 'REDIS_URL=redis://redis:6379'
# an exact-literal-value match. docker-compose.yml hardcodes
# redis://redis:6379 directly for the supervisor container, so .env's own
# REDIS_URL is only ever read when pointing something at the *host-mapped*
# port -- and this project's own docker-compose.override.yml exists
# specifically to remap that host port away from 6379 (another local
# project already uses it). If .env's REDIS_URL has ever been adjusted to
# match that remap (e.g. redis://localhost:6380), the literal replace
# above silently no-ops, and .env.k8s -- and therefore the Secret -- would
# carry a REDIS_URL that resolves to nothing inside the supervisor pod's
# own network namespace. That failure would be silent: a cache miss just
# falls through to a real LLM call, so nothing crashes, it's just slower
# and never caches -- exactly the kind of bug this project's Section 6
# warns about chasing later as a mystery.
# Fix: match the KEY regardless of current value; append the line if the
# key is missing from .env entirely.
$envLines = Get-Content .env
if ($envLines -match '^REDIS_URL=') {
    $envLines = $envLines -replace '^REDIS_URL=.*$', 'REDIS_URL=redis://redis:6379'
} else {
    $envLines += 'REDIS_URL=redis://redis:6379'
}
$envLines | Set-Content .env.k8s

# Boolean-only presence check before creating the secret -- never print
# key values (real keys have leaked into chat/logs before on this
# project, once via `docker compose config`'s resolved-env output).
# Without this check, a blank/placeholder key still produces a "successful"
# secret and a healthy pod (healthcheck.py only confirms the gRPC server
# is listening, not that any provider key actually works) -- the failure
# would only surface later as a confusing downstream 401 from test_client.py.
$required = @("GROQ_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY")
$missing = $required | Where-Object { -not ($envLines -match "^$_=.+") }
if ($missing) {
    Write-Error "Missing/empty required key(s) in .env: $($missing -join ', '). Fix .env before continuing -- the pod would still pass its readiness probe with these blank."
    exit 1
}
Write-Host "Required keys present (values not shown)." -ForegroundColor Green

# --- Step 3: namespace + secret ---
Write-Host "`n== Step 3: namespace + secret ==" -ForegroundColor Cyan
#
# FIX 2 (namespace): applying 00-namespace.yaml directly instead of a
# separate `kubectl create namespace hydra` avoids the harmless-but-noisy
# "missing kubectl.kubernetes.io/last-applied-configuration annotation"
# warning `apply` prints the first time it touches a resource that
# `create` (not `apply`) made -- and keeps the namespace under the same
# apply-based management as everything else in infra/k8s/.
kubectl apply -f infra/k8s/00-namespace.yaml

# Idempotent secret creation -- delete-if-exists then recreate, so
# re-running this script after an .env change doesn't error on "already
# exists" instead of picking up the new values.
kubectl delete secret hydra-secrets -n hydra --ignore-not-found | Out-Null
kubectl create secret generic hydra-secrets --from-env-file=.env.k8s -n hydra

# Confirm the right *keys* landed without ever decoding values. Deliberately
# -o json (not -o jsonpath='{.data}') -- kubectl's jsonpath printer renders
# map fields using Go's fmt formatting, not valid JSON, so piping that
# straight to ConvertFrom-Json would error. -o json avoids that footgun.
Write-Host "Secret keys present:" -ForegroundColor DarkGray
(kubectl get secret hydra-secrets -n hydra -o json | ConvertFrom-Json).data |
    Get-Member -MemberType NoteProperty | Select-Object -ExpandProperty Name

Remove-Item .env.k8s   # don't leave a decrypted copy of secrets on disk longer than needed

# --- Step 4: apply everything else ---
Write-Host "`n== Step 4: applying manifests ==" -ForegroundColor Cyan
kubectl apply -f infra/k8s/

# --- Step 5: bounded poll for Ready (not a blind `kubectl get pods -w`) ---
Write-Host "`n== Step 5: waiting for $ExpectedPods pods (ceiling: $($MaxPollTries * $PollIntervalSeconds)s) ==" -ForegroundColor Cyan
#
# FIX 3 (readiness check): checks BOTH that all $ExpectedPods pod objects
# exist AND that each reports the Ready condition True (the same signal
# `kubectl get pods`'s READY column and `kubectl wait --for=condition=Ready`
# use). Checking readyCount == total alone, without also requiring
# total == $ExpectedPods, would false-positive the moment the *first*
# pod (e.g. redis) comes up Ready before the other two have even been
# scheduled -- worth stating explicitly since it's the kind of
# looks-right-but-isn't check this project's "verify, don't guess"
# principle exists to catch.
$ready = $false
for ($i = 1; $i -le $MaxPollTries; $i++) {
    $pods = kubectl get pods -n hydra -o json | ConvertFrom-Json
    $total = $pods.items.Count
    # @() forces array context. Without it, PowerShell unwraps a single
    # matching object instead of a one-element array, and .Count on that
    # bare object is undefined (prints blank, not 0 or 1) -- a well-known
    # PowerShell gotcha that would otherwise surface here the first time
    # exactly one pod (out of several) is Ready.
    $readyCount = @($pods.items | Where-Object {
        ($_.status.conditions | Where-Object { $_.type -eq "Ready" }).status -eq "True"
    }).Count
    Write-Host "  [$i/$MaxPollTries] $readyCount/$total pods ready"
    if ($total -eq $ExpectedPods -and $readyCount -eq $ExpectedPods) { $ready = $true; break }
    Start-Sleep -Seconds $PollIntervalSeconds
}

if (-not $ready) {
    Write-Warning "Pods didn't reach Ready within the poll window. Diagnostics:"
    kubectl get pods -n hydra -o wide
    $diag = kubectl describe pods -n hydra
    $diag | Select-String -Pattern "Warning|Error|ErrImageNeverPull|CrashLoopBackOff|BackOff"

    if ($diag -match "ErrImageNeverPull") {
        # Confirmed on real hardware: disabling default build attestations
        # alone did NOT fix this -- the actual fix was loading images
        # directly into the node via `kind load docker-image` (see Step
        # 1.5 above, and the README's ErrImageNeverPull section for the
        # full story). If you're seeing this, either kind isn't installed
        # yet, or Step 1.5 ran before these specific images existed.
        Write-Host "`nErrImageNeverPull persisted even after Step 1.5. If kind wasn't installed yet:" -ForegroundColor Yellow
        Write-Host '    winget install Kubernetes.kind   # then open a NEW PowerShell window' -ForegroundColor Yellow
        Write-Host '    kind load docker-image hydra-gateway:latest --name desktop' -ForegroundColor Yellow
        Write-Host '    kind load docker-image hydra-supervisor:latest --name desktop' -ForegroundColor Yellow
        Write-Host '    .\scripts\test-k8s.ps1 -Teardown' -ForegroundColor Yellow
        Write-Host '    .\scripts\test-k8s.ps1 -SkipBuild' -ForegroundColor Yellow
        Write-Host 'If kind load itself errors, check that the cluster is really named "desktop": kind get clusters' -ForegroundColor Yellow
    }

    Write-Host "`nAlso check: kubectl logs -n hydra deploy/supervisor  /  deploy/gateway" -ForegroundColor Yellow
    exit 1
}
Write-Host "All $ExpectedPods pods Ready." -ForegroundColor Green

# --- Step 6: reach the gateway ---
Write-Host "`n== Step 6: gateway Service ==" -ForegroundColor Cyan
$svc = kubectl get service gateway -n hydra -o json | ConvertFrom-Json
# @() wraps the WHOLE pipeline, not just the input -- assigning a
# pipeline's output directly (even one that started as @(...)) re-collapses
# to a bare object when exactly one element survives the filter.
$ingress = @(@($svc.status.loadBalancer.ingress) | Where-Object { $_ })
$portForwardJob = $null

if ($ingress.Count -gt 0) {
    $externalTarget = if ($ingress[0].ip) { $ingress[0].ip } else { $ingress[0].hostname }
    Write-Host "EXTERNAL-IP: $externalTarget" -ForegroundColor Green
    Write-Host "(test_client.py hits localhost:8080 directly -- Docker Desktop's LB already maps that.)" -ForegroundColor DarkGray
} else {
    Write-Host "EXTERNAL-IP still <pending> -- falling back to port-forward, per README step 5." -ForegroundColor Yellow
    $portForwardJob = Start-Job { kubectl port-forward -n hydra service/gateway 8080:8080 }
    Start-Sleep -Seconds 3   # give the forward a moment to bind before the test client connects
}

# --- Step 7: real end-to-end test ---
Write-Host "`n== Step 7: test_client.py ==" -ForegroundColor Cyan
# Reuses the project's venv, matching the existing standard rebuild-and-
# test sequence, instead of whatever bare `python` resolves to on PATH.
$pythonExe = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" } else { "python" }
Write-Host "Let this run to completion -- Ctrl+C mid-run has destroyed diagnostic info before." -ForegroundColor DarkGray
& $pythonExe scripts/test_client.py "the checkout service keeps restarting, can you check on it"

if ($portForwardJob) {
    Stop-Job $portForwardJob | Out-Null
    Remove-Job $portForwardJob | Out-Null
}

Write-Host "`nDone. For a clean re-run: .\scripts\test-k8s.ps1 -Teardown" -ForegroundColor Cyan
