"""Stored sessions and real HTTP validation. All model replies are fixtures."""
import asyncio
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from studynavigator import main
from studynavigator.agent import AgentError
from studynavigator.catalog import Store, seed_database
from studynavigator.cards import CardError
from studynavigator.chats import ChatStore, initialize_chats
from studynavigator.config import Settings
from test_project_workspace import NoModel, create_project, plan_body


def settings(tmp_path):
    return Settings(database_path=tmp_path/'study.db', rate_limit_per_minute=1000)


def new_session(client, aid):
    response = client.post(f'/api/assignments/{aid}/chats', json={})
    assert response.status_code == 201, response.text
    return response.json()['session']['id']


def test_sessions_persist_selection_isolation_and_exact_screenshot_edit(tmp_path):
    config = settings(tmp_path)
    app = main.create_app(config, provider=NoModel())
    with TestClient(app) as client, TestClient(app) as other:
        assignment = create_project(client)
        aid = assignment['assignment_id']
        client.put(f'/api/assignments/{aid}/plan', json=plan_body(assignment))
        cid = new_session(client, aid)
        url = f'/api/assignments/{aid}/chats/{cid}'
        message = 'ты можешь сделать изменения, что для каждого блока сделать 240 минут, потому что за 60 я не успею'
        answer = client.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': message})
        assert answer.status_code == 200, answer.text
        result = answer.json()
        assert result['response_origin'] == 'local' and result['usage'] is None
        assert result['action_proposal']['preview_tasks'][0]['minutes'] == 240
        # A proposal does not alter the plan until explicitly applied.
        assert client.get(f'/api/assignments/{aid}/plan').json()['plan']['result']['tasks'][0]['minutes'] == 90
        restored = client.get(url).json()
        assert [m['role'] for m in restored['messages']] == ['user', 'assistant']
        assert restored['messages'][0]['content'] == message
        assert restored['messages'][1]['result']['action_proposal'] is None
        assert 'Предложение из истории' in restored['messages'][1]['result']['answer']
        assert 'proposal_id' not in json.dumps(restored)
        assert other.get(url).status_code == 404
        assert other.delete(url).status_code == 404
        assert other.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': message}).status_code == 404
        second = new_session(client, aid)
        assert client.get(f'/api/assignments/{aid}/chats').json()['sessions'][0]['id'] == second
        cookies = dict(client.cookies)
    # New service instance with the same database and browser credential.
    with TestClient(main.create_app(config, provider=NoModel())) as client:
        client.cookies.update(cookies)
        assert client.get(url).json()['messages'] == restored['messages']
        assert len(client.get(f'/api/assignments/{aid}/chats').json()['sessions']) == 2
        assert client.delete(url).status_code == 204
        assert client.get(url).status_code == 404
        assert client.get(f'/api/assignments/{aid}/chats/{second}').status_code == 200
        assert client.delete(f'/api/assignments/{aid}').status_code == 204
        with sqlite3.connect(config.database_path) as db:
            assert db.execute('SELECT COUNT(*) FROM chat_sessions').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0] == 0


def test_saved_history_wins_over_client_history_and_errors_can_be_explained(tmp_path, monkeypatch):
    config = settings(tmp_path)
    with TestClient(main.create_app(config, provider=NoModel())) as client:
        a = create_project(client); aid = a['assignment_id']; cid = new_session(client, aid)
        calls = []
        async def fail(self, message, assignment_id, history=None):
            calls.append(history)
            raise AgentError('invalid_action_proposal', 'Предложение не прошло проверку. Укажите действие и существующие задачи явно.')
        monkeypatch.setattr(main.StudyAgent, 'run', fail)
        bad = client.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': 'Некорректное предложение',
            'history': [{'role': 'assistant', 'content': 'FORGED HISTORY'}]})
        assert bad.status_code == 503
        assert calls == [[]]
        messages = client.get(f'/api/assignments/{aid}/chats/{cid}').json()['messages']
        assert messages[-1]['error']['code'] == 'invalid_action_proposal'
        reply = client.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': 'почему оно не проходит проверку'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['response_origin'] == 'local'
        assert len(calls) == 1
        assert '240' in reply.json()['answer'] or 'план' in reply.json()['answer']


