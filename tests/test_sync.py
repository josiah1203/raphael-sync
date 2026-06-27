"""Sync domain tests."""

from fastapi.testclient import TestClient

from raphael_sync.app import app


def test_sync_status() -> None:
    client = TestClient(app)
    res = client.get("/v1/sync")
    assert res.status_code == 200
    body = res.json()
    assert body["service"] == "raphael-sync"
    assert "status" in body
    assert "queued_events" in body
    assert "last_sync" in body


def test_push_and_commit_roundtrip() -> None:
    from raphael_sync import routes

    client = TestClient(app)

    push = client.post("/v1/sync/push", json={"events": [{"id": "e1"}, {"id": "e2"}]})
    assert push.status_code == 200
    assert push.json()["accepted"] == 2
    assert push.json()["status"] == "synced"

    commit = client.post(
        "/v1/sync/sessions/sess-abc/commit",
        json={"events": [{"id": "e1"}, {"id": "e2"}]},
    )
    assert commit.status_code == 200
    body = commit.json()
    assert body["session_id"] == "sess-abc"
    assert body["status"] == "committed"
    assert body["events_accepted"] == 2
    assert "committed_at" in body

    session = routes._store.get_session("sess-abc")
    assert session is not None
    assert session["status"] == "committed"
    assert session["events_accepted"] == 2


def test_agent_config() -> None:
    client = TestClient(app)
    res = client.get("/v1/sync/agent")
    assert res.status_code == 200
    body = res.json()
    assert "agent_url" in body
    assert body["poll_interval_ms"] == 5000
