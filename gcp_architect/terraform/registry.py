"""Terraform resource catalog and official provider documentation access.

Documentation source of truth: the hashicorp/terraform-provider-google repository,
whose `website/docs` markdown is what registry.terraform.io renders.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import httpx

from .. import catalog_data

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "terraform_resources.json"

REGISTRY_BASE = "https://registry.terraform.io/providers/hashicorp/{provider}/latest/docs/{kind}/{name}"
RAW_BASE = "https://raw.githubusercontent.com/hashicorp/terraform-provider-{provider}/main/website/docs/{kind}/{name}.html.markdown"
REGISTRY_API = "https://registry.terraform.io/v1/providers/hashicorp/google"

_doc_cache: dict[str, dict] = {}


@lru_cache(maxsize=1)
def _load() -> dict:
    with CATALOG_PATH.open() as f:
        return json.load(f)


def default_provider_version() -> str:
    return _load().get("provider_default_version", "8.0.0")


def latest_provider_version(timeout: float = 5.0) -> str:
    """Latest hashicorp/google version from the registry; falls back to the pinned default."""
    try:
        resp = httpx.get(REGISTRY_API, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        v = resp.json().get("version")
        if v and re.match(r"^\d+\.\d+\.\d+$", v):
            return v
    except Exception:  # noqa: BLE001
        pass
    return default_provider_version()


def resources_for_product(product_id: str) -> dict | None:
    prod = catalog_data.get_product(product_id)
    if not prod:
        return None
    entry = _load()["products"].get(prod["id"], {"resources": [], "apis": []})
    provider = "google-beta" if entry.get("beta") else "google"
    return {
        "product": prod["id"],
        "name": prod["name"],
        "provider": provider,
        "resources": [
            {"name": r, "docs": REGISTRY_BASE.format(provider=provider, kind="resources", name=r.removeprefix("google_"))}
            for r in entry.get("resources", [])
        ],
        "apis": entry.get("apis", []),
        "notes": entry.get("notes"),
        "product_docs": prod.get("docs"),
    }


def _split_sections(md: str) -> dict[str, str]:
    """Split provider doc markdown into top-level '## ' sections."""
    sections: dict[str, str] = {}
    current = "_intro"
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def _strip_html(text: str) -> str:
    text = re.sub(r"<div class=\"oics-button\".*?</div>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch_resource_doc(resource: str, max_chars: int = 12000, timeout: float = 20.0) -> dict:
    """Fetch the official doc for a resource or data source, trying google then google-beta."""
    name = resource.strip()
    kind = "r"
    if name.startswith("data.") or name.startswith("data:"):
        kind = "d"
        name = name.split(".", 1)[-1].split(":", 1)[-1]
    short = name.removeprefix("google_")
    cache_key = f"{kind}/{short}"
    if cache_key in _doc_cache:
        return _doc_cache[cache_key]

    last_error = None
    for provider in ("google", "google-beta"):
        url = RAW_BASE.format(provider=provider, kind=kind, name=short)
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 404:
            last_error = f"{url} -> 404"
            continue
        if resp.status_code != 200:
            last_error = f"{url} -> HTTP {resp.status_code}"
            continue
        md = resp.text
        # Drop the YAML/front-matter + generated header.
        md = re.sub(r"^---.*?---\s*", "", md, flags=re.S)
        sections = _split_sections(md)
        example_keys = [k for k in sections if k.lower().startswith("example usage")]
        args = sections.get("Argument Reference", "")
        attrs = sections.get("Attributes Reference", "")
        timeouts = sections.get("Timeouts", "")
        imports = sections.get("Import", "")
        beta = provider == "google-beta" or "google-beta" in md.split("## ")[0]
        doc = {
            "found": True,
            "resource": f"google_{short}" if not short.startswith("google_") else short,
            "kind": "data_source" if kind == "d" else "resource",
            "provider": "google-beta" if provider == "google-beta" else "google",
            "beta_note": beta,
            "registry_url": REGISTRY_BASE.format(provider=provider, kind="resources" if kind == "r" else "data-sources", name=short),
            "source_url": url,
            "intro": _strip_html(sections.get("_intro", ""))[:1500],
            "examples": [{"title": k, "hcl": _strip_html(sections[k])} for k in example_keys[:2]],
            "argument_reference": _strip_html(args),
            "attributes_reference": _strip_html(attrs)[:2500],
            "timeouts": _strip_html(timeouts)[:600],
            "import": _strip_html(imports)[:600],
        }
        # Enforce an overall budget, trimming the argument reference last.
        total = sum(len(str(v)) for v in doc.values())
        if total > max_chars:
            overflow = total - max_chars
            ar = doc["argument_reference"]
            doc["argument_reference"] = ar[: max(2000, len(ar) - overflow)] + "\n…(truncated; raise max_chars for the full list)"
            doc["truncated"] = True
        _doc_cache[cache_key] = doc
        return doc
    return {"found": False, "resource": resource, "error": last_error or "not found",
            "hint": "Check the resource name on https://registry.terraform.io/providers/hashicorp/google/latest/docs"}
