"""Offline validation of generated Slides API requests against the discovery schema."""

from __future__ import annotations

import json
from pathlib import Path

import googleapiclient
import pytest

from gcp_architect.config import load_settings
from gcp_architect.diagram.icons import IconResolver
from gcp_architect.diagram.layout import build_layout
from gcp_architect.diagram.slides_builder import DeckBuilder
from gcp_architect.diagram.spec import ArchitectureSpec, example_spec

DISCOVERY = Path(googleapiclient.__file__).parent / "discovery_cache" / "documents" / "slides.v1.json"


@pytest.fixture(scope="module")
def schemas():
    with DISCOVERY.open() as f:
        return json.load(f)["schemas"]


def _check(value, schema, schemas, path):
    """Minimal structural validator for discovery-doc schemas."""
    if "$ref" in schema:
        return _check(value, schemas[schema["$ref"]], schemas, path)
    t = schema.get("type")
    if t == "object":
        assert isinstance(value, dict), f"{path}: expected object"
        props = schema.get("properties", {})
        if props:
            for k, v in value.items():
                assert k in props, f"{path}.{k}: unknown field (allowed: {sorted(props)})"
                _check(v, props[k], schemas, f"{path}.{k}")
        elif "additionalProperties" in schema:
            for k, v in value.items():
                _check(v, schema["additionalProperties"], schemas, f"{path}.{k}")
    elif t == "array":
        assert isinstance(value, list), f"{path}: expected array"
        for i, v in enumerate(value):
            _check(v, schema["items"], schemas, f"{path}[{i}]")
    elif t == "string":
        assert isinstance(value, str), f"{path}: expected string, got {type(value).__name__}"
        if "enum" in schema:
            assert value in schema["enum"], f"{path}: '{value}' not in enum {schema['enum']}"
    elif t in ("number", "integer"):
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{path}: expected number"
    elif t == "boolean":
        assert isinstance(value, bool), f"{path}: expected boolean"


CREATE_KINDS = {"createSlide", "createShape", "createImage", "createLine", "createTable"}


def _ids_created_before_use(requests):
    created = set()
    for i, req in enumerate(requests):
        (kind, body), = req.items()
        if kind in CREATE_KINDS:
            created.add(body["objectId"])
            page = body.get("elementProperties", {}).get("pageObjectId")
            if page:
                assert page in created, f"request {i}: page {page} used before creation"
        elif kind == "deleteObject":
            continue
        else:
            oid = body.get("objectId")
            assert oid in created, f"request {i} ({kind}): object {oid} used before creation"
            if kind == "updateLineProperties":
                lp = body["lineProperties"]
                for key in ("startConnection", "endConnection"):
                    assert lp[key]["connectedObjectId"] in created, f"request {i}: connector target missing"


def test_example_requests_match_schema(schemas):
    spec = ArchitectureSpec.model_validate(example_spec())
    plan = DeckBuilder(spec, IconResolver(load_settings())).build()
    assert plan.requests
    for i, req in enumerate(plan.requests):
        _check(req, schemas["Request"], schemas, f"requests[{i}]")
    _ids_created_before_use(plan.requests)
    ids = [next(iter(r.values()))["objectId"] for r in plan.requests if next(iter(r)) in CREATE_KINDS]
    assert len(ids) == len(set(ids)), "duplicate object ids"
    assert all(5 <= len(i) <= 50 for i in ids)


def test_elements_inside_page(schemas):
    spec = ArchitectureSpec.model_validate(example_spec())
    plan = DeckBuilder(spec, IconResolver(load_settings())).build()
    for req in plan.requests:
        (kind, body), = req.items()
        ep = body.get("elementProperties")
        if not ep or kind == "createLine":
            continue
        x, y = ep["transform"]["translateX"], ep["transform"]["translateY"]
        w, h = ep["size"]["width"]["magnitude"], ep["size"]["height"]["magnitude"]
        assert -0.5 <= x and x + w <= 720.5, f"{body['objectId']} overflows horizontally"
        assert -0.5 <= y and y + h <= 405.5, f"{body['objectId']} overflows vertically"


def test_big_spec_compacts_and_warns():
    spec = example_spec()
    for i in range(24):
        spec["nodes"].append({"id": f"extra_{i}", "label": f"Extra {i}", "product": "cloud_run", "zone": "vpc"})
    parsed = ArchitectureSpec.model_validate(spec)
    plan = DeckBuilder(parsed, IconResolver(load_settings())).build()
    assert plan.warnings, "dense diagrams must warn"
    lay = build_layout(parsed)
    # Text and geometry must stay consistent: fonts shrink only via text_scale together with cards.
    assert lay.params.level >= 2
    assert lay.fonts["label"] >= 6.0
    for nb in lay.nodes.values():
        assert nb.rect.w >= 50


def test_spec_validation_errors():
    bad = example_spec()
    bad["edges"].append({"source": "nope", "target": "lb"})
    with pytest.raises(Exception):
        ArchitectureSpec.model_validate(bad)
