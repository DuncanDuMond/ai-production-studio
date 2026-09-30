# Production Studio maintenance update

## Scope

Local maintenance only: no public deployment, cloud accounts, provider spending,
or existing workspace migrations. The SQLite backup API was used before changes.
All original records were subsequently compared against the backup: **3 projects,
6 documents, 5 runs**, unchanged; `PRAGMA integrity_check` returned `ok`.
Browser mutation tests use a separate scratch database at loopback port 8766.

## Dependency refresh

The lockfile refresh changed **47 existing package version sets**, added five
packages, and removed one. These counts compare package names present in both
`git show HEAD:uv.lock` and the refreshed lockfile, preserving conditional
multi-version entries; removed packages are counted separately, not as upgrades.

| Package | Before | Updated |
|---|---|---|
| FastAPI | 0.141.1 | 0.142.2 |
| Uvicorn | 0.53.0 | 0.54.0 |
| LiteLLM | 1.101.0 | 1.103.1 |
| Phoenix OTEL | 0.17.1 | 0.17.2 |
| OpenTelemetry SDK | 1.44.0 | 1.45.0 |
| PyJWT | 2.14.0 | 2.15.1 |

PyJWT CVE-2026-101918 is removed from the audit. An explicit uv constraint prevents
future resolution below 2.15.1. The fresh audit still exits 1 with four raw entries
covering two unique unresolved advisories: Ragas CVE-2026-6587 and diskcache
CVE-2025-69872. No current patched releases were listed; neither advisory is ignored.

This is a compatibility update, not a blind latest-major upgrade. Haystack,
DeepEval and Langfuse remain on their supported major lines. Pydantic AI remains
2.31.1 because attempting 2.52.0 fails dependency resolution: it requires OpenAI
>=3.19.0 while Guardrails 0.11.0 requires OpenAI <3. The exact
`langchain-community==0.4.1` pin remains required for Ragas 0.4.3 imports.
Existing immutable service image/provenance snapshots are retained, not presented
as newly fetched latest releases.

## Behavior fix

The integration catalog now uses the same telemetry enablement check as settings
and the exporter. An endpoint with `OTEL_SDK_DISABLED=true`, or a whitespace-only
endpoint, cannot present export as configured. The regression was observed failing
before the change and passing afterward. Catalog configuration still does not
claim external service health.

## Verification

- Original baseline: 41 passed, 1 opt-in browser skipped, 44 subtests passed.
- Isolated real Chromium acceptance: 14 passed; six views, ten integration cards,
  successful local execution, inert user text, JSON export, and no mobile overflow.
- Final suite with Docker acceptance enabled: **119 passed, 1 opt-in browser
  skipped, 47 subtests passed**. Four upstream Typer deprecation warnings remain.
- Ruff, lockfile consistency, and static deployment checks passed.
- Final locked Docker image build passed using the existing pinned base digests.
- Real isolated Docker acceptance passed all 11 checks: production authentication,
  nonroot/read-only/cap-drop hardening, real local library pipeline, matching and
  nonmatching Ragas/DeepEval scores, JSON export equality, strict consent,
  persistence across restart, trace reception in the pinned Collector, privacy,
  and verified resource cleanup. No original database was mounted. Cleanup now
  tracks attempted creation before invoking Docker, reconciles ambiguous timeouts
  using exact names and owner labels, retains immutable container/network IDs
  after successful creation, re-verifies ownership before deletion, leaves
  unowned replacements untouched, and fails closed when absence/ownership cannot
  be verified. Fault-injection tests
  and real daemon-created/lost-client-response probes cover containers, networks
  and volumes, including Docker's idempotent volume creation.
- Actual base Compose plus telemetry Compose startup passed using only isolated
  scratch overrides for ephemeral ports, an already built image, and a debug
  Collector destination. Docker health became `healthy`; local execution and
  trace delivery passed; the host could reach the Collector's loopback OTLP port;
  all temporary Compose containers/networks/volumes were removed and absence read back.
- CI now builds the image and runs the isolated smoke before the unchanged,
  unsuppressed vulnerability gate. The hosted GitHub Actions job was not executed.
- Independent maintenance code review passed. Both cleanup findings were fixed;
  the final reviewer independently exercised 75 smoke CLI/fault-injection cases
  and found no blocking security or logic issues in the reviewed scope.

## Docker 29 networking correction

Published ports on internal-only bridge networks were unreachable during real
smoke execution. Shipped Compose files now attach published services to a
separate ingress bridge with outbound IP masquerading disabled, while retaining
the internal backend network. Database/worker services are not exposed and all
published host ports remain bound to 127.0.0.1. NAT disablement is not a complete
host egress firewall. The regression was observed failing before the network
configuration change and passing afterward.

Re-run the reproducible isolated acceptance with an already built image:

```sh
docker build -t ai-production-studio:update .
uv run --frozen python scripts/smoke_container.py --image ai-production-studio:update --evidence /managed/scratch/studio-smoke.json --scratch-dir /managed/scratch
STUDIO_DOCKER_E2E=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest -q tests deploy
```

Replace `/managed/scratch` with an absolute managed scratch path for your host.
The script generates a temporary operator credential, never prints it, and removes
only its own uniquely named Docker resources. Evidence reports contain no user
content or credentials.

## Release boundary

The app remains local-first and single-operator. A Studio/Collector smoke test is
not a Langfuse/Phoenix deployment, cloud release, paid provider test, or security
certification. Public deployment remains gated on the unresolved advisories,
identity/TLS/budgets, service migrations, and separately authorized integration
verification.
