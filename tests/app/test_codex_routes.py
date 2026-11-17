"""Public HTTP contract: distinct browser ownership and scoped agent credentials."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from studynavigator.config import Settings
from studynavigator.main import create_app
from test_project_workspace import NoModel, create_project, plan_body


@pytest.fixture
def clients(tmp_path):
    config = Settings(database_path=tmp_path / "db.sqlite", rate_limit_per_minute=1000)
    app = create_app(settings=config, provider=NoModel())
    with TestClient(app, base_url="https://navigator.test") as owner, \
         TestClient(app, base_url="https://navigator.test") as stranger:
        project = create_project(owner)
        project_id = project["assignment_id"]
        assert owner.put(f"/api/assignments/{project_id}/plan", json=plan_body(project)).status_code == 200
        yield config, owner, stranger, project


def connection(owner, project):
    root = f"/api/assignments/{project['assignment_id']}/codex"
    response = owner.post(root + "/connections", json={"label": "My laptop"})
    assert response.status_code == 201, response.text
    return root, response.json()


def test_http_lifecycle_and_scope(clients):
    config, owner, stranger, project = clients
    root, issued = connection(owner, project)
    auth = {"Authorization": "Bearer " + issued["token"]}
    assert owner.get("/api/codex/project").status_code == 401  # Cookie does not authorize tools.
    assert stranger.get(root + "/connections", headers=auth).status_code == 404  # Token does not authorize owner UI.
    assert stranger.post(root + "/connections", json={"label": "stolen"}).status_code == 404
    fetched = stranger.get("/api/codex/project", headers=auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["cache-control"] == "private, no-store"
    assert fetched.json()["assignment"]["assignment_id"] == project["assignment_id"]
    assert issued["token"] not in fetched.text
    assert issued["token"] not in owner.get(root + "/connections").text
    with sqlite3.connect(config.database_path) as db:
        assert issued["token"] not in str(db.execute("SELECT * FROM codex_connections").fetchall())
    assert stranger.post("/api/codex/search", headers=auth, json={"query": "workshop"}).json()["matches"]
    claim = {"task_id": "draft", "card_revision": project["card_revision"],
             "plan_revision": 1, "request_id": "claim-1"}
    response = stranger.post("/api/codex/claim", headers=auth, json=claim)
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["status"] == "in_progress"
    report = {"run_id": run["id"], "status": "awaiting_review", "summary": "Slides created",
              "evidence": ["slides.md", "Reviewed required topics"], "request_id": "report-1"}
    assert stranger.post("/api/codex/report", headers=auth, json=report).status_code == 200
    assert stranger.post("/api/codex/report", headers=auth, json=report).status_code == 200
    assert owner.get(root + "/runs").json()["runs"][0]["status"] == "awaiting_review"
    plan_url = f"/api/assignments/{project['assignment_id']}/plan"
    assert not owner.get(plan_url).json()["plan"]["input"]["tasks"][0]["completed"]
    accept = {"card_revision": project["card_revision"], "plan_revision": 1}
    assert stranger.post(root + f"/runs/{run['id']}/accept", headers=auth, json=accept).status_code == 404
    result = owner.post(root + f"/runs/{run['id']}/accept", json=accept)
    assert result.status_code == 200, result.text
    assert result.json()["plan"]["input"]["tasks"][0]["completed"]
    assert owner.get(plan_url).json()["plan"]["revision"] == 2
    assert owner.delete(root + f"/connections/{issued['connection']['id']}").status_code == 200
    revoked = stranger.get("/api/codex/project", headers=auth)
    assert revoked.status_code == 401
    assert revoked.headers["www-authenticate"].startswith("Bearer")


def test_strict_transport_arguments_and_origin(clients):
    _, owner, stranger, project = clients
    root, issued = connection(owner, project)
    auth = {"Authorization": "Bearer " + issued["token"]}
    for body in ({"label": "X", "workspace_id": "other"}, {"label": "X", "expires_days": True},
                 {"label": "X", "expires_days": 31}):
        assert owner.post(root + "/connections", json=body).status_code == 422
    assert owner.post(root + "/connections", json={"label": "X"},
                      headers={"Origin": "https://attacker.test"}).status_code == 403
    assert stranger.post("/api/codex/search", headers=auth,
                         json={"query": "workshop", "assignment_id": "personal-project"}).status_code == 422
    assert stranger.post("/api/codex/report", headers=auth, json={"run_id": "x", "status": "completed",
           "summary": "done", "evidence": [], "request_id": "x"}).status_code == 422
    assert stranger.get("http://navigator.test/api/codex/project", headers=auth).status_code == 403
    assert stranger.get("http://navigator.test/api/codex/project",
                        headers={**auth, "Host": "localhost"}).status_code == 403
    assert owner.post("http://navigator.test" + root + "/connections", json={"label": "X"}).status_code == 403
    assert stranger.get("/api/codex/project", params={"token": issued["token"]}).status_code == 401


def test_revoked_and_expired_tokens_cannot_write_or_recover_owner(clients):
    config, owner, stranger, project = clients
    root, issued = connection(owner, project)
    with sqlite3.connect(config.database_path) as db:
        db.execute("UPDATE codex_connections SET expires_at='2000-01-01' WHERE id=?",
                   (issued["connection"]["id"],))
    auth = {"Authorization": "Bearer " + issued["token"]}
    response = stranger.post("/api/codex/claim", headers=auth, json={"task_id": "draft",
        "card_revision": project["card_revision"], "plan_revision": 1, "request_id": "x"})
    assert response.status_code == 401
    assert stranger.get(root + "/runs", headers=auth).status_code == 404


def test_bearer_rate_limit_is_bounded_and_has_retry_header(clients):
    _, owner, stranger, project = clients
    _, issued = connection(owner, project)
    auth = {"Authorization": "Bearer " + issued["token"]}
    for _ in range(90):
        assert stranger.get("/api/codex/project", headers=auth).status_code == 200
    limited = stranger.get("/api/codex/project", headers=auth)
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"


def test_project_deletion_invalidates_agent_access(clients):
    config, owner, stranger, project = clients
    _, issued = connection(owner, project)
    auth = {"Authorization": "Bearer " + issued["token"]}
    claimed = stranger.post("/api/codex/claim", headers=auth, json={"task_id": "draft",
        "card_revision": project["card_revision"], "plan_revision": 1, "request_id": "delete-claim"})
    assert claimed.status_code == 200
    reported = stranger.post("/api/codex/report", headers=auth, json={
        "run_id": claimed.json()["run"]["id"], "status": "blocked", "summary": "Needs review",
        "evidence": [], "request_id": "delete-report"})
    assert reported.status_code == 200
    assert owner.delete(f"/api/assignments/{project['assignment_id']}").status_code == 204
    assert stranger.get("/api/codex/project", headers=auth).status_code == 401
    with sqlite3.connect(config.database_path) as db:
        for table in ("codex_connections", "codex_runs", "codex_report_requests"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
