# AI Production Studio — implementation contract

BELL-31. New local project; do not touch hermes-growth-studio. Python 3.11, FastAPI, SQLite, vanilla JS/CSS UI. No cloud deployment, provider spending, or public exposure authorized. Bind 127.0.0.1:8765. Docker is absent; only 9.5 GB free. All live calls require explicit server configuration and per-request consent. Offline runs must be labeled `local`, not represented as model-generated intelligence. Use real installed upstream libraries where possible; disclose service health as unknown/unconfigured until checked.

## Owned files
- Coordinator: pyproject.toml, uv.lock, studio/app.py, studio/store.py, studio/models.py, tests/test_api.py, README.md.
- Integration agent: studio/engine.py, studio/telemetry.py, tests/test_engine.py, docs/INTEGRATIONS.md. Do NOT edit other files.
- UI agent: studio/static/index.html, app.js, styles.css, tests/test_ui.py (stdlib/static tests), docs/UI.md. Do NOT edit other files.
- Infrastructure agent: deploy/*, .env.example, Dockerfile, .dockerignore, .github/workflows/ci.yml, docs/DEPLOYMENT.md, docs/upstreams.json, scripts/check_deploy.py. Do NOT edit other files.

## API contract
GET /api/health -> {status:'ok',mode:'local'|'live-enabled',version:'0.1.0'}
GET /api/integrations -> {items:[{id,name,role,repo,version,status,detail}],total:10}. status: available/unconfigured/unavailable. This is capability availability, NOT external connectivity.
GET /api/projects -> {items:[{id,name,description,created_at,document_count,run_count}]}
POST /api/projects {name,description} -> project 201
GET /api/projects/{id}/documents -> {items:[{id,title,content,created_at}]}
POST /api/projects/{id}/documents {title,content} -> document 201
DELETE /api/projects/{id}/documents/{document_id} -> 204
POST /api/projects/{id}/runs {prompt,mode:'local'|'live',engine:'pydantic-ai'|'instructor',top_k:3,reference:'',allow_paid:false} -> run 201
GET /api/projects/{id}/runs -> {items:[run]} newest first
GET /api/runs/{id} -> run
GET /api/runs/{id}/export -> downloadable JSON
GET /api/summary -> {projects,documents,runs,passed_runs,blocked_runs,average_latency_ms:null|number}
GET /api/settings -> {live_enabled,model,telemetry_enabled,authentication_required}; never return secrets
Static UI at /. No fake seeded metrics or fake runs. Empty states; optional UI button imports explicitly labeled sample project/documents through actual API calls. API errors {detail:string}. API Bearer authentication if STUDIO_API_TOKEN configured. UI session-only token entry; never localStorage. Same-origin checks on writes. Production mode requires token. All POST endpoints validate sizes and no arbitrary URLs, file paths, models or shell commands accepted. Runs synchronous in threadpool, rate/concurrency bounded. SQLite persisted under data/studio.db by default.

## Run schema
{id,project_id,prompt,mode,engine,status:'passed'|'blocked'|'failed',created_at,latency_ms,output:{answer:string,citations:[{id,title,content}],confidence:number}|null,evaluations:[{name,framework,score:number|null,threshold:number,passed:boolean|null,detail:string}],guardrails:{passed:boolean,detail:string},steps:[{name,framework,status,detail,duration_ms:number}],trace_id:string|null,error:string|null}

## Engine interface (owned by integration agent)
`def integration_catalog() -> list[dict]`: exactly ten entries, ids: otel,langfuse,phoenix,ragas,deepeval,guardrails,pydantic-ai,instructor,litellm,haystack. Use installed distribution versions, explicit unavailable/unconfigured status.
`def execute_run(prompt: str, documents: list[dict], *, mode='local', engine='pydantic-ai', top_k=3, reference='', allow_paid=False) -> dict`: returns Run fields output,status,evaluations,guardrails,steps,trace_id,error. Real Haystack BM25 retrieval, real Pydantic AI TestModel for clearly labeled offline schema exercise, Guardrails, real non-LLM Ragas and DeepEval metrics; live Pydantic AI or Instructor path via LiteLLM OpenAI-compatible proxy with env STUDIO_LITELLM_BASE_URL and STUDIO_LITELLM_API_KEY, STUDIO_MODEL. Live requires STUDIO_ENABLE_LIVE=true and allow_paid=true. No implicit fallback from live failure to local success. Missing dependencies/config fail clearly. Validate citations refer to retrieved documents. Do not send raw content to telemetry by default. OTLP through configured STUDIO_OTLP_ENDPOINT only. Langfuse/Phoenix receive Collector traces configured separately, no independent duplicate SDK exporters.

## Tests / commands
`uv run pytest -q`; `uv run ruff check .`; `uv run python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765`.
Follow test-driven-development skill: vertical failing test -> run RED -> implement -> GREEN. Tests must not send provider requests or require credentials. Independent components may use unittest if pytest not installed yet. All external services are opt-in. Disable upstream telemetry env where appropriate. Document actual upstream references and what was/wasn't executed.
