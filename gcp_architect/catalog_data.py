"""Curated Google Cloud product catalog: names, categories, official docs and icons."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "products.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    with CATALOG_PATH.open() as f:
        return json.load(f)


def categories() -> dict[str, str]:
    return dict(_load()["categories"])


def all_products() -> list[dict[str, Any]]:
    return list(_load()["products"])


@lru_cache(maxsize=1)
def _by_id() -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in _load()["products"]}


def get_product(product_id: str) -> dict[str, Any] | None:
    """Exact lookup by catalog id, then by alias."""
    pid = _norm(product_id)
    prod = _by_id().get(pid)
    if prod:
        return prod
    alias = _load()["aliases"].get(pid.replace("_", " ")) or _load()["aliases"].get(pid)
    if alias:
        return _by_id().get(alias)
    return None


def search_products(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Fuzzy-ish search over id, name, aliases and summary."""
    q = _norm(query).replace("_", " ")
    if not q:
        return []
    exact = get_product(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for p in all_products():
        name = p["name"].lower()
        score = 0
        if exact and p["id"] == exact["id"]:
            score = 100
        elif q == name or q == p["id"].replace("_", " "):
            score = 90
        elif q in name or q in p["id"].replace("_", " "):
            score = 70
        elif all(tok in name for tok in q.split()):
            score = 60
        elif q in p.get("summary", "").lower():
            score = 30
        else:
            toks = [t for t in q.split() if len(t) > 2]
            hits = sum(1 for t in toks if t in name or t in p.get("summary", "").lower())
            if toks and hits:
                score = 10 * hits
        if score:
            scored.append((score, p))
    scored.sort(key=lambda s: (-s[0], s[1]["name"]))
    return [p for _, p in scored[:limit]]


def resolve_icon_id(product_id: str | None, icon_override: str | None = None) -> tuple[str | None, bool]:
    """Return (icon_id, is_approximate) for a node."""
    if icon_override:
        return icon_override, False
    prod = get_product(product_id) if product_id else None
    if not prod:
        return None, False
    return prod.get("icon"), bool(prod.get("icon_approx"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9_ ./-]", "", (s or "").strip().lower()).replace("-", "_")
