"""GCP Architect: a Gemini (ADK) agent that designs enterprise Google Cloud architectures
and renders them as editable Google Slides decks with official icons and documentation links.

`adk web` discovers `root_agent` from this module.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, google_search
from google.genai import types

from .config import load_settings
from .prompts import RESEARCHER_INSTRUCTION, architect_instruction
from .tools import (
    check_setup,
    fetch_official_doc,
    get_architecture_spec_schema,
    get_gcp_product,
    get_last_architecture_spec,
    list_gcp_products,
    render_architecture_deck,
    search_gcp_products,
    validate_architecture_spec,
)

settings = load_settings()


def _gen_config(thinking_level: str) -> types.GenerateContentConfig:
    """Gemini 3.x thinking level (low | medium | high). 'minimal' is rejected by 3.8 Flash."""
    level = {"low": types.ThinkingLevel.LOW, "medium": types.ThinkingLevel.MEDIUM, "high": types.ThinkingLevel.HIGH}.get(
        thinking_level, types.ThinkingLevel.MEDIUM
    )
    return types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=level))

# Grounded research sub-agent. Gemini's built-in google_search cannot share a request with
# function tools, so it lives in its own agent and is exposed to the architect as a tool.
docs_researcher = LlmAgent(
    name="gcp_docs_researcher",
    model=settings.researcher_model,
    description="Searches official Google Cloud documentation (cloud.google.com) with Google Search grounding.",
    instruction=RESEARCHER_INSTRUCTION,
    generate_content_config=_gen_config(settings.researcher_thinking),
    tools=[google_search],
)

search_gcp_docs = AgentTool(agent=docs_researcher, propagate_grounding_metadata=True)
search_gcp_docs.name = "search_gcp_docs"
search_gcp_docs.description = (
    "Research a question against official Google Cloud documentation (Architecture Center, product docs, "
    "quotas, SLAs, pricing models). Input: a specific natural-language question. Returns an answer with official URLs."
)

root_agent = LlmAgent(
    name="gcp_architect",
    model=settings.architect_model,
    description="Enterprise Google Cloud architect that produces editable Google Slides architecture decks.",
    instruction=architect_instruction(),
    generate_content_config=_gen_config(settings.architect_thinking),
    tools=[
        search_gcp_docs,
        fetch_official_doc,
        list_gcp_products,
        search_gcp_products,
        get_gcp_product,
        get_architecture_spec_schema,
        validate_architecture_spec,
        render_architecture_deck,
        get_last_architecture_spec,
        check_setup,
    ],
)
