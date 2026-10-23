# ADK patterns: which primitive to reach for

A cross-reference of the ADK primitives used across these five agents, with the
decision that led to each one. Written as the notes you would want before
starting agent number six.

## Agent types

### `LlmAgent` — a model with tools

The default. One model, one instruction, a set of tools, optional
`output_schema`.

Used as the root agent in all five.

```python
root_agent = LlmAgent(
    name="scout",
    model="gemini-2.5-pro",
    instruction=ROOT_INSTRUCTION,
    tools=[get_user_choice, *agent_tools],
    before_agent_callback=seed_state,
    after_tool_callback=promote_state,
)
```

### `SequentialAgent` — a fixed pipeline

When the order is known and must not vary. Every stage sees the previous
stages' session state.

Used for: both phases of agent **a**, the analysis pipeline in **b**, and both
pipelines in **d**.

The ordering is load-bearing more often than it looks. In **d**, `CodeAnalyzer`
runs first because it is the step that stores the submission in state and
detects a syntax error — so no later stage has to re-handle unparseable input.

### `LoopAgent` — iterate until good enough

A bounded loop. A sub-agent calls `exit_loop` to release it early.

Used for: the research critic loop in **a**, the fix loop in **d**.

```python
LoopAgent(
    name="DeepResearchLoop",
    max_iterations=config.max_research_iterations,
    sub_agents=[researcher, consolidator, critic],  # critic holds exit_loop
)
```

Two rules make a loop worth having:

1. **Only the gate holds `exit_loop`.** If any other member can call it, the
   loop can end for the wrong reason. Both agents have a test asserting this.
2. **The gate must be able to say no.** A critic instructed to "check the work"
   approves everything. A critic given explicit criteria — *at least 3
   companies with dated sources, at least 2 distinct signal types* — does not.

### `ParallelAgent` — fan out

Independent branches, concurrently. Branches share session state, so they must
write to **distinct keys** (`signal_finding_0`, `signal_finding_1`, …) or they
will overwrite each other.

Used inside the custom orchestrators in agent **a**.

### Custom `BaseAgent` — dynamic composition

Needed when the branch count is only known at runtime. `ParallelAgent` wants
its sub-agents declared up front; a custom agent can build them from session
state and then delegate.

```python
class ReferenceResearchOrchestrator(BaseAgent):
    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        companies = ctx.session.state.get("reference_companies", {}).get("companies", [])
        branches = [self._branch(i, c) for i, c in enumerate(companies[:MAX_BRANCHES])]
        fan_out = ParallelAgent(name="DynamicResearcher", sub_agents=branches)
        async for event in fan_out.run_async(ctx):
            yield event
```

**Cap the fan-out.** Ten branches is plenty; an uncapped one will exhaust your
model quota before it improves the research.

## `sub_agents` or `AgentTool`?

The decision that most changes how an agent behaves, and the one most often
made by accident.

| | `sub_agents=[...]` | `AgentTool(agent=...)` |
| --- | --- | --- |
| Control | Hands the turn over | Parent keeps it |
| Output | Becomes the reply | Returns to the parent |
| Parent can act in between | No | Yes |
| Use when | The sub-agent's answer *is* the answer | You need the result to do something with |

Agent **a** uses `AgentTool` for all three phases, because the root agent must
show the user the patterns and **ask before** starting the expensive lead
generation. Plain delegation would hand the conversation away mid-workflow.

Agent **d** uses `sub_agents`, because a code review is a hand-off: once the
user has pasted code, the pipeline's output is the answer and the root agent
has nothing to add on top.

## Tools

### Function tools — the workhorse

A plain Python function. ADK derives the schema from the signature, and **the
docstring is the prompt the model reads to decide whether to call it**. A
vague docstring is a tool that gets called at the wrong time.

```python
async def score_leads(tool_context: ToolContext) -> dict[str, Any]:
    """Score every researched lead in session state.

    Reads the pattern report and per-candidate evidence, scores each
    candidate, sorts by fit score and writes the result back to state.

    Args:
        tool_context: ADK tool context, used for session state access.

    Returns:
        A status dict with the scored leads and a short summary.
    """
```

Two conventions worth adopting:

- **Return a dict with a `status` key. Never raise.** An exception gives the
  model a traceback; `{"status": "error", "message": "..."}` gives it something
  it can act on or report.
- **Make the error message actionable.** "The CLI exceeded its 600s budget.
  Narrow the task — name a specific directory — and try again" beats
  "TimeoutError".

### Built-in tools — and why they need wrapping

`google_search`, `enterprise_web_search`, `url_context`. Gemini will not accept
a built-in tool alongside arbitrary function tools on the same agent.

