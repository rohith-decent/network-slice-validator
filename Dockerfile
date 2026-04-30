# ── Base Image ─────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System Dependencies + Docker CLI ───────────────────────────────
# Required for collector/main.py to run `docker stats`
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# ── Application Directories ────────────────────────────────────────
RUN mkdir -p /app /data /ml /app/collector /app/ml /app/api /app/dashboard /app/sb

# ── Working Directory ──────────────────────────────────────────────
WORKDIR /app

# ── Python Dependencies ────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Source Code ────────────────────────────────────────────────────
COPY collector/  /app/collector/
COPY ml/         /app/ml/
COPY api/        /app/api/
COPY dashboard/  /app/dashboard/
COPY sb/         /app/sb/
COPY entrypoint.sh /app/entrypoint.sh

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# ── Environment Variables ──────────────────────────────────────────
ENV PYTHONPATH=/app
ENV DB_PATH=/data/metrics.db
ENV MODEL_PATH=/ml/model.pkl
ENV COLLECTOR_INTERVAL=5
ENV API_PORT=8000
ENV DASHBOARD_PORT=8501
ENV LOG_LEVEL=INFO

# ── Exposed Ports ──────────────────────────────────────────────────
EXPOSE 8000 8501

# ── Entry Point ────────────────────────────────────────────────────
ENTRYPOINT ["/app/entrypoint.sh"]