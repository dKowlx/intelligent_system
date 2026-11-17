"""Browser-owned credential management and scoped agent HTTP operations."""
from collections import OrderedDict, deque
import ipaddress
import threading
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .catalog import Store
from .codex_access import CodexAccess, CodexAccessError
from .mcp_server import Identifier

Revision = Annotated[StrictInt, Field(ge=0)]
Text = Annotated[str, Field(min_length=1, max_length=3000, pattern=r"\S")]
Evidence = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewConnection(StrictBody):
    label: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    expires_days: Annotated[StrictInt, Field(ge=1, le=30)] = 7


class Search(StrictBody):
    query: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 5


class Claim(StrictBody):
    task_id: Identifier
    card_revision: Revision
    plan_revision: Revision
    request_id: Identifier


class Report(StrictBody):
    run_id: Identifier
    status: Literal["awaiting_review", "blocked"]
    summary: Text
    evidence: Annotated[list[Evidence], Field(max_length=10)]
    request_id: Identifier


class Accept(StrictBody):
    card_revision: Revision
    plan_revision: Revision


class ConnectionRateLimit:
    """Bounded process-local buckets, appropriate for the single-worker MVP."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def check(self, key, limit=90):
        now = time.monotonic()
        with self.lock:
            if key not in self.entries and len(self.entries) >= 2048:
                self.entries.popitem(last=False)
            bucket = self.entries.setdefault(key, deque())
            self.entries.move_to_end(key)
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= limit:
                raise CodexAccessError("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
            bucket.append(now)


def create_codex_router(access: CodexAccess) -> APIRouter:
    router = APIRouter()
    limits = ConnectionRateLimit()

    def secure(request):
        try:
            peer_is_local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            peer_is_local = False
        loopback = peer_is_local and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        if request.url.scheme != "https" and not loopback:
            raise CodexAccessError("https_required", "Подключение доступно только по HTTPS.", 403)

    def browser(request, purpose="read"):
        secure(request)
        workspace = request.state.workspace_id
        limits.check(("browser", workspace, purpose), 10 if purpose == "create" else 90)
        return workspace

    def agent(request):
        secure(request)
        # Never accept a cookie or model-provided project/workspace as agent authority.
        host = request.client.host if request.client else "unknown"
        limits.check(("agent_ip", host), 180)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 140:
            raise CodexAccessError("invalid_token", "Нужен действующий ключ подключения.", 401)
        context = access.authenticate(header[7:])
        limits.check(("agent", context["connection_id"]), 90)
        return context

    @router.get("/api/assignments/{assignment_id}/codex/connections")
    def connections(assignment_id: Identifier, request: Request):
        return {"connections": access.list_connections(browser(request), assignment_id)}

    @router.post("/api/assignments/{assignment_id}/codex/connections", status_code=201)
    def connect(assignment_id: Identifier, body: NewConnection, request: Request):
        return access.create_connection(browser(request, "create"), assignment_id,
                                        body.label, body.expires_days)

    @router.delete("/api/assignments/{assignment_id}/codex/connections/{connection_id}")
    def revoke(assignment_id: Identifier, connection_id: Identifier, request: Request):
        access.revoke_connection(browser(request), assignment_id, connection_id)
        return {"revoked": True}

    @router.get("/api/assignments/{assignment_id}/codex/runs")
    def runs(assignment_id: Identifier, request: Request):
        workspace = browser(request)
        selected = Store(access.db_path, workspace).get_assignment(assignment_id)["assignment"]
        plan = selected.get("project_context", {}).get("plan")
        return {"runs": access.list_runs(workspace, assignment_id),
                "card_revision": selected["card_revision"],
                "plan_revision": plan["revision"] if plan else 0}

    @router.post("/api/assignments/{assignment_id}/codex/runs/{run_id}/accept")
    def accept(assignment_id: Identifier, run_id: Identifier, body: Accept, request: Request):
        return access.accept_run(browser(request), assignment_id, run_id,
                                 body.card_revision, body.plan_revision)

    @router.post("/api/assignments/{assignment_id}/codex/runs/{run_id}/dismiss")
    def dismiss(assignment_id: Identifier, run_id: Identifier, body: StrictBody, request: Request):
        return {"run": access.dismiss_run(browser(request), assignment_id, run_id)}

    @router.get("/api/codex/project")
    def project(request: Request):
        context = agent(request)
        return access.get_project(context)

    @router.post("/api/codex/search")
    def search(body: Search, request: Request):
        return access.search_materials(agent(request), body.query, body.limit)

    @router.post("/api/codex/claim")
    def claim(body: Claim, request: Request):
        return {"run": access.claim_task(agent(request), **body.model_dump())}

    @router.post("/api/codex/report")
    def report(body: Report, request: Request):
        return {"run": access.report_task(agent(request), **body.model_dump())}

    return router
