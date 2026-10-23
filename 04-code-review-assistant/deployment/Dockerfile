# Cloud Run image for the Code Review Assistant.
# Build from the agent root:
#   docker build -f deployment/Dockerfile -t code-review-assistant .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY code_review_assistant ./code_review_assistant

# Non-root, and it matters more here than usual: this container executes
# model-written tests against user-submitted code. The resource limits in
# tools/execution.py are process-level; running as root would hand anything
# that escaped them the rest of the container too.
RUN useradd --create-home --uid 1001 reviewer && chown -R reviewer:reviewer /app
USER reviewer

EXPOSE 8080

CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT:-8080} ."]
