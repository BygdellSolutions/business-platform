# syntax=docker/dockerfile:1
#
# The production image of the FastAPI backend (build context: this directory, `backend/`).
#
#   docker build -t business-platform-backend backend
#
# What is in the final image: Python 3.13, the locked PRODUCTION dependencies (`uv sync --frozen --no-dev`), the
# application package (with the bundled, checksummed PDF fonts), and Alembic's files so that a SEPARATE one-shot
# migration job can run from the same image. What is not: tests, the development dependencies, the host's virtual
# environment, any .env file, the development seed and the test-database reset script (see .dockerignore), compilers
# and the package managers (uv and pip are removed).
#
# Runtime: a non-root user, ONE Uvicorn worker, port 8000. The web process never runs migrations; they are an explicit,
# separate step (D2 adds the migrate wrapper). All configuration comes from the environment at run time.

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
# Only the dependency manifests first: this layer is rebuilt only when the lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
# Bytecode is compiled now (as root) so the read-only application tree never needs to be written at run time.
RUN /app/.venv/bin/python -m compileall -q /app/app  && python -m pip uninstall -y pip >/dev/null 2>&1; rm -f /usr/local/bin/pip /usr/local/bin/pip3*
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
USER 10001:10001
EXPOSE 8000
# Interim (D1): the existing cheap liveness endpoint. D2 replaces it with the readiness check (database reachable and
# exactly this image's Alembic head); until then this only says "the process answers".
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
