"""Tests for catalog lookups, spec validation tool and connector routing."""

from __future__ import annotations

from gcp_architect import catalog_data
from gcp_architect.diagram.layout import Rect
from gcp_architect.diagram.routing import SITE_BOTTOM, SITE_LEFT, SITE_RIGHT, SITE_TOP, route
from gcp_architect.diagram.spec import example_spec
from gcp_architect.tools import fetch_official_doc, get_gcp_product, list_gcp_products, search_gcp_products, validate_architecture_spec


def test_catalog_integrity():
    cats = catalog_data.categories()
    ids = set()
    for p in catalog_data.all_products():
        assert p["id"] not in ids, f"duplicate id {p['id']}"
        ids.add(p["id"])
        assert p["category"] in cats, p["id"]
        assert p["docs"] is None or p["docs"].startswith("https://"), p["id"]
        assert p["icon"]
    for alias, target in catalog_data._load()["aliases"].items():
        assert target in ids, f"alias {alias} -> unknown {target}"


def test_lookup_and_aliases():
    assert get_gcp_product("cloud_run")["name"] == "Cloud Run"
    assert get_gcp_product("waf")["id"] == "cloud_armor"
    assert get_gcp_product("load balancer")["id"] == "cloud_load_balancing"
    assert get_gcp_product("GKE")["id"] == "gke"
    missing = get_gcp_product("quantum toaster")
    assert missing["found"] is False
    assert search_gcp_products("cache")["results"][0]["id"] == "memorystore"
    assert any(p["id"] == "datastream" for p in search_gcp_products("cdc")["results"])
    assert list_gcp_products("databases")["products"]


def test_validate_tool_reports_unknown_products():
    spec = example_spec()
    spec["nodes"].append({"id": "x", "label": "Mystery", "product": "not_a_product", "zone": "vpc"})
    out = validate_architecture_spec(spec)
    assert out["valid"] and any("Unknown product" in w for w in out["warnings"])
    bad = validate_architecture_spec({"title": "t", "nodes": [{"id": "Bad-Id", "label": "x"}]})
    assert bad["valid"] is False


def test_fetch_official_doc_refuses_non_official_hosts():
    out = fetch_official_doc("https://example.com/whatever")
    assert "error" in out and "Refusing" in out["error"]
    out = fetch_official_doc("http://cloud.google.com/run/docs")  # must be https
    assert "error" in out


def test_routing_sites():
    a = Rect(0, 0, 100, 60)
    right = Rect(200, 0, 100, 60)
    below = Rect(0, 100, 100, 60)
    far_below = Rect(0, 300, 100, 60)
    r = route(a, right)
    assert (r.start_site, r.end_site) == (SITE_RIGHT, SITE_LEFT)
    r = route(a, below)
    assert (r.start_site, r.end_site) == (SITE_BOTTOM, SITE_TOP)
    r = route(a, far_below)  # skips a card: loop around on the right
    assert (r.start_site, r.end_site) == (SITE_RIGHT, SITE_RIGHT)
    assert all(x > 100 for x, _ in r.points[1:3])
