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


@pytest.mark.asyncio
async def test_stdio_browser_plan_artifact_and_owner_correction(local_site, tmp_path):
    """Opt-in real Chrome: no paid model and no existing browser profile/data.

    Run with NAVIGATOR_PLAYWRIGHT_MODULE pointing to an installed Playwright
    module (or 'playwright'); optional NAVIGATOR_CHROMIUM_PATH selects Chrome.
    The fixture starts its own HTTP server and disposable database, including
    when run on a host/VPS. It never connects to the production workspace.
    """
    import asyncio
    import json

    if not os.environ.get("NAVIGATOR_PLAYWRIGHT_MODULE"):
        pytest.skip("Real browser requires explicit NAVIGATOR_PLAYWRIGHT_MODULE")
    url, owner = local_site
    project = create_project(owner)
    project_id = project["assignment_id"]
    base = f"/api/assignments/{project_id}"
    assert owner.put(base + "/plan", json=plan_body(project)).status_code == 200
    browser = await asyncio.create_subprocess_exec("node", str(ROOT / "tests/browser/codex-session.cjs"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=os.environ.copy())

    async def ui(operation, **payload):
        browser.stdin.write((json.dumps({"op": operation, **payload}) + "\n").encode())
        await browser.stdin.drain()
        raw = await asyncio.wait_for(browser.stdout.readline(), timeout=35)
        assert raw, "Browser companion stopped unexpectedly"
        result = json.loads(raw)
        assert result.get("ok"), result.get("error", "Browser operation failed")
        return result

    def params(token):
        return StdioServerParameters(command=sys.executable,
            args=["-m", "studynavigator.codex_bridge"], cwd=ROOT,
            env={**os.environ, "NAVIGATOR_URL": url, "NAVIGATOR_TOKEN": token})

    async def call(client, name, arguments):
        result = await client.call_tool(name, arguments)
        assert not result.is_error, str(result)
        return result.structured_content

    try:
        await ui("open", url=url, cookies=[{"name":cookie.name,"value":cookie.value}
                                         for cookie in owner.cookies.jar])
        # An explicitly read-only credential is enforced server-side.
        readonly = owner.post(base + "/codex/connections", json={"label":"read-only smoke","scopes":["read"]}).json()
        async with Client(params(readonly["token"]), cache=None) as client:
            read = await call(client, "get_project", {})
            assert read["connection"]["scopes"] == ["read"]
            denied = await client.call_tool("update_plan", {"changes":{"reserve_percent":10},
                "reason":"Unauthorized test", "card_revision":project["card_revision"],
                "plan_revision":1, "request_id":"browser-denied-plan"})
            assert denied.is_error
        assert owner.delete(base + "/codex/connections/" + readonly["connection"]["id"]).status_code == 200
        # The owner explicitly enables each additional scope and automatic completion in UI.
        issued = await ui("grant", scopes=["read","reports","progress","plan"], mode="auto")
        async with Client(params(issued["token"]), cache=None) as client:
            tools = await client.list_tools()
            assert {"get_project","update_plan","update_progress","report_task"} <= {t.name for t in tools.tools}
            read = await call(client, "get_project", {})
            assert read["connection"]["completion_mode"] == "auto"
            sources = await call(client, "search_materials", {"query":"workshop"})
            assert sources["matches"]
            changed = await call(client, "update_plan", {"changes":{"tasks":[{"id":"draft","minutes":30}],
                "availability":[{"day":1,"minutes":60}]}, "reason":"Small agenda draft fits one available day",
                "card_revision":project["card_revision"], "plan_revision":1, "request_id":"browser-plan"})
            card_revision, plan_revision = changed["card_revision"], changed["plan_revision"]
            claimed = await call(client, "claim_task", {"task_id":"draft","card_revision":card_revision,
                "plan_revision":plan_revision,"request_id":"browser-claim"})
            run_id = claimed["run"]["id"]
            started = await call(client, "update_progress", {"task_id":"draft","action":"start",
                "summary":"Drafting a source-based workshop outline", "card_revision":card_revision,
                "plan_revision":plan_revision,"request_id":"browser-start","run_id":run_id})
            artifact = tmp_path / "workshop-outline.md"
            artifact.write_text("# Workshop slides outline\n\n## Goal\nPrepare a team workshop.\n\n"
                "## Agenda\n- Explain the topic with slides.\n- Discuss examples together.\n\n"
                "## Feedback\nCollect feedback with a short form.\n", encoding="utf-8")
            text = artifact.read_text(encoding="utf-8")
            assert all(heading in text for heading in ("## Goal", "## Agenda", "## Feedback"))
            assert text.count("\n- ") == 2
            # Explicit synthetic user-supplied fixture minutes; never fabricated measured time.
            finished = await call(client, "update_progress", {"task_id":"draft","action":"complete",
                "summary":"Workshop outline written and checked", "card_revision":card_revision,
                "plan_revision":started["plan_revision"],"request_id":"browser-complete","run_id":run_id,
                "time":{"source":"user","minutes":7},
                "evidence":["workshop-outline.md: Goal, Agenda, Feedback sections verified; two agenda entries"]})
            await ui("expect", minutes=7, completed=True)
            await ui("correct", minutes=11)
            readback = await call(client, "get_project", {})
            task = readback["plan"]["input"]["tasks"][0]
            assert task["actual_minutes"] == 11 and task["completed"] is False
            assert task["actual_source"] == "user"
            await ui("draft", minutes=19)
            await call(client, "update_progress", {"task_id":"draft","action":"record",
                "summary":"Concurrent synthetic user fixture correction", "card_revision":card_revision,
                "plan_revision":readback["plan"]["revision"],"request_id":"browser-concurrent",
                "time":{"source":"user","minutes":13}})
            await ui("expect-draft", minutes=19)
            assert finished["plan_revision"] < readback["plan"]["revision"]
            audit = owner.get(base + "/codex/runs").json()["audit"]
            assert any(entry["actor"] == "Владелец" for entry in audit)
            assert issued["token"] not in text + json.dumps(audit)
        await ui("close")
    finally:
        browser.stdin.close()
        try:
            await asyncio.wait_for(browser.wait(), timeout=5)
        except TimeoutError:
            browser.kill()
            await browser.wait()
