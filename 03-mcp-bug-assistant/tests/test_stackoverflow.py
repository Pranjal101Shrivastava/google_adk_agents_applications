"""Tests for the Stack Overflow tool.

The HTTP boundary is stubbed. What is tested is the handling of the Stack
Exchange API's actual failure modes, which is where this tool earns its place
over a thin wrapper.
"""

from __future__ import annotations

import pytest

from software_bug_assistant.tools import stackoverflow
from software_bug_assistant.tools.stackoverflow import (
    _strip_html,
    search_stackoverflow,
)


def _payload(items, **extra):
    return {"items": items, "quota_remaining": 280, **extra}


ITEM = {
    "title": "Why do I get &quot;could not convert string to float&quot;?",
    "score": 42,
    "answer_count": 3,
    "is_answered": True,
    "accepted_answer_id": 991,
    "tags": ["python", "floating-point"],
    "body": "<p>The value was <code>'N/A'</code>, which <b>float()</b> rejects.</p>",
    "link": "https://stackoverflow.com/q/123",
}


async def test_empty_query_is_rejected_before_any_request(monkeypatch):
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("no HTTP request should be made for an empty query")

    monkeypatch.setattr(stackoverflow, "_fetch", must_not_be_called)
    result = await search_stackoverflow("   ")
    assert result["status"] == "error"


async def test_successful_search_maps_the_fields(monkeypatch):
    monkeypatch.setattr(stackoverflow, "_fetch", lambda *a, **k: _payload([ITEM]))
    result = await search_stackoverflow("could not convert string to float")

    assert result["status"] == "success"
    assert result["result_count"] == 1
    hit = result["results"][0]
    assert hit["score"] == 42
    assert hit["has_accepted_answer"] is True
    assert hit["url"] == "https://stackoverflow.com/q/123"


async def test_html_entities_are_decoded_in_titles(monkeypatch):
    monkeypatch.setattr(stackoverflow, "_fetch", lambda *a, **k: _payload([ITEM]))
    result = await search_stackoverflow("x")
    assert '"could not convert string to float"' in result["results"][0]["title"]
    assert "&quot;" not in result["results"][0]["title"]


async def test_body_html_is_flattened_for_the_prompt(monkeypatch):
    monkeypatch.setattr(stackoverflow, "_fetch", lambda *a, **k: _payload([ITEM]))
    result = await search_stackoverflow("x")
    excerpt = result["results"][0]["excerpt"]
    assert "<p>" not in excerpt and "<code>" not in excerpt
    assert "float()" in excerpt


async def test_no_results_suggests_a_next_step(monkeypatch):
    """An empty result means "may be new"; the agent needs that distinction."""
    monkeypatch.setattr(stackoverflow, "_fetch", lambda *a, **k: _payload([]))
    result = await search_stackoverflow("zzzz-no-such-error")
    assert result["status"] == "success"
    assert result["result_count"] == 0
    assert "google_search" in result["summary"]


async def test_api_error_message_is_surfaced(monkeypatch):
    """Quota exhaustion comes back with HTTP 200 and an error in the body."""
    monkeypatch.setattr(
        stackoverflow,
        "_fetch",
        lambda *a, **k: {"error_message": "too many requests from this IP"},
    )
    result = await search_stackoverflow("x")
    assert result["status"] == "error"
    assert "too many requests" in result["message"]


async def test_network_failure_tells_the_agent_not_to_fake_completeness(monkeypatch):
    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(stackoverflow, "_fetch", boom)
    result = await search_stackoverflow("x")
    assert result["status"] == "error"
    assert "google_search" in result["message"]
    assert "rather than presenting a partial answer as complete" in result["message"]


async def test_max_results_is_capped_by_config(monkeypatch):
    captured: dict[str, str] = {}

    def capture(url, timeout):
        captured["url"] = url
        return _payload([])

    monkeypatch.setattr(stackoverflow, "_fetch", capture)
    await search_stackoverflow("x", max_results=999)
    assert "pagesize=5" in captured["url"]


async def test_accepted_only_is_passed_through(monkeypatch):
    captured: dict[str, str] = {}

    def capture(url, timeout):
        captured["url"] = url
        return _payload([])

    monkeypatch.setattr(stackoverflow, "_fetch", capture)
    await search_stackoverflow("x", accepted_only=True)
    assert "accepted=True" in captured["url"]


async def test_throttle_hint_is_reported(monkeypatch):
    monkeypatch.setattr(
        stackoverflow, "_fetch", lambda *a, **k: _payload([], backoff=10)
    )
    result = await search_stackoverflow("x")
    assert result["throttle_seconds"] == 10


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>hello</p>", "hello"),
        ("a &amp; b", "a & b"),
        ("<div>  lots   of\n space </div>", "lots of space"),
    ],
)
def test_strip_html(raw, expected):
    assert _strip_html(raw) == expected


def test_strip_html_truncates():
    assert _strip_html("x" * 1000, limit=50).endswith("...")


def test_langchain_tool_is_optional():
    """It must return None rather than raising when the dep is absent."""
    result = stackoverflow.langchain_stackexchange_tool()
    assert result is None or type(result).__name__ == "LangchainTool"
