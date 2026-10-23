# Cloud Run image for the MCP Bug Assistant.
# Build from the agent root:
#   docker build -f deployment/Dockerfile -t bug-assistant .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY software_bug_assistant ./software_bug_assistant

RUN useradd --create-home --uid 1001 agent && chown -R agent:agent /app
USER agent

EXPOSE 8080

CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT:-8080} ."]
