# Registry manifests and retrieval evidence: deploy/evidence/image-manifests.json
FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv
FROM docker.io/library/python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable --no-cache

FROM docker.io/library/python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84 AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOME=/tmp STUDIO_DB_PATH=/app/data/studio.db
RUN groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid 10001 --no-create-home studio \
    && mkdir -p /app/data && chown 10001:10001 /app/data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --chown=10001:10001 studio ./studio
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8765/api/health', headers={'Authorization':'Bearer '+os.environ.get('STUDIO_API_TOKEN','')}); urllib.request.urlopen(r, timeout=3)"]
CMD ["uvicorn", "studio.app:app", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
