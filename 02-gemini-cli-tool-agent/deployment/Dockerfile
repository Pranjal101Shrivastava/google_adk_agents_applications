# Cloud Run image for the Gemini CLI Tool Agent.
#
# This image is unusual: it carries a Python agent *and* a Node CLI, because the
# agent's main tool is the Gemini CLI subprocess. Build from the agent root:
#   docker build -f deployment/Dockerfile -t gemini-cli-agent .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

# git clones the repositories; Node runs the Gemini CLI; ca-certificates lets
# both reach their endpoints over TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Pin the CLI. An unpinned `@latest` means the tool your agent depends on can
# change its flags between two deploys of identical application code.
ARG GEMINI_CLI_VERSION=0.4.1
RUN npm install -g "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
    && npm cache clean --force \
    && gemini --version

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gemini_cli_agent ./gemini_cli_agent

# Workspaces are cloned here. Cloud Run's filesystem is an in-memory tmpfs, so
# this counts against the instance's memory - which is why MAX_WORKSPACE_MB and
# MAX_CONCURRENT_WORKSPACES exist.
ENV WORKSPACE_ROOT=/workspaces
RUN mkdir -p /workspaces \
    && useradd --create-home --uid 1001 agent \
    && chown -R agent:agent /app /workspaces
USER agent

EXPOSE 8080

CMD ["sh", "-c", "adk web --host 0.0.0.0 --port ${PORT:-8080} ."]
