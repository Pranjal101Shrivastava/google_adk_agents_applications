# Setup

## Prerequisites

| Requirement | Needed for | Notes |
| --- | --- | --- |
| Python 3.10–3.13 | all agents | 3.12 recommended |
| A Gemini model endpoint | all agents | Vertex AI, or an AI Studio API key |
| Node 20+ and the Gemini CLI | agent **b** only | `npm install -g @google/gemini-cli` |
| Docker + Compose | agents **c**, **e** | for local Postgres and MCP Toolbox |
| `gcloud` CLI | deployment | not needed for local development |

Nothing above is needed to run the **tests** — all 509 are offline.

## Install

```bash
git clone https://github.com/Pranjal101Shrivastava/google_adk_agents_applications
cd google_adk_agents_applications

make install      # shared .venv + every agent's dependencies
make test         # 509 tests, no credentials required
```

Per-agent, if you prefer an isolated environment:

```bash
cd 04-code-review-assistant
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Model credentials

Each agent reads its own `.env`. Copy the template and pick one option.

### Option A — Vertex AI

The right choice for anything beyond a demo: IAM rather than a key, and
quotas, logging and tracing come with it.

```bash
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com
```

```bash
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

The identity you use needs `roles/aiplatform.user`.

### Option B — Google AI Studio

Fastest way to a running agent. Get a key at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

```bash
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your-key
```

## First run

```bash
cd 01-lead-generation-deep-research
cp .env.example .env     # edit it
make web                 # http://localhost:8000
```

`adk web` serves the dev UI: a chat window, plus an event trace showing every
sub-agent and tool call. For a multi-agent system that trace is the main
debugging tool — it is how you see that the critic loop exited early, or that
a formatter drifted off-schema.

## Per-agent extras

### (b) Gemini CLI Tool Agent

```bash
npm install -g @google/gemini-cli
gemini --version

cd 02-gemini-cli-tool-agent
make check        # confirms the agent can see the CLI
```

Without the CLI the agent still starts and reports the problem as an
environment issue, with the install command. It does not pretend to analyse
anything.

### (c) MCP Bug Assistant

```bash
cd 03-mcp-bug-assistant
make inventory    # which tool groups are live, and why the others are not
make db-up        # Postgres + pgvector + Toolbox
make db-init      # schema + 10 seed tickets
```

Optional: a GitHub PAT in `GITHUB_PERSONAL_ACCESS_TOKEN` enables the GitHub MCP
tools. Read-only scopes are enough — the tool filter is an allowlist of
read-only tools and a test fails if a write tool ever enters it.

### (e) E-Commerce Agent

```bash
cd 05-ecommerce-agent
make db-up
make db-init
make serve        # FastAPI on :8080, with verified identity
```

`make web` works too, but the ADK dev UI cannot supply a Google ID token, so it
runs as a guest and the personal-data tools correctly refuse. For the
authenticated flows you need the backend and a `GOOGLE_CLIENT_ID`.

## Troubleshooting

**`ModuleNotFoundError: No module named 'google.adk'`**
The venv is not active, or `make install` has not run. Each agent's `Makefile`
defaults to `../.venv`; set `VENV=.venv` if you made a per-agent one.

**`DefaultCredentialsError`**
`gcloud auth application-default login`, or switch to the AI Studio option.

**`ToolboxToolset requires the 'toolbox-adk' package`**
`pip install 'google-adk[toolbox]'`. Agents **c** and **e** declare this
already; it appears if you installed a bare `google-adk`.

**Agent **c** or **e** says the database is unavailable**
Expected without `make db-up`. `make inventory` (agent c) shows exactly which
groups are live. The agent reports the gap rather than inventing data, which is
the designed behaviour.

**`DeprecationWarning: SequentialAgent is deprecated`**
ADK 2.x is migrating the workflow agents to a new `Workflow` primitive that
cannot yet be nested under an `LlmAgent`. The current classes still work; the
warning is filtered in each agent's pytest config and noted in
[ADK_PATTERNS.md](ADK_PATTERNS.md).

**Tests fail on macOS in agent (d)**
`test_execution.py` is skipped off Linux: it relies on `setsid` and POSIX
resource limits. Everything else runs anywhere.

## Running the evals

Unlike the tests, evals call a model and cost money.

```bash
cd 03-mcp-bug-assistant
make eval
```

See [EVALUATION.md](EVALUATION.md) for what the eval sets assert and how to add
a case.
