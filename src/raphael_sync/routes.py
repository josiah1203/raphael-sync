"""Sync API — desktop agent coordination."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["sync"])

_state = {"status": "synced", "queued": 0, "last_sync": datetime.now(timezone.utc).isoformat()}


@router.get("")
def sync_status() -> dict:
    return {"service": "raphael-sync", "status": _state["status"], "queued_events": _state["queued"], "last_sync": _state["last_sync"]}


@router.post("/push")
def push_events(body: dict) -> dict:
    events = body.get("events", [])
    _state["queued"] = max(0, _state["queued"] - len(events))
    _state["last_sync"] = datetime.now(timezone.utc).isoformat()
    _state["status"] = "synced"
    return {"accepted": len(events), "status": "synced"}


@router.get("/agent")
def agent_config() -> dict:
    return {
        "agent_url": os.environ.get("RAPHAEL_SYNC_AGENT_URL", "http://127.0.0.1:8765"),
        "poll_interval_ms": 5000,
    }


@router.post("/sessions/{session_id}/commit")
def commit_session(session_id: str, body: dict | None = None) -> dict:
    return {
        "session_id": session_id,
        "status": "committed",
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "events_accepted": len((body or {}).get("events", [])),
    }
