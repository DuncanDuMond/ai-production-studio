# Integration implementation and evidence

## Resolved Ragas compatibility pin

Ragas `0.4.3` imports `langchain_community.chat_models.vertexai`. That module
is absent from `langchain-community==0.4.2`, so the application explicitly pins
`langchain-community==0.4.1`, whose public wheel contains the module and permits
the installed LangChain Core 1.x line. After resolving the lock and environment,
the real Ragas reference-metric test passes. Keep this compatibility pin until
Ragas removes the obsolete import or the dependency relationship is revalidated.

## Ten integrations (installed distribution metadata, not health checks)

| ID | Distribution / locked version | Actual role in this application |
|---|---|---|
| `otel` | `opentelemetry-sdk` 1.44.0 | Private manual spans; no exporter unless `STUDIO_OTLP_ENDPOINT` is configured. |
| `langfuse` | `langfuse` 3.15.0 | Optional Collector destination, not an independently initialized SDK. Service health unknown. |
| `phoenix` | `arize-phoenix-otel` 0.17.1 | Optional Collector destination, not Phoenix server or a duplicate exporter. Service health unknown. |
| `ragas` | `ragas` 0.4.3 | Real `StringPresence.ascore(reference=..., response=...)`, no LLM/embeddings. |
| `deepeval` | `deepeval` 3.9.9 | Real `ExactMatchMetric.measure(LLMTestCase(...))`, no LLM. |
| `guardrails` | `guardrails-ai` 0.11.0 | Real `Guard(output_schema=...).parse(..., num_reasks=0)` plus application provenance checks. |
| `pydantic-ai` | `pydantic-ai-slim[openai]` 2.31.1 | Real `Agent(TestModel(custom_output_args=...))` locally; live OpenAIChatModel via configured proxy. |
| `instructor` | `instructor` 1.17.0 | Live `from_openai(..., mode=TOOLS)` through the same proxy; not used to pretend to generate offline. |
| `litellm` | `litellm` 1.101.0 | OpenAI-compatible proxy boundary. The app intentionally does not import the router SDK or spin up a proxy. |
| `haystack` | `haystack-ai` 2.31.0 | Real InMemoryDocumentStore and InMemoryBM25Retriever, no embeddings/model downloads. |

The catalog gets versions from `importlib.metadata.version`, not this table.
`available` means installed local capability, **not a successful service connection**.
`unconfigured` means the installed integration still needs explicit service setup.
Langfuse and Phoenix remain unconfigured because Collector routing is outside the
application's knowledge. Missing distributions are unavailable.

## What local mode means

Local mode is an explicitly labeled deterministic schema and retrieval exercise,
not model-generated intelligence. Haystack ranks the actual supplied documents;
Pydantic AI's real TestModel returns a fixture consisting of a literal local-mode
label followed by the retrieved source text. `confidence=0.0` is a sentinel, not
a claimed calibrated probability. Local Instructor runs are blocked rather than
silently using a different engine.

Guardrails checks the answer JSON schema. Application checks additionally require
each citation's id, title and content to match a retrieved document exactly and
reject duplicate citations. These checks do not establish semantic faithfulness,
prompt-injection resistance, toxicity safety, or factual correctness.

Evaluation uses only the caller's supplied reference. Without a reference both
metrics explicitly have `score=null`, `passed=null`, and skipped steps. There is
no invented ground truth or default perfect score. Ragas tests case-sensitive
substring presence; DeepEval compares exact strings after edge whitespace is
stripped. Their threshold is 1.0. Any supplied-reference metric failure blocks
the run; schema-valid output remains available for inspection. Neither metric
is a substitute for semantic evaluation. To get a passing local exact-match
exercise, supply the complete expected fixture string, including the local label.
A passed run without a reference establishes pipeline/schema success only, not
answer quality. Selected context above 20000 characters is blocked before any
generation, rather than silently truncating evidence or sending unbounded inputs.

Guardrails 0.11.0 can prune unknown JSON fields before reporting success. The
application therefore also strictly validates the original payload with Pydantic;
unexpected fields/coercions cannot be accepted merely because Guardrails pruned
them. A small Guard subclass forces `allow_metrics_collection=False` even during
constructor configuration, not just afterward, avoiding temporary re-enablement
of the shared usage-telemetry singleton during concurrent requests.

## Live mode and spend boundaries

Live execution requires all of:

