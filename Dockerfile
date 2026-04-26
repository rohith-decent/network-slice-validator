FROM python:3.11-slim

# ── Install system deps + Docker CLI (static binary) ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (not daemon)
RUN curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

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

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

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