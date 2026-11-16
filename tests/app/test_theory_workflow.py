"""Theory over real MCP, private cached readers and contextual plan edits; no paid API."""
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from studynavigator.agent import retrieval_intent
from studynavigator.catalog import Store, seed_database
from studynavigator.config import Settings
from studynavigator.main import create_app
from studynavigator.mcp_server import create_server
from studynavigator.theory import WikimediaSearch
from studynavigator.theory_cache import TheoryCache
from test_project_workspace import create_project, plan_body, NoModel

SOURCE = {"source_id": "web-theory-test", "scope": "web_theory", "kind": "theory",
          "title": "Матричная экспонента <script>bad()</script>", "excerpt": "Функция exp(A) определяется степенным рядом.",
          "url": "https://ru.wikipedia.org/wiki/Matrix", "external_url": "https://ru.wikipedia.org/wiki/Matrix",
          "page": None, "provider": "Wikipedia", "license": "CC BY-SA"}


def test_contextual_time_preview_apply_reload_and_unclear_confirmation(tmp_path):
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    with TestClient(create_app(settings, provider=NoModel())) as client:
        assignment = create_project(client)
        aid = assignment["assignment_id"]
        assert client.put(f"/api/assignments/{aid}/plan", json=plan_body(assignment)).status_code == 200
        response = client.post("/api/chat", json={"assignment_id": aid, "message": "да, подстрой все", "history": [
            {"role": "user", "content": "Мне понадобится по 3–4 часа на каждый пункт"},
            {"role": "assistant", "content": "Выбрать 240 минут на каждое действие?"}]})
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["response_origin"] == "local" and answer["usage"] is None
        proposal = answer["action_proposal"]
        assert proposal["preview_tasks"][0]["minutes"] == 240
        assert client.get(f"/api/assignments/{aid}/plan").json()["plan"]["result"]["tasks"][0]["minutes"] == 90
        applied = client.post(f"/api/assignments/{aid}/assistant-actions", json={
            "apply": True, **{k: proposal[k] for k in ("proposal_id", "card_revision", "plan_revision")}})
        assert applied.status_code == 200, applied.text
        saved = client.get(f"/api/assignments/{aid}/plan").json()["plan"]
        task = saved["result"]["tasks"][0]
        assert (task["minutes"], task["min_minutes"], task["max_minutes"]) == (240, 180, 240)
        assert saved["input"]["availability"] == [{"day": 1, "minutes": 0}, {"day": 2, "minutes": 60}]
        unclear = client.post("/api/chat", json={"assignment_id": aid, "message": "да, подстрой все", "history": [
            {"role": "assistant", "content": "Поставь все действия на 4 часа"}]}).json()
        assert unclear["status"] == "insufficient_data" and unclear["action_proposal"] is None


