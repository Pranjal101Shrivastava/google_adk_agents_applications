"""Gemini CLI Tool Agent: the Gemini CLI as a tool inside an ADK pipeline."""

from . import agent
from .agent import root_agent

__version__ = "1.0.0"

__all__ = ["agent", "root_agent", "__version__"]
