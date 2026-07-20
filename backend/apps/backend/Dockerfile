# Use a Python base image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

# Copy dependency definition files
COPY uv.lock pyproject.toml /app/

# Install dependencies first (for docker layer caching)
RUN uv sync --frozen --no-install-project --no-dev

# Copy the source code and sync the project
COPY . /app
RUN uv sync --frozen --no-dev

# Final light weight runner stage
FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy the virtualenv and app code from the builder stage
COPY --from=builder /app /app

# Place the virtual environment's bin folder at the front of the PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONUNBUFFERED=1

# Expose port 8080 (standard for Cloud Run, but we'll bind dynamically)
EXPOSE 8080

# Start Uvicorn pointing to the main FastAPI app entrypoint, using the PORT env var
CMD ["sh", "-c", "uvicorn chatbot.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
