"""Stack Overflow search, as a third-party-API tool.

Implemented directly against the Stack Exchange API rather than through
LangChain's ``StackExchangeTool``. Two reasons:

* **Dependency weight.** The LangChain route pulls in ``langchain-community``
  and ``stackapi`` to make one HTTP call.
* **Error handling.** Rate limits and quota exhaustion are the normal failure
  mode of this API, and the agent needs to be *told* that, with the throttle
  seconds, rather than receiving an empty list it will read as "no results".

If you want the LangChain path for parity with the ADK samples,
:func:`langchain_stackexchange_tool` builds it when the optional dependency is
installed, and returns ``None`` when it is not.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.stackexchange.com/2.3/search/advanced"
_USER_AGENT = "adk-software-bug-assistant/1.0"

# The API returns HTML in `body_markdown` even when asked for markdown.
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str, limit: int = 600) -> str:
    """Flatten an API HTML snippet into plain text for a prompt."""
    plain = html.unescape(_TAG_RE.sub(" ", text or ""))
    plain = " ".join(plain.split())
    return plain[:limit] + ("..." if len(plain) > limit else "")


def _fetch(url: str, timeout: int) -> dict[str, Any]:
    """Fetch and decode a JSON response. Separated out so tests can stub it."""
    import json

    request = Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 - fixed https host
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


async def search_stackoverflow(
    query: str,
    max_results: int = 5,
    accepted_only: bool = False,
) -> dict[str, Any]:
    """Search Stack Overflow for questions matching an error or symptom.

    Best used with the ``suggested_search_query`` from ``parse_stack_trace``
    rather than a raw traceback — the volatile parts of a trace (IDs, hex
    addresses, absolute paths) stop otherwise-matching posts from being found.

    Args:
        query: The search text. An exception type plus a short message works
            well, for example ``"ValueError could not convert string to float"``.
        max_results: How many questions to return (1-20).
        accepted_only: Restrict to questions that have an accepted answer.
            Useful when you want a fix rather than a discussion.

    Returns:
        A dict with the matching ``results``, each carrying its title, score,
        answer count, whether it is answered, its tags, a body excerpt and its
        URL. On a quota or network failure, an error message saying so.
    """
    text = (query or "").strip()
    if not text:
        return {"status": "error", "message": "The `query` argument was empty."}

    limit = max(1, min(int(max_results or 1), config.stackexchange_max_results))

    params: dict[str, str] = {
        "order": "desc",
        "sort": "relevance",
        "q": text,
        "site": config.stackexchange_site,
        "pagesize": str(limit),
        "filter": "withbody",
    }
    if accepted_only:
        params["accepted"] = "True"
    if config.stackexchange_key:
        params["key"] = config.stackexchange_key

    url = f"{_API_BASE}?{urlencode(params)}"

    try:
        payload = _fetch(url, config.stackexchange_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - network, quota and decode all land here
        logger.warning("Stack Exchange search failed: %s", exc)
        return {
            "status": "error",
            "message": (
                f"Stack Overflow search failed: {exc}. Fall back to google_search, "
                "and tell the user the Stack Overflow results are missing rather "
                "than presenting a partial answer as complete."
            ),
        }

    # Quota exhaustion is the normal failure here, and the API reports it in the
    # body with HTTP 200. Surfacing it explicitly keeps the agent from reading
    # an empty list as "nothing exists".
    if payload.get("error_message"):
        return {
            "status": "error",
            "message": f"Stack Exchange API error: {payload['error_message']}",
        }

    items = payload.get("items") or []
    results = [
        {
            "title": html.unescape(item.get("title", "")),
            "score": item.get("score", 0),
            "answer_count": item.get("answer_count", 0),
            "is_answered": bool(item.get("is_answered")),
            "has_accepted_answer": bool(item.get("accepted_answer_id")),
            "tags": item.get("tags", []),
            "excerpt": _strip_html(item.get("body", "")),
            "url": item.get("link", ""),
        }
        for item in items
    ]

    quota_remaining = payload.get("quota_remaining")
    if isinstance(quota_remaining, int) and quota_remaining < 20:
        logger.warning("Stack Exchange quota nearly exhausted: %d left", quota_remaining)

    return {
        "status": "success",
        "query": text,
        "result_count": len(results),
        "results": results,
        "quota_remaining": quota_remaining,
        "throttle_seconds": payload.get("backoff"),
        "summary": (
            f"Found {len(results)} Stack Overflow question(s) for {text!r}."
            if results
            else (
                f"No Stack Overflow questions matched {text!r}. The query may be too "
                "specific - try the exception type alone, or use google_search."
            )
        ),
    }


def langchain_stackexchange_tool():
    """Build the LangChain Stack Exchange tool, if the dependency is installed.

    Provided for parity with the ADK samples, which demonstrate third-party
    tool integration via ``LangchainTool``. Install with
    ``pip install '.[langchain]'``.

    Returns:
        An ADK ``LangchainTool``, or ``None`` when ``langchain-community`` is
        not available.
    """
    try:
        from google.adk.tools.langchain_tool import LangchainTool
        from langchain_community.tools import StackExchangeTool
        from langchain_community.utilities import StackExchangeAPIWrapper
    except ImportError as exc:
        logger.info("LangChain Stack Exchange tool unavailable: %s", exc)
        return None

    try:
        return LangchainTool(StackExchangeTool(api_wrapper=StackExchangeAPIWrapper()))
    except Exception as exc:  # noqa: BLE001 - the wrapper validates eagerly
        logger.warning("Could not build the LangChain Stack Exchange tool: %s", exc)
        return None