def test_theory_cached_readers_isolation_staleness_and_delete(tmp_path, monkeypatch):
    calls = []
    async def search(self, query):
        calls.append(query)
        return [SOURCE]
    monkeypatch.setattr(WikimediaSearch, "search", search)
    settings = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    app = create_app(settings, provider=NoModel())
    with TestClient(app) as client, TestClient(app) as stranger:
        assignment = create_project(client)
        aid = assignment["assignment_id"]
        url = f"/api/assignments/{aid}/theory"
        assert client.get(url).json()["status"] == "not_searched" and calls == []
        assert stranger.get(url).status_code == 404
        assert stranger.post(url, json={}).status_code == 404 and calls == []
        refreshed = client.post(url, json={})
        assert refreshed.status_code == 200, refreshed.text
        data = refreshed.json()
        assert data["status"] == "ready"
        source = next(s for s in data["sources"] if s["scope"] == "web_theory")
        assert source["url"] == "/sources/web-theory-test"
        assert client.get(url).json() == data
        assert len(calls) == 1
        page = client.get(source["url"])
        assert page.status_code == 200 and "CC BY-SA" in page.text
        assert "<script>bad()" not in page.text
        assert client.get("/api/sources/web-theory-test").json()["excerpt"] == SOURCE["excerpt"]
        assert stranger.get(source["url"]).status_code == 404
        assert stranger.get("/api/sources/web-theory-test").status_code == 404
        # Material change invalidates overview, never silently shows old task links.
        added = client.post(f"/api/assignments/{aid}/materials", json={"title": "Дополнение", "text": "Другое требование"})
        assert added.status_code == 201
        assert client.get(url).json()["status"] == "not_searched"
        assert client.delete(f"/api/assignments/{aid}").status_code == 204
        assert client.get(source["url"]).status_code == 404
        with sqlite3.connect(settings.database_path) as db:
            assert db.execute("SELECT count(*) FROM theory_cache WHERE assignment_id=?", (aid,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_mcp_theory_is_readable_but_never_added_to_requirements(tmp_path):
    path = tmp_path / "study.db"
    seed_database(path)
    calls = []
    async def search(query):
        calls.append(query)
        return [SOURCE]
    server = create_server(path, workspace_id="owner", theory_search=search)
    async with Client(server, cache=None) as client:
        result = await client.call_tool("search_materials", {"query": "Объясни матричную экспоненту", "assignment_id": "personal-project", "include_theory": True})
        assert not result.is_error
        data = result.structured_content
        assert data["theory_retrieval"]["status"] == "ready"
        assert any(s["scope"] == "web_theory" for s in data["matches"])
        assert len(calls) == 1
        assert TheoryCache(Store(path, "owner")).source(SOURCE["source_id"])
        assert TheoryCache(Store(path, "other")).source(SOURCE["source_id"]) is None
        assignment = (await client.call_tool("get_assignment", {"assignment_id": "personal-project"})).structured_content["assignment"]
        assert SOURCE["source_id"] not in assignment["source_ids"]
        forbidden = await client.call_tool("search_materials", {"query": "экспонента", "assignment_id": "other", "include_theory": True})
        assert forbidden.is_error and len(calls) == 1


@pytest.mark.asyncio
async def test_theory_failure_is_explicit_and_project_materials_remain(tmp_path):
    path = tmp_path / "study.db"
    seed_database(path)
    async def fail(query):
        raise httpx.ConnectError("private diagnostics")
    async with Client(create_server(path, theory_search=fail), cache=None) as client:
        result = await client.call_tool("search_materials", {"query": "MCP", "assignment_id": "personal-project", "include_theory": True})
        data = result.structured_content
        assert data["matches"] and data["theory_retrieval"]["status"] == "unavailable"
        assert "private diagnostics" not in json.dumps(data)


def test_internet_intent_does_not_research_time_edits_or_project_requirements():
    assert retrieval_intent("Объясни матричную экспоненту") == (False, True)
    assert retrieval_intent("Как лучше распределить нагрузку и перерывы?") == (True, False)
    for text in ["Поменяй всем время на 3 часа", "Расскажи требования", "Объясни срок сдачи"]:
        assert retrieval_intent(text) == (False, False)


@pytest.mark.asyncio
@pytest.mark.parametrize('forged_citation', [False, True])
async def test_stdio_cached_theory_reaches_grounded_payload_and_checked_citations(tmp_path, forged_citation):
    """Actual stdio tool exchange; cached web evidence and HTTP-only model fixture."""
    from studynavigator.agent import AgentError, StudyAgent
    from studynavigator.mcp_gateway import MCPGateway
    from studynavigator.providers import ChatCompletionsProvider
    from test_format_repair import configured

    path = tmp_path / 'study.db'
    seed_database(path)
    owner = 'theory-owner'
    store = Store(path, workspace_id=owner)
    assignment = store.create_text_assignment('Математика', 'Изучить матричную экспоненту.')["assignment"]
    aid = assignment['assignment_id']
    question = 'Объясни матричную экспоненту'
    cache = TheoryCache(store)
    warmups = []

    async def public_fixture(query):
        warmups.append(query)
        return [SOURCE]

    cached = await cache.search(aid, question, search=public_fixture)
    assert warmups == ['матричную экспоненту']
    assert cached['status'] == 'ready'
    observed = []

    def model_fixture(request):
        payload = json.loads(request.content)
        observed.append(payload)
        assert 'tools' not in payload
        data = json.loads(payload['messages'][1]['content'])
        assert data['question'] == question
        assert data['MCP_DATA'][0]['assignment']['assignment_id'] == aid
        retrieved = data['MCP_DATA'][1]
        assert retrieved['theory_retrieval'] == {'status': 'ready', 'provider': 'Wikipedia'}
        web = next(source for source in retrieved['matches'] if source.get('scope') == 'web_theory')
        assert web['source_id'] == SOURCE['source_id']
        assert web['excerpt'] == SOURCE['excerpt']
        assert web['url'] == '/sources/' + SOURCE['source_id']
        answer = {'answer': 'Матричная экспонента определяется степенным рядом.', 'status': 'answered',
                  'source_ids': ['web-theory-forged' if forged_citation else web['source_id']],
                  'facts': [], 'answer_basis': 'materials'}
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant',
            'content': json.dumps(answer, ensure_ascii=False)}, 'finish_reason': 'stop'}]})

    settings = configured(database_path=path)
    provider = ChatCompletionsProvider(settings, transport=httpx.MockTransport(model_fixture))
    async with MCPGateway(path, workspace_id=owner) as gateway:
        tool = next(tool for tool in gateway.tools if tool['name'] == 'search_materials')
        assert 'include_theory' in tool['inputSchema']['properties']
        if forged_citation:
            with pytest.raises(AgentError) as caught:
                await StudyAgent(gateway, provider).run(question, assignment_id=aid)
            assert caught.value.code == 'unsupported_source'
        else:
            answer = await StudyAgent(gateway, provider).run(question, assignment_id=aid)
            assert [source['source_id'] for source in answer['sources']] == [SOURCE['source_id']]
            assert answer['sources'][0]['scope'] == 'web_theory'
            assert answer['sources'][0]['external_url'] == SOURCE['external_url']
            assert answer['facts'] == []
        assert [(step['name'], step['status']) for step in gateway.trace] == [
            ('get_assignment', 'ok'), ('search_materials', 'ok')]
    assert len(observed) == 1
    assert warmups == ['матричную экспоненту']
