"""Tests for the AST security review.

Half of these assert a finding fires; the other half assert it does *not*,
because a security linter that cries wolf gets switched off.
"""

from __future__ import annotations

import pytest

from code_review_assistant.constants import StateKeys
from code_review_assistant.tools.security import review_security


async def _scan(code: str, tool_context):
    return await review_security(code, tool_context)


async def _kinds(code: str, tool_context) -> set[str]:
    result = await _scan(code, tool_context)
    return {f["kind"] for f in result.get("findings", [])}


# --- True positives ---------------------------------------------------
@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("def f(x):\n    return eval(x)\n", "eval"),
        ("def f(x):\n    exec(x)\n", "exec"),
        ("import pickle\ndef f(x):\n    return pickle.loads(x)\n", "pickle.loads"),
        ("import os\ndef f(x):\n    os.system(x)\n", "os.system"),
        ("import yaml\ndef f(x):\n    return yaml.load(x)\n", "yaml.load"),
        ("import hashlib\ndef f(x):\n    return hashlib.md5(x).hexdigest()\n", "hashlib.md5"),
        ("import marshal\ndef f(x):\n    return marshal.loads(x)\n", "marshal.loads"),
    ],
)
async def test_dangerous_calls_are_found(tool_context, code, kind):
    assert kind in await _kinds(code, tool_context)


async def test_shell_true_is_found(tool_context):
    code = 'import subprocess\ndef f(x):\n    subprocess.run("ls " + x, shell=True)\n'
    assert "subprocess_shell" in await _kinds(code, tool_context)


async def test_disabled_tls_verification_is_found(tool_context):
    code = "import requests\ndef f(u):\n    return requests.get(u, verify=False)\n"
    assert "tls_verification_disabled" in await _kinds(code, tool_context)


async def test_hardcoded_secret_is_critical(tool_context):
    result = await _scan('API_KEY = "sk-live-9f8e7d6c5b4a3210"\n', tool_context)
    finding = next(f for f in result["findings"] if f["kind"] == "hardcoded_secret")
    assert finding["severity"] == "critical"
    # The value itself must not be echoed back into the transcript.
    assert "9f8e7d6c5b4a3210" not in finding["snippet"]


@pytest.mark.parametrize(
    "code",
    [
        'def f(i):\n    return f"SELECT * FROM users WHERE id = {i}"\n',
        'def f(i):\n    return "DELETE FROM t WHERE x = " + i\n',
        'def f(i):\n    return "UPDATE t SET a = 1 WHERE id = %s" % i\n',
    ],
)
async def test_sql_interpolation_is_found(tool_context, code):
    assert "sql_injection" in await _kinds(code, tool_context)


async def test_assert_used_for_validation_is_found(tool_context):
    code = 'def f(user):\n    assert user.is_admin, "not allowed"\n    return 1\n'
    assert "assert_for_validation" in await _kinds(code, tool_context)


# --- False positives that must not fire -------------------------------
async def test_clean_code_has_no_findings(tool_context, clean_code):
    result = await _scan(clean_code, tool_context)
    assert result["finding_count"] == 0
    assert result["score"] == 100


async def test_safe_yaml_loader_is_not_flagged(tool_context):
    code = "import yaml\ndef f(x):\n    return yaml.load(x, Loader=yaml.SafeLoader)\n"
    assert "yaml.load" not in await _kinds(code, tool_context)


async def test_safe_load_is_not_flagged(tool_context):
    code = "import yaml\ndef f(x):\n    return yaml.safe_load(x)\n"
    assert await _kinds(code, tool_context) == set()


async def test_md5_with_usedforsecurity_false_is_not_flagged(tool_context):
    """An explicit non-security use is a deliberate opt-out, not a finding."""
    code = (
        "import hashlib\n"
        "def f(x):\n"
        "    return hashlib.md5(x, usedforsecurity=False).hexdigest()\n"
    )
    assert "hashlib.md5" not in await _kinds(code, tool_context)


async def test_subprocess_without_shell_is_not_flagged(tool_context):
    code = 'import subprocess\ndef f(x):\n    subprocess.run(["ls", x], shell=False)\n'
    assert "subprocess_shell" not in await _kinds(code, tool_context)


