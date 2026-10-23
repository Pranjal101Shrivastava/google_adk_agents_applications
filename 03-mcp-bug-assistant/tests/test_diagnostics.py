"""Tests for the deterministic diagnostic tools."""

from __future__ import annotations

import pytest

from software_bug_assistant.constants import PRIORITIES, StateKeys
from software_bug_assistant.tools.diagnostics import (
    TRIAGE_SIGNALS,
    get_current_date,
    parse_stack_trace,
    triage_priority,
)


# --- Stack trace parsing: Python --------------------------------------
async def test_python_exception_type_and_message(tool_context, python_trace):
    result = await parse_stack_trace(python_trace, tool_context)
    assert result["status"] == "success"
    assert result["exception_type"] == "ValueError"
    assert "could not convert string to float" in result["exception_message"]


async def test_python_frames_are_all_found(tool_context, python_trace):
    result = await parse_stack_trace(python_trace, tool_context)
    assert result["frame_count"] == 3
    assert result["languages"] == ["python"]


async def test_library_frames_are_marked_third_party(tool_context, python_trace):
    result = await parse_stack_trace(python_trace, tool_context)
    by_file = {f["file"]: f for f in result["frames"]}
    assert by_file["/app/svc/pricing/aggregate.py"]["is_third_party"] is False
    assert by_file[
        "/usr/local/lib/python3.12/site-packages/pydantic/main.py"
    ]["is_third_party"] is True


async def test_root_cause_is_the_innermost_first_party_frame(tool_context, python_trace):
    """Not the innermost frame overall - that is usually library code.

    Python puts the throwing frame last, so the culprit is near the bottom.
    """
    result = await parse_stack_trace(python_trace, tool_context)
    root = result["root_cause_frame"]
    assert root["file"] == "/app/svc/pricing/aggregate.py"
    assert root["line"] == 142
    assert root["function"] == "compute_totals"
    assert root["is_third_party"] is False


async def test_search_query_strips_volatile_detail(tool_context):
    """IDs, hex addresses and long paths stop otherwise-matching posts from being found."""
    trace = (
        'File "/app/svc/handler.py", line 7, in handle\n'
        "RuntimeError: session 998877 failed at 0xdeadbeef in /very/long/nested/path\n"
    )
    result = await parse_stack_trace(trace, tool_context)
    query = result["suggested_search_query"]
    assert "RuntimeError" in query
    assert "998877" not in query
    assert "0xdeadbeef" not in query
    assert "/very/long/nested/path" not in query


async def test_parse_result_is_stored_in_state(tool_context, python_trace):
    await parse_stack_trace(python_trace, tool_context)
    assert tool_context.state[StateKeys.LAST_STACK_TRACE]["exception_type"] == "ValueError"


async def test_chained_traceback_reports_the_propagated_exception(tool_context):
    """The error that reached the caller is the one worth reporting."""
    trace = """Traceback (most recent call last):
  File "/app/a.py", line 1, in f
    raise KeyError('inner')
KeyError: 'inner'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/app/b.py", line 2, in g
    raise RuntimeError('outer')
RuntimeError: outer
"""
    result = await parse_stack_trace(trace, tool_context)
    assert result["exception_type"] == "RuntimeError"
    assert result["exception_message"] == "outer"


# --- Stack trace parsing: other languages -----------------------------
async def test_java_trace(tool_context, java_trace):
    result = await parse_stack_trace(java_trace, tool_context)
    assert result["exception_type"] == "java.lang.NullPointerException"
    assert result["languages"] == ["jvm"]
    assert result["root_cause_frame"]["file"] == "OrderService.java"
    assert result["root_cause_frame"]["line"] == 142


async def test_spring_frames_are_third_party_despite_having_no_path(tool_context, java_trace):
    """JVM frames carry a bare class filename, so detection is package-based."""
    result = await parse_stack_trace(java_trace, tool_context)
    by_file = {f["file"]: f for f in result["frames"]}
    assert by_file["InvocableHandlerMethod.java"]["is_third_party"] is True
    assert by_file["OrderService.java"]["is_third_party"] is False
    assert result["first_party_frame_count"] == 2


async def test_javascript_trace(tool_context, js_trace):
    result = await parse_stack_trace(js_trace, tool_context)
    assert result["exception_type"] == "TypeError"
    assert result["languages"] == ["javascript"]
    # JS traces are innermost-first, so the culprit is the top frame.
    assert result["root_cause_frame"]["file"] == "/app/src/services/user.js"
    assert result["root_cause_frame"]["line"] == 42


async def test_node_modules_frames_are_third_party(tool_context, js_trace):
    result = await parse_stack_trace(js_trace, tool_context)
    node_frames = [f for f in result["frames"] if "node_modules" in f["file"]]
    assert node_frames and all(f["is_third_party"] for f in node_frames)


async def test_go_panic(tool_context, go_trace):
    result = await parse_stack_trace(go_trace, tool_context)
    assert result["exception_type"] == "panic"
    assert "index out of range" in result["exception_message"]
    # Go traces are innermost-first: processBatch threw, main called it.
    assert result["root_cause_frame"]["file"] == "/app/cmd/worker/batch.go"
    assert result["root_cause_frame"]["line"] == 88


