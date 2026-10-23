# Cloud Run image for the Deep Research Lead Generation Agent.
# Build from the agent root:  docker build -f deployment/Dockerfile -t leadgen .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY lead_generation_agent ./lead_generation_agent

# Run as a non-root user; Cloud Run does not require root and this limits the
# blast radius if the container is ever compromised.
RUN useradd --create-home --uid 1001 agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

# `adk web` serves both the API and the dev UI. For a production front door,
# swap to `adk api_server` and put your own UI in front of it.
CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT:-8080} ."]
