FROM python:3.11-slim

# ── System deps ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    ca-certificates \
    gnupg \
    lsb-release \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
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
