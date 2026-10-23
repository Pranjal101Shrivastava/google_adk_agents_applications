# Deployment

The thing that shapes every decision here: this agent **executes code a model
wrote against code a stranger submitted**. Read the isolation section before
you expose it to anyone.

## Local

```bash
cd 04-code-review-assistant
cp .env.example .env
make dev
make sandbox      # prints the execution limits and the real boundary
make test
make web          # http://localhost:8000
```

## Cloud Run

```bash
gcloud artifacts repositories create agents \
  --repository-format=docker --location=us-central1

gcloud builds submit --config deployment/cloudbuild.yaml \
  --substitutions=_REGION=us-central1
```

### Isolation: what you get, and what you need to add

`tools/execution.py` applies, in the child process:

| Control | Stops |
| --- | --- |
| Separate process + process group | A crash or `sys.exit` taking the agent down |
| `RLIMIT_CPU` | `while True: pass` |
| `RLIMIT_AS` | `[0] * 10**10` |
| `RLIMIT_NPROC` | A fork bomb |
| `RLIMIT_FSIZE` | Filling the disk |
| Wall-clock timeout, group kill | A sleeping or blocked process |
| Environment scrubbed to `PATH`/`LANG`/`LC_ALL` | Submitted code reading `GOOGLE_API_KEY` |
| `python -E -s`, throwaway cwd | Module injection via `PYTHONPATH` or the user site dir |

Each of those has a test. What they are **not** is a sandbox: there is no
network namespace, no filesystem namespace and no seccomp filter. Submitted
code can still open sockets and read anything the container user can read.

So the deployment supplies the real boundary:

- **`--execution-environment=gen2`** in `cloudbuild.yaml`. This is the gVisor
  sandbox, and it is the control that actually contains a hostile submission.
  gen1 does not have it. Do not deploy this agent on gen1.
- **Non-root user** in the Dockerfile, so anything that escapes the rlimits
  does not inherit the container.
- **Egress restriction** if the submissions are untrusted: a VPC connector with
  no default route, so code that does open a socket reaches nothing.
- **`--no-allow-unauthenticated`**, with `roles/run.invoker` granted narrowly.

If you cannot give it gen2 or an equivalent sandbox, set
`ENABLE_CODE_EXECUTION=false`. The review still runs — structure, style and
security are all static — and the grade reports correctness as "not assessed"
and redistributes its weight. That is a smaller review, honestly labelled,
which is the right trade against running untrusted code unsandboxed.

### Sizing

- **2 vCPU / 2 GiB.** The test subprocess's `RLIMIT_AS` is 512 MiB by default;
  the rest covers the agent. Raise `EXECUTION_MEMORY_MB` and the instance
  memory together.
- **900 s timeout.** A review is five model stages plus a test subprocess; the
  fix loop adds up to three more rounds on top.

### Required IAM

| Role | Why |
| --- | --- |
| `roles/aiplatform.user` | Call Gemini |
| `roles/logging.logWriter` | Structured logs |
| `roles/cloudtrace.agent` | Traces across the pipeline stages |
| `roles/storage.objectAdmin` on `ARTIFACT_BUCKET` | Only if artifact storage is enabled |

## Vertex AI Agent Engine

```bash
pip install -e '.[deploy]'
python deployment/deploy_agent_engine.py --create
```

Agent Engine runs a managed Python runtime, and the
subprocess-with-rlimits path may be restricted there. If every test run comes
back as an `exec` error, set `ENABLE_CODE_EXECUTION=false` for that deployment
rather than leaving it reporting failures it cannot explain. For a deployment
that must run tests, Cloud Run with gen2 is the better target.

## Tuning the review

The grading weights are the policy knob:

```bash
# A security-first team
CORRECTNESS_WEIGHT=0.30
SECURITY_WEIGHT=0.45
STYLE_WEIGHT=0.10
STRUCTURE_WEIGHT=0.15
```

They must sum to 1.0; `pydantic` rejects anything else at startup rather than
letting a typo quietly skew every score.
