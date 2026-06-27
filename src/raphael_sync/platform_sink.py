"""Platform event delivery sinks for sync agent."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class PlatformSink(ABC):
    """Interface for platform event delivery."""

    @abstractmethod
    def produce(self, event: dict[str, Any]) -> None:
        """Deliver a platform event to the configured sink."""


class KafkaPlatformSink(PlatformSink):
    def __init__(self, config: dict[str, Any]) -> None:
        from calliope_sdk import PlatformProducer

        self._producer = PlatformProducer(config)

    def produce(self, event: dict[str, Any]) -> None:
        self._producer.produce(event)


class LocalWALSink(PlatformSink):
    """File-backed WAL for offline / local-mode event buffering."""

    def __init__(self, wal_path: Path) -> None:
        self._wal_path = wal_path
        self._wal_path.parent.mkdir(parents=True, exist_ok=True)

    def produce(self, event: dict[str, Any]) -> None:
        with self._wal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
            handle.write("\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self._wal_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self._wal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    def flush(self) -> list[dict[str, Any]]:
        events = self.read_all()
        if self._wal_path.exists():
            self._wal_path.unlink()
        return events