- Server `STUDIO_ENABLE_LIVE=true`.
- Server-only `STUDIO_LITELLM_BASE_URL`, `STUDIO_LITELLM_API_KEY`, `STUDIO_MODEL`.
- Per-request `mode=live` and literal boolean `allow_paid=true`.

The base URL must be HTTP(S), without userinfo, query or fragment. Use `/v1` for
the OpenAI-compatible proxy API and HTTPS outside a trusted local network. The
application never accepts a model, URL, file path or credential in a run request.
Both SDK routes receive bounded text documents as data, not URL/media objects.
The proxy itself must be operated and secured separately, including provider
credentials, model alias allowlists, budgets, and server-side retry policy.

Pydantic AI uses OpenAIChatModel/OpenAIProvider with an explicit AsyncOpenAI client;
Instructor wraps an explicit synchronous OpenAI client. OpenAI retries are zero;
Pydantic AI allows one model request and no schema retries; Instructor allows one
attempt. Clients use 30-second timeouts, no environment HTTP proxy inheritance,
no redirects, and an output cap of 2000 tokens. Proxy-side retries remain an
operator responsibility. Provider failures become failed runs with redacted
exception type only and never fall back to a successful local fixture.

## Telemetry and privacy

The application owns a private OpenTelemetry provider, not the global provider.
Manual run/stage spans contain only framework/mode/engine, counts, and status.
No prompt, document, reference, answer, URL, credential, exception message or
stack trace is exported. Automatic Pydantic AI instrumentation is disabled via
`agent.instrument=False`; Guardrails tracing and metrics collection are disabled.
SDK usage analytics are disabled before imports with `HAYSTACK_TELEMETRY_ENABLED=false`,
`RAGAS_DO_NOT_TRACK=true`, `DEEPEVAL_TELEMETRY_OPT_OUT=true`,
`DEEPEVAL_UPDATE_WARNING_OPT_IN=0`, `DEEPEVAL_FILE_SYSTEM=READ_ONLY`, and
`ERROR_REPORTING=false`.

Only a nonempty `STUDIO_OTLP_ENDPOINT` with `OTEL_SDK_DISABLED=false` activates
the application's HTTP OTLP exporter; supply the complete trace URL, e.g.
`http://127.0.0.1:4318/v1/traces`. Settings report export disabled when the SDK
disable flag is true, and invalid/non-recording spans expose no trace ID. Generic
OTEL endpoint variables alone do not enable export. Configuration is read on first
provider use; restart after changing it. Langfuse/Phoenix SDKs are not initialized: one Collector
receives the traces and its explicitly configured exporters route them to the
backends. Backend health, export delivery, and UI rendering are not claimed.

Application storage/export still contains user prompts and outputs by design;
telemetry privacy does not make the database or run downloads content-free.
Protect local files and API access. Do not start this process using third-party
auto-instrumentation that captures raw SDK calls.

## Security release caveats

The coordinator's dependency audit reports **unresolved advisories**, not a clean
release audit: `ragas==0.4.3` CVE-2026-6587 (multimodal metric URL/SSRF path) and
`diskcache==5.6.3` CVE-2025-69872 (unsafe pickle when attackers can write caches).
This engine only calls the deterministic Ragas string metric; it never invokes
multimodal metrics or fetches URLs from input. DeepEval's evaluate/cache machinery
is not used: it calls the deterministic metric directly, disables Confident
logging, and uses read-only SDK filesystem mode. These restrictions are
mitigations, **not upstream fixes or a claim the advisories are resolved**.
Never share application/cache directories with untrusted users. Re-audit and
review fixed versions before broader deployment.

## Upstream API references actually inspected

Public documentation pages that returned HTTP 403 were replaced with the
upstream repositories' raw documentation/source, not guessed APIs:

