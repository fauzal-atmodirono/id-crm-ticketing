FROM python:3.12-slim

WORKDIR /app

# Install the project first with just the metadata + package so dependency
# layers cache independently of application code changes.
COPY pyproject.toml ./
COPY app ./app
# scripts/ ships one-time ops scripts (e.g. register_bot.py) run via
# `docker compose exec agent python -m scripts.register_bot ...`, which
# needs the package present in the image relative to WORKDIR.
COPY scripts ./scripts

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
