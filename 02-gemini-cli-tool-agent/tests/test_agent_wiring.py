"""Structural tests over the Gemini CLI agent graph."""

from __future__ import annotations

from gemini_cli_agent import root_agent
from gemini_cli_agent.schemas import CliPlan, CliStep, RepoRequest
from gemini_cli_agent.sub_agents.analysis_pipeline import codebase_analysis_pipeline


def squashed(text: str) -> str:
    """Lowercase and collapse whitespace, so wrapped prompt lines still match."""
    return " ".join(text.lower().split())


def tool_names(agent) -> list[str]:
    """Tool names, whether the entry is a tool object or a bare function."""
    return [
        getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__
        for t in agent.tools
    ]


def test_root_agent_exposes_the_full_toolset():
    assert tool_names(root_agent) == [
        "prepare_workspace",
        "list_workspace_files",
        "read_workspace_file",
        "run_gemini_cli",
        "check_gemini_cli",
        "list_active_workspaces",
        "cleanup_workspace",
        "codebase_analysis_pipeline",
    ]


def test_root_instruction_states_why_the_cli_exists():
    """Without this framing the model reads files one by one instead."""
    instruction = squashed(root_agent.instruction)
    assert "do not try to read a codebase file by file" in instruction
    assert "never fabricate" in instruction


def test_root_instruction_tells_the_model_when_to_use_the_pipeline():
    instruction = root_agent.instruction
    assert "codebase_analysis_pipeline" in instruction
    assert "narrow" in instruction.lower()


def test_pipeline_order_is_plan_execute_synthesise():
    assert [a.name for a in codebase_analysis_pipeline.sub_agents] == [
        "IntakeAgent",
        "ScoutAgent",
        "PlannerAgent",
        "ExecutorAgent",
        "SynthesisAgent",
    ]


def test_only_the_executor_may_invoke_the_cli():
    """Any other step calling the CLI would bypass the plan."""
    for agent in codebase_analysis_pipeline.sub_agents:
        names = tool_names(agent) if getattr(agent, "tools", None) else []
        if agent.name == "ExecutorAgent":
            assert "run_gemini_cli" in names
        else:
            assert "run_gemini_cli" not in names


def test_structured_steps_enforce_their_schemas():
    by_name = {a.name: a for a in codebase_analysis_pipeline.sub_agents}
    assert by_name["IntakeAgent"].output_schema is RepoRequest
    assert by_name["PlannerAgent"].output_schema is CliPlan


def test_planner_uses_the_orchestrator_model():
    """Planning is the step where reasoning quality pays for itself."""
    from gemini_cli_agent.config import config

    by_name = {a.name: a for a in codebase_analysis_pipeline.sub_agents}
    assert by_name["PlannerAgent"].model == config.orchestrator_model
    assert by_name["SynthesisAgent"].model == config.orchestrator_model
    assert by_name["IntakeAgent"].model == config.worker_model


def test_pipeline_description_names_its_precondition():
    """The root model must know a workspace has to exist first."""
    description = codebase_analysis_pipeline.description
    assert "prepare_workspace" in description


def test_scout_is_told_not_to_interpret():
    """A scout that editorialises corrupts the planner's input."""
    by_name = {a.name: a for a in codebase_analysis_pipeline.sub_agents}
    instruction = squashed(by_name["ScoutAgent"].instruction)
    assert "no commentary" in instruction


def test_cli_step_requires_a_self_contained_task():
    step = CliStep(step_id=1, purpose="orient", task="Read src/ and list entry points.")
    assert step.subdirectory == ""
    assert step.depends_on_previous is False


def test_cli_plan_round_trips_through_json():
    plan = CliPlan(
        objective="explain the service",
        steps=[CliStep(step_id=1, purpose="p", task="t")],
        expected_deliverable="an architecture note",
    )
    assert CliPlan.model_validate_json(plan.model_dump_json()) == plan