async def test_trace_ordering_differs_by_language(tool_context, python_trace, java_trace):
    """Python puts the culprit last; the JVM puts it first. Both must resolve."""
    py = await parse_stack_trace(python_trace, tool_context)
    jvm = await parse_stack_trace(java_trace, tool_context)
    # Python: the culprit is the second of three frames, not the entry point.
    assert py["root_cause_frame"]["file"].endswith("aggregate.py")
    assert py["frames"][0]["file"].endswith("routes.py")  # entry point, listed first
    # JVM: the culprit is the first frame, and the entry point is listed later.
    assert jvm["root_cause_frame"]["file"] == "OrderService.java"
    assert jvm["frames"][0]["file"] == "OrderService.java"


# --- Stack trace parsing: failure modes -------------------------------
async def test_empty_input_is_rejected(tool_context):
    result = await parse_stack_trace("   ", tool_context)
    assert result["status"] == "error"


async def test_prose_is_rejected_with_advice(tool_context):
    """A bug report is not a stack trace; say so rather than inventing frames."""
    result = await parse_stack_trace(
        "The checkout page is broken and my users are angry.", tool_context
    )
    assert result["status"] == "error"
    assert "plain bug description" in result["message"]


async def test_exception_line_alone_still_parses(tool_context):
    result = await parse_stack_trace("ValueError: bad input", tool_context)
    assert result["status"] == "success"
    assert result["exception_type"] == "ValueError"
    assert result["frame_count"] == 0


async def test_all_library_frames_falls_back_to_the_deepest_frame(tool_context):
    trace = (
        'File "/usr/lib/python3.12/json/decoder.py", line 355, in raw_decode\n'
        "JSONDecodeError: Expecting value\n"
    )
    result = await parse_stack_trace(trace, tool_context)
    assert result["first_party_frame_count"] == 0
    assert result["root_cause_frame"]["is_third_party"] is True


# --- Priority triage --------------------------------------------------
async def test_no_signals_defaults_to_low(tool_context):
    result = await triage_priority([], tool_context)
    assert result["priority"] == "P3 - Low"
    assert result["score"] == 0
    assert "no impact signals were evidenced" in result["rationale"]


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (["data_loss"], "P1 - High"),
        (["security_impact", "affects_all_users"], "P0 - Critical"),
        (["production_outage", "no_workaround"], "P0 - Critical"),
        (["regression", "reproducible"], "P2 - Medium"),
        (["reproducible"], "P3 - Low"),
    ],
)
async def test_rubric_maps_signals_to_priorities(tool_context, signals, expected):
    result = await triage_priority(signals, tool_context)
    assert result["priority"] == expected


async def test_triage_is_deterministic(tool_context):
    signals = ["data_loss", "regression"]
    first = await triage_priority(signals, tool_context)
    second = await triage_priority(signals, tool_context)
    assert first["priority"] == second["priority"]
    assert first["score"] == second["score"]


async def test_unrecognised_signals_are_reported_not_scored(tool_context):
    result = await triage_priority(["data_loss", "vibes", "gut_feel"], tool_context)
    assert result["matched_signals"] == ["data_loss"]
    assert set(result["unrecognised_signals"]) == {"vibes", "gut_feel"}
    assert result["score"] == TRIAGE_SIGNALS["data_loss"][0]
    assert "Ignored unrecognised" in result["rationale"]


async def test_signal_names_are_normalised(tool_context):
    """The model will send 'Data Loss' and 'data-loss'; both must work."""
    result = await triage_priority(["Data Loss", "PRODUCTION-OUTAGE"], tool_context)
    assert set(result["matched_signals"]) == {"data_loss", "production_outage"}


async def test_duplicate_signals_are_counted_once(tool_context):
    once = await triage_priority(["data_loss"], tool_context)
    thrice = await triage_priority(["data_loss"] * 3, tool_context)
    assert once["score"] == thrice["score"]


async def test_rationale_names_the_signals(tool_context):
    result = await triage_priority(["data_loss", "no_workaround"], tool_context)
    assert "Data is lost or corrupted" in result["rationale"]
    assert "No workaround exists" in result["rationale"]


async def test_rubric_is_returned_so_the_agent_can_explain_itself(tool_context):
    result = await triage_priority(["data_loss"], tool_context)
    assert result["rubric"]["data_loss"] == 50
    assert set(result["rubric"]) == set(TRIAGE_SIGNALS)


async def test_every_priority_is_reachable(tool_context):
    """A rubric with an unreachable tier is a broken rubric."""
    reached = set()
    for signals in (
        [],
        ["reproducible"],
        ["regression", "reproducible"],
        ["data_loss"],
        ["security_impact", "production_outage"],
    ):
        reached.add((await triage_priority(signals, tool_context))["priority"])
    assert reached == set(PRIORITIES)


async def test_triage_result_is_stored_in_state(tool_context):
    await triage_priority(["data_loss"], tool_context)
    assert tool_context.state[StateKeys.LAST_TRIAGE]["priority"] == "P1 - High"


# --- Date -------------------------------------------------------------
def test_current_date_shape():
    result = get_current_date()
    assert len(result["current_date"]) == 10
    assert result["current_date"].count("-") == 2
    assert "T" in result["current_datetime_utc"]
