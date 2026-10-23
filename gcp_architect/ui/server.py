"""gArchi workspace: FastAPI backend serving the GUI and streaming the ADK agent.

Run:  uvicorn gcp_architect.ui.server:app --port 8000   (or scripts/run.sh)

Endpoints
  GET  /                                  GUI
  GET  /api/sessions                      list engagements
  POST /api/sessions {title, brief?}      create engagement
  GET  /api/sessions/{id}                 brief + deliverables + chat history
  PUT  /api/sessions/{id}/brief           save the customer brief (session state)
  DELETE /api/sessions/{id}
  POST /api/sessions/{id}/chat {message}  SSE stream of agent events
  GET  /api/health
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, DatabaseSessionService, InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from ..agent import root_agent
from ..brief import STATE_BRIEF, CustomerBrief
from ..config import PROJECT_DIR, load_settings
from ..tools import STATE_LAST_SPEC, STATE_PRESENTATION_ID, STATE_PRESENTATION_URL
from ..tools_terraform import STATE_TF_PROJECT

log = logging.getLogger(__name__)

APP_NAME = "garchi"
STATIC_DIR = Path(__file__).resolve().parent / "static"
USER_ID = os.getenv("GARCHI_USER", getpass.getuser())

STATE_TITLE = "title"
STATE_THUMB = "deck_thumbnail_url"
STATE_TF_PATH = "terraform_path"


def _session_service() -> BaseSessionService:
    url = os.getenv("SESSION_DB_URL")
    if url == "memory":
        return InMemorySessionService()
    if not url:
        db = Path(os.getenv("OUTPUT_DIR", PROJECT_DIR / "output")).expanduser() / "sessions.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{db}"
    return DatabaseSessionService(db_url=url)


session_service = _session_service()
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
app = FastAPI(title="gArchi workspace")
_locks: dict[str, asyncio.Lock] = {}


# ----------------------------------------------------------------------------- models
class CreateSession(BaseModel):
    title: str = Field(default="New engagement")
    brief: CustomerBrief | None = None


class ChatIn(BaseModel):
    message: str


# ----------------------------------------------------------------------------- helpers
def _deliverables(state: dict) -> dict:
    return {
        "presentation_url": state.get(STATE_PRESENTATION_URL),
        "presentation_id": state.get(STATE_PRESENTATION_ID),
        "deck_thumbnail_url": state.get(STATE_THUMB),
        "terraform_project": state.get(STATE_TF_PROJECT),
        "terraform_path": state.get(STATE_TF_PATH),
        "has_spec": bool(state.get(STATE_LAST_SPEC)),
    }


def _summarise_tool_result(name: str, resp: Any) -> dict:
    """Small, UI-friendly summary of a tool response."""
    out: dict[str, Any] = {"name": name}
    if not isinstance(resp, dict):
        return out
    if name == "render_architecture_deck":
        out.update(ok=resp.get("ok"), url=resp.get("url"), thumbnail=resp.get("thumbnail_url"),
                   warnings=resp.get("warnings"), error=resp.get("error") or resp.get("errors"))
    elif name in ("write_terraform_files", "lint_terraform_project", "scaffold_terraform_project"):
        lint = resp.get("lint") or []
        out.update(ok=resp.get("ok"), path=resp.get("path"), tree=resp.get("tree") or sorted((resp.get("files") or {}).keys()),
                   errors=sum(1 for f in lint if f.get("severity") == "error"),
                   warnings=sum(1 for f in lint if f.get("severity") == "warning"))
    elif name == "validate_architecture_spec":
        out.update(valid=resp.get("valid"), nodes=resp.get("nodes"), warnings=resp.get("warnings"))
    elif name == "search_gcp_docs":
        text = resp.get("result") or resp.get("output") or ""
        out.update(preview=str(text)[:240])
    elif name == "fetch_official_doc":
        out.update(title=resp.get("title"), url=resp.get("url"), error=resp.get("error"))
    elif name == "fetch_terraform_resource_doc":
        out.update(resource=resp.get("resource"), found=resp.get("found"), provider=resp.get("provider"))
    else:
        keys = list(resp.keys())[:6]
        out.update(keys=keys)
    return out


def _history(events) -> list[dict]:
    """Flatten ADK events into chat messages for the UI."""
    msgs: list[dict] = []
    for ev in events:
        if getattr(ev, "partial", False) or not ev.content or not ev.content.parts:
            continue
        role = "user" if ev.content.role == "user" else "agent"
        for part in ev.content.parts:
            if part.text:
                if role == "agent" and getattr(part, "thought", False):
                    continue
                msgs.append({"role": role, "type": "text", "text": part.text, "ts": ev.timestamp})
            elif part.function_call:
                msgs.append({"role": "agent", "type": "tool_call", "name": part.function_call.name,
                             "args": _short_args(part.function_call.args), "ts": ev.timestamp})
            elif part.function_response:
                msgs.append({"role": "agent", "type": "tool_result", "ts": ev.timestamp,
                             **_summarise_tool_result(part.function_response.name, part.function_response.response)})
    return msgs


def _short_args(args: Any) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        s = json.dumps(v, default=str) if not isinstance(v, str) else v
        out[k] = s if len(s) <= 160 else s[:157] + "…"
    return out


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _get(session_id: str):
    s = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


async def _update_state(session_id: str, delta: dict) -> None:
    """Persist a state change without a model turn (state_delta on a no-op invocation)."""
    from google.adk.events import Event, EventActions

    s = await _get(session_id)
    await session_service.append_event(s, Event(author="system", invocation_id=f"state-{int(time.time()*1000)}",
                                                actions=EventActions(state_delta=delta)))


# ----------------------------------------------------------------------------- routes
@app.get("/api/health")
async def health() -> dict:
    st = load_settings()
    return {"ok": True, "project": st.project, "model": st.architect_model, "icons": st.icons_enabled, "user": USER_ID}


@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    res = await session_service.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    out = []
    for s in sorted(res.sessions, key=lambda x: x.last_update_time or 0, reverse=True):
        full = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=s.id)
        state = (full.state if full else s.state) or {}
        brief = state.get(STATE_BRIEF) or {}
        out.append({"id": s.id, "title": state.get(STATE_TITLE) or brief.get("customer") or "Untitled",
                    "customer": brief.get("customer", ""), "updated": s.last_update_time,
                    "deliverables": _deliverables(state)})
    return out


@app.post("/api/sessions")
async def create_session(body: CreateSession) -> dict:
    state = {STATE_TITLE: body.title, STATE_BRIEF: (body.brief or CustomerBrief()).model_dump()}
    s = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, state=state)
    return {"id": s.id, "title": body.title}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    s = await _get(session_id)
    state = s.state or {}
    return {
        "id": s.id,
        "title": state.get(STATE_TITLE) or "Untitled",
        "brief": CustomerBrief.model_validate(state.get(STATE_BRIEF) or {}).model_dump(),
        "deliverables": _deliverables(state),
        "messages": _history(s.events),
    }


@app.put("/api/sessions/{session_id}/brief")
async def put_brief(session_id: str, brief: CustomerBrief) -> dict:
    delta = {STATE_BRIEF: brief.model_dump()}
    if brief.customer.strip():
        delta[STATE_TITLE] = brief.customer.strip()
    await _update_state(session_id, delta)
    return {"ok": True}


@app.put("/api/sessions/{session_id}/title")
async def put_title(session_id: str, body: dict) -> dict:
    await _update_state(session_id, {STATE_TITLE: str(body.get("title", "")).strip() or "Untitled"})
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    await session_service.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatIn) -> StreamingResponse:
    await _get(session_id)
    lock = _locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "the agent is still working on the previous message")

    async def stream() -> AsyncIterator[str]:
        async with lock:
            yield _sse("start", {"ts": time.time()})
            content = types.Content(role="user", parts=[types.Part(text=body.message)])
            cfg = RunConfig(streaming_mode=StreamingMode.SSE)
            errored = False
            try:
                async for ev in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=content, run_config=cfg):
                    for item in _events_to_sse(ev):
                        if item.startswith("event: error"):
                            errored = True
                        yield item
            except Exception as exc:  # noqa: BLE001
                log.exception("agent run failed")
                if not errored:
                    yield _sse("error", {"message": _explain_error(exc)})
            s = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
            yield _sse("state", _deliverables((s.state if s else {}) or {}))
            yield _sse("done", {"ts": time.time()})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _explain_error(exc: Exception) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    low = str(exc).lower()
    if "api key" in low or "credentials" in low or "default credentials" in low:
        msg += ("\n\nGemini is not configured. In gcp_architect/.env set GOOGLE_GENAI_USE_VERTEXAI=TRUE, "
                "GOOGLE_CLOUD_PROJECT=<your project>, GOOGLE_CLOUD_LOCATION=us-central1, then run "
                "`gcloud auth application-default login` (scripts/setup.sh does all of this).")
    return msg


def _events_to_sse(ev) -> list[str]:
    out: list[str] = []
    if ev.error_message:
        out.append(_sse("error", {"message": _explain_error(RuntimeError(ev.error_message)), "code": ev.error_code}))
    if not ev.content or not ev.content.parts:
        return out
    for part in ev.content.parts:
        if part.text:
            if getattr(part, "thought", False):
                out.append(_sse("thought", {"delta": part.text, "partial": bool(ev.partial)}))
            elif ev.partial:
                out.append(_sse("delta", {"text": part.text}))
            else:
                out.append(_sse("message", {"text": part.text, "author": ev.author}))
        elif part.function_call:
            out.append(_sse("tool_call", {"name": part.function_call.name, "args": _short_args(part.function_call.args),
                                          "id": part.function_call.id}))
        elif part.function_response:
            summary = _summarise_tool_result(part.function_response.name, part.function_response.response)
            out.append(_sse("tool_result", summary))
            if part.function_response.name == "render_architecture_deck" and isinstance(part.function_response.response, dict):
                r = part.function_response.response
                if r.get("thumbnail_url") or r.get("url"):
                    out.append(_sse("deliverable", {"deck_thumbnail_url": r.get("thumbnail_url"), "presentation_url": r.get("url")}))
            if part.function_response.name in ("write_terraform_files", "scaffold_terraform_project") and isinstance(part.function_response.response, dict):
                r = part.function_response.response
                if r.get("path"):
                    out.append(_sse("deliverable", {"terraform_path": r.get("path"), "terraform_project": r.get("project")}))
    return out


# Persist deliverable extras (thumbnail, tf path) the tools do not store themselves.
@app.post("/api/sessions/{session_id}/deliverables")
async def put_deliverables(session_id: str, body: dict) -> dict:
    delta = {k: v for k, v in body.items() if k in (STATE_THUMB, STATE_TF_PATH) and v}
    if delta:
        await _update_state(session_id, delta)
    return {"ok": True}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
