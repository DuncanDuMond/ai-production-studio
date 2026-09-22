# AI Production Studio

Local-first AI engineering workbench built around the ten repositories requested in **BELL-31**. It combines a browser studio, persistent SQLite workspaces, retrieval, structured agent runs, validation, evaluation evidence and opt-in observability infrastructure.

**Release status:** verified local build; not approved for public production. Docker and provider credentials were unavailable on the build machine. Two unresolved upstream dependency advisories require review (see [Security](docs/SECURITY.md)).

## Run locally

Requirements: Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/). No Node build or Docker required for the local studio.

```sh
uv sync --frozen
uv run --frozen python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765**. On Windows you can also run `start.cmd` from this directory. Keep the server terminal open. All third-party versions are recorded in `uv.lock`.

1. Create a workspace or import the explicitly labeled sample from the interface.
2. Add text documents with titles. Content stays in the local database unless you explicitly select a live run or configure telemetry.
3. Run a prompt in **Local** mode. This is deterministic retrieval and a structured-schema exercise, not an LLM response. It is useful for testing the pipeline without credentials or model charges.
4. Inspect citations, guardrail decisions, evaluation details and execution steps. Metrics state what they actually measure; local checks do not demonstrate factual correctness or model quality.
5. Inspect history and export a run as JSON. Workspace summaries are computed from persisted records, never seeded charts.

## Repository roles

| Repository | Role |
|---|---|
| [OpenTelemetry Collector](https://github.com/open-telemetry/opentelemetry-collector) | Opt-in OTLP trace pipeline and service fan-out |
| [Langfuse](https://github.com/langfuse/langfuse) | LLM observability destination; self-host service configuration |
| [Phoenix](https://github.com/Arize-ai/phoenix) | Trace inspection destination; self-host service configuration |
| [Ragas](https://github.com/vibrantlabsai/ragas) | Deterministic text evaluation adapter; no vulnerable multimodal URL/file path |
| [DeepEval](https://github.com/confident-ai/deepeval) | Deterministic evaluation evidence |
| [Guardrails](https://github.com/guardrails-ai/guardrails) | Structured-output validation |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | Typed agent execution and explicit local TestModel |
| [Instructor](https://github.com/567-labs/instructor) | Structured-output execution adapter |
| [LiteLLM](https://github.com/BerriAI/litellm) | Configurable model gateway for live runs |
| [Haystack](https://github.com/deepset-ai/haystack) | Document retrieval |

These are integrations, not ten full source forks or a merged monorepo. See [integration details](docs/INTEGRATIONS.md), [upstream provenance](docs/upstreams.json), and the installed versions shown in the UI. **Available** in the catalog means a library/configuration is available; it does not claim that a remote service is reachable.

## Enable live models and observability

Follow [deployment instructions](docs/DEPLOYMENT.md) and `.env.example`. Never paste provider secrets into source files, run exports or chat. The core live variables are `STUDIO_ENABLE_LIVE`, `STUDIO_MODEL`, `STUDIO_LITELLM_BASE_URL`, and `STUDIO_LITELLM_API_KEY`. Both server opt-in and the interface's per-run paid-call consent are required. A live error never silently becomes a local success.

For an environment file, use:

```sh
uv run --frozen python -m uvicorn studio.app:app --env-file .env --host 127.0.0.1 --port 8765
```

Tracing is off until explicitly configured. Use the Collector route to avoid duplicate Langfuse/Phoenix exporters. Real connectivity, credentials, service health and trace arrival must be tested after provisioning; having configuration files is not proof of a successful deployment.

## Verify

```sh
# PYTEST_DISABLE_PLUGIN_AUTOLOAD avoids upstream pytest plugins and their telemetry.
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen python scripts/check_deploy.py
uv run --frozen pip-audit --format=json --output=dependency-audit.json
```

On Windows cmd.exe use `set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` before the pytest command. The audit currently fails on upstream advisories documented in [Security](docs/SECURITY.md); that is an intentional release warning, not a clean security certification.

## API and data

- `GET /api/health`, `/api/settings`, `/api/integrations`, `/api/summary`
- `GET/POST /api/projects`
- `GET/POST /api/projects/{id}/documents`; `DELETE /api/projects/{id}/documents/{document_id}`
- `GET/POST /api/projects/{id}/runs` (history currently returns the newest 200 per workspace)
- `GET /api/runs/{id}` and `/api/runs/{id}/export`

When `STUDIO_API_TOKEN` is set, send `Authorization: Bearer ...` for every API endpoint except health. The interface keeps a token only in current-page memory. `STUDIO_ENV=production` refuses to start without a token of at least 32 characters.

State lives at `data/studio.db` (override `STUDIO_DB_PATH`). SQLite data includes submitted text in plaintext. This is a **single-operator** application; projects are not multi-tenant authorization boundaries. Keep one server worker for process-local limits. Configure TLS/SSO, retention, backups and distributed limits before shared/public production use.

## Project layout

```text
studio/          API, SQLite storage, security boundary, integrations, web interface
 tests/          API, engine, UI and deployment checks
 deploy/         Opt-in container and observability configuration
 docs/           Integration rationale, upstream provenance, deployment and security
 scripts/        Deployment validation tooling
 uv.lock         Resolved dependency versions
```

No GitHub publication, cloud deployment, external account creation or paid model call is performed by the local setup.
