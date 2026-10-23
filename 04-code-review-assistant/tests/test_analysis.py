"""Tests for the AST structural analysis."""

from __future__ import annotations

from code_review_assistant.constants import StateKeys
from code_review_assistant.tools.analysis import analyze_code_structure


async def test_clean_code_analyses_without_issues(tool_context, clean_code):
    result = await analyze_code_structure(clean_code, tool_context)
    assert result["status"] == "success"
    analysis = result["analysis"]
    assert analysis["metrics"]["function_count"] == 1
    assert analysis["metrics"]["docstring_coverage"] == 1.0
    assert analysis["metrics"]["module_has_docstring"] is True
    assert analysis["issues"] == []


async def test_code_is_stored_in_state_for_later_steps(tool_context, clean_code):
    """The later review steps read the code from state, not from arguments."""
    await analyze_code_structure(clean_code, tool_context)
    assert tool_context.state[StateKeys.CODE_TO_REVIEW] == clean_code
    assert tool_context.state[StateKeys.CODE_LINE_COUNT] > 0


async def test_syntax_error_reports_the_line_and_stops(tool_context, broken_code):
    result = await analyze_code_structure(broken_code, tool_context)
    assert result["status"] == "error"
    assert result["error_type"] == "syntax"
    assert result["line"] == 1
    assert "do not guess" in result["advice"]
    assert tool_context.state[StateKeys.SYNTAX_ERROR]


async def test_fixed_syntax_clears_the_stale_error(tool_context, broken_code, clean_code):
    """Otherwise a later grade is still capped by a problem already resolved."""
    await analyze_code_structure(broken_code, tool_context)
    assert StateKeys.SYNTAX_ERROR in tool_context.state

    await analyze_code_structure(clean_code, tool_context)
    assert StateKeys.SYNTAX_ERROR not in tool_context.state


async def test_empty_submission_is_rejected(tool_context):
    assert (await analyze_code_structure("   ", tool_context))["status"] == "error"


async def test_oversized_submission_is_rejected(tool_context, monkeypatch):
    from code_review_assistant.config import config

    monkeypatch.setattr(config, "max_code_chars", 10)
    result = await analyze_code_structure("x = 1\ny = 2\nz = 3\n", tool_context)
    assert result["status"] == "error"
    assert "one module at a time" in result["message"]


# --- Issue detection --------------------------------------------------
async def test_mutable_default_is_flagged_as_high(tool_context, messy_code):
    result = await analyze_code_structure(messy_code, tool_context)
    issues = {i["kind"]: i for i in result["analysis"]["issues"]}
    assert "mutable_default" in issues
    assert issues["mutable_default"]["severity"] == "high"
    assert "items" in issues["mutable_default"]["message"]


async def test_bare_except_is_flagged(tool_context, messy_code):
    result = await analyze_code_structure(messy_code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "bare_except" in kinds


async def test_identity_comparison_with_a_literal_is_flagged(tool_context, messy_code):
    result = await analyze_code_structure(messy_code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "identity_comparison" in kinds


async def test_is_none_is_not_flagged(tool_context):
    """`is None` is correct and must not be reported."""
    result = await analyze_code_structure("def f(x):\n    return x is None\n", tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "identity_comparison" not in kinds


async def test_swallowed_exception_is_flagged(tool_context):
    code = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"
    result = await analyze_code_structure(code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "swallowed_exception" in kinds


async def test_handled_exception_is_not_flagged(tool_context):
    code = (
        "import logging\n\n"
        "def f():\n"
        "    try:\n        g()\n"
        "    except ValueError:\n        logging.exception('failed')\n"
    )
    result = await analyze_code_structure(code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "swallowed_exception" not in kinds


async def test_keyword_only_mutable_default_is_caught(tool_context):
    code = "def f(*, cache={}):\n    return cache\n"
    result = await analyze_code_structure(code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "mutable_default" in kinds


async def test_none_default_is_not_flagged(tool_context):
    code = "def f(items=None):\n    return items or []\n"
    result = await analyze_code_structure(code, tool_context)
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "mutable_default" not in kinds


# --- Complexity -------------------------------------------------------
async def test_simple_function_has_complexity_one(tool_context):
    result = await analyze_code_structure("def f():\n    return 1\n", tool_context)
    assert result["analysis"]["functions"][0]["complexity"] == 1


async def test_branches_increase_complexity(tool_context):
    simple = await analyze_code_structure("def f(x):\n    return x\n", tool_context)
    branchy = await analyze_code_structure(
        "def f(x):\n"
        "    if x:\n        return 1\n"
        "    for i in range(3):\n        pass\n"
        "    while x:\n        break\n"
        "    return 0\n",
        tool_context,
    )
    assert (
        branchy["analysis"]["functions"][0]["complexity"]
        > simple["analysis"]["functions"][0]["complexity"]
    )


async def test_boolean_operators_count_as_paths(tool_context):
    """`if a and b and c` is three ways to fail, not one."""
    single = await analyze_code_structure(
        "def f(a, b, c):\n    if a:\n        return 1\n    return 0\n", tool_context
    )
    chained = await analyze_code_structure(
        "def f(a, b, c):\n    if a and b and c:\n        return 1\n    return 0\n",
        tool_context,
    )
    assert (
        chained["analysis"]["functions"][0]["complexity"]
        - single["analysis"]["functions"][0]["complexity"]
        == 2
    )


async def test_excess_complexity_is_flagged(tool_context, monkeypatch):
    from code_review_assistant.config import config

    monkeypatch.setattr(config, "max_complexity", 2)
    code = "def f(x):\n    if x:\n        return 1\n    if not x:\n        return 2\n    return 3\n"
    result = await analyze_code_structure(code, tool_context)
    assert result["analysis"]["functions"][0]["too_complex"] is True
    kinds = {i["kind"] for i in result["analysis"]["issues"]}
    assert "complexity" in kinds


# --- Classes and methods ---------------------------------------------
async def test_methods_are_analysed_and_namespaced(tool_context, messy_code):
    result = await analyze_code_structure(messy_code, tool_context)
    names = [f["name"] for f in result["analysis"]["functions"]]
    assert "Handler.handle" in names
    assert result["analysis"]["classes"][0]["name"] == "Handler"
    assert result["analysis"]["classes"][0]["method_count"] == 1


async def test_async_functions_are_recognised(tool_context):
    result = await analyze_code_structure("async def f():\n    return 1\n", tool_context)
    assert result["analysis"]["functions"][0]["is_async"] is True


async def test_imports_are_collected(tool_context):
    code = "import os\nfrom pathlib import Path, PurePath\n"
    result = await analyze_code_structure(code, tool_context)
    assert result["analysis"]["metrics"]["import_count"] == 2


async def test_decorators_are_recorded(tool_context):
    code = "import functools\n\n@functools.cache\ndef f():\n    return 1\n"
    result = await analyze_code_structure(code, tool_context)
    assert result["analysis"]["functions"][0]["decorators"] == ["functools.cache"]


async def test_return_annotation_is_recorded(tool_context, clean_code):
    result = await analyze_code_structure(clean_code, tool_context)
    assert result["analysis"]["functions"][0]["returns_annotation"] == "float"
