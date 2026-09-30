# Deployment — local-only reference, not a production certification

## Release status and hard limits

**Docker is now available, and the locked Studio image was built successfully.**
The maintenance update also exercises isolated container verification; see
[UPDATE.md](UPDATE.md) for the current executed evidence. YAML parsing, deployment
policy tests, the official Compose JSON Schema, and upstream/registry metadata
retrieval remain separate from runtime evidence. Full Langfuse/Phoenix startup,
migrations and real provider calls are not covered by the Studio smoke test.
The GitHub Actions workflow is supplied, not represented as an executed CI run.

There are unresolved dependency advisories for `diskcache 5.6.3` (CVE-2025-69872)
and `ragas 0.4.3` (CVE-2026-6587); see [SECURITY.md](SECURITY.md) and the coordinator's
`dependency-audit.json`. No patched versions were listed in that audit. The CI audit
step has no advisory ignore list or `continue-on-error`; it is expected to fail while
these findings remain. Passing static deployment checks does **not** make this production-clean.

This is a single-operator, single-host reference. It has no HA, remote TLS, SSO gateway,
distributed rate limiter or tested disaster recovery. All published ports bind `127.0.0.1`.
Do not change them to a public bind, add tunnels or deploy these files to the Internet
without a separate security review. Inside a container, `0.0.0.0` is intentional; the
host-side port publication is still loopback-only.

Check free disk space before enabling the full stack. SDK installation, image
extraction, ClickHouse, traces and build cache need additional space. The supplied
memory caps are starting limits, not measured capacity guarantees. Start with
Studio alone, then add only the backend you need. Do not run all profiles simply
because they are present.

## Layout and opt-in profiles

Always use `deploy/compose.yml` as the **first** file. Relative mounts/build paths resolve
from that file. All services have profiles; no service starts implicitly. Separate files
also prevent mandatory interpolation for a backend you did not select.

| Additional file | Profile | Services / loopback UI |
|---|---|---|
| None (base only) | `studio` | Studio, `http://127.0.0.1:8765` |
| `compose.proxy.yml` | `proxy` | LiteLLM, port 4000 |
| `compose.phoenix.yml` | `phoenix` | Phoenix, `http://127.0.0.1:6006` |
| `compose.langfuse.yml` | `langfuse` | Web, worker, PostgreSQL, ClickHouse, Redis, MinIO; web at `http://127.0.0.1:3000` |
| **One** of `compose.telemetry-{phoenix,langfuse,both}.yml` | `telemetry` | Core Collector OTLP/HTTP, port 4318 |

The default backend Docker network is `internal: true`. Services with published
loopback ports also join a dedicated ingress bridge with outbound IP masquerading
disabled; Docker 29 otherwise suppresses port publication on internal-only
networks. Only the explicitly enabled LiteLLM proxy joins an additional
NAT-enabled egress network. NAT disablement is not a complete host firewall:
review host routing and enforce an egress firewall when required. No app data is
sent to a remote observability endpoint by this configuration. Downloading
packages/images and CI vulnerability lookup do require network access; the latter
sends dependency identifiers to advisory services,
not Studio documents. Do not interpret telemetry opt-out flags as an audited egress
firewall for a host-run application.

## Secrets and initial setup

From the repository root, with Python 3.11 available:

```bash
python deploy/generate_env.py
uv run --locked python scripts/check_deploy.py
```

The helper reads `.env.example`, creates **only** a local `.env` using cryptographic
randomness, prints no secret values, makes no network requests and refuses to overwrite
an existing file. POSIX creation mode is `0600`; on Windows review the file ACL yourself.
Keep `.env` out of Git, backups shared with others, screenshots and log captures.
The Docker build context is an allowlist and excludes `.env`, data and the host `.venv`.
No dotenv file is copied wholesale into any container; each service receives an explicit
set of environment fields.

Important generated values:

- `STUDIO_API_TOKEN`: use this in the UI's session-only token field. Every workspace shares
  this one operator authorization boundary. Production mode requires at least 32 characters.
- LiteLLM's master key and `STUDIO_LITELLM_API_KEY` are initially the same. This is a
  single-operator, configuration-only proxy, not a multi-user billing/key-management service.
- Langfuse passwords are hexadecimal so `DATABASE_URL` does not require URI escaping.
  `LANGFUSE_ENCRYPTION_KEY` is exactly 64 hexadecimal characters. Preserve it and the salt
  across restores; do not casually regenerate the `.env` for existing volumes.
