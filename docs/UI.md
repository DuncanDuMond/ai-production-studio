# Studio frontend

A dependency-free, same-origin HTML/CSS/JavaScript application served at `http://127.0.0.1:8765/`. There is no npm build step, CDN, external font, client framework, seeded dashboard, or browser database.

## Screens and behavior

- **Overview:** actual `/api/summary` totals; a null metric is shown as `—`, not zero. Recent runs are scoped to the selected workspace. The illustrated pipeline describes the workflow, not a live execution.
- **Workspaces:** create a workspace, select it, add plain-text source documents, expand their content, and delete with confirmation. Mutations are read back from the API before success is reported. The API has no document-edit or workspace-delete endpoint, so neither is advertised.
- **Workbench:** explicit local/live mode and Pydantic AI/Instructor engine selection, prompt, retrieval count, optional reference, and per-request live cost/data-sharing consent. Local is clearly labeled an offline schema exercise, not model inference. Live is unavailable unless the server reports it enabled; a failed live request never silently becomes a local run. Consent resets after submission.
- **Run evidence:** persisted output, citation IDs/titles/content, raw evaluation scores and thresholds, explicit unevaluated metrics, guardrail evidence, actual framework steps/timings, and trace ID. A trace ID does not prove delivery to an external service. Tab navigation supports arrow keys, Home, and End.
- **Run history:** selected workspace's returned runs, prompt/status filters, re-open evidence via `/api/runs/{id}`, and authenticated JSON download via `/api/runs/{id}/export`. No client-created run records.
- **Integrations:** all returned catalog entries with backend versions, roles, status, and detail. Declared counts are reconciled and duplicate IDs rejected. Search and availability filters are functional. Repository links accept only HTTPS GitHub repository URLs without embedded credentials. Availability is never described as external connectivity.
- **Setup & access:** read-only `/api/settings` snapshot and server configuration instructions. This page does not install dependencies, change environment variables, enable a provider, or send a test provider request. Session token controls are the only credential form.

## Sample content

Nothing is imported on page load. **Add sample workspace** explicitly creates `Sample · Launch knowledge` and two documents labeled **SAMPLE CONTENT — fictional ... for testing only**, through real API writes. Import verifies the saved documents and starts no run. Each click creates a new sample workspace; an interrupted import reports partial completion and asks the user to inspect before retrying.

## Security and data handling

- All project names, prompts, source content, answers, errors, and evidence are rendered with `textContent`/DOM nodes. No HTML interpolation or unsafe HTML sinks are used; model output is deliberately plain text rather than untrusted markdown/HTML.
- Bearer tokens are held in a JavaScript closure in the current tab's memory. No local/session storage, IndexedDB, cookies, or URL parameters persist the token. Reloading clears it. The password field is cleared on apply; the setup view reports only whether a token is present.
- Requests are restricted to same-origin `/api/` paths with `credentials: same-origin`, `cache: no-store`, and rejected redirects. JSON downloads use the same authenticated API client; tokens never appear in download links.
- Provider keys belong in the server environment, not this frontend. Only server-selected models and contract-defined engines are used.
- Fetch errors are surfaced, never replaced with fabricated successful data. Snapshot endpoints are independently loaded: a catalog failure does not erase real workspace totals. Failed data loads are distinct from true empty states.
- Duplicate run submission is blocked while a run is pending. Network errors warn that a submitted run might still complete, directing the user to inspect history before retrying.
- This interface is not a production-security approval. Follow `docs/SECURITY.md` and deployment guidance before exposure. No provider spending or external telemetry was exercised by frontend verification.

## Accessibility and responsive behavior

System font stacks, self-contained SVG symbols, visible focus, semantic labels, a skip link, native modal dialogs, live status/error announcements, keyboard-operable tabs, and reduced-motion support. The desktop sidebar becomes a horizontally scrollable navigation strip on narrow screens; content stacks without page-level horizontal overflow. Long source content and IDs wrap. Empty screens offer only implemented actions.

## Verification

The test suite is Python `unittest` and uses only the Python standard library. Core HTML/CSS/security checks require no JS dependency. Executable client tests optionally invoke an installed Node runtime; they are skipped if Node is absent. No packages are installed by the tests.

```bash
python -m unittest discover -s tests -p test_ui.py -v
node --check studio/static/app.js
uv run --no-sync ruff check tests/test_ui.py
```

An **opt-in**, isolated Chromium/CDP test is included in the same test file. It does not reuse or close the user's browser/profile. It uses Node 22+'s built-in WebSocket and the Python standard library rather than Playwright/Selenium/npm. The server must already be running. Override `STUDIO_UI_BROWSER` with a Chromium executable if auto-detection does not fit the machine.

```bash
# Read-only: navigation, initial no-POST guarantee, runtime errors, screenshots, mobile overflow.
STUDIO_UI_E2E=1 python -m unittest discover -s tests -p test_ui.py -v

# MUTATES the running server: explicitly clicks sample import; adds/deletes a test
# document; creates a LOCAL run; checks evidence tabs/history; downloads JSON.
# Use a disposable STUDIO_DB_PATH if you do not want to retain the labeled sample.
STUDIO_UI_E2E=1 STUDIO_UI_MUTATE=1 python -m unittest discover -s tests -p test_ui.py -v
```

Optional variables: `STUDIO_UI_URL` (defaults to the single local origin), `STUDIO_UI_SCREENSHOTS` (defaults to the system temp directory's `studio-ui-evidence`). Live mode must be disabled for the mutation acceptance test.

### Executed verification

- Real Chromium workflow visited all six views with **no JavaScript runtime exceptions**, no automatic POSTs, ten actual catalog cards, and **no page-level overflow at 390 px**.
- Clicking sample import persisted two labeled documents; adding markup-shaped text rendered it inert, and deleting that test document was verified.
- A real local run returned **passed**, with two source citations, guardrail evidence, actual execution steps, and two explicitly skipped reference-based evaluations. No reference score was invented.
- Output/Citations/Evaluations/Trace tabs, prompt filtering, persisted history, and JSON export were exercised against the running API.
- Desktop and mobile screenshots were inspected. Screenshots and the downloaded run JSON reside outside the repository in the temporary evidence directory.
- Live-provider calls, authenticated-server browser login, external collector delivery, and public deployment were **not** exercised. Authentication header/session behavior and live consent validation are covered by executable isolated client tests.

## Owned files

`studio/static/index.html`, `studio/static/app.js`, `studio/static/styles.css`, `tests/test_ui.py`, and this document. Backend, dependencies, and deployment files are owned separately and were not edited for the frontend.
