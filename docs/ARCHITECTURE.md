# Architecture

How the five agents compare, and the one principle they share.

## The principle

> **Tools establish facts. Models exercise judgement. Nothing is invented in
> between.**

An LLM asked to review code produces plausible prose. It will say a function is
too complex without knowing its complexity, claim the tests pass without
running them, and give a score it made up. All three answers are *shaped* like
correct answers, which is what makes them expensive.

So each agent moves the parts with a right answer into code:

| Agent | In code (deterministic) | Left to the model |
| --- | --- | --- |
| **a** lead generation | Weighted pattern scoring, tier thresholds | What to research, which signals are real, how to explain the ranking |
| **b** Gemini CLI | URL and ref validation, argv construction, path confinement | What to ask the CLI, how to decompose a broad request, how to write up the findings |
| **c** bug assistant | Stack trace parsing, priority rubric | Whether a ticket is a duplicate, what the likely cause is, what to do next |
| **d** code review | AST analysis, pycodestyle, test execution, weighted grade | What to test, which finding matters most, how to teach it, how to fix it |
| **e** e-commerce | **All money arithmetic**, size conversion | Which products fit the need, how to explain a price, when to confirm |

## Shape of each agent

### (a) Deep research — iterative depth over parallel breadth

```
Scout (LlmAgent)
 ├─ AgentTool(intent_extractor)        → structured brief
 ├─ AgentTool(pattern_discovery)       → SequentialAgent
 │    ├─ ResearchPlanner               → decompose before searching
 │    ├─ ReferenceFinder → Formatter   → who already did this
 │    ├─ LoopAgent (≤2)                ← the "deep" in deep research
 │    │    ├─ Orchestrator → ParallelAgent(N × Validate→Search→Format)
 │    │    ├─ Consolidator
 │    │    └─ Critic ──exit_loop──▶
 │    └─ Synthesizer → Formatter       → weighted PatternReport
 └─ AgentTool(lead_generation)         → SequentialAgent
      LeadFinder → Formatter → ParallelAgent(N) → **Scorer** → Report
```

The distinguishing feature is the critic loop. One-shot search gives you a
plausible list; a critic that checks the evidence against the plan's success
criteria and sends it round again gives you a defensible one.

### (b) Gemini CLI — an agent driving an agent

```
Hatch (LlmAgent)
 ├─ prepare_workspace / list / read / cleanup     ← sandboxed checkout
 ├─ run_gemini_cli ──────▶ subprocess: gemini -m M -p TASK
 ├─ check_gemini_cli                                 (argv list, shell=False)
 └─ AgentTool(analysis_pipeline)
      Intake → Scout → Planner → Executor → Synthesis
                         │          └─ calls run_gemini_cli per step
                         └─ turns one unanswerable question into 2–5
                            answerable ones, each scoped to a directory
```

Two paths on purpose: a narrow question goes straight to `run_gemini_cli`; a
broad one goes through the pipeline, because one CLI call for "explain this
codebase" must be either too vague to be useful or too large to finish.

### (c) Bug assistant — breadth of tool integration

```
Patch (LlmAgent, instruction carries the live tool inventory)
 ├─ ① function tools      parse_stack_trace, triage_priority, get_current_date
 ├─ ② built-in            AgentTool(web_search_agent → google_search)
 ├─ ③ third-party API     search_stackoverflow
 ├─ ④ Google Cloud        ToolboxToolset ──MCP──▶ Toolbox ──SQL──▶ Postgres
 └─ ⑤ MCP server          McpToolset ──HTTP──▶ GitHub (read-only filter)
```

A single agent, five integration paths, each independently switchable. The
registry builds them separately and writes the resulting inventory into the
instruction, so an agent with no ticket database says so rather than inventing
ticket numbers.

### (d) Code review — review pipeline plus a verifying fix loop

