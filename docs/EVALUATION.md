# Evaluation

Two layers, with different jobs.

| | Unit tests | ADK eval sets |
| --- | --- | --- |
| What they check | Tools, schemas, graph shape | Whether the agent *behaves* |
| Model calls | None | Yes |
| Cost | Free | Real |
| Speed | Seconds | Minutes |
| Runs in CI | Yes, on every push | No — needs credentials |
| Deterministic | Yes | No |

## Layer 1: unit tests (509, all offline)

```bash
make test                      # everything
make test-04                   # one agent
cd 04-code-review-assistant && pytest tests/test_grading.py -v
```

No credentials, no network, no database, no Gemini CLI. A test that needs a
secret is a test that does not run on a fork's pull request.

| Agent | Tests | What they actually cover |
| --- | --- | --- |
| a | 59 | Scoring arithmetic (including hallucinated pattern names earning zero), state transitions, graph shape |
| b | 123 | One test per attack class in the sandbox, exact argv, env scrubbing, real local git clones |
| c | 73 | Parsing across four languages, the triage rubric, graceful degradation |
| d | 141 | Every detection *and* its non-detection, real subprocess resource limits, the grading caps |
| e | 113 | Money arithmetic exhaustively, the identity boundary, `tools.yaml` security properties |

### Four kinds of test, and why each exists

**1. Deterministic tool tests.** The core value. Scoring, parsing, money,
grading — all pure functions, tested including the adversarial cases.

```python
def test_hallucinated_pattern_earns_no_points():
    result = score_lead(PATTERNS, ["Local leadership hire", "Astrological alignment"], SOURCES)
    assert result["fit_score"] == 40
```

**2. Negative tests — the false positives that must not fire.** Roughly half of
agent **d**'s security tests assert a finding does *not* appear, because a
linter that cries wolf gets switched off:

```python
async def test_md5_with_usedforsecurity_false_is_not_flagged(tool_context):
    """An explicit non-security use is a deliberate opt-out, not a finding."""
    assert "hashlib.md5" not in await _kinds(code, tool_context)
```

**3. Graph-shape tests.** The ones that earn their keep. A wiring bug in a
multi-agent system does not crash; it quietly produces a worse answer.

```python
def test_only_the_validator_can_release_the_loop():
    for agent in loop.sub_agents:
        if agent.name == "FixValidator":
            assert "exit_loop" in tool_names(agent)
        else:
            assert "exit_loop" not in tool_names(agent)
```

**4. Prompt-contract tests.** Where a specific instruction *is* a control:

```python
def test_instruction_forbids_accepting_a_typed_email():
    assert "never ask a customer to type their email address" in squashed(
        root_agent.instruction
    )
```

These feel odd at first — asserting on English. But when the prompt line is the
countermeasure, deleting it is a regression, and a test is how you find out.

## Layer 2: ADK eval sets

```bash
cd 03-mcp-bug-assistant
make eval
```

Eleven eval sets across the five agents, scored on two criteria from
`eval/test_config.json`:

```json
{ "criteria": { "tool_trajectory_avg_score": 0.6, "response_match_score": 0.3 } }
```

`tool_trajectory_avg_score` is the one that matters. It compares which tools
were called, in what order, against the expected trajectory — and tool choice
is where an agent's behaviour actually lives. `response_match_score` is kept
low deliberately: the exact wording of a good answer varies, and demanding a
close match would fail correct behaviour.

### What the eval sets assert

| Agent | Set | The behaviour under test |
| --- | --- | --- |
| a | `clarification` | Asks for missing fields; never invents a market |
| a | `pattern_discovery` | Runs discovery before leads; does not rediscover |
| a | `lead_generation` | Refuses "skip the analysis, just give me leads" |
| b | `workspace_lifecycle` | Prepares before analysing; asks for a URL rather than inventing one |
| b | `cli_delegation` | Narrow → direct, broad → pipeline; refuses a non-allowlisted host |
| c | `triage` | Parses before searching; finds the semantic duplicate; **asks** rather than assuming an impact signal |
| c | `ticket_management` | Confirms before writing; does not invent ticket 9999 |
| d | `review` | Reports a syntax error and stops; states the security cap *as a cap* |
| d | `fix` | Reports an unverified fix **as unverified, first** |
| e | `shopping` | Converts US sizes before searching; checks stock; uses the tool for every total |
| e | `identity` | Refuses a typed email; refuses cross-account access even when signed in |

**Most of these test a refusal.** That is the point. The interesting failures of
an agent are not "it could not do the task" — they are "it did the task it was
not supposed to do": inventing a ticket, cancelling a shipped order, claiming
untested code works, showing someone else's orders.

### Adding a case

```json
{
  "eval_id": "descriptive_snake_case_name",
  "conversation": [
    {
      "invocation_id": "unique-001",
      "user_content": { "role": "user", "parts": [{ "text": "..." }] },
      "final_response": { "role": "model", "parts": [{ "text": "..." }] },
      "intermediate_data": {
        "tool_uses": [{ "name": "expected_tool", "args": {} }],
        "intermediate_responses": []
      }
    }
  ],
  "session_input": {
    "app_name": "the_agent_package_name",
    "user_id": "eval-user",
    "state": {}
  }
}
```

Three things worth getting right:

- **`session_input.state` is how you test a mid-conversation decision** without
  replaying the whole conversation. Agent **e**'s identity cases set
  `is_authenticated` directly, which is the only way to test the signed-in
  refusal path.
- **`tool_uses` is the assertion.** Write the trajectory you actually want, not
  the one you got.
- **Write the `final_response` you would want to receive.** It is scored
  loosely, but it documents the intended behaviour better than a comment.

CI validates every eval set's structure on each push — it cannot run them
without credentials, but a malformed set is caught immediately rather than when
someone next runs `make eval`.

## What is not measured

Worth stating plainly:

- **Research quality in agent (a).** Whether the discovered patterns are
  *genuinely predictive* needs outcome data from real sales cycles. The evals
  check the process, not the prediction.
- **Review usefulness in agent (d).** Whether an author's next submission is
  better is the only real measure, and it needs a longitudinal study.
- **Generated test quality in agent (d).** The tests catch obvious breakage
  well and subtle logic errors poorly. Mutation testing would quantify it.
- **Latency and cost.** Neither is tracked. For agents that fan out to a
  reasoning model, both matter in production and neither is in CI.
