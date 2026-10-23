"""Production-quality code review assistant built with the Google ADK."""

from . import agent
from .agent import root_agent

__version__ = "1.0.0"

__all__ = ["agent", "root_agent", "__version__"]
