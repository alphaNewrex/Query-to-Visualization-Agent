# syntax=docker/dockerfile:1
# Backend image. Build from the repository root, because the recorded examples live in docs/:
#   docker build -f backend/Dockerfile -t ctviz-backend .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env ctviz-backend

ARG PYTHON_IMAGE=python:3.13.16-slim-trixie

# ---- builder: installs exactly what uv.lock says; only /app/.venv leaves this stage ---------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml or uv.lock changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, installed as a wheel so the runtime stage needs no source tree.
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime: the same Python, the environment and the examples; no uv, no compiler, no source ----
FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /app --shell /usr/sbin/nologin app
COPY --from=builder /app/.venv /app/.venv
COPY docs/examples /app/examples
# Second line of defence behind .dockerignore: an env file inside the image fails the build.
RUN ! find /app -name '.env*' | grep .
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CTVIZ_EXAMPLES_DIR=/app/examples
WORKDIR /app
USER 10001:10001
EXPOSE 8000

# Liveness only: /healthz makes no upstream call. http.client ignores proxy variables (urllib does
# not, and then never reports healthy behind a proxy); anything but a 200 exits 1.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import http.client as h; c = h.HTTPConnection('127.0.0.1', 8000, timeout=2); \
          c.request('GET', '/healthz'); raise SystemExit(c.getresponse().status != 200)"]

# Exec form, so uvicorn is PID 1 and gets SIGTERM from `docker stop`. One process on purpose: the
# registry rate limiter and cache live in this process's memory (do not add --workers or replicas).
CMD ["uvicorn", "ctviz.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
