FROM python:3.11-slim

# Metadata
LABEL maintainer="AutoTwin AI <dev@autotwin.ai>"
LABEL description="AutoTwin AI — Confidence-Aware Self-Healing Financial Intelligence Backend"
LABEL version="1.0.0"

# System dependencies for bcrypt + motor
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Non-root user for security
RUN addgroup --system autotwin && adduser --system --ingroup autotwin autotwin
RUN chown -R autotwin:autotwin /app
USER autotwin

EXPOSE 8000

# Health check — polls /health every 30 s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
