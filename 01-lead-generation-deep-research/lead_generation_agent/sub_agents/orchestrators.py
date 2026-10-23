"""Custom ``BaseAgent`` orchestrators for dynamic parallel research.

ADK's ``ParallelAgent`` needs its branches declared up front, but we only learn
how many companies to research *after* the finder agent has run. These
orchestrators build the fan-out at invocation time from session state, then
delegate to a throwaway ``ParallelAgent``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai.types import Content, Part

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    LEAD_EVIDENCE_FORMATTER_PROMPT,
    LEAD_EVIDENCE_PROMPT,
    SIGNAL_FORMATTER_PROMPT,
    SIGNAL_RESEARCHER_PROMPT,
    VALIDATOR_PROMPT,
)
from ..schemas import LeadEvidence, SignalFinding, ValidationVerdict

logger = logging.getLogger(__name__)

# Keep the fan-out bounded: an unbounded ParallelAgent will happily melt your
# model quota, and the research quality does not improve past this point.
MAX_PARALLEL_BRANCHES = 10


def _note(author: str, text: str) -> Event:
    """Build a plain progress event so the fan-out is visible in traces."""
    return Event(author=author, content=Content(parts=[Part(text=text)]))


def _safe_format(template: str, **values: Any) -> str:
    """Format a prompt, leaving unknown placeholders untouched.

    The prompt templates also contain ``{state_key}`` placeholders that ADK
    resolves itself. ``str.format`` would raise ``KeyError`` on those, so we
    substitute only the keys we were given.
    """
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


class ReferenceResearchOrchestrator(BaseAgent):
    """Validate and research every reference company found, in parallel.

    For each company the branch is ``Validator -> SignalResearcher ->
    SignalFormatter``: cheap model gates the company, reasoning model searches
    the web, cheap model structures the result.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        reference_set = state.get(StateKeys.REFERENCE_COMPANIES) or {}
        companies = (reference_set.get("companies") or [])[:MAX_PARALLEL_BRANCHES]

        if not companies:
            state[StateKeys.ALL_RESEARCH_FINDINGS] = (
                "No reference companies were found, so no signals could be researched."
            )
            yield _note(self.name, "No reference companies to research.")
            return

        country = state.get(StateKeys.COUNTRY, "the target market")
        industry = state.get(StateKeys.INDUSTRY, "the target industry")

        branches: list[BaseAgent] = []
        for index, company in enumerate(companies):
            branches.append(
                SequentialAgent(
                    name=f"ReferenceBranch_{index}",
                    description=f"Validate and research reference company {index}.",
                    sub_agents=[
                        LlmAgent(
                            name=f"Validator_{index}",
                            model=config.fast_model,
                            instruction=_safe_format(
                                VALIDATOR_PROMPT,
                                company_to_validate=company,
                                country=country,
                                industry=industry,
                            ),
                            output_schema=ValidationVerdict,
                            output_key=StateKeys.validation(index),
                        ),
                        LlmAgent(
                            name=f"SignalResearcher_{index}",
                            model=config.advanced_model,
                            instruction=_safe_format(
                                SIGNAL_RESEARCHER_PROMPT,
                                company_data=company,
                                country=country,
                                industry=industry,
                            ),
                            tools=[_google_search()],
                            output_key=StateKeys.signal_output(index),
                        ),
                        LlmAgent(
                            name=f"SignalFormatter_{index}",
                            model=config.fast_model,
                            instruction=_safe_format(
                                SIGNAL_FORMATTER_PROMPT,
                                signal_researcher_output="{"
                                + StateKeys.signal_output(index)
                                + "}",
                            ),
                            output_schema=SignalFinding,
                            output_key=StateKeys.signal_finding(index),
                        ),
                    ],
                )
            )

        yield _note(
            self.name,
            f"Researching {len(branches)} reference companies in parallel.",
        )

        fan_out = ParallelAgent(
            name="DynamicReferenceResearcher",
            description="Parallel validation and signal research over reference companies.",
            sub_agents=branches,
        )
        async for event in fan_out.run_async(ctx):
            yield event


