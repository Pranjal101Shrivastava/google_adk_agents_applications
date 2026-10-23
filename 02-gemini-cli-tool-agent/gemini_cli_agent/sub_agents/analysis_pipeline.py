"""The plan-execute-synthesise pipeline around the Gemini CLI.

A broad request ("explain this codebase", "write a test plan") is badly served
by a single CLI call: one invocation has to be either too vague to be useful or
too large to finish. So the pipeline splits it.

::

    IntakeAgent      normalise the request            (output_schema)
      -> ScoutAgent      list the repo layout             (tool)
      -> PlannerAgent    2-5 focused CLI invocations      (output_schema)
      -> ExecutorAgent   run them, retry timeouts once    (tool, looped by the model)
      -> SynthesisAgent  write the deliverable

The planner is the reason this works: it converts one unanswerable question
into several answerable ones, each scoped to a directory.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, SequentialAgent

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    EXECUTOR_PROMPT,
    INTAKE_PROMPT,
    PLANNER_PROMPT,
    SYNTHESIS_PROMPT,
)
from ..schemas import CliPlan, RepoRequest
from ..tools.gemini_cli import run_gemini_cli
from ..tools.workspace_tools import list_workspace_files

intake_agent = LlmAgent(
    name="IntakeAgent",
    model=config.worker_model,
    description="Normalises the user's request into a structured RepoRequest.",
    instruction=INTAKE_PROMPT,
    output_schema=RepoRequest,
    output_key=StateKeys.REPO_REQUEST,
)

scout_agent = LlmAgent(
    name="ScoutAgent",
    model=config.worker_model,
    description="Lists the repository layout so the planner can scope its steps.",
    instruction=(
        "Call `list_workspace_files` exactly once, using the `workspace_id` from "
        "{active_workspace} with `max_depth=2`.\n\n"
        "Then reproduce the returned `entries` list as a plain, newline-separated "
        "listing and stop. Add no commentary, no analysis, no guesses about what "
        "the files do - the planner needs the raw shape of the repository, not an "
        "interpretation of it.\n\n"
        "If no workspace is active, say exactly: 'No workspace prepared.'"
    ),
    tools=[list_workspace_files],
    output_key="workspace_listing",
)

planner_agent = LlmAgent(
    name="PlannerAgent",
    model=config.orchestrator_model,
    description="Turns the request into 2-5 scoped Gemini CLI invocations.",
    instruction=PLANNER_PROMPT,
    output_schema=CliPlan,
    output_key=StateKeys.CLI_PLAN,
)

executor_agent = LlmAgent(
    name="ExecutorAgent",
    model=config.worker_model,
    description="Runs each planned CLI invocation in order and records failures.",
    instruction=EXECUTOR_PROMPT,
    tools=[run_gemini_cli],
    output_key="execution_log",
)

synthesis_agent = LlmAgent(
    name="SynthesisAgent",
    model=config.orchestrator_model,
    description="Writes the final engineering deliverable from the CLI findings.",
    instruction=SYNTHESIS_PROMPT,
    output_key=StateKeys.ANALYSIS_REPORT,
)

codebase_analysis_pipeline = SequentialAgent(
    name="codebase_analysis_pipeline",
    description=(
        "Plan-execute-synthesise analysis of a prepared repository. Decomposes a "
        "broad request into several scoped Gemini CLI invocations and writes the "
        "deliverable from their combined findings. Requires prepare_workspace to "
        "have run first. Use this for architecture explanations, test plans, "
        "security reviews and migration assessments; for a single narrow question, "
        "call run_gemini_cli directly instead."
    ),
    sub_agents=[
        intake_agent,
        scout_agent,
        planner_agent,
        executor_agent,
        synthesis_agent,
    ],
)
