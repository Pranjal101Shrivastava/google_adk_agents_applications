# Deployment

Three ways to run this agent, in increasing order of operational weight.

## 1. Local (development)

```bash
cp .env.example .env     # fill in project + location, or an AI Studio API key
make dev
make web                 # http://localhost:8000
```

## 2. Cloud Run (stateless HTTP service)

Cloud Run is the right target when you want a URL behind IAM and you are happy
to bring your own session storage.

```bash
gcloud artifacts repositories create agents \
  --repository-format=docker --location=us-central1

gcloud builds submit --config deployment/cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_SERVICE=leadgen-agent
```

The build runs the test suite before it builds the image, so a red suite never
reaches production. The service deploys with `--no-allow-unauthenticated`;
grant `roles/run.invoker` to the identities that should reach it.

Sizing notes baked into `cloudbuild.yaml`: 2 vCPU, 2 GiB and a 900 s request
timeout, because one deep-research turn fans out into many parallel searches
and routinely runs for minutes.

## 3. Vertex AI Agent Engine (managed agent runtime)

Agent Engine manages sessions, scaling and tracing for you — the better fit for
long-running research turns.

```bash
pip install -e '.[deploy]'
gcloud auth application-default login

python deployment/deploy_agent_engine.py --create
python deployment/deploy_agent_engine.py --list
python deployment/deploy_agent_engine.py --quicktest --resource_id=<id>
```

Tear down when you are done — a deployment bills while it exists:

```bash
python deployment/deploy_agent_engine.py --delete --resource_id=<id>
```

## Required IAM

The runtime service account needs:

| Role | Why |
| --- | --- |
| `roles/aiplatform.user` | Call Gemini models on Vertex AI |
| `roles/storage.objectAdmin` on the staging bucket | Agent Engine packaging |
| `roles/logging.logWriter` | Structured logs |
| `roles/cloudtrace.agent` | Traces for the multi-agent fan-out |

## Cost control

Pattern discovery and lead generation both fan out to the reasoning model.
Before pointing this at a wide brief, cap the breadth:

```bash
DEFAULT_PATTERN_COMPANIES=3
DEFAULT_LEAD_CANDIDATES=5
MAX_RESEARCH_ITERATIONS=1
```
