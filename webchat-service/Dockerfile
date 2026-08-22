FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY webchat ./webchat
RUN pip install --no-cache-dir .

EXPOSE 4020

CMD ["uvicorn", "webchat.main:app", "--host", "0.0.0.0", "--port", "4020"]

FROM base AS test

COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"

CMD ["pytest", "-q"]

FROM base AS runtime