The supported workaround is a single-purpose agent exposed as an `AgentTool`:

```python
search_agent = LlmAgent(name="web_search", model=..., tools=[google_search])
search_tool = AgentTool(agent=search_agent)   # now composable with anything
```

Agents **a** and **c** both do this.

### `McpToolset` — an MCP server

```python
McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": f"Bearer {token}"},
    ),
    tool_filter=["get_issue", "list_issues", "search_code"],
)
```

**`tool_filter` is an allowlist, and it is a security control.** The GitHub MCP
server also exposes write tools. A bug assistant that can close issues on its
own is a bug assistant nobody will install. Agent **c** has a test that fails
if a write tool ever enters the default filter.

### `ToolboxToolset` — a database, safely

[MCP Toolbox for Databases](https://googleapis.github.io/genai-toolbox/) turns
parameterised SQL into MCP tools. Used by agents **c** and **e**.

Three properties worth the extra service:

1. **The agent cannot issue arbitrary SQL.** Only the declared statements, with
   typed parameters. No prompt injection reaches a `DROP TABLE`, because no
   tool takes SQL as an argument.
2. **The agent holds no credentials.** Toolbox does. The agent process cannot
   leak what it never had.
3. **Identity can be bound to a verified token claim**, which is the thing
   agent **e** is built around:

   ```yaml
   parameters:
     - name: user_email
       authServices:
         - name: google_signin
           field: email        # injected from the VERIFIED token
   ```

   The model cannot set it, see it, or override it.

`ToolboxToolset` resolves its toolset lazily, so constructing it does not
require the server to be up — which is what makes local development possible
with no database at all.

## Structured output

`output_schema` makes ADK enforce a Pydantic model on an agent's output.

```python
LlmAgent(
    name="IntentExtractor",
    model=config.fast_model,
    instruction=INTENT_PROMPT,
    output_schema=IntentExtraction,
    output_key="intent_extraction_result",
)
```

**The searcher/formatter split.** A Gemini call cannot reliably use a built-in
tool like `google_search` *and* honour a strict response schema at the same
time. So every research step in agent **a** is a pair: a reasoning-model agent
that searches and writes prose, then a cheap agent that converts that prose
into the schema.

It also happens to be the right cost split — you pay reasoning prices only for
reasoning.

## Session state

One shared dict across the whole agent graph. Without discipline it becomes a
junk drawer, and the classic failure is one agent writing `"patterns"` while
the next reads `"discovered_patterns"`.

Three conventions, used in all five agents:

1. **A `StateKeys` class.** Every key in one reviewable place, with the
   per-index fan-out keys as static methods.
2. **Callbacks own the transitions.** `before_agent_callback` seeds defaults
   and the timestamp; `after_tool_callback` promotes sub-agent results into
   flat keys the next phase reads. Not scattered through prompts.
3. **A backstop in code.** Agent **a**'s callback refuses to leave the
   `clarify` stage while a required field is blank, whatever the extractor
   claimed. Models over-claim; the callback is where you stop it mattering.

### Template placeholders

ADK resolves `{key}` in an instruction from session state, and `{key?}` makes
it optional. Two traps:

- **A missing non-optional key is an error at invocation time.** Use `{key?}`
  unless the callback guarantees the key exists.
- **`str.format` on an instruction will explode on them.** When you need to
  substitute build-time values into a template that also has state
  placeholders, replace only your own keys:

  ```python
  def _substitute(template, **values):
      result = template
      for key, value in values.items():
          result = result.replace("{" + key + "}", str(value))
      return result
  ```

  Agents **b**, **c** and **e** all need this.

## Model routing

Use two models, not one. Every agent here splits them in `config.py`:

| Stage | Model | Why |
| --- | --- | --- |
| Extraction, formatting, validation | `gemini-2.5-flash` | Mechanical: schema in, schema out |
| Planning, research, synthesis, fixing | `gemini-2.5-pro` | Where reasoning quality shows up |

In agent **d** the test *author* gets the reasoning model while the style
checker gets flash, because writing tests that probe boundaries rather than
restating the happy path is the hardest judgement call in that pipeline.

## Deprecation note (ADK 2.x)

`SequentialAgent`, `LoopAgent` and `ParallelAgent` emit a `DeprecationWarning`
in favour of a new `Workflow` primitive. As of ADK 2.12, `Workflow` **cannot
yet be nested under an `LlmAgent`**, which every agent here needs. The current
classes work; the warning is filtered in each agent's pytest config. When
`Workflow` supports nesting, the migration is contained to the `sub_agents/`
modules.
