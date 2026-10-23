# Google ADK Agent Applications

Five end-to-end agent applications built with the
[Google Agent Development Kit](https://google.github.io/adk-docs/), each
covering a different ADK capability — and each shipping the parts that usually
get left out of a reference implementation: tests, evals, deployment, and an
honest account of what it does not do.

**509 offline tests.** No model credentials, no database, no network. CI runs
them on every push.

```bash
git clone https://github.com/Pranjal101Shrivastava/google_adk_agents_applications
cd google_adk_agents_applications
make install
make test
```

---

## The agents

| | Agent | What it is | Headline ADK pattern |
| --- | --- | --- | --- |
| **a** | [Deep Research Lead Generation](01-lead-generation-deep-research/) | Learns the signals that preceded comparable deals, then finds companies showing them today | `LoopAgent` + dynamic `ParallelAgent` fan-out |
| **b** | [Gemini CLI Tool Agent](02-gemini-cli-tool-agent/) | Drives the Gemini CLI as a tool against a sandboxed repo checkout | An agent driving an agent, via subprocess |
| **c** | [MCP Bug Assistant](03-mcp-bug-assistant/) | On-call triage over a ticket database, GitHub, Stack Overflow and the web | All five ADK tool integration paths at once |
| **d** | [Code Review Assistant](04-code-review-assistant/) | Reviews Python, runs the tests it writes, grades it, then fixes it | Review pipeline + iterative fix `LoopAgent` |
| **e** | [E-Commerce Agent](05-ecommerce-agent/) | Sports retail assistant over AlloyDB via MCP Toolbox | Verified-identity claim binding |

Each directory is self-contained: its own `README.md`, `pyproject.toml`,
`.env.example`, `Makefile`, tests, eval sets and deployment. Start anywhere.

| Agent | Tests | Tools | Eval sets | Deploy target |
| --- | --- | --- | --- | --- |
| a — lead generation | 59 | 4 + 13 sub-agents | 3 | Cloud Run, Agent Engine |
| b — Gemini CLI | 123 | 8 | 2 | Cloud Run (gen2) |
| c — bug assistant | 73 | 6 groups, 12 SQL tools | 2 | Cloud Run + Cloud SQL |
| d — code review | 141 | 7 | 2 | Cloud Run (gen2) |
| e — e-commerce | 113 | 5 + 19 SQL tools | 2 | Cloud Run + AlloyDB |

## The design principle these share

> **Tools establish facts. Models exercise judgement. Nothing is invented in
> between.**

Every one of these agents moves something out of the prompt and into code,
because that something has a right answer:

| Agent | Moved into code | Why the model should not do it |
| --- | --- | --- |
| a | Weighted lead scoring | "Why is this a tier A?" needs an arithmetic answer |
| b | The CLI command line | A prompt interpolated into a shell is a command injection |
| c | Stack trace parsing, priority rubric | A model picks a plausible frame; a parser picks the right one |
| d | AST analysis, style, test execution, grading | Same submission must always get the same grade |
| e | **All money arithmetic** | A plausible wrong total looks exactly like a right one |

The models still do the hard part — deciding what to research, which finding
matters, how to explain it. They just do not get to make up numbers.

## What "end-to-end" means here

Each agent ships:

- **The agent package** — `agent.py`, prompts in their own module, typed
  schemas, a `config.py` validated by `pydantic-settings` at import so a bad
  value fails at startup rather than mid-run.
- **Offline tests** — no credentials, no network. They test the deterministic
  tools properly, and pin the graph shape, because wiring bugs in a multi-agent
  system do not crash: they quietly produce worse answers.
- **ADK eval sets** — the routing and refusal decisions that matter, including
  the cases where the right answer is "no".
- **Deployment** — `Dockerfile`, Cloud Build pipeline that runs the tests
  before it builds, and a `deployment/README.md` explaining the sizing and IAM
  rather than just listing commands.
- **A Known limits section** in every README. If an agent cannot do something,
  that is written down.

## Repository layout

```
.
├── 01-lead-generation-deep-research/   (a) deep research
├── 02-gemini-cli-tool-agent/           (b) CLI as a tool
├── 03-mcp-bug-assistant/               (c) MCP tools
├── 04-code-review-assistant/           (d) code review
├── 05-ecommerce-agent/                 (e) commerce
├── docs/
│   ├── ARCHITECTURE.md                 the patterns, compared side by side
│   ├── SETUP.md                        prerequisites and first run
│   ├── ADK_PATTERNS.md                 which ADK primitive to reach for
│   ├── EVALUATION.md                   how these agents are tested
│   └── SECURITY.md                     the threat model across all five
├── .github/workflows/ci.yml            tests, lint, SQL, YAML, eval sets
├── Makefile                            monorepo targets
└── pytest.ini                          collects every agent's suite
```

## Getting started

Full detail in [`docs/SETUP.md`](docs/SETUP.md). The short version:

```bash
make install          # shared venv + every agent's dependencies
make test             # all 509 tests
make lint             # ruff across all five
make agents           # list the agents and their packages
```

Then pick one:

```bash
cd 04-code-review-assistant
cp .env.example .env  # add a GCP project, or an AI Studio API key
make web              # ADK dev UI at http://localhost:8000
```

Two agents need more than an API key to be fully useful:

- **b** needs the Gemini CLI: `npm install -g @google/gemini-cli`
- **c** and **e** need a database behind MCP Toolbox: `make db-up && make db-init`

Both degrade honestly without it — they report the missing integration rather
than inventing data. That is tested, not assumed.

## Model configuration

Every agent runs on Gemini via either Vertex AI or AI Studio, set per agent in
`.env`:

```bash
# Vertex AI
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_CLOUD_LOCATION=us-central1

# or AI Studio
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your-key
```

The agents route between a fast model and a reasoning model rather than using
one for everything — flash for extraction, formatting and validation, pro for
planning, research and synthesis. Both are configurable; see each agent's
README for which stages use which.

## Credits

Built against the Google Cloud blog posts, codelabs and samples listed in each
agent's README. Where this repository diverges from a reference implementation,
the agent's README says what changed and why — in several cases because the
reference has a defect worth not copying.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
