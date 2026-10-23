"""GUI backend tests: sessions, brief persistence, dynamic instruction (no model calls)."""

from __future__ import annotations

import os
import types

import pytest

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ["SESSION_DB_URL"] = "memory"

from fastapi.testclient import TestClient  # noqa: E402

from gcp_architect.agent import _dynamic_instruction  # noqa: E402
from gcp_architect.brief import CustomerBrief, brief_from_state  # noqa: E402
from gcp_architect.ui import server  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_health_and_index(client):
    assert client.get("/api/health").json()["ok"] is True
    r = client.get("/")
    assert r.status_code == 200 and "Customer brief" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_session_and_brief_roundtrip(client):
    s = client.post("/api/sessions", json={"title": "Acme"}).json()
    sid = s["id"]
    brief = {"customer": "Acme Retail", "objectives": "Replace on-prem order platform", "availability": "99.95% RPO 0",
             "network": "10.0.0.0/8 on-prem; GCP 10.10.0.0/16", "compliance": "PCI DSS"}
    assert client.put(f"/api/sessions/{sid}/brief", json=brief).json()["ok"]
    got = client.get(f"/api/sessions/{sid}").json()
    assert got["brief"]["customer"] == "Acme Retail"
    assert got["brief"]["network"].startswith("10.0.0.0/8")
    assert got["title"] == "Acme Retail"  # title follows the customer name
    assert got["deliverables"]["presentation_url"] is None
    assert got["messages"] == []
    lst = client.get("/api/sessions").json()
    assert any(x["id"] == sid and x["title"] == "Acme Retail" for x in lst)
    assert client.delete(f"/api/sessions/{sid}").json()["ok"]
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_chat_rejects_unknown_session(client):
    r = client.post("/api/sessions/nope/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_brief_markdown_and_dynamic_instruction():
    b = CustomerBrief(customer="Acme", objectives="Go faster", regions="europe-west3", notes="")
    mdtext = b.to_markdown()
    assert "### Customer" in mdtext and "**Regions:** europe-west3" in mdtext and "### Notes" not in mdtext
    assert brief_from_state({"brief": CustomerBrief().model_dump()}) is None  # empty brief ignored
    ctx = types.SimpleNamespace(state={"brief": b.model_dump(), "presentation_url": "https://docs.google.com/presentation/d/x/edit"})
    text = _dynamic_instruction(ctx)
    assert "Customer brief" in text and "europe-west3" in text and "docs.google.com" in text
    assert "No customer brief" in _dynamic_instruction(types.SimpleNamespace(state={}))


def test_history_flattening():
    from google.adk.events import Event
    from google.genai import types as gt

    evs = [
        Event(author="user", invocation_id="i1", content=gt.Content(role="user", parts=[gt.Part(text="design it")])),
        Event(author="gcp_architect", invocation_id="i1", partial=True, content=gt.Content(role="model", parts=[gt.Part(text="partial")])),
        Event(author="gcp_architect", invocation_id="i1", content=gt.Content(role="model", parts=[
            gt.Part(function_call=gt.FunctionCall(name="render_architecture_deck", args={"spec": {"title": "x" * 300}}))])),
        Event(author="gcp_architect", invocation_id="i1", content=gt.Content(role="model", parts=[
            gt.Part(function_response=gt.FunctionResponse(name="render_architecture_deck", response={"ok": True, "url": "https://u", "thumbnail_url": "https://t"}))])),
        Event(author="gcp_architect", invocation_id="i1", content=gt.Content(role="model", parts=[gt.Part(text="Done: https://u")])),
    ]
    msgs = server._history(evs)
    kinds = [(m["role"], m["type"]) for m in msgs]
    assert kinds == [("user", "text"), ("agent", "tool_call"), ("agent", "tool_result"), ("agent", "text")]
    assert msgs[1]["args"]["spec"].endswith("…")  # long args are truncated
    assert msgs[2]["url"] == "https://u"
