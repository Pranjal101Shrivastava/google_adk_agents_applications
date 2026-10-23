# Deployment

## Local

The CLI must be on your PATH before the agent is useful:

```bash
npm install -g @google/gemini-cli
gemini --version

cd 02-gemini-cli-tool-agent
cp .env.example .env
make dev
make check         # confirms the agent can see the CLI
make web
```

## Cloud Run

```bash
gcloud artifacts repositories create agents \
  --repository-format=docker --location=us-central1

gcloud builds submit --config deployment/cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_SERVICE=gemini-cli-agent
```

### Why this deployment is shaped the way it is

**The image carries two runtimes.** Python for the agent, Node for the CLI.
That is the cost of using a CLI as a tool, and it is why the Dockerfile pins
`GEMINI_CLI_VERSION` — an unpinned `@latest` means the tool your agent depends
on can change its flags between two deploys of identical application code.

**Memory is sized for the clones, not the agent.** Cloud Run's writable
filesystem is an in-memory tmpfs, so every cloned repository is charged against
instance memory. 4 GiB with `MAX_WORKSPACE_MB=512` and
`MAX_CONCURRENT_WORKSPACES=5` leaves headroom for the Python process and the
Node subprocess. Raise one, raise the other.

**Timeout is 3600 s.** A clone plus three or four CLI invocations does not fit
in the 300 s default.

**Session affinity is on.** A `workspace_id` refers to a directory on one
instance's disk. Without affinity a follow-up request can land on an instance
that never cloned anything, and the agent will correctly but unhelpfully report
an unknown workspace.

**Writes are off.** `GEMINI_CLI_ALLOW_WRITE` defaults to false, so the CLI runs
without `--yolo` and cannot modify the checkout. Turn it on only for a
deployment whose users you trust with the repositories it can reach.

### Required IAM

| Role | Why |
| --- | --- |
| `roles/aiplatform.user` | The agent and the CLI both call Gemini |
| `roles/logging.logWriter` | Structured logs |
| `roles/cloudtrace.agent` | Traces across the pipeline |

## Vertex AI Agent Engine

Agent Engine is **not** a good fit here. It runs a managed Python runtime; it
does not let you install a Node CLI alongside your agent, and this agent's
primary tool is that CLI. Use Cloud Run, or GKE if you need persistent
workspace volumes rather than tmpfs.

## Hardening checklist before you expose this

- [ ] `ALLOWED_CLONE_HOSTS` lists only hosts you intend to permit.
- [ ] `GEMINI_CLI_ALLOW_WRITE=false` unless you have a reason.
- [ ] `--no-allow-unauthenticated`, with `roles/run.invoker` granted narrowly.
- [ ] `MAX_WORKSPACE_MB` and `MAX_CONCURRENT_WORKSPACES` sized under your
      instance memory.
- [ ] Egress restricted if the repositories are private: a VPC connector with
      Cloud NAT, rather than open egress.
- [ ] The CLI version pinned, and a plan for bumping it.
