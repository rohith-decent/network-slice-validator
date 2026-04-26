FROM python:3.11-slim

# ── Install system deps + Docker CLI (static binary) ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-25.0.6.tgz \
    | tar xz -C /usr/local/bin --strip-components=1 docker/docker

# ── App directories ───────────────────────────────────────────────
RUN mkdir -p /app /data /ml /app/collector /app/ml /app/api /app/dashboard
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Source code ───────────────────────────────────────────────────
COPY collector/ /app/collector/
COPY ml/        /app/ml/
COPY api/       /app/api/
COPY dashboard/ /app/dashboard/
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

# ── Environment (Section 11) ──────────────────────────────────────
ENV PYTHONPATH=/app
ENV DB_PATH=/data/metrics.db
ENV MODEL_PATH=/ml/model.pkl
ENV COLLECTOR_INTERVAL=5
ENV API_PORT=8000
ENV DASHBOARD_PORT=8501
ENV LOG_LEVEL=INFO

EXPOSE 8000 8501
ENTRYPOINT ["/app/entrypoint.sh"]