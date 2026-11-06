FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    GOOGLE_GENAI_USE_VERTEXAI=true \
    GOOGLE_CLOUD_PROJECT=lv-playground-genai \
    GOOGLE_CLOUD_LOCATION=global

WORKDIR /app

# Dependencies first so the layer caches across code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY logisly_agents ./logisly_agents
COPY serve.py ./
# deploy.sh stages the demo data here; Settings prefers logisly_agents/data/.
COPY .stage/data ./logisly_agents/data
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "serve.py"]
