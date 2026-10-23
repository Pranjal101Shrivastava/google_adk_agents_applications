"""Production-quality e-commerce agent built with the Google ADK."""

from . import agent
from .agent import build_agent, root_agent

__version__ = "1.0.0"

__all__ = ["agent", "build_agent", "root_agent", "__version__"]