```
Lint (LlmAgent)
 ├─ sub_agent CodeReviewPipeline (SequentialAgent)
 │    CodeAnalyzer → StyleChecker → SecurityReviewer → TestRunner → Synthesizer
 │       ast           pycodestyle     ast patterns     subprocess    compute_grade
 └─ sub_agent CodeFixPipeline (SequentialAgent)
      LoopAgent (≤3): CodeFixer → FixTestRunner → FixValidator ──exit_loop──▶
                                     ▲ the ORIGINAL tests, unchanged
      → FixSynthesizer
```

`run_fix_tests` takes no test argument. It re-runs the review phase's tests
against the fixed code, which is what makes the fix *verified* rather than
*claimed* — a fix checked by new tests written to suit it proves nothing.

### (e) E-commerce — identity as an architectural boundary

```
Customer ──Bearer <id-token>──▶ FastAPI backend
                                 ├─ verify (signature + AUDIENCE + email_verified)
                                 ├─ write verified email into session state
                                 └─ Runner + SessionService (built ONCE)
                                      │
                                 Nova (LlmAgent)
                                   ├─ calculate_cart_totals   ← every figure shown
                                   ├─ normalize_size, validate_quantity
                                   └─ ToolboxToolset ──MCP──▶ Toolbox ──SQL──▶ AlloyDB
                                        9 personal tools bind user_email to the
                                        VERIFIED claim — not a model argument
```

Identity never passes through the model. There is no code path by which a
model-supplied email reaches a query.

## Cross-cutting conventions

Shared across all five, because each earns its keep:

**`config.py` validated at import.** `pydantic-settings` with field validators,
so a bad value fails at startup rather than mid-run. Agent **d** validates that
the four grading weights sum to 1.0 — a typo there would skew every score
silently.

**`StateKeys` in `constants.py`.** The multi-agent failure mode is one agent
writing `"patterns"` and the next reading `"discovered_patterns"`. One module,
one source of truth.

**Prompts in their own module.** Reviewable and diffable without reading the
graph. In a multi-agent system the prompts *are* most of the behaviour, and a
prompt change buried in an agent definition is a behaviour change nobody
reviewed.

**Graceful degradation with a reported inventory.** Agents **c** and **e**
build each integration independently and tell the model what is actually
available. A silently-empty toolset produces an agent that invents data, which
is strictly worse than one that says "I cannot reach the database".

**Tools return `{"status": ...}` and never raise.** A traceback tells the model
nothing it can act on; a structured error with a next step does.

**Graph-shape tests.** Pipeline order, which agent holds `exit_loop`, that
scoring precedes reporting, that every `AgentTool` target has a description.
Wiring bugs do not crash — they quietly produce worse answers, which is exactly
the class of bug that survives to production.

## Security boundaries

| Agent | Risk | Boundary |
| --- | --- | --- |
| **b** | Executes a subprocess against untrusted cloned source | argv lists with `shell=False`, host/scheme/path allowlisting, symlink-safe path confinement, scrubbed env, size/count/timeout caps |
| **d** | Executes model-written tests against stranger-written code | Separate process group, `RLIMIT_CPU`/`AS`/`NPROC`/`FSIZE`, wall-clock kill, scrubbed env — and gVisor (Cloud Run gen2) as the real boundary |
| **c**, **e** | Database access from a model-driven agent | No arbitrary SQL: only declared parameterised statements. Credentials held by Toolbox, not the agent. Read-only toolsets available |
| **e** | Cross-account data access | Identity bound to a verified token claim; never a tool argument |
| **c** | Unintended writes | GitHub tool filter is a read-only allowlist; write restriction served by the toolset, not prompted |

Full detail in [SECURITY.md](SECURITY.md).

## What is deliberately not here

- **No shared library.** Each agent duplicates its `config.py` and `StateKeys`
  pattern. For a reference repository, five readable self-contained agents beat
  four agents plus a framework you have to read first.
- **No agent-to-agent protocol.** ADK supports A2A; none of these five needs
  it, and adding it for symmetry would be structure without purpose.
- **No shared session store across agents.** They are separate applications
  that happen to live in one repository.