- The helper generates a Langfuse org/project and API-key pair with a local-only bootstrap
  administrator email, `admin@localhost.invalid`. Change that email before first startup
  if a real operator identity is required. No mail is sent by this configuration.
- `LANGFUSE_BASIC_AUTH` is **base64(public_key:secret_key)**, not the web-login password,
  not a bearer key and not encryption. The helper derives it from the same pair used for
  project initialization. If you rotate/import project keys, regenerate this value locally
  with `base64.b64encode((public_key + ':' + secret_key).encode()).decode()`; do not paste keys
  into shell history, chat or external Base64 websites.
- `OPENAI_API_KEY`, `LITELLM_PROVIDER_MODEL` and `PHOENIX_API_KEY` deliberately remain blank.
  No helper fabricates usable third-party credentials or registers a remote account.

Compose uses `${VARIABLE:?message}` for service secrets. Missing values cause a parse-time
error, even when a service in an included file is not started. Include only the files you
need. `STUDIO_LITELLM_API_KEY` may remain empty in the base file because live execution is
explicitly disabled; it is required by the live application path once you enable it.
Do not print `docker compose config` with credentials into CI logs; use `config --quiet`.

## Studio only

The following are **operator instructions**, not commands already executed here:

```bash
docker compose --env-file .env -f deploy/compose.yml --profile studio config --quiet
docker compose --env-file .env -f deploy/compose.yml --profile studio up -d --build
```

Open `http://127.0.0.1:8765`. The local port must be free: stop a host-run Studio first
if it already occupies 8765. The image uses a retrieved Python 3.11 image digest,
a retrieved uv image digest and `uv sync --locked --no-dev --no-install-project`.
It copies the complete `studio/` application (including security and static assets),
not development tools, repository metadata or a local virtualenv. Runtime UID/GID is
`10001:10001`; the root filesystem is read-only, `/tmp` is a bounded temporary filesystem,
and `/app/data` is a named writable volume. An authenticated local HTTP healthcheck is provided.

`STUDIO_ENV=production` is set by Compose. For a host-run process, an `.env` file is not
a promise of automatic loading: explicitly supply your environment using your process
manager. The container default LiteLLM hostname is `litellm`; a host-run Studio instead
uses `http://127.0.0.1:4000/v1`. Do not add an external hostname to `STUDIO_ALLOWED_HOSTS`
until designing and reviewing an authenticated TLS ingress separately.

## Add Phoenix

```bash
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.phoenix.yml --profile phoenix up -d
```

Phoenix uses the retrieved `20.12.0-nonroot` image with authentication enabled,
`PHOENIX_SECRET`, and your generated initial admin password. The initial password is
used only on first database creation; changing the environment does not reset an
existing account.[20] Sign in locally and create a **system API key**, then place that key
in the private `PHOENIX_API_KEY` field. It is used only by the Collector, not the Studio.
The optional powerful `PHOENIX_ADMIN_SECRET` bypass token is intentionally not configured.

Data is persisted in a named volume mounted over the image's nonroot home, with
`PHOENIX_WORKING_DIR=/home/nonroot/.phoenix` and a SQLite database. Verify ownership and
SQLite initialization on first actual container startup; those were not runtime-tested.
SQLite is suitable for this single-instance reference; review PostgreSQL for a larger
Phoenix deployment.[15] Cookies are non-secure only because this reference is local HTTP;
a remote TLS deployment must revisit this setting.

## Add Langfuse — full stack

```bash
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.langfuse.yml --profile langfuse config --quiet
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.langfuse.yml --profile langfuse up -d
```

The supplied dependencies follow the **v4.36.1** upstream Compose topology: web and worker
both connect to PostgreSQL, ClickHouse, Redis and S3-compatible object storage. Web and
worker wait for dependency healthchecks. ClickHouse cluster mode is disabled for the
single-node instance. Redis requires authentication, uses AOF persistence and `noeviction`.
MinIO creates the `langfuse` bucket before serving; this follows the current official
self-host recipe and must still be exercised with the pinned image on your Docker host.[11]

Database/native ClickHouse/Redis/worker/MinIO-console ports are **not published**. MinIO's
S3 API alone is published at `127.0.0.1:9090` because browser media uploads use presigned
URLs. Internal service traffic uses `http://minio:9000`; the web service has separate
internal and browser-facing media endpoints. Event and media credentials are mandatory;
batch exports and in-app AI-agent features are disabled. Self-host operation, including
storage and worker maintenance, is your responsibility.[11][14]