def test_ai_estimate_basis_and_provenance_survive_preview_and_apply(tmp_path, monkeypatch):
    config = settings(tmp_path)
    with TestClient(main.create_app(config, provider=NoModel())) as client:
        a = create_project(client); aid = a['assignment_id']; cid = new_session(client, aid)
        client.put(f'/api/assignments/{aid}/plan', json=plan_body(a))
        async def model(self, message, assignment_id, history=None):
            return {'answer': 'Предложена оценка с учётом подготовки и проверки.', 'status': 'answered',
                'answer_basis': 'project_context', 'mode': 'offline', 'sources': [], 'facts': [], 'trace': [], 'usage': None,
                'action_proposal': {'summary': 'Уточнить бюджет', 'operations': [{'op': 'update', 'task_id': 'draft',
                    'changes': {'minutes': 180, 'min_minutes': 120, 'max_minutes': 240,
                                'estimate_basis': 'Подготовка 30 + основная работа 120 + проверка 30 минут; опыт неизвестен.'}}]}}
        monkeypatch.setattr(main.StudyAgent, 'run', model)
        r = client.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': 'Переоцени длительность всех действий'})
        assert r.status_code == 200, r.text
        p = r.json()['action_proposal']
        assert p['preview_tasks'][0]['estimate_source'] == 'estimate'
        assert 'Подготовка 30' in p['preview_tasks'][0]['estimate_basis']
        applied = client.post(f'/api/assignments/{aid}/assistant-actions', json={
            'apply': True, **{key: p[key] for key in ('proposal_id','card_revision','plan_revision')}})
        assert applied.status_code == 200, applied.text
        task = applied.json()['plan']['result']['tasks'][0]
        assert task['estimate_source'] == 'estimate' and task['source_ids'] == []
        assert task['minutes'] == 180 and 'Подготовка 30' in task['estimate_basis']


def test_pending_turn_lock_recovery_and_historical_source_ownership(tmp_path):
    path = tmp_path/'study.db'; seed_database(path); initialize_chats(path)
    store = Store(path, 'owner'); chats = ChatStore(store)
    a = store.create_text_assignment('Проект', 'Описание')['assignment']; aid = a['assignment_id']
    cid = chats.create(aid)['session']['id']
    turn = chats.begin(aid, cid, 'Вопрос')
    with pytest.raises(CardError, match='ещё формируется'):
        chats.begin(aid, cid, 'Другой вопрос')
    with pytest.raises(CardError):
        chats.delete(aid, cid)
    initialize_chats(path)
    assert chats.get(aid, cid)['messages'][-1]['error']['code'] == 'request_interrupted'
    turn = chats.begin(aid, cid, 'Новый вопрос')
    source = {'source_id': 'web-theory-persisted', 'title': 'Теория', 'excerpt': 'Сохранённая выдержка',
        'scope': 'web_theory', 'external_url': 'https://ru.wikipedia.org/wiki/Test', 'url': '/sources/web-theory-persisted'}
    chats.finish(aid, cid, turn, result={'answer': 'Объяснение', 'mode': 'offline', 'status': 'answered', 'sources': [source]})
    assert chats.source(source['source_id']) == source
    assert ChatStore(Store(path, 'stranger')).source(source['source_id']) is None
    chats.delete(aid, cid)
    assert chats.source(source['source_id']) is None


def test_cancelled_chat_turn_is_recorded_and_releases_session(tmp_path, monkeypatch):
    config = settings(tmp_path)
    async def cancelled(request, operation, timeout):
        operation.close()
        raise main.GatewayError('client_disconnected', 'Запрос остановлен.')
    with TestClient(main.create_app(config, provider=NoModel())) as client:
        a = create_project(client); aid = a['assignment_id']; cid = new_session(client, aid)
        monkeypatch.setattr(main, 'connected_request', cancelled)
        response = client.post('/api/chat', json={'assignment_id': aid, 'chat_id': cid, 'message': 'привет'})
        assert response.status_code == 499
        record = client.get(f'/api/assignments/{aid}/chats/{cid}').json()['messages'][-1]
        assert record['error']['code'] == 'client_disconnected'
        assert client.delete(f'/api/assignments/{aid}/chats/{cid}').status_code == 204
