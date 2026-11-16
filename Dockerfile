# Образ можно получать из Docker Hub или официального зеркала ECR.
# По умолчанию ECR: обход отказа Docker Hub при загрузке manifest (401).
ARG PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && groupadd --system bot && useradd --system --gid bot --home-dir /app bot

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini main.py ./
RUN mkdir -p /app/data && chown -R bot:bot /app
USER bot

CMD ["python", "main.py"]
