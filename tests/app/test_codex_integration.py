"""Full local stdio → HTTP → SQLite lifecycle, with a provider that forbids LLM calls."""
import os
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from studynavigator.config import Settings
from studynavigator.main import create_app
from test_project_workspace import NoModel, create_project, plan_body

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def local_site(tmp_path):
    app = create_app(settings=Settings(database_path=tmp_path / "integration.sqlite",
        rate_limit_per_minute=1000), provider=NoModel())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                pytest.fail("Local fixture server did not start")
            time.sleep(0.01)
        with httpx.Client(base_url=url, trust_env=False, timeout=15) as owner:
            yield url, owner
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.asyncio
async def test_stdio_http_sqlite_review_and_revocation(local_site):
    url, owner = local_site
    project = create_project(owner)
    project_id = project["assignment_id"]
    plan_url = f"/api/assignments/{project_id}/plan"
    root = f"/api/assignments/{project_id}/codex"
    response = owner.put(plan_url, json=plan_body(project))
    assert response.status_code == 200, response.text
    issued_response = owner.post(root + "/connections", json={"label": "stdio integration"})
    assert issued_response.status_code == 201, issued_response.text
    issued = issued_response.json()
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "studynavigator.codex_bridge"], cwd=ROOT,
        env={**os.environ, "NAVIGATOR_URL": url, "NAVIGATOR_TOKEN": issued["token"]})
    async with Client(params, cache=None) as client:
        project_result = await client.call_tool("get_project", {})
        assert not project_result.is_error
        assert project_result.structured_content["assignment"]["assignment_id"] == project_id
        assert project_result.structured_content["plan"]["revision"] == 1
        search = await client.call_tool("search_materials", {"query": "workshop", "limit": 3})
        assert not search.is_error and search.structured_content["matches"]
        claimed = await client.call_tool("claim_task", {"task_id": "draft",
            "card_revision": project["card_revision"], "plan_revision": 1, "request_id": "integration-claim"})
        assert not claimed.is_error
        run = claimed.structured_content["run"]
        assert run["status"] == "in_progress"
        report = await client.call_tool("report_task", {"run_id": run["id"],
            "status": "awaiting_review", "summary": "Local lifecycle verified",
            "evidence": ["Real stdio MCP, local HTTP, isolated SQLite; no model invoked"],
            "request_id": "integration-report"})
        assert not report.is_error
        assert report.structured_content["run"]["status"] == "awaiting_review"
        assert issued["token"] not in str(project_result) + str(claimed) + str(report)
        assert not owner.get(plan_url).json()["plan"]["input"]["tasks"][0]["completed"]
        accepted = owner.post(root + f"/runs/{run['id']}/accept", json={
            "card_revision": project["card_revision"], "plan_revision": 1})
        assert accepted.status_code == 200, accepted.text
        persisted = owner.get(plan_url).json()["plan"]
        assert persisted["revision"] == 2
        assert persisted["input"]["tasks"][0]["completed"] is True
        assert owner.get(root + "/runs").json()["runs"][0]["status"] == "accepted"
        revoked = owner.delete(root + f"/connections/{issued['connection']['id']}")
        assert revoked.status_code == 200
        denied = await client.call_tool("get_project", {})
        assert denied.is_error and "navigator_http_401" in str(denied)
        assert issued["token"] not in str(denied)
