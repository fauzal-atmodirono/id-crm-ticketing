FROM python:3.12-slim

WORKDIR /app

# Install the project first with just the metadata + package so dependency
# layers cache independently of application code changes.
COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