The `LANGFUSE_INIT_*` values provision a local organization, project, operator and API-key
pair. Bootstrap variables are **not** a credential-rotation mechanism for an already
initialized database. Confirm login, migrations and API-key ownership in the actual UI
before connecting a Collector. Public signup is disabled. SMTP, provider connections
and autonomous evaluation jobs are not configured.

## Opt into local trace export

Set these values in the private `.env`, then recreate Studio:

```dotenv
STUDIO_OTLP_ENDPOINT=http://collector:4318/v1/traces
OTEL_SDK_DISABLED=false
```

The application HTTP exporter expects the complete traces URL, not only the host.
For a host-run Studio, use `http://127.0.0.1:4318/v1/traces` instead. Changing an already
running process's environment does not recreate its initialized tracing provider.

**Phoenix only** (after obtaining `PHOENIX_API_KEY`):

```bash
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.phoenix.yml -f deploy/compose.telemetry-phoenix.yml \
  --profile studio --profile phoenix --profile telemetry up -d --build
```

**Langfuse only**:

```bash
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.langfuse.yml -f deploy/compose.telemetry-langfuse.yml \
  --profile studio --profile langfuse --profile telemetry up -d --build
```

**Both**, only when the additional resource cost is acceptable:

```bash
docker compose --env-file .env -f deploy/compose.yml \
  -f deploy/compose.phoenix.yml -f deploy/compose.langfuse.yml \
  -f deploy/compose.telemetry-both.yml \
  --profile studio --profile phoenix --profile langfuse --profile telemetry up -d --build
```

Select **exactly one** telemetry Compose file. They define the same Collector service;
merging multiple target files is not a supported way to configure routes.

Routing is explicit:

| Target | Exact HTTP traces endpoint | Authorization |
|---|---|---|
| Phoenix | `http://phoenix:6006/v1/traces` | `Bearer ${env:PHOENIX_API_KEY}` |
| Langfuse | `http://langfuse-web:3000/api/public/otel/v1/traces` | `Basic ${env:LANGFUSE_BASIC_AUTH}` |

Langfuse additionally receives `x-langfuse-ingestion-version: 4`; the current documentation
requires that opt-in for real-time v4 ingestion. The `/api/public/otel` path is the base
OTLP endpoint; the signal-specific `traces_endpoint` must include `/v1/traces`.[13]

Only `otlp`, `memory_limiter`, `batch` and `otlphttp` components are configured. They are in
the pinned **core 0.160.0** distribution manifest. No contrib binary, `basicauth` extension,
`transform`, `file_storage` or deprecated `logging` exporter is silently required.[22]
The latest repository release observed was 0.161.0, but its tested image manifest URLs
returned 404; the deployable 0.160.0 digest was retrieved instead. Repository release
numbers are not evidence that an identically tagged image exists.

Studio manually records allowlisted metadata, not document/prompt/answer content. The
Collector is **not** a PII-redaction boundary for other clients: never send unreviewed
spans to its listener. Local users and containers sharing this private Docker network
can reach unauthenticated OTLP intake. On a shared/multi-tenant host, add authenticated
transport and isolation as a separate design. No debug payload exporter is enabled.
Queues and retries are bounded and memory-only: prolonged backend failure or a Collector
restart can lose spans. Configure durable buffering only after explicitly selecting a
distribution that supplies the needed components.

## Optional paid/live proxy path

Only after obtaining consent and configuring provider quotas, set a real `OPENAI_API_KEY`
and an explicit provider model in `LITELLM_PROVIDER_MODEL` (for example a model available
to your account with the `openai/` prefix). The public proxy alias is always `studio-model`;
keep `STUDIO_MODEL=studio-model`. The shipped config has one explicitly named route, no
provider fallback, no remote logging callback and no automatic model discovery.

```bash
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.proxy.yml \
  --profile studio --profile proxy config --quiet
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.proxy.yml \
  --profile studio --profile proxy up -d --build
```

`STUDIO_ENABLE_LIVE=false` remains the default. Both server opt-in and per-run paid consent
are required by Studio; merely starting LiteLLM does not authorize a provider request.
The proxy itself is a paid-call boundary: anyone holding its master key can bypass the
Studio UI consent step. Protect that key and enforce provider-side quotas.

This is LiteLLM's single-instance, config-only route, **not** its complete multi-user
production platform: no proxy database-backed spend ledger, virtual-key lifecycle or
cross-instance Redis limiter is supplied. Those features require additional infrastructure
and operational design.[16] The digest was retrieved from the official `main-stable`
image; its OCI source revision resolves to source version **1.101.0**.[23] The digest,
not the mutable alias, controls the deployed bytes. The upstream proxy image runs as root;
this reference does not pretend every vendor image has been made rootless. Review that
image and its required writable paths before tightening its runtime user.

