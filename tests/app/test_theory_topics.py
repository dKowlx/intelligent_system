"""Bounded topic batches over the real in-memory MCP protocol; no web calls."""
import asyncio

import pytest
from mcp import Client

from studynavigator.agent import reestimate_theory_topics
from studynavigator.catalog import seed_database
from studynavigator.mcp_server import create_server
from studynavigator.theory import theory_query
from test_theory_workflow import SOURCE


def test_reestimate_topics_skip_generic_old_task_and_keep_distinct_methods():
    tasks = [{"title": title} for title in [
        "Выполнить: Решить все задачи из списка второй части",
        "Решить интегралы методом подстановки",
        "Проверить матричную экспоненту",
        "Вычислить собственные значения",
        "Лишняя четвертая тема",
    ]]
    assert theory_query(tasks[0]["title"]) == ""
    assert reestimate_theory_topics({"card": {"tasks": tasks}}) == [
        "интегралы методом подстановки", "матричную экспоненту", "Вычислить собственные значения"]


@pytest.mark.asyncio
async def test_actual_mcp_batch_concurrent_deduplicated_bounded_and_validated(tmp_path):
    path = tmp_path / "study.db"
    seed_database(path)
    calls, active = [], set()
    all_started = asyncio.Event()

    async def search(topic):
        calls.append(topic)
        active.add(topic)
        if len(active) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return [{**SOURCE, "source_id": "web-theory-" + topic}, SOURCE]

    async with Client(create_server(path, theory_search=search), cache=None) as client:
        arguments = {"query": "MCP", "assignment_id": "personal-project", "include_theory": True,
            "theory_topics": ["Интегралы", "Матрицы", "Производная"]}
        result = await client.call_tool("search_materials", arguments)
        assert not result.is_error
        data = result.structured_content
        assert data["theory_retrieval"]["status"] == "ready"
        assert set(calls) == set(arguments["theory_topics"])
        assert len([s for s in data["matches"] if s.get("scope") == "web_theory"]) == 3
        await client.call_tool("search_materials", arguments)
        assert len(calls) == 3  # Cache survives separate protocol requests.
        for topics in [["x"] * 4, ["x" * 121], ["   "]]:
            invalid = await client.call_tool("search_materials", {**arguments, "theory_topics": topics})
            assert invalid.is_error
        assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected", [(False, "empty"), (True, "unavailable")])
async def test_actual_mcp_empty_and_unavailable_batch_status(tmp_path, failure, expected):
    path = tmp_path / "study.db"
    seed_database(path)

    async def search(topic):
        if failure:
            raise TimeoutError()
        return []

    async with Client(create_server(path, theory_search=search), cache=None) as client:
        result = await client.call_tool("search_materials", {"query": "MCP", "assignment_id": "personal-project",
            "include_theory": True, "theory_topics": ["Матрицы", "Интегралы"]})
        assert result.structured_content["theory_retrieval"]["status"] == expected
