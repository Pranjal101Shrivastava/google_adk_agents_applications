"""Offline fixtures for the code review assistant tests."""

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
def clean_code() -> str:
    """Code that should score well: documented, simple, typed, safe."""
    return '''"""Utilities for summing sequences."""


def total(values: list[float]) -> float:
    """Return the sum of `values`, or 0.0 when it is empty.

    Args:
        values: The numbers to add.

    Returns:
        The sum, as a float.
    """
    if not values:
        return 0.0
    return float(sum(values))
'''


@pytest.fixture
def messy_code() -> str:
    """Code with one of each structural problem the analyser looks for."""
    return '''import os

def process( items=[] ,flag=None ):
    result=[]
    try:
        for i in items:
            if i and flag or i is "skip":
                result.append(i)
            elif i == 0:
                continue
            else:
                result.append(i*2)
    except:
        pass
    return result

class Handler:
    def handle(self,x):
        return x
'''


@pytest.fixture
def insecure_code() -> str:
    """Code with findings at several severities."""
    return '''import hashlib
import os
import pickle
import subprocess

API_KEY = "sk-live-9f8e7d6c5b4a3210"


def run(user_input, user_id):
    subprocess.run("ls " + user_input, shell=True)
    data = pickle.loads(user_input)
    digest = hashlib.md5(user_input.encode()).hexdigest()
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return eval(user_input), data, digest, query
'''


@pytest.fixture
def broken_code() -> str:
    """Code that does not parse."""
    return "def broken(:\n    return 1\n"
