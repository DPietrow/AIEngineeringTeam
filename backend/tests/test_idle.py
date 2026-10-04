from datetime import UTC, datetime, timedelta

import pytest

from agentteam.api import activity_file, idle_report
from agentteam.app import create_app
from agentteam.db import connect

SECRET = "s" * 48
PASSWORD = "correct horse battery staple"


def set_status(db_path, run_id, status, updated_at):
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at.isoformat(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_empty_database_reports_no_activity(tracer, db_path):
    report = idle_report(str(db_path))
    assert report == {"active_runs": 0, "awaiting_approval": 0, "idle_minutes": None}


def test_active_waiting_and_finished_runs_are_told_apart(tracer, db_path):
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    running = tracer.create_run("a", config_hash="h")
    waiting = tracer.create_run("b", config_hash="h")
    done = tracer.create_run("c", config_hash="h")
    set_status(db_path, running, "running", now - timedelta(minutes=5))
    set_status(db_path, waiting, "awaiting_approval", now - timedelta(minutes=30))
    set_status(db_path, done, "done", now - timedelta(minutes=90))
    report = idle_report(str(db_path), now=now)
    assert report["active_runs"] == 1
    assert report["awaiting_approval"] == 1
    assert report["idle_minutes"] == 5.0  # measured from the newest run update


def test_recent_api_use_resets_the_idle_clock(tracer, db_path):
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    run_id = tracer.create_run("a", config_hash="h")
    set_status(db_path, run_id, "done", now - timedelta(hours=3))
    activity_file(str(db_path)).touch()
    # The file was touched "now" in wall-clock time, so against a later clock it is older.
    later = datetime.now(UTC) + timedelta(minutes=10)
    assert 9.5 <= idle_report(str(db_path), now=later)["idle_minutes"] <= 10.5


@pytest.fixture
def secured(settings):
    return create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": settings.database_path,
            "API_PASSWORD": PASSWORD,
            "JWT_SECRET": SECRET,
        }
    )


def test_idle_endpoint_needs_a_token(secured):
    c = secured.test_client()
    assert c.get("/api/idle").status_code == 401
    token = c.post("/api/login", json={"password": PASSWORD}).get_json()["token"]
    resp = c.get("/api/idle", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert set(resp.get_json()) == {"active_runs", "awaiting_approval", "idle_minutes"}


def test_the_idle_probe_does_not_count_as_use_but_dashboard_calls_do(secured, settings):
    """If logging in and polling /api/idle reset the clock, the server could never look idle."""
    c = secured.test_client()
    marker = activity_file(settings.database_path)
    assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
    token = c.post("/api/login", json={"password": PASSWORD}).get_json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert c.get("/api/idle", headers=auth).status_code == 200
    assert c.get("/health").status_code == 200
    assert not marker.exists()
    assert c.get("/api/runs", headers=auth).status_code == 200  # a real dashboard request
    assert marker.exists()


def test_unauthenticated_requests_do_not_count_as_use(secured, settings):
    c = secured.test_client()
    assert c.get("/api/runs").status_code == 401
    assert not activity_file(settings.database_path).exists()