## Persistence, upgrades and operations

- Studio: `studio_data` contains plaintext SQLite, prompts, documents and history. Use
  SQLite's backup API or stop the app before copying the database/WAL pair; test restoration.
- Phoenix: preserve its data volume and secret. Review migrations on every image upgrade.
- Langfuse: back up PostgreSQL, ClickHouse and MinIO consistently, retain Redis AOF where
  appropriate, and securely retain encryption/salt/bootstrap material. A database-only
  backup is not a full observability restore. No automated backup job is claimed here.
- Volumes persist across `down`. `down -v` is destructive; do not use it as routine cleanup.
  The helper never deletes data or rotates keys under existing databases.
- Internal service traffic is plaintext on one host. Do not reuse these endpoints across
  hosts without authenticated TLS. Environment secrets remain visible to Docker admins;
  adopt a reviewed external secret manager before shared/network production.
- Images are pinned by retrieved manifest digests. Pinning prevents silent drift but does
  not establish provenance signatures, SBOM completeness or absence of vulnerabilities.
  Review licenses, scan images, update pins deliberately and stage migrations before rollout.
- Healthchecks in this reference are liveness/dependency probes, not evidence that the whole
  ingestion path works. Verify one explicitly consented test trace reaches each selected UI.
  Registry metadata retrieval is not a container test.

## Checks and CI

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --locked pytest -q tests deploy
uv run --locked ruff check .
uv run --locked python scripts/check_deploy.py
```

The deployment regression tests were developed against observed failing cases. They cover
public binds, absent profiles, unpinned images, privileged/host-network settings, secret
defaults, incorrect Collector auth/paths, unavailable core components, a missing Langfuse
worker, and local secret generation/overwrite protection. The validator checks the
artifact inventory, exact ten upstream URLs, image provenance and required environment
coverage. It does **not** execute Docker interpolation, test a registry pull or prove
service compatibility. The official Compose JSON Schema was separately checked against
all seven Compose files without Docker.

CI pins action commits, installs locked dependencies with pinned uv, disables DeepEval's
automatic pytest plugin loading, runs pytest/ruff/the offline validator, and runs
`docker compose ... config --quiet` using ephemeral generated local credentials and
nonfunctional CI-only provider placeholders. It never builds images or starts services.
The final unsuppressed `pip-audit` step is a release gate; see the known blockers above.

## Upstream provenance and licenses

`upstreams.json` records all **ten exact requested repository URLs**, canonical URLs,
observed releases, release timestamps, documentation, release-tagged README/license
snapshots and retrieved image references. `deploy/evidence/` retains the official API,
license, documentation and registry responses. These are source snapshots, **not active
Compose files**; do not run the upstream example Compose in that evidence directory.
Upstream examples contain insecure defaults that were not copied into this deployment.

**Phoenix is source-available under Elastic License 2.0**, not permissively licensed or
OSI-approved open source; its license restricts hosted/managed services exposing a
substantial set of its functionality.[26] Langfuse core is MIT with separately licensed
enterprise directories; LiteLLM likewise separates its enterprise directory.[25][32]
Review those terms, each infrastructure image and transitive dependencies before
redistribution or commercial hosting. These notes are not legal advice. The latest
observed repository release is distinct from the installed SDK version in `uv.lock`;
compatibility-constrained SDKs are intentionally not described as the latest release.

## Sources

[11] https://raw.githubusercontent.com/langfuse/langfuse/v4.36.1/docker-compose.yml
[13] https://langfuse.com/integrations/native/opentelemetry
[14] https://langfuse.com/self-hosting/deployment/docker-compose
[15] https://arize.com/docs/phoenix/self-hosting/deployment-options/docker
[16] https://docs.litellm.ai/docs/proxy/deploy
[20] https://raw.githubusercontent.com/Arize-ai/phoenix/arize-phoenix-v20.12.0/src/phoenix/config.py
[22] https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-releases/v0.160.0/distributions/otelcol/manifest.yaml
[23] https://raw.githubusercontent.com/BerriAI/litellm/18243cd7af4c3325165ba68b21379e2719e051c7/pyproject.toml
[25] https://raw.githubusercontent.com/langfuse/langfuse/v4.36.1/LICENSE
[26] https://raw.githubusercontent.com/Arize-ai/phoenix/arize-phoenix-v20.12.0/LICENSE
[32] https://raw.githubusercontent.com/BerriAI/litellm/v1.101.0/LICENSE
