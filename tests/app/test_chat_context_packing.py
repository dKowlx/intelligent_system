"""Large saved routes reach the model without duplicated task descriptions (offline)."""
import copy
import json

import pytest

from studynavigator.agent import StudyAgent
from test_agent import FixtureGateway, ScriptedProvider


def large_assignment():
    gateway = FixtureGateway()
    value = gateway.assignment
    value.update(is_uploaded=True, facts=[])
    tasks = [{"id": f"group-{i}", "title": f"Разобрать объем и критерии {i}", "minutes": 120,
              "actual_minutes": None, "completed": False, "source_ids": ["personal-project-p1"],
              "depends_on": [f"group-{i-1}"] if i else [],
              "expected_result": "Результат " * 60, "done_when": "Проверено " * 60,
              "estimate_basis": "Основание " * 60,
              "substeps": [{"id": f"step-{j}", "title": f"Шаг {j}", "minutes": 20,
                            "done_when": "Проверка результата " * 20} for j in range(6)]}
             for i in range(5)]
    value["card"] = {"goal": {"text": "Цель", "origin": "user", "source_ids": []},
                     "deadline": {"date": None, "origin": "unknown", "source_ids": []}, "tasks": tasks}
    saved_tasks = copy.deepcopy(tasks)
    saved_tasks[0]["actual_minutes"] = 12
    value["project_context"] = {"plan": {"revision": 4, "card_revision": 1,
        "result": {"tasks": saved_tasks}, "input": {"availability": [{"day": 1, "minutes": 120}]}},
        "analytics": {"history": []}}
    return gateway


class GroundedProvider(ScriptedProvider):
    mode = "live"
    preload_assignment = True

    async def complete_grounded(self, messages):
        self.calls.append(copy.deepcopy(messages))
        return {"content": json.dumps({"answer": "Уточните, какие пункты завершены.",
            "status": "insufficient_data", "source_ids": [], "facts": [], "answer_basis": "project_context"})}


@pytest.mark.asyncio
async def test_large_saved_route_keeps_actions_and_progress_within_existing_limit():
    gateway = large_assignment()
    before = copy.deepcopy(gateway.assignment)
    provider = GroundedProvider([])
    result = await StudyAgent(gateway, provider).run(
        "я разобрал объем и критерии, потратил на это 120 минут, заполни эти пункты")
    assert result["status"] == "insufficient_data"
    tools = [json.loads(m["content"]) for m in provider.calls[0] if m["role"] == "tool"]
    assignment = tools[0]["assignment"]
    # Current saved tasks are passed once; stale card copies must not mask progress.
    assert assignment["card"]["tasks"] == before["project_context"]["plan"]["result"]["tasks"]
    assert "tasks" not in assignment["project_context"]["plan"]
    assert assignment["project_context"]["plan"]["tasks_ref"] == "assignment.card.tasks"
    assert len(json.dumps(tools, ensure_ascii=False)) < 32000
    assert gateway.assignment == before
    assert [name for name, _ in gateway.dispatches] == ["get_assignment", "search_materials"]
