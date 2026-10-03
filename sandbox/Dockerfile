# Sandbox image for running a target repo's tests with NO network.
# Dependencies are baked in at build time from the target repo's lockfile, so the container
# can run with `--network none`.
#
# Build (context = the toy repo, so its pyproject.toml and uv.lock are available):
#   docker build -f sandbox/Dockerfile -t agentteam-sandbox:py312 C:\path\to\agentteam-toy
#
# Rebuild whenever the target repo's dependencies change.
FROM python:3.12-slim

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

RUN pip install --no-cache-dir uv

WORKDIR /deps
COPY pyproject.toml uv.lock ./
# Installs runtime + dev dependencies (flask, pytest, ruff) into /opt/venv, not the project itself.
RUN uv sync --frozen --no-install-project

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
WORKDIR /workspace
USER 10001:10001
