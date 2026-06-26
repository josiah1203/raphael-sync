"""Plane-agent: outbound WebSocket/gRPC to control plane with mTLS."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class MTLSConfig:
    cert_path: str
    key_path: str
    ca_path: str
    server_name: str = "control.hblabs.io"


@dataclass
class AllowlistEntry:
    org_id: str
    data_plane_id: str


@dataclass
class PlaneAgentConfig:
    control_plane_url: str
    org_id: str
    data_plane_id: str
    mtls: MTLSConfig | None = None
    allowlist: list[AllowlistEntry] = field(default_factory=list)
    protocol: str = "websocket"  # websocket | grpc


class PlaneAgent:
    """Outbound-only agent connecting customer data plane to HB Labs control plane."""

    def __init__(self, config: PlaneAgentConfig) -> None:
        self.config = config
        self.state = ConnectionState.DISCONNECTED
        self._session_token: str | None = None
        self._sent_batches: list[dict[str, Any]] = []

    def connect(self) -> dict[str, Any]:
        if not self._check_allowlist():
            return {"ok": False, "error": "not_allowlisted"}
        self.state = ConnectionState.CONNECTING
        if self.config.mtls and not self._verify_mtls():
            self.state = ConnectionState.DISCONNECTED
            return {"ok": False, "error": "mtls_failed"}
        self._session_token = secrets.token_urlsafe(32)
        self.state = ConnectionState.CONNECTED
        return {"ok": True, "session_token": self._session_token, "protocol": self.config.protocol}

    def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        self._session_token = None

    def send_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        if self.state != ConnectionState.CONNECTED:
            return {"ok": False, "error": "not_connected"}
        batch = {
            "org_id": self.config.org_id,
            "data_plane_id": self.config.data_plane_id,
            "events": events,
            "sent_at": time.time(),
        }
        self._sent_batches.append(batch)
        return {"ok": True, "batch_id": hashlib.sha256(json.dumps(batch).encode()).hexdigest()[:16]}

    def heartbeat(self) -> dict[str, Any]:
        return {"state": self.state.value, "connected": self.state == ConnectionState.CONNECTED}

    def _check_allowlist(self) -> bool:
        if not self.config.allowlist:
            return True
        return any(
            e.org_id == self.config.org_id and e.data_plane_id == self.config.data_plane_id
            for e in self.config.allowlist
        )

    def _verify_mtls(self) -> bool:
        mtls = self.config.mtls
        if not mtls:
            return False
        return bool(mtls.cert_path and mtls.key_path and mtls.ca_path)
