"""Screenshot regression through HTTP, real MCP and a scripted (unpaid) model."""
import json

from fastapi.testclient import TestClient

from studynavigator.assistant_actions import prepare_assistant_actions
from studynavigator.config import Settings
from studynavigator.main import create_app
from test_assistant_workflow_v3 import assignment


MESSAGE = "я разобрал объем и критерии, потратил на это 120 минут, заполни эти пункты"


def test_fill_progress_is_an_explicit_edit_request():
    proposal = {"summary": "Записать выполненную работу", "operations": [
        {"op": "complete", "task_id": "a", "actual_minutes": 120}]}
    result = prepare_assistant_actions(assignment(), proposal, MESSAGE)
    assert result["operations"][0]["actual_minutes"] == 120


class ProgressModel:
    mode = "live"
    model = "scripted-progress-regression"
    preload_assignment = True

    def __init__(self):
        self.calls = 0

    async def complete_grounded(self, messages):
        self.calls += 1
        evidence = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        tasks = evidence[0]["assignment"]["card"]["tasks"]
        assert len(tasks) == 5
        assert tasks[0]["title"] == "Разобрать объём и критерии"
        assert tasks[0]["minutes"] == 180 and tasks[0]["actual_minutes"] is None
        assert all(len(task["substeps"]) == 6 for task in tasks)
        return {"content": json.dumps({"answer": "Предлагаю отметить разбор выполненным и записать 120 минут. Нажмите «Применить».",
            "status": "answered", "answer_basis": "project_context", "facts": [], "source_ids": [],
            "action_proposal": {"summary": "Разбор выполнен за 120 минут", "operations": [
                {"op": "complete", "task_id": tasks[0]["id"], "actual_minutes": 120}]}}, ensure_ascii=False)}


def test_large_project_progress_preview_apply_and_reload(tmp_path):
    provider = ProgressModel()
    config = Settings(database_path=tmp_path / "study.db", rate_limit_per_minute=1000)
    with TestClient(create_app(config, provider=provider)) as client:
        created = client.post("/api/assignments/from-text", json={"title": "Большой проект", "text": "Разобрать объём и критерии, подготовить и проверить результат."})
        assert created.status_code == 201
        selected = created.json()["assignment"]
        aid, card = selected["assignment_id"], selected["card"]
        card["deliverable"] = {"text": "Проверенный проект"}
        card["requirements"] = [{"id": "req", "text": "Подготовить результат"}]
        card["tasks"] = [{"id": f"task-{i}", "title": "Разобрать объём и критерии" if i == 1 else f"Действие {i}",
            "minutes": 180, "requirement_ids": ["req"], "depends_on": [] if i == 1 else [f"task-{i-1}"],
            "expected_result": "Результат " * 55, "done_when": "Проверено " * 55,
            "estimate_basis": "Подготовка и проверка " * 25,
            "substeps": [{"id": f"step-{j}", "title": f"Проверяемый шаг {j}", "minutes": 30,
                         "done_when": "Результат подтверждён " * 15} for j in range(6)]} for i in range(1, 6)]
        confirmed = client.post(f"/api/assignments/{aid}/confirm", json={"revision": selected["card_revision"], "card": card})
        assert confirmed.status_code == 200, confirmed.text
        selected = confirmed.json()["assignment"]
        saved = client.put(f"/api/assignments/{aid}/plan", json={"revision": 0, "card_revision": selected["card_revision"],
            "availability": [{"day": i, "minutes": 240} for i in range(1, 8)], "reserve_percent": 20,
            "tasks": [{**t, "completed": False, "actual_minutes": None} for t in selected["card"]["tasks"]]})
        assert saved.status_code == 200, saved.text
        chat = client.post(f"/api/assignments/{aid}/chats", json={}).json()["session"]["id"]
        response = client.post("/api/chat", json={"assignment_id": aid, "chat_id": chat, "message": MESSAGE})
        assert response.status_code == 200, response.text
        result = response.json()
        assert provider.calls == 1
        assert result["trace"][0]["name"] == "get_assignment"
        proposal = result["action_proposal"]
        before = client.get(f"/api/assignments/{aid}/plan").json()["plan"]
        assert not before["result"]["tasks"][0]["completed"]
        assert before["result"]["tasks"][0]["actual_minutes"] is None
        applied = client.post(f"/api/assignments/{aid}/assistant-actions", json={"apply": True,
            **{key: proposal[key] for key in ("proposal_id", "card_revision", "plan_revision")}})
        assert applied.status_code == 200, applied.text
        after = client.get(f"/api/assignments/{aid}/plan").json()["plan"]["result"]["tasks"]
        assert after[0]["completed"] and after[0]["actual_minutes"] == 120
        assert after[0]["minutes"] == 180 and after[0]["requirement_ids"] == ["req"]
        assert after[1:] == before["result"]["tasks"][1:]
        history = client.get(f"/api/assignments/{aid}/chats/{chat}").json()["messages"]
        assert history[0]["content"] == MESSAGE and history[1]["result"]["action_proposal"] is None
