# syntax=docker/dockerfile:1

FROM python:3.12-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN mkdir -p webchat && touch webchat/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip pip install .
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

FROM dependencies AS test

# The deterministic provider is available only in the isolated automated-test image.
ENV ENVIRONMENT=test

COPY webchat ./webchat
COPY tests ./tests
RUN --mount=type=cache,target=/root/.cache/pip pip install ".[dev]"

CMD ["pytest", "-q"]

FROM dependencies AS runtime

COPY webchat ./webchat

EXPOSE 4020

CMD ["uvicorn", "webchat.main:app", "--host", "0.0.0.0", "--port", "4020"]
