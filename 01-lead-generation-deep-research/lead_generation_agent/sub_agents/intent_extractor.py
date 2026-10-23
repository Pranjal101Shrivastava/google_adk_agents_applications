"""Phase 0: turn a free-form request into a structured brief."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from ..config import config
from ..constants import StateKeys
from ..prompts import INTENT_EXTRACTOR_PROMPT
from ..schemas import IntentExtraction

intent_extractor_agent = LlmAgent(
    name="intent_extractor_agent",
    model=config.fast_model,
    description=(
        "Extracts the target country, industry, buyer persona, value proposition "
        "and next workflow stage from the user's request. Call this on every new "
        "user message before doing anything else."
    ),
    instruction=INTENT_EXTRACTOR_PROMPT,
    output_schema=IntentExtraction,
    output_key=StateKeys.INTENT_EXTRACTION,
)
