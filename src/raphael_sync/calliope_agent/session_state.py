"""Persist partial session state for crash recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from raphael_audit.core.paths import calliope_home


class SessionState:
    """File-backed shadow models and session metadata."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (calliope_home() / "session_state.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"session_id": None, "shadows": {}, "pending_diffs": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @property
    def session_id(self) -> str | None:
        return self._data.get("session_id")

    def set_session_id(self, session_id: str) -> None:
        self._data["session_id"] = session_id
        self.save()

    def get_shadow(self, document_id: str) -> dict[str, Any] | None:
        return self._data.get("shadows", {}).get(document_id)

    def set_shadow(self, document_id: str, snapshot: dict[str, Any]) -> None:
        self._data.setdefault("shadows", {})[document_id] = snapshot
        self.save()

    def clear_shadow(self, document_id: str) -> None:
        shadows = self._data.setdefault("shadows", {})
        shadows.pop(document_id, None)
        self.save()

    def enqueue_pending_diff(self, item: dict[str, Any]) -> None:
        self._data.setdefault("pending_diffs", []).append(item)
        self.save()

    def pop_pending_diffs(self) -> list[dict[str, Any]]:
        pending = list(self._data.get("pending_diffs", []))
        self._data["pending_diffs"] = []
        self.save()
        return pending
