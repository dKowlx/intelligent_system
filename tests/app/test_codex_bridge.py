"""Real stdio MCP protocol against a local HTTP fixture, without any model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from studynavigator.codex_bridge import BridgeConfig

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "fixture-private-token"


@pytest.fixture
def upstream():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            calls.append((self.command, self.path, self.headers.get("Authorization"), body))
            self.send_response(403 if body.get("query") == "denied" else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "echo": TOKEN}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()
    server.server_close()
    worker.join()


@pytest.mark.parametrize("url", ["http://example.com", "https://u:p@example.com", "https://example.com?a=1",
                                  "https://example.com#x", "ftp://example.com", "https://example.com:bad", ""])
def test_bad_configuration(url, monkeypatch):
    monkeypatch.setenv("NAVIGATOR_URL", url)
    monkeypatch.setenv("NAVIGATOR_TOKEN", TOKEN)
    with pytest.raises(ValueError) as caught:
        BridgeConfig.from_env()
    assert TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_stdio_protocol_discovery_validation_and_all_tools(upstream):
    url, calls = upstream
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "studynavigator.codex_bridge"], cwd=ROOT,
        env={**os.environ, "NAVIGATOR_URL": url, "NAVIGATOR_TOKEN": TOKEN})
    async with Client(params, cache=None) as client:
        tools = (await client.list_tools()).tools
        assert {tool.name for tool in tools} == {"get_project", "search_materials", "claim_task", "report_task", "update_progress", "update_plan"}
        assert all(tool.input_schema["additionalProperties"] is False for tool in tools)
        assert all("NAVIGATOR_TOKEN" not in json.dumps(tool.model_dump()) for tool in tools)
        for name, args in [
            ("get_project", {}), ("search_materials", {"query": "requirements"}),
            ("claim_task", {"task_id": "t1", "card_revision": 1, "plan_revision": 1, "request_id": "r1"}),
            ("report_task", {"run_id": "run1", "status": "awaiting_review", "summary": "Checked", "evidence": ["pytest: passed"], "request_id": "r2"}),
        ]:
            result = await client.call_tool(name, args)
            assert not result.is_error
            assert TOKEN not in str(result)
            assert "REDACTED" in str(result)
        assert len(calls) == 4
        assert all(call[2] == "Bearer " + TOKEN for call in calls)
        for name, args in [
            ("search_materials", {"query": "x", "limit": True}),
            ("search_materials", {"query": " "}),
            ("get_project", {"token": "forged"}),
            ("claim_task", {"task_id": "t", "card_revision": True, "plan_revision": 1, "request_id": "r"}),
            ("report_task", {"run_id": "r", "status": "completed", "summary": "x", "evidence": [], "request_id": "r"}),
        ]:
            assert (await client.call_tool(name, args)).is_error
        assert len(calls) == 4
        error = await client.call_tool("search_materials", {"query": "denied"})
        assert error.is_error and "navigator_http_403" in str(error)
        assert TOKEN not in str(error)


def test_check_is_read_only_and_does_not_print_secret(upstream):
    url, calls = upstream
    result = subprocess.run([sys.executable, "-m", "studynavigator.codex_bridge", "--check"],
        env={**os.environ, "NAVIGATOR_URL": url, "NAVIGATOR_TOKEN": TOKEN},
        cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert len(calls) == 1 and calls[0][0:2] == ("GET", "/api/codex/project")
    assert TOKEN not in result.stdout + result.stderr


def test_unavailable_check_is_sanitized():
    result = subprocess.run([sys.executable, "-m", "studynavigator.codex_bridge", "--check"],
        env={**os.environ, "NAVIGATOR_URL": "http://127.0.0.1:1", "NAVIGATOR_TOKEN": TOKEN},
        cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert TOKEN not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [("redirect", "navigator_http_302"),
    ("timeout", "navigator_timeout"), ("network", "navigator_unavailable"),
    ("invalid", "navigator_invalid_response")])
async def test_transport_errors_are_sanitized_and_redirects_disabled(monkeypatch, kind, expected):
    import httpx
    from mcp.server.mcpserver.exceptions import ToolError
    from studynavigator.codex_bridge import Bridge

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 15, "follow_redirects": False, "trust_env": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            if kind == "timeout":
                raise httpx.ReadTimeout(TOKEN)
            if kind == "network":
                raise httpx.ConnectError(TOKEN)
            return httpx.Response(302 if kind == "redirect" else 200,
                headers={"Location": "https://other.example/"}, text=TOKEN)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(ToolError) as caught:
        await Bridge(BridgeConfig("https://example.com", TOKEN)).request("GET", "/api/codex/project")
    assert expected in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_uv_command_finds_module_from_another_repository(tmp_path):
    import shutil
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not installed")
    env_file = tmp_path / ".env.codex"
    # Invalid configuration exits before any network request. Reaching our
    # sanitized diagnostic proves module resolution from a foreign cwd.
    env_file.write_text("NAVIGATOR_URL=invalid\nNAVIGATOR_TOKEN=dummy-private-token\n")
    result = subprocess.run([uv, "run", "--directory", str(ROOT), "--locked",
        "--offline", "--cache-dir", str(tmp_path / "cache"), "--env-file", str(env_file),
        "python", "-m", "studynavigator.codex_bridge", "--check"],
        cwd=tmp_path, env={**os.environ, "NAVIGATOR_URL": "invalid", "NAVIGATOR_TOKEN": "dummy-private-token"},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "Project Navigator: ошибка подключения" in result.stderr
    assert "No module named" not in result.stderr
    assert "dummy-private-token" not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_stdio_timer_generates_active_measurement_and_stops_on_report(upstream):
    url, calls = upstream
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'studynavigator.codex_bridge'], cwd=ROOT,
        env={**os.environ, 'NAVIGATOR_URL': url, 'NAVIGATOR_TOKEN': TOKEN})
    async with Client(params, cache=None) as client:
        base = {'task_id': 'task', 'card_revision': 1, 'plan_revision': 1, 'summary': 'Working'}
        assert not (await client.call_tool('update_progress', {**base, 'action': 'start', 'request_id': 'timer-start'})).is_error
        paused = await client.call_tool('update_progress', {**base, 'action': 'pause', 'request_id': 'timer-pause'})
        assert not paused.is_error
        measured = calls[-1][3]['time']
        assert measured['source'] == 'measured' and measured['minutes'] == 0
        assert measured['measurement'] == 'bridge_timer' and measured['basis']
        assert not (await client.call_tool('update_progress', {**base, 'action': 'resume', 'request_id': 'timer-resume'})).is_error
        assert not (await client.call_tool('report_task', {'run_id': 'unmapped-run', 'status': 'partial',
            'summary': 'Partial work', 'evidence': ['checked'], 'request_id': 'timer-report',
            'expected_updated_at': '2026-11-18T00:00:00+00:00'})).is_error
        assert calls[-1][3]['expected_updated_at'] == '2026-11-18T00:00:00+00:00'
        # A report stopped even an unmapped timer; idle no longer produces measurements.
        assert not (await client.call_tool('update_progress', {**base, 'action': 'record', 'request_id': 'timer-after-report'})).is_error
        assert 'time' not in calls[-1][3]
