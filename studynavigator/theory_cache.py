"""Workspace-scoped cache of public theory, never project requirements."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time

import httpx

from .theory import WikimediaSearch, build_task_theory, local_task_theory, theory_query


def project_tasks(assignment):
    plan = (assignment.get("project_context") or {}).get("plan")
    if plan and not plan.get("stale"):
        return plan["result"].get("tasks", [])
    return (assignment.get("card") or {}).get("tasks", [])


def fingerprint(assignment):
    identity = [assignment.get("card_revision"), assignment.get("materials_revision"),
                [(t["id"], t["title"], t.get("source_ids", [])) for t in project_tasks(assignment)]]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class TheoryCache:
    def __init__(self, store):
        self.store = store
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS theory_cache (
                assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL, cache_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (assignment_id,workspace_id,cache_key))""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.store.db_path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self, assignment_id, key, identity="", ttl=86400):
        self.store.get_assignment(assignment_id)
        with self.connection() as db:
            row = db.execute("SELECT payload,fingerprint,updated_at FROM theory_cache WHERE assignment_id=? AND workspace_id=? AND cache_key=?",
                             (assignment_id, self.store.workspace_id or "", key)).fetchone()
        if row and row[1] == identity and time.time() - row[2] < ttl:
            return json.loads(row[0])
        return None

    def write(self, assignment_id, key, payload, identity=""):
        self.store.get_assignment(assignment_id)
        workspace = self.store.workspace_id or ""
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO theory_cache VALUES (?,?,?,?,?,?)",
                       (assignment_id, workspace, key, identity, json.dumps(payload, ensure_ascii=False), time.time()))
            db.execute("""DELETE FROM theory_cache WHERE assignment_id=? AND workspace_id=? AND cache_key!='overview'
                AND rowid NOT IN (SELECT rowid FROM theory_cache WHERE assignment_id=? AND workspace_id=?
                ORDER BY updated_at DESC LIMIT 32)""", (assignment_id, workspace, assignment_id, workspace))

    async def search(self, assignment_id, query, *, search=None):
        self.store.get_assignment(assignment_id)
        query = theory_query(query)
        if not query:
            return {"sources": [], "status": "empty"}
        key = "query:" + hashlib.sha256(query.encode()).hexdigest()
        cached = self.read(assignment_id, key, ttl=3600)
        if cached is not None:
            return cached
        try:
            async with asyncio.timeout(6):
                sources = await (search or WikimediaSearch().search)(query)
            sources = [dict(s, url="/sources/" + s["source_id"]) for s in sources[:2]
                       if s.get("scope") == "web_theory"]
            result = {"sources": sources, "status": "ready" if sources else "empty"}
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            return {"sources": [], "status": "unavailable"}
        self.write(assignment_id, key, result)
        return result

    def overview(self, assignment_id):
        assignment = self.store.get_assignment(assignment_id)["assignment"]
        cached = self.read(assignment_id, "overview", fingerprint(assignment))
        result = cached or local_task_theory(assignment, tasks=project_tasks(assignment))
        return dict(result, assignment_id=assignment_id)

    async def refresh(self, assignment_id, *, search=None):
        assignment = self.store.get_assignment(assignment_id)["assignment"]

        async def retrieve(query):
            result = await self.search(assignment_id, query, search=search)
            if result["status"] == "unavailable":
                raise TimeoutError("theory_unavailable")
            return result["sources"]

        result = await build_task_theory(assignment, tasks=project_tasks(assignment), search=retrieve)
        self.write(assignment_id, "overview", result, fingerprint(assignment))
        return dict(result, assignment_id=assignment_id)

    def source(self, source_id):
        # Joining the current owner also protects cached sources after deletion.
        with self.connection() as db:
            rows = db.execute("""SELECT c.payload FROM theory_cache c JOIN assignments a USING(assignment_id)
                WHERE c.workspace_id=? AND (a.workspace_id IS NULL OR a.workspace_id=?)
                ORDER BY c.updated_at DESC""", (self.store.workspace_id or "", self.store.workspace_id)).fetchall()
        for row in rows:
            for source in json.loads(row[0]).get("sources", []):
                if source.get("source_id") == source_id and source.get("scope") == "web_theory":
                    return source
        return None