@pytest.mark.parametrize(
    "value", ["", "changeme", "your-api-key", "xxxxxx", "<redacted>", "${API_KEY}", "example"]
)
async def test_placeholder_credentials_are_not_flagged(tool_context, value):
    assert "hardcoded_secret" not in await _kinds(f'API_KEY = "{value}"\n', tool_context)


async def test_short_values_are_not_flagged_as_secrets(tool_context):
    assert "hardcoded_secret" not in await _kinds('token = "ab"\n', tool_context)


async def test_non_credential_names_are_not_flagged(tool_context):
    assert "hardcoded_secret" not in await _kinds(
        'greeting = "hello there friend"\n', tool_context
    )


async def test_parameterised_sql_is_not_flagged(tool_context):
    code = (
        "def f(cursor, i):\n"
        '    cursor.execute("SELECT * FROM users WHERE id = %s", (i,))\n'
    )
    assert "sql_injection" not in await _kinds(code, tool_context)


async def test_fstring_without_interpolation_is_not_flagged(tool_context):
    code = 'def f():\n    return f"SELECT * FROM users"\n'
    assert "sql_injection" not in await _kinds(code, tool_context)


async def test_assert_in_a_test_body_is_still_reported(tool_context):
    """The tool cannot tell a test from production code; the note covers it."""
    code = "def test_thing():\n    assert 1 == 1\n"
    assert "assert_for_validation" in await _kinds(code, tool_context)


# --- Scoring and reporting -------------------------------------------
async def test_findings_are_sorted_worst_first(tool_context, insecure_code):
    result = await _scan(insecure_code, tool_context)
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    severities = [rank[f["severity"]] for f in result["findings"]]
    assert severities == sorted(severities)


async def test_score_falls_with_severity(tool_context):
    low = await _scan("import random\ndef f():\n    return random.random()\n", tool_context)
    high = await _scan("def f(x):\n    return eval(x)\n", tool_context)
    assert high["score"] < low["score"]


async def test_score_never_goes_negative(tool_context, insecure_code):
    result = await _scan(insecure_code, tool_context)
    assert result["score"] == 0


async def test_critical_flag_is_set(tool_context, insecure_code):
    result = await _scan(insecure_code, tool_context)
    assert result["has_critical"] is True
    assert result["by_severity"]["critical"] >= 1


async def test_scope_note_refuses_to_imply_a_sign_off(tool_context, clean_code):
    """A clean scan is the moment this disclaimer matters most."""
    result = await _scan(clean_code, tool_context)
    assert "not a security sign-off" in result["scope_note"]
    assert "dataflow" in result["scope_note"]


async def test_report_is_stored_in_state(tool_context, insecure_code):
    await _scan(insecure_code, tool_context)
    assert tool_context.state[StateKeys.SECURITY_REPORT]["has_critical"] is True


async def test_unparseable_code_is_reported_not_crashed(tool_context, broken_code):
    result = await _scan(broken_code, tool_context)
    assert result["status"] == "error"
    assert result["error_type"] == "syntax"


async def test_code_is_read_from_state_when_not_passed(tool_context, insecure_code):
    tool_context.state[StateKeys.CODE_TO_REVIEW] = insecure_code
    result = await review_security("", tool_context)
    assert result["status"] == "success"
    assert result["finding_count"] > 0


@pytest.mark.parametrize(
    "value",
    [
        "sk-live-9f8e7d6c5b4a3210",
        "AKIAIOSFODNN7EXAMPLE",
        "hunter2pass",
        "ghp_1234567890abcdefghij",
    ],
)
async def test_credential_shaped_values_are_still_flagged(tool_context, value):
    """The placeholder suppression must not swallow the real thing."""
    assert "hardcoded_secret" in await _kinds(f'API_KEY = "{value}"\n', tool_context)


@pytest.mark.parametrize("value", ["my-secret-key", "TODO replace", "change_me"])
async def test_more_placeholder_shapes_are_suppressed(tool_context, value):
    assert "hardcoded_secret" not in await _kinds(f'PASSWORD = "{value}"\n', tool_context)
