# Security boundary and release gates

This release is a local-first, single-operator workbench, **not a certified production deployment or multi-tenant service**.

- Listen on loopback by default. Do not use an unauthenticated public bind.
- Production (`STUDIO_ENV=production`) requires `STUDIO_API_TOKEN` of at least 32 characters. Configure `STUDIO_ALLOWED_HOSTS` for a reverse proxy hostname; terminate TLS and perform SSO/RBAC outside the app. This token grants access to every workspace. Workspace separation is organizational, not a user-authorization boundary.
- Authentication uses a constant-time comparison. Browser tokens live only in page memory, never localStorage. No credentials are returned in settings or catalogs.
- Same-origin write checks, a restrictive Content Security Policy, trusted Host validation, a 256 KiB request-body limit, bounded schema lengths, 100 documents per workspace, two concurrent executions and 30 runs/minute protect the single-process local app. These limits are not distributed; use one application worker or an external gateway limiter.
- SQLite uses parameterized SQL, WAL and a ten-second busy timeout. Files under `data/` contain prompts, documents, generated responses and history in plaintext. Restrict their filesystem access. Back up using SQLite's backup API, not by copying an active `.db` alone. Encrypt disk/backups, define retention and audit access before using sensitive data.
- Live requests require both a server opt-in and per-run consent. The API accepts only the literal JSON boolean `true` for `allow_paid`; coercible strings or numbers are rejected. Local deterministic schema/retrieval checks are not a substitute for LLM quality evaluation. Provider quotas/budgets must also be enforced on LiteLLM/provider accounts.
- No user-supplied URLs, shell commands, model identifiers or filesystem paths are executed. Uploaded text is data, not instructions. Schema and citation checks do not prove immunity to prompt injection or hallucinations.
- Third-party analytics is disabled for offline paths where SDK configuration permits. OTLP export is opt-in; metadata-only spans must not carry submitted content or secrets. A configured paid model necessarily receives the prompt and selected document text.

## Dependency audit — unresolved upstream advisories

The fresh audit after the maintenance update returned two distinct unresolved advisories, duplicated in the feed (four raw findings). It also verified that **PyJWT was upgraded from 2.14.0 to 2.15.1**, removing CVE-2026-101918 / GHSA-42vr-xj54-vc7v. The lockfile and `pyjwt>=2.15.1` uv constraint prevent resolution back to the affected parser. This does not make the remaining audit clean:

1. **diskcache 5.6.3 — CVE-2025-69872 / GHSA-w8v5-vhqr-4h9v / PYSEC-2026-2447.** Default pickle serialization can execute code if an attacker can modify its cache directory. No patched version was listed. Do not mount untrusted/shared caches or load arbitrary cache data. The application stores its own state as JSON in SQLite, not pickle.
2. **ragas 0.4.3 — CVE-2026-6587 / GHSA-95ww-475f-pr4f / PYSEC-2026-3046.** Multimodal-faithfulness URL/local-file processing can cause SSRF. No patched version was listed. The studio's Ragas adapter must use deterministic text-only scoring and must never invoke multimodal or URL/file processing.

The audit is **not clean**. Avoidance of the affected paths reduces exposure but is not equivalent to an upstream patch. Re-audit before release; production requires patched versions or an explicit, reviewed risk acceptance. Do not suppress these advisories in CI to claim a clean build.

## Before network production

Provision secrets outside Git; verify container images and licenses; run real provider and end-to-end OTLP tests; configure TLS/identity/budgets; load-test resource bounds; exercise database restore and retention; assess both unresolved dependency advisories; add distributed storage/job workers for scale. Container configuration cannot be certified by YAML parsing alone.
