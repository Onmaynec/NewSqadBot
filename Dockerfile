FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --system bot && useradd --system --gid bot --home-dir /app bot

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini main.py ./
RUN mkdir -p /app/data && chown -R bot:bot /app
USER bot

CMD ["python", "main.py"]
