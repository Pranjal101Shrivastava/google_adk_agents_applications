# Cloud Run image for the E-Commerce Agent backend.
# Build from the agent root:
#   docker build -f deployment/Dockerfile -t ecommerce-agent .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ecommerce_agent ./ecommerce_agent
COPY backend ./backend

RUN useradd --create-home --uid 1001 app && chown -R app:app /app
USER app

EXPOSE 8080

# One worker. The session service and the agent are built once per process at
# startup; more workers means more processes, so set SESSION_DB_URL before
# raising this or two workers will not see each other's conversations.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