class ReferenceFindingsConsolidator(BaseAgent):
    """Collapse the parallel branch outputs into one text blob for synthesis."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        reference_set = state.get(StateKeys.REFERENCE_COMPANIES) or {}
        companies = (reference_set.get("companies") or [])[:MAX_PARALLEL_BRANCHES]

        if not companies:
            state[StateKeys.ALL_RESEARCH_FINDINGS] = "No reference companies were found."
            state[StateKeys.COMPANIES_STUDIED] = 0
            yield _note(self.name, "Nothing to consolidate.")
            return

        blocks: list[str] = []
        validated = 0

        for index, company in enumerate(companies):
            verdict = state.get(StateKeys.validation(index)) or {}
            finding = state.get(StateKeys.signal_finding(index)) or {}
            name = company.get("company_name", f"company-{index}")

            is_valid = bool(verdict.get("is_valid"))
            if is_valid:
                validated += 1

            lines = [
                f"--- Reference company {index + 1}: {name} ---",
                f"HQ: {verdict.get('corrected_country_of_origin') or company.get('country_of_origin', 'unknown')}",
                f"Expansion: {company.get('expansion_type', 'unknown')} on {company.get('expansion_date', 'unknown date')}",
                f"Validation: {'VALID' if is_valid else 'REJECTED'} - {verdict.get('reasoning', 'no reasoning recorded')}",
            ]
            if is_valid and finding:
                lines.append(f"Signal summary: {finding.get('summary', 'none recorded')}")
                for signal in finding.get("signals") or []:
                    lines.append(f"  * {signal}")
                sources = finding.get("sources") or []
                if sources:
                    lines.append("Sources: " + ", ".join(sources))
            blocks.append("\n".join(lines))

        state[StateKeys.ALL_RESEARCH_FINDINGS] = "\n\n".join(blocks)
        state[StateKeys.COMPANIES_STUDIED] = validated

        logger.info(
            "Consolidated %d reference companies (%d validated).", len(companies), validated
        )
        yield _note(
            self.name,
            f"Consolidated findings for {len(companies)} companies ({validated} validated).",
        )


class LeadResearchOrchestrator(BaseAgent):
    """Verify every lead candidate against the discovered patterns, in parallel."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        candidate_set = state.get(StateKeys.LEAD_CANDIDATES) or {}
        candidates = (candidate_set.get("candidates") or [])[:MAX_PARALLEL_BRANCHES]

        if not candidates:
            yield _note(self.name, "No lead candidates to verify.")
            return

        country = state.get(StateKeys.COUNTRY, "the target market")
        industry = state.get(StateKeys.INDUSTRY, "the target industry")
        patterns = state.get(StateKeys.DISCOVERED_PATTERNS, "")

        branches: list[BaseAgent] = []
        for index, candidate in enumerate(candidates):
            branches.append(
                SequentialAgent(
                    name=f"LeadBranch_{index}",
                    description=f"Verify lead candidate {index} against the patterns.",
                    sub_agents=[
                        LlmAgent(
                            name=f"LeadEvidenceResearcher_{index}",
                            model=config.advanced_model,
                            instruction=_safe_format(
                                LEAD_EVIDENCE_PROMPT,
                                company_data=candidate,
                                country=country,
                                industry=industry,
                                discovered_patterns=patterns,
                            ),
                            tools=[_google_search()],
                            output_key=StateKeys.lead_evidence_output(index),
                        ),
                        LlmAgent(
                            name=f"LeadEvidenceFormatter_{index}",
                            model=config.fast_model,
                            instruction=_safe_format(
                                LEAD_EVIDENCE_FORMATTER_PROMPT,
                                lead_evidence_output="{"
                                + StateKeys.lead_evidence_output(index)
                                + "}",
                            ),
                            output_schema=LeadEvidence,
                            output_key=StateKeys.lead_evidence(index),
                        ),
                    ],
                )
            )

        yield _note(self.name, f"Verifying {len(branches)} lead candidates in parallel.")

        fan_out = ParallelAgent(
            name="DynamicLeadResearcher",
            description="Parallel pattern verification over lead candidates.",
            sub_agents=branches,
        )
        async for event in fan_out.run_async(ctx):
            yield event


def _google_search():
    """Import ``google_search`` lazily so module import stays side-effect free."""
    from google.adk.tools import google_search

    return google_search
