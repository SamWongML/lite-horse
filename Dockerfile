FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv pip install --system .

EXPOSE 8080

# Default command runs the API; ECS overrides for scheduler / worker.
#   api:        uvicorn lite_horse.web.app:create_app --factory --host 0.0.0.0 --port 8080
#   scheduler:  python -m lite_horse.scheduler
#   worker:     python -m lite_horse.worker
CMD ["uvicorn", "lite_horse.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
