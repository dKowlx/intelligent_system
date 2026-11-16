"""Shared evidence crosses real MCP and remains separate from requirements."""
import json

import pytest
from mcp import Client

from studynavigator.agent import AgentError, StudyAgent
from studynavigator.catalog import seed_database
from studynavigator.mcp_server import create_server
from test_agent import FixtureGateway, final_turn

SOURCE = {"source_id": "research-test-1", "title": "Test study", "excerpt": "Break effects vary.",
          "scope": "shared_research", "kind": "research", "authors": ["Test Author"],
          "doi": "10.1234/test", "external_url": "https://example.org/study"}


class FakeKnowledge:
    async def asearch(self, query, limit=5):
        return {"sources": [SOURCE], "retrieval_status": "hybrid", "degraded_reason": None}


@pytest.mark.asyncio
async def test_real_mcp_search_adds_research_without_changing_assignment(tmp_path):
    path = tmp_path / "study.db"
    seed_database(path)
    server = create_server(path, knowledge=FakeKnowledge())
    async with Client(server, cache=None) as session:
        result = await session.call_tool("search_materials", {"query": "перерывы", "assignment_id": "personal-project", "include_research": True})
        assert not result.is_error
        payload = result.structured_content
        assert SOURCE in payload["matches"]
        assert payload["research_retrieval"]["retrieval_status"] == "hybrid"
        assignment = await session.call_tool("get_assignment", {"assignment_id": "personal-project"})
        assert SOURCE["source_id"] not in assignment.structured_content["assignment"]["source_ids"]


class ResearchGateway(FixtureGateway):
    async def call(self, name, arguments):
        if name == "search_materials":
            self.search_arguments = dict(arguments)
            self.trace.append({"name": name, "status": "ok"})
            return {"matches": [SOURCE]}
        return await super().call(name, arguments)


class ResearchProvider:
    mode = "offline"
    def __init__(self, source):
        self.turn = 0
        self.source = source
    async def complete(self, messages, tools, require_tool=False):
        self.turn += 1
        if self.turn <= 2:
            name = "get_assignment" if self.turn == 1 else "search_materials"
            args = {"assignment_id": "personal-project"} if self.turn == 1 else {"query": "breaks"}
            return {"tool_calls": [{"id": str(self.turn), "function": {"name": name, "arguments": json.dumps(args)}}]}
        return final_turn(facts=[], source_ids=[self.source])


@pytest.mark.asyncio
@pytest.mark.parametrize("source,accepted", [("research-test-1", True), ("research-unknown", False)])
async def test_only_retrieved_research_citations_accepted(source, accepted):
    agent = StudyAgent(ResearchGateway(), ResearchProvider(source))
    if accepted:
        result = await agent.run("Что известно о перерывах?")
        assert result["sources"] == [SOURCE]
    else:
        with pytest.raises(AgentError) as error:
            await agent.run("Что известно о перерывах?")
        assert error.value.code == "unsupported_source"


@pytest.mark.asyncio
async def test_research_failure_is_partial_not_empty_success(tmp_path):
    class BrokenKnowledge:
        async def asearch(self, query, limit=5):
            raise RuntimeError("private upstream diagnostic")
    path = tmp_path / "study.db"
    seed_database(path)
    async with Client(create_server(path, knowledge=BrokenKnowledge()), cache=None) as client:
        result = await client.call_tool("search_materials", {"query": "MCP", "assignment_id": "personal-project", "include_research": True})
        assert not result.is_error
        payload = result.structured_content
        assert payload["matches"]
        assert payload["research_retrieval"] == {
            "retrieval_status": "unavailable", "degraded_reason": "research_search_failed"}
        assert "private upstream" not in json.dumps(payload)
        missing = await client.call_tool("search_materials", {"query": "breaks", "assignment_id": "foreign-project", "include_research": True})
        assert missing.is_error


@pytest.mark.asyncio
async def test_default_search_never_calls_research_provider(tmp_path):
    class ForbiddenKnowledge:
        async def asearch(self, query, limit=5):
            pytest.fail("Public material search must not invoke embeddings")
    path = tmp_path / "study.db"
    seed_database(path)
    async with Client(create_server(path, knowledge=ForbiddenKnowledge()), cache=None) as client:
        result = await client.call_tool("search_materials", {"query": "MCP"})
        assert not result.is_error
        assert result.structured_content["matches"]
        assert "research_retrieval" not in result.structured_content


@pytest.mark.asyncio
async def test_legacy_search_schema_does_not_receive_research_flag():
    gateway = ResearchGateway()
    for tool in gateway.tools:
        if tool["name"] == "search_materials":
            tool["inputSchema"]["properties"].pop("include_research", None)
    provider = ResearchProvider("research-test-1")
    result = await StudyAgent(gateway, provider).run("Совет о перерывах")
    assert result["sources"] == [SOURCE]

    assert "include_research" not in gateway.search_arguments