- Haystack retriever: <https://raw.githubusercontent.com/deepset-ai/haystack/main/haystack/components/retrievers/in_memory/bm25_retriever.py>
- Haystack store: <https://raw.githubusercontent.com/deepset-ai/haystack/main/haystack/document_stores/in_memory/document_store.py>
- Pydantic AI TestModel: <https://raw.githubusercontent.com/pydantic/pydantic-ai/main/pydantic_ai_slim/pydantic_ai/models/test.py>
- Pydantic AI OpenAI configuration: <https://raw.githubusercontent.com/pydantic/pydantic-ai/main/docs/models/openai.md>
- Instructor compatible OpenAI clients: <https://raw.githubusercontent.com/567-labs/instructor/main/docs/integrations/openai.md>
- Instructor/LiteLLM integration: <https://raw.githubusercontent.com/567-labs/instructor/main/docs/integrations/litellm.md>
- LiteLLM proxy/OpenAI-client boundary: <https://raw.githubusercontent.com/BerriAI/litellm/main/README.md>
- Langfuse OpenTelemetry architecture: <https://raw.githubusercontent.com/langfuse/langfuse-python/main/README.md> (main documents a later SDK; this app does not initialize either SDK version).
- Guardrails Guard API: <https://raw.githubusercontent.com/guardrails-ai/guardrails/main/guardrails/guard.py>
- Ragas string metrics: <https://raw.githubusercontent.com/vibrantlabsai/ragas/main/src/ragas/metrics/_string.py>; the installed 0.4.3 wheel's public `ragas.metrics.collections.StringPresence` is used, not the deprecated sample API.
- DeepEval exact-match source: <https://raw.githubusercontent.com/confident-ai/deepeval/main/deepeval/metrics/exact_match/exact_match.py>; verified against the public 3.9.9 wheel.
- OTEL example: <https://raw.githubusercontent.com/open-telemetry/opentelemetry-python/main/docs/examples/basic_tracer/README.rst>
- Phoenix lightweight tracing package: <https://raw.githubusercontent.com/Arize-ai/phoenix/main/packages/phoenix-otel/README.md>
- Distribution compatibility metadata/wheels: <https://pypi.org/pypi/ragas/0.4.3/json>, <https://pypi.org/pypi/deepeval/3.9.9/json>, <https://pypi.org/pypi/guardrails-ai/0.11.0/json>, <https://pypi.org/pypi/langchain-community/0.4.1/json>.

Compatibility lessons: repository main is not the installed version. Haystack
2.31.0 does not support the later `shared=False` argument, so this application
reuses a locked, emptied index to avoid per-run global-store growth and data
mixing. Pydantic AI 2.31.1 no longer accepts `instrument` in Agent's constructor;
the public property is set instead. Ragas' transitive imports need an explicit
compatibility pin. Never patch fake modules into `sys.modules` to make a required
integration look available.

## Verification commands and boundaries

Tests were developed in vertical RED → GREEN slices, with actual failing output
before implementation: catalog, local retrieval/schema, reference evaluation,
OTEL privacy/export opt-in, consent gating, both SDK transports, citation
provenance, failure redaction, skipped references, and invalid requests.

Use:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest tests/test_engine.py -q
uv run --no-sync ruff check studio/engine.py studio/telemetry.py tests/test_engine.py
```

Disabling pytest plugin autoload is important: DeepEval's installed pytest plugin
can import its SDK before the application's telemetry opt-outs take effect.
The live-route tests replace **only HTTP transport**, exercise the real SDKs and
use clearly named transport fixtures. They are serialization/parsing tests, not
provider-generated answers or evidence that a deployed LiteLLM proxy works.
No provider keys, paid requests, Docker services, external trace backends, or
remote deployments were used.

### Executed evidence after compatibility resolution

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest -q tests deploy`:
  **41 passed, 1 skipped, 44 subtests passed**. The skipped case is the opt-in
  isolated Chromium workflow, which was then run separately with mutation enabled.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --frozen pytest tests/test_ui.py -q -s`
  with `STUDIO_UI_E2E=1 STUDIO_UI_MUTATE=1`: **14 passed**. Chromium exercised
  all six views, sample/document creation and deletion, inert XSS-shaped text,
  a real local run, evidence tabs, history, JSON export, and a 390 px viewport
  with no runtime exceptions or horizontal overflow.
- The Ragas/DeepEval reference test passes for matching and nonmatching references.
  Real HTTP persisted runs independently produced score pairs `[1.0, 1.0]` and
  `[0.0, 0.0]`, were read back, and matched exported JSON.
- `uv run --frozen ruff check .`: **All checks passed**.
- Four concurrent thread-pool local runs executed real Haystack/Pydantic AI/
  Guardrails successfully, with distinct traces and isolated document citations.
- In-memory OTEL export, content/exception redaction, constructor-safe Guardrails
  telemetry opt-out, strict original-payload validation, typed live SDK transport
  parsing, consent gates, and no-live-fallback behavior passed.

The warnings are upstream Typer/Starlette deprecations. Deterministic/schema
evidence is not presented as a live provider or deployed-service test. Docker,
real provider calls, and external OTLP delivery were not available in this build
environment.
