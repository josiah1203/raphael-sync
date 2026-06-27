"""LocalWALSink tests."""

import json
import tempfile
from pathlib import Path

from raphael_sync.platform_sink import LocalWALSink


def test_local_wal_sink_append_and_flush() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wal_path = Path(tmp) / "events.jsonl"
        sink = LocalWALSink(wal_path)
        event_a = {"event_id": "ev-1", "event_type": "design.save", "payload": {"doc": "a"}}
        event_b = {"event_id": "ev-2", "event_type": "design.save", "payload": {"doc": "b"}}
        sink.produce(event_a)
        sink.produce(event_b)

        assert wal_path.exists()
        lines = wal_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_id"] == "ev-1"

        flushed = sink.flush()
        assert len(flushed) == 2
        assert flushed[1]["event_id"] == "ev-2"
        assert not wal_path.exists()
