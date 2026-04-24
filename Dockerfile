FROM python:3.11-slim

# ── System deps ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# ── App dirs ─────────────────────────────────────────────────────────
RUN mkdir -p /app /data /ml /app/collector /app/ml /app/api /app/dashboard

WORKDIR /app

# ── Python deps ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Source code ──────────────────────────────────────────────────────
COPY collector/ /app/collector/
COPY ml/        /app/ml/
COPY api/        /app/api/
COPY dashboard/  /app/dashboard/
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENV PYTHONPATH=/app

EXPOSE 8000 8501

ENTRYPOINT ["/app/entrypoint.sh"]
