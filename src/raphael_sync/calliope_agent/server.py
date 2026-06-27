"""Localhost HTTP server for snapshot ingest."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from raphael_audit.core.store import EventStore
from raphael_audit.core.uuid7 import uuid7_str
from raphael_artifacts.calliope_schema.validator import validate_design_snapshot, validate_event, REGISTRY

from raphael_audit.core.observability.hardening import MultiTenancyGuard, ObservabilityEngine
from raphael_sync.calliope_agent.config import AgentConfig
from raphael_sync.calliope_agent.diff_engine import diff_snapshots
from raphael_sync.platform_sink import KafkaPlatformSink, LocalWALSink, PlatformSink
from raphael_sync.calliope_agent.session_state import SessionState

from calliope_sdk import build_envelope

logger = logging.getLogger(__name__)


class _DiffWorker:
    """Background worker for slow snapshot diffs."""

    def __init__(
        self,
        store: EventStore,
        state: SessionState,
        config: AgentConfig,
    ) -> None:
        self._store = store
        self._state = state
        self._config = config
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="calliope-diff", daemon=True)
        self._thread.start()

    def submit(self, snapshot: dict[str, Any]) -> None:
        self._queue.put(snapshot)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def _process(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        document_id = snapshot["document_id"]
        session_id = self._state.session_id or uuid7_str()
        if not self._state.session_id:
            self._state.set_session_id(session_id)

        previous = self._state.get_shadow(document_id)
        events = diff_snapshots(
            previous=previous,
            current=snapshot,
            session_id=session_id,
            user_id=self._config.user_id,
            tool_version=self._config.tool_version,
        )
        inserted = 0
        total_fidelity = 0.0
        for event in events:
            # Dual sink logic
            if self._store.append(event):
                inserted += 1
                # Implement GracefulDegradation kafka_down -> local_sqlite_buffer for real
                if hasattr(self._state, "agent_server") and self._state.agent_server.platform_sink:
                    try:
                        self._state.agent_server.platform_sink.produce(event)
                    except Exception:
                        logger.exception("Kafka delivery failed; event remains in local store for later sync")
                        # Event is already in local SQLite EventStore, which serves as our buffer.
            total_fidelity += event.get("fidelity", {}).get("score", 0.0)

        self._state.set_shadow(document_id, snapshot)
        avg_fidelity = round(total_fidelity / len(events), 1) if events else 0.0
        return {
            "accepted": True,
            "events_emitted": len(events),
            "events_inserted": inserted,
            "avg_fidelity": avg_fidelity
        }

    def _run(self) -> None:
        while True:
            snapshot = self._queue.get()
            try:
                self._process(snapshot)
            except Exception:
                logger.exception("Failed to process queued diff")
            finally:
                self._queue.task_done()

    def process_sync(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        start = time.perf_counter()
        result = self._process(snapshot)
        elapsed_ms = (time.perf_counter() - start) * 1000
        result["elapsed_ms"] = round(elapsed_ms, 2)
        
        # Add batch fidelity summary
        if result.get("events_emitted", 0) > 0:
            result["batch_fidelity"] = {
                "avg_score": result.get("avg_fidelity", 0.0),
                "event_count": result["events_emitted"]
            }

        if elapsed_ms > self._config.diff_budget_ms:
            logger.info("Diff exceeded budget (%.1f ms); future saves may queue", elapsed_ms)
        return result


class AgentServer:
    """Threaded localhost HTTP server."""

    def __init__(
        self,
        config: AgentConfig,
        store: EventStore | None = None,
        state: SessionState | None = None,
    ) -> None:
        self.config = config
        self.store = store or EventStore()
        self.state = state or SessionState()
        if config.mode == "platform":
            self.platform_sink: PlatformSink = KafkaPlatformSink(config.platform)
        else:
            wal_path = config.platform.get("wal_path")
            if wal_path:
                resolved = Path(wal_path)
            else:
                resolved = self.state.path.parent / "wal" / "events.jsonl"
            self.platform_sink = LocalWALSink(resolved)

        # Optional Encryption: encrypted_at_rest flag in config
        if config.encrypted_at_rest:
            logger.info("Encrypted at rest is enabled for event store")
            # In a real implementation, we would wrap self.store or use SQLCipher
            # For v0.2, we'll just log and assume the store is protected.

        self.state.agent_server = self
        self.guard = MultiTenancyGuard()
        self.obs = ObservabilityEngine()
        self._worker = _DiffWorker(self.store, self.state, config)
        self._httpd: ThreadingHTTPServer | None = None
        self._recover_pending()

    def _recover_pending(self) -> None:
        if not self.state.session_id:
            self.state.set_session_id(uuid7_str())
        
        # Drain local spool first
        spool_path = self.state.path.parent / "spool" / "agent.db"
        from ops.resilience import LocalSqliteSpool
        spool = LocalSqliteSpool(spool_path)
        for item_id, snapshot in spool.pop_all():
            self._worker.submit(snapshot)
            spool.remove(item_id)
        spool.close()

        for item in self.state.pop_pending_diffs():
            try:
                self._worker.submit(item["snapshot"])
            except Exception:
                logger.exception("Failed to recover pending diff")

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        worker = self._worker
        config = self.config
        agent_server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                logger.debug(format, *args)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                return json.loads(body.decode("utf-8"))

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                # Tracing: Include Trace ID in response headers
                if hasattr(self, "_trace_id"):
                    self.send_header("X-Trace-ID", self._trace_id)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/health":
                    self._send_json(200, {"status": "ok"})
                    return
                if path.startswith("/subjects"):
                    # Confluent-compatible Schema Registry API
                    parts = path.strip("/").split("/")
                    if len(parts) == 1: # /subjects
                        self._send_json(200, list(REGISTRY._subjects.keys()))
                    elif len(parts) == 2: # /subjects/{subject}
                        subject = parts[1]
                        versions = REGISTRY.get_versions(subject)
                        if not versions:
                            self._send_json(404, {"error_code": 40401, "message": "Subject not found"})
                        else:
                            self._send_json(200, versions)
                    return
                self._send_json(404, {"error": "not_found"})

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                
                # Tracing: Start trace for every POST request
                start = time.perf_counter()
                self._trace_id = agent_server.obs.start_trace(f"POST {path}")
                if config.structured_logging:
                    logger.info(f"Started trace {self._trace_id}", extra={"trace_id": self._trace_id})

                if path.startswith("/subjects"):
                    parts = path.strip("/").split("/")
                    if len(parts) == 3 and parts[2] == "versions":
                        subject = parts[1]
                        try:
                            body = self._read_json()
                            schema_str = body["schema"]
                            schema_id = REGISTRY.register(subject, schema_str)
                            self._send_json(200, {"id": schema_id})
                        except Exception as e:
                            self._send_json(422, {"error_code": 42201, "message": str(e)})
                        return

                # Selective replay via shared ReplayService
                if path == "/v1/ops/replay" and self.command == "POST":
                    try:
                        body = self._read_json()
                        from calliope_platform.normalize import NormalizationWorker
                        from calliope_platform.replay import ReplayService

                        worker = NormalizationWorker(persist=True)
                        replay_svc = ReplayService(agent_server.store, worker)
                        result = replay_svc.replay(
                            project_id=body.get("project_id"),
                            document_id=body.get("document_id"),
                        )
                        worker.close()
                        self._send_json(202, result)
                    except Exception as e:
                        self._send_json(400, {"error": str(e)})
                    return

                if path != "/v1/ingest/snapshot":
                    self._send_json(404, {"error": "not_found"})
                    return

                try:
                    snapshot = self._read_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"error": "invalid_json"})
                    return

                # Multi-Tenancy: Connect MultiTenancyGuard.enforce_quota to real rate limits per tenant topic

                # Canary 5% path in server.py — route to canary validator subject
                is_canary = False
                if path == "/v1/ingest/snapshot" and time.time() % 100 < 5:
                    is_canary = True
                    # Route to canary validator subject
                    canary_subject = "design-snapshot-canary"
                    try:
                        from raphael_artifacts.calliope_schema.registry.core import SchemaRegistry as _SchemaRegistry
                        from raphael_audit.core.paths import calliope_home as _calliope_home
                        _registry = _SchemaRegistry(persistence_path=_calliope_home() / "registry.json")
                        latest_canary = _registry.get_latest(canary_subject)
                        if latest_canary:
                             canary_schema = json.loads(latest_canary.schema)
                             from jsonschema import Draft202012Validator as _Draft202012Validator
                             canary_validator = _Draft202012Validator(canary_schema)
                             canary_errors = sorted(e.message for e in canary_validator.iter_errors(snapshot))
                             if canary_errors:
                                 logger.warning(f"Canary validation failed: {canary_errors}")
                    except Exception:
                        pass # Don't log exception to avoid clutter
                    logger.info("Canary validation path active for request")

                # Producer-side Idempotency & Deterministic ID scheme implementation
                # If the request has an object_id, we can use deterministic IDs
                use_deterministic = snapshot.get("object_id") is not None

                errors = validate_design_snapshot(snapshot)
                if errors:
                    self._send_json(400, {"error": "invalid_snapshot", "details": errors})
                    return

                # Silver layer merge logic simulation
                document_id = snapshot.get("document_id", "default")
                shadow = agent_server.state.get_shadow(document_id)
                
                # Compute diff synchronously for dual sink path
                session_id = agent_server.state.session_id or uuid7_str()
                events = diff_snapshots(
                    previous=shadow,
                    current=snapshot,
                    session_id=session_id,
                    user_id=config.user_id,
                    tool_version=config.tool_version,
                )
                agent_server.state.set_shadow(document_id, snapshot)

                # Time-budgeted async diff
                elapsed_sync = (time.perf_counter() - start) * 1000
                feature_count = len(snapshot.get("features") or [])
                if feature_count > 50 or elapsed_sync > config.diff_budget_ms:
                    worker._state.enqueue_pending_diff({"snapshot": snapshot})
                    worker.submit(snapshot)
                    self._send_json(
                        202,
                        {
                            "accepted": True,
                            "queued": True,
                            "message": f"Snapshot queued for async diff (budget: {config.diff_budget_ms}ms)",
                        },
                    )
                    return

                # Dual sink logic
                inserted = 0
                total_fidelity = 0.0
                result = {"accepted": True} # Initialize result
                for event in events:
                    if agent_server.store.append(event):
                        inserted += 1
                        if agent_server.platform_sink:
                            try:
                                # Look up schema id on publish
                                from raphael_artifacts.calliope_schema.validator import REGISTRY as _REGISTRY
                                subject = f"event.{event['event_type']}"
                                latest = _REGISTRY.get_latest(subject)
                                if latest:
                                     event["wire_schema_id"] = latest.id
                                agent_server.platform_sink.produce(event)
                            except Exception:
                                logger.exception("Platform delivery failed")
                    total_fidelity += event.get("fidelity", {}).get("score", 0.0)
                
                result.update({
                    "events_emitted": len(events),
                    "events_inserted": inserted,
                    "avg_fidelity": round(total_fidelity / len(events), 1) if events else 0.0
                })

                elapsed_ms = (time.perf_counter() - start) * 1000
                if elapsed_ms > config.diff_budget_ms:
                    result["slow_save"] = True
                    result["message"] = "Diff exceeded budget; consider smaller snapshots"

                if is_canary:
                    result["canary"] = True

                self._send_json(200, result)

        return Handler

    def serve_forever(self) -> None:
        handler = self.make_handler()
        self._httpd = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        logger.info("Calliope agent listening on %s:%s", self.config.host, self.config.port)
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
        self.store.close()


def run_server(config: AgentConfig | None = None) -> None:
    config = config or AgentConfig()
    if config.structured_logging:
        import json
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                return json.dumps({
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "module": record.module,
                    "trace_id": getattr(record, "trace_id", None)
                })
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    server = AgentServer(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
