"""Offline fixtures for the bug assistant tests."""

from __future__ import annotations

from typing import Any

import pytest


class FakeToolContext:
    """Minimal stand-in for ``google.adk.tools.ToolContext``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})


@pytest.fixture
def tool_context() -> FakeToolContext:
    return FakeToolContext()


@pytest.fixture
def python_trace() -> str:
    """A chained Python traceback with both first-party and library frames."""
    return """Traceback (most recent call last):
  File "/app/svc/api/routes.py", line 88, in checkout
    totals = compute_totals(cart)
  File "/app/svc/pricing/aggregate.py", line 142, in compute_totals
    return sum(float(item["price"]) for item in items)
  File "/usr/local/lib/python3.12/site-packages/pydantic/main.py", line 211, in __get__
    raise ValidationError(err)
ValueError: could not convert string to float: 'N/A'
"""


@pytest.fixture
def java_trace() -> str:
    return """java.lang.NullPointerException: Cannot invoke "String.length()" because "name" is null
\tat com.example.order.OrderService.validate(OrderService.java:142)
\tat com.example.order.OrderController.submit(OrderController.java:67)
\tat org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:205)
"""


@pytest.fixture
def js_trace() -> str:
    return """TypeError: Cannot read properties of undefined (reading 'id')
    at resolveUser (/app/src/services/user.js:42:18)
    at async handler (/app/src/api/handler.ts:17:5)
    at /app/node_modules/express/lib/router/route.js:149:13
"""


@pytest.fixture
def go_trace() -> str:
    return """panic: runtime error: index out of range [5] with length 3

goroutine 1 [running]:
main.processBatch(0xc000104000)
\t/app/cmd/worker/batch.go:88 +0x1f
main.main()
\t/app/cmd/worker/main.go:24 +0x45
"""
