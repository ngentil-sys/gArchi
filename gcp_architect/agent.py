"""GCP Architect: a Gemini (ADK) agent that designs enterprise Google Cloud architectures
and renders them as editable Google Slides decks with official icons and documentation links.

`adk web` discovers `root_agent` from this module.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, google_search
from google.genai import types

from .brief import brief_from_state
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
from .tools_terraform import (
    fetch_terraform_resource_doc,
    get_terraform_resources,
    lint_terraform_project,
    read_terraform_project,
    scaffold_terraform_project,
    write_terraform_files,
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


def _dynamic_instruction(ctx) -> str:
    """Per-session context: the customer brief from the workspace form, plus current deliverables."""
    parts: list[str] = []
    brief = brief_from_state(ctx.state)
    if brief:
        parts.append(
            "# Customer brief (captured in the workspace form — treat as the source of truth; ask only about gaps)\n"
            + brief.to_markdown()
        )
    url = ctx.state.get("presentation_url")
    if url:
        parts.append(f"# Current deliverables\n- Architecture deck (rebuild in place on changes): {url}")
    tf = ctx.state.get("terraform_project")
    if tf:
        parts.append(f"- Terraform project slug: {tf}")
    return "\n\n".join(parts) if parts else "No customer brief has been captured yet; gather requirements in the conversation."


RESEARCH_BUDGET = int(os.getenv("RESEARCH_BUDGET_PER_TURN", "6"))


def _research_budget(tool, args, tool_context):
    """Google Search grounding is billed per grounded prompt; cap research calls per user turn."""
    if tool.name != "search_gcp_docs":
        return None
    key = f"_research_calls:{tool_context.invocation_id}"
    used = int(tool_context.state.get(key, 0))
    if used >= RESEARCH_BUDGET:
        return {"error": f"Research budget for this turn ({RESEARCH_BUDGET} searches) is used up. "
                         "Use fetch_official_doc on URLs you already know, or proceed with stated assumptions."}
    tool_context.state[key] = used + 1
    return None

root_agent = LlmAgent(
    name="gcp_architect",
    model=settings.architect_model,
    description="Enterprise Google Cloud architect that produces editable Google Slides architecture decks and variablized Terraform.",
    # The long, stable method lives in static_instruction (cache-friendly); the per-engagement
    # customer brief captured in the GUI is appended dynamically from session state.
    static_instruction=architect_instruction(),
    instruction=_dynamic_instruction,
    before_tool_callback=_research_budget,
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
        # Terraform
        get_terraform_resources,
        fetch_terraform_resource_doc,
        scaffold_terraform_project,
        write_terraform_files,
        lint_terraform_project,
        read_terraform_project,
    ],
)
