"""Function tools exposed to the Gemini agent."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from google.adk.tools import ToolContext
from pydantic import ValidationError

from . import catalog_data
from .config import load_settings
from .diagram.spec import ArchitectureSpec, example_spec

log = logging.getLogger(__name__)

OFFICIAL_DOC_HOSTS = (
    "cloud.google.com",
    "docs.cloud.google.com",
    "developers.google.com",
    "firebase.google.com",
    "google.github.io",
    "github.com/GoogleCloudPlatform",
)

STATE_LAST_SPEC = "last_spec"
STATE_PRESENTATION_ID = "presentation_id"
STATE_PRESENTATION_URL = "presentation_url"


# ----------------------------------------------------------------------------- catalog
def list_gcp_products(category: str = "") -> dict:
    """List Google Cloud products available in the catalog (ids, names, categories).

    Use the returned `id` values in the `product` field of diagram nodes and component rows.
    Args:
        category: optional category filter. One of: compute, serverless, containers, storage,
            databases, analytics, ai_ml, networking, security, operations, devops, integration,
            api_management, hybrid, migration, management, industry, external. Empty = all.
    """
    cats = catalog_data.categories()
    prods = catalog_data.all_products()
    if category:
        prods = [p for p in prods if p["category"] == category]
    return {
        "categories": cats,
        "products": [{"id": p["id"], "name": p["name"], "category": p["category"]} for p in prods],
    }


def get_gcp_product(product_id: str) -> dict:
    """Get details for one catalog product: official docs URL, icon, and a short summary.

    Args:
        product_id: catalog id (e.g. 'cloud_run') or a common alias (e.g. 'gke', 'postgres', 'waf').
    """
    prod = catalog_data.get_product(product_id)
    if not prod:
        matches = catalog_data.search_products(product_id)
        return {"found": False, "suggestions": [{"id": p["id"], "name": p["name"]} for p in matches]}
    return {"found": True, **prod}


def search_gcp_products(query: str) -> dict:
    """Search the catalog by product name, alias, or capability keyword (e.g. 'cache', 'cdc', 'warehouse')."""
    matches = catalog_data.search_products(query, limit=10)
    return {"results": [{"id": p["id"], "name": p["name"], "category": p["category"], "summary": p.get("summary", "")} for p in matches]}


# ----------------------------------------------------------------------------- docs
def fetch_official_doc(url: str, max_chars: int = 8000) -> dict:
    """Fetch an official Google Cloud documentation page and return its readable text.

    Only cloud.google.com / developers.google.com style hosts are allowed. Use this to confirm
    limits, quotas, SLAs, regional availability, pricing model names, and best practices before
    stating them. Cite the URL in your answer.
    Args:
        url: full https URL of the documentation page.
        max_chars: maximum characters of text to return (default 8000).
    """
    host = urlparse(url).netloc.lower()
    path_host = host + urlparse(url).path
    if not url.startswith("https://") or not any(path_host.startswith(h) or host == h or host.endswith("." + h) for h in OFFICIAL_DOC_HOSTS):
        return {"error": f"Refusing to fetch non-official host '{host}'. Allowed: {OFFICIAL_DOC_HOSTS}"}
    try:
        with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": "gcp-architect-agent/0.1"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"error": f"fetch failed: {exc}"}
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "devsite-header", "devsite-footer", "devsite-book-nav"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    title = (soup.title.string or "").strip() if soup.title else ""
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
    truncated = len(text) > max_chars
    return {"url": str(resp.url), "title": title, "text": text[:max_chars], "truncated": truncated}


# ----------------------------------------------------------------------------- spec / deck
def get_architecture_spec_schema() -> dict:
    """Return the JSON schema and a complete example of the architecture spec accepted by render_architecture_deck."""
    return {"schema": ArchitectureSpec.model_json_schema(), "example": example_spec()}


def validate_architecture_spec(spec: dict) -> dict:
    """Validate a spec without rendering. Returns layout statistics and warnings (unknown products, scale, etc.)."""
    try:
        parsed = ArchitectureSpec.model_validate(spec)
    except ValidationError as exc:
        return {"valid": False, "errors": json.loads(exc.json())}
    from .diagram.layout import build_layout

    lay = build_layout(parsed)
    unknown = sorted({n.product for n in parsed.nodes if n.product and not catalog_data.get_product(n.product)})
    warnings = list(lay.warnings)
    if unknown:
        warnings.append(f"Unknown product ids (no icon/docs): {unknown}. Use search_gcp_products to find the right id.")
    return {"valid": True, "nodes": len(parsed.nodes), "zones": len(parsed.zones), "edges": len(parsed.edges),
            "scale": round(lay.scale, 2), "warnings": warnings}


def render_architecture_deck(spec: dict, tool_context: ToolContext, update_existing: bool = True) -> dict:
    """Render the architecture as an editable Google Slides deck and return its URL.

    The deck contains: cover, an editable architecture diagram (official Google Cloud icons,
    native shapes, bound connectors), a component table with official documentation links,
    design-consideration cards (Architecture Framework pillars) and references.

    Args:
        spec: the architecture spec (see get_architecture_spec_schema). Required keys: title, nodes.
            Nodes should reference catalog `product` ids; group nodes with `zones` (nested via `parent`).
        update_existing: if a deck was already created in this session, rebuild it in place
            (same URL) instead of creating a new one.
    """
    try:
        parsed = ArchitectureSpec.model_validate(spec)
    except ValidationError as exc:
        return {"ok": False, "errors": json.loads(exc.json()),
                "hint": "Fix the spec and call render_architecture_deck again."}

    from .diagram.render import render_deck
    from .gapi.auth import AuthError
    from .gapi.slides import SlidesError

    pres_id = tool_context.state.get(STATE_PRESENTATION_ID) if update_existing else None
    try:
        result = render_deck(parsed, presentation_id=pres_id)
    except (AuthError, SlidesError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("render failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    tool_context.state[STATE_PRESENTATION_ID] = result.presentation_id
    tool_context.state[STATE_PRESENTATION_URL] = result.url
    tool_context.state[STATE_LAST_SPEC] = parsed.model_dump(mode="json")
    out = {"ok": True, **result.to_dict()}
    out["rebuilt_in_place"] = bool(pres_id)
    return out


def get_last_architecture_spec(tool_context: ToolContext) -> dict:
    """Return the most recently rendered spec in this session (to make incremental edits)."""
    spec = tool_context.state.get(STATE_LAST_SPEC)
    if not spec:
        return {"found": False}
    return {"found": True, "presentation_url": tool_context.state.get(STATE_PRESENTATION_URL), "spec": spec}


def check_setup() -> dict:
    """Diagnose local configuration: project, model, credentials/scopes, icon hosting. Call when a tool fails."""
    s = load_settings()
    report: dict[str, Any] = {
        "project": s.project,
        "location": s.location,
        "architect_model": s.architect_model,
        "icon_host": s.icon_host,
        "icon_bucket": s.icon_bucket,
        "icon_base_url": s.icon_base_url,
        "icons_enabled": s.icons_enabled,
    }
    try:
        from .gapi.auth import credential_scopes_ok

        ok, msg = credential_scopes_ok()
        report["credentials"] = {"ok": ok, "detail": msg}
    except Exception as exc:  # noqa: BLE001
        report["credentials"] = {"ok": False, "detail": str(exc)}
    from .diagram.icons import IconResolver, known_icon_ids

    report["local_icon_count"] = len(known_icon_ids())
    if s.icons_enabled:
        try:
            url = IconResolver(s).url_for("cloud_run")
            probe = httpx.head(url, follow_redirects=True, timeout=10) if url else None
            report["icon_probe"] = {"url": (url or "")[:120], "status": probe.status_code if probe else None}
        except Exception as exc:  # noqa: BLE001
            report["icon_probe"] = {"error": str(exc)}
    return report
