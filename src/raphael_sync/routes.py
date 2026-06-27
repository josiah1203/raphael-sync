"""Sync API — desktop agent coordination."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter

from raphael_sync.store import SyncStore

router = APIRouter(tags=["sync"])
_store = SyncStore()


@router.get("")
def sync_status() -> dict:
    state = _store.get_status()
    return {
        "service": "raphael-sync",
        "status": state["status"],
        "queued_events": state["queued_events"],
        "last_sync": state["last_sync"],
    }


@router.post("/push")
def push_events(body: dict) -> dict:
    events = body.get("events", [])
    result = _store.push_events(events)
    return {"accepted": result["accepted"], "status": result["status"]}


@router.get("/agent")
def agent_config() -> dict:
    return {
        "agent_url": os.environ.get("RAPHAEL_SYNC_AGENT_URL", "http://127.0.0.1:8765"),
        "poll_interval_ms": 5000,
    }


@router.post("/sessions/{session_id}/commit")
def commit_session(session_id: str, body: dict | None = None) -> dict:
    events = (body or {}).get("events", [])
    return _store.commit_session(session_id, events)
