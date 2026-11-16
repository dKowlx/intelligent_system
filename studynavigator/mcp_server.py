"""Own MCP server. Run with ``python -m studynavigator.mcp_server --db PATH``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .catalog import CatalogError, Store

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")]
Query = Annotated[str, Field(min_length=1, max_length=500)]
TheoryTopic = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
TheoryTopics = Annotated[list[TheoryTopic], Field(min_length=1, max_length=3)]
ResultLimit = Annotated[StrictInt, Field(ge=1, le=10)]


class AssignmentList(BaseModel):
    assignments: list[dict[str, Any]]


class AssignmentResult(BaseModel):
    assignment: dict[str, Any]


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    matches: list[dict[str, Any]]


def create_server(db_path: Path, workspace_id: str | None = None, *, knowledge=None, theory_search=None) -> MCPServer:
    # Trusted backend context, never a model-supplied tool argument.
    store = Store(db_path, workspace_id=workspace_id)
    if knowledge is None and os.environ.get("STUDYNAV_KNOWLEDGE_DB_PATH"):
        from .knowledge import KnowledgeStore
        knowledge = KnowledgeStore.from_env()
    server = MCPServer("StudyNavigator", version="0.1.0", log_level="WARNING",
                       instructions="Read course sources before answering. Missing deadlines remain unknown.")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=annotations)
    def list_assignments(course_id: Identifier | None = None) -> AssignmentList:
        """Получить учебные задания и известные сроки курса."""
        return AssignmentList(**store.list_assignments(course_id))

    @server.tool(annotations=annotations)
    def get_assignment(assignment_id: Identifier) -> AssignmentResult:
        """Получить требования, критерии, факты и подтверждающие источники задания."""
        try:
            return AssignmentResult(**store.get_assignment(assignment_id))
        except CatalogError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def search_materials(query: Query, limit: ResultLimit = 5,
                         assignment_id: Identifier | None = None,
                         include_research: StrictBool = False,
                         include_theory: StrictBool = False,
                         theory_topics: TheoryTopics | None = None) -> SearchResult:
        """Найти материалы проекта, исследования для советов и при include_theory общую теорию в Wikipedia. Внешние источники не задают требования проекта."""
        try:
            result = store.search_materials(query, limit, assignment_id)
            if include_theory and assignment_id is not None:
                store.get_assignment(assignment_id)
                from .theory_cache import TheoryCache
                cache = TheoryCache(store)
                if theory_topics:
                    calls = [asyncio.create_task(cache.search(assignment_id, topic, search=theory_search))
                             for topic in dict.fromkeys(theory_topics)]
                    try:
                        done, pending = await asyncio.wait(calls, timeout=6)
                        for call in pending:
                            call.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        found, statuses, seen = [], [], set()
                        for call in calls:
                            if call not in done:
                                statuses.append("unavailable")
                                continue
                            item = call.result()
                            statuses.append(item["status"])
                            for source in item["sources"]:
                                if source["source_id"] not in seen:
                                    found.append(source)
                                    seen.add(source["source_id"])
                                    break
                        theory = {"sources": found, "status": "ready" if found else
                                  "unavailable" if "unavailable" in statuses else "empty"}
                    finally:
                        for call in calls:
                            if not call.done():
                                call.cancel()
                        await asyncio.gather(*calls, return_exceptions=True)
                else:
                    theory = await cache.search(assignment_id, query, search=theory_search)
                result["matches"].extend(theory["sources"])
                result["theory_retrieval"] = {"status": theory["status"], "provider": "Wikipedia"}
            if include_research and knowledge is not None:
                # Verify ownership before shared retrieval, even when FTS found no rows.
                if assignment_id is not None:
                    store.get_assignment(assignment_id)
                try:
                    research = await knowledge.asearch(query, limit=min(limit, 3))
                    shared = [source for source in research.get("sources", [])
                              if source.get("scope") == "shared_research" and source.get("kind") == "research"]
                    # Each channel is bounded. Project evidence is never evicted by advice.
                    result["matches"].extend(shared[:3])
                    result["research_retrieval"] = {key: research.get(key) for key in
                        ("retrieval_status", "degraded_reason")}
                except Exception:
                    result["research_retrieval"] = {
                        "retrieval_status": "unavailable", "degraded_reason": "research_search_failed"}
            return SearchResult(**result)
        except CatalogError as exc:
            raise ToolError(f"{exc.code}: {exc.message}") from exc

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)  # stderr; stdout belongs to MCP.
    create_server(args.db, workspace_id=args.workspace).run(transport="stdio")


if __name__ == "__main__":
    main()
