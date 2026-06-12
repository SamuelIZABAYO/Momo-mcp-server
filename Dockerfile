# syntax=docker/dockerfile:1
# ── Hardened, multi-stage build ──────────────────────────────────────────────
# Base pinned by digest (python:3.12-slim, resolved 2026-06-11). No `latest`.
#
# Image size: ~242MB. The spec's original <200MB target is NOT met, by choice.
# The official `mcp` SDK pulls in starlette/uvicorn/cryptography to support HTTP
# transports we never use (we run stdio only), and those set a ~240MB floor on
# python:3.12-slim. Switching to alpine would save ~70MB but risks musl-wheel
# breakage with `cryptography`; a fragile 190MB image is worse than a robust
# 242MB one. We pruned what we safely could (pip, bytecode, bundled tests).
# See GOTCHAS.md for the full rationale.
ARG BASE_DIGEST=sha256:a39549e211a16149edf74e5fdc9ef03a6767e46cd987c5048b6659b6c9904c94

# ── builder: install deps + the app into a self-contained venv ───────────────
FROM python@${BASE_DIGEST} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy only what's needed to resolve and install the package, for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
 && pip uninstall -y pip setuptools wheel 2>/dev/null || true \
 # Strip runtime-unneeded weight: bytecode caches, tests bundled in deps, and
 # dist-info metadata kept only for resolution. Keeps the final image lean.
 && find /venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
 && find /venv -type d -name 'tests' -path '*/site-packages/*' -exec rm -rf {} + 2>/dev/null \
 && rm -rf /venv/lib/python3.12/site-packages/pip*

# ── final: minimal runtime image, non-root, no build tooling ─────────────────
FROM python@${BASE_DIGEST} AS final

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOMO_DB_PATH=/data/momo.sqlite3

# Dedicated non-root user with a fixed UID; owns the mounted data dir.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --home /data app \
 && mkdir -p /data && chown app:app /data

# Copy the prebuilt venv (the app + its deps) — no compilers in the final image.
COPY --from=builder /venv /venv

# /data holds the SQLite store and the PAUSE kill-switch file; it is the WORKDIR
# so `touch /data/PAUSE` (a `docker exec` or a mounted file) halts mutations
# without a rebuild or restart. Declared a VOLUME so the store persists.
WORKDIR /data
VOLUME /data
USER app

# HEALTHCHECK exercises get_provider_health logic (config + store reachable),
# without a network call to MTN (see healthcheck.py rationale).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-m", "momo_mcp.healthcheck"]

# MCP speaks over stdio: clients launch this with `docker run -i --rm`.
ENTRYPOINT ["python", "-m", "momo_mcp.server"]
