"""API routes for raphael-sync."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["raphael-sync"])


@router.get("")
def list_root() -> dict[str, str]:
  return {"service": "raphael-sync", "status": "stub"}
