"""Local web service with actual MCP requests and an explicitly separate demo mode."""

import asyncio
import base64
import html
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .agent import AgentError, HistoryMessage, StudyAgent
from .catalog import CatalogError, PDF_PATH, PROJECT_ROOT, Store, seed_database
from .cards import Card, CardError, CardSubstep, analyze_assignment, confirm_card
from .config import Settings
from .ingestion import MAX_UPLOAD_BYTES, UploadError, ingest_documents
from .limits import MAX_TEXT_CHARACTERS, MAX_TEXT_REQUEST_BYTES
from .knowledge import KnowledgeStore
from .live_budget import BudgetTransport
from .mcp_gateway import GatewayError, MCPGateway
from .planner import build_plan, prepare_task_overrides, tasks_from_card
from .providers import ChatCompletionsProvider, OfflineProvider, ProviderError
from .sessions import BrowserSessionMiddleware
from .codex_access import CodexAccess, CodexAccessError
from .codex_routes import create_codex_router
from .theory_cache import TheoryCache
from .chats import ChatStore, initialize_chats

logger = logging.getLogger("studynavigator")
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    assignment_id: Identifier = "personal-project"
    chat_id: Identifier | None = None
    history: Annotated[list[HistoryMessage], Field(max_length=6)] = Field(default_factory=list)


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    minutes: Annotated[StrictInt, Field(ge=1, le=4800)]
    estimate_source: str = "user"
    id: Identifier | None = None
    source_ids: Annotated[list[Identifier], Field(max_length=5)] = Field(default_factory=list)
    priority: Literal["high", "medium", "low"] = "medium"
    priority_reason: Annotated[str, StringConstraints(max_length=1000)] = ""
    completed: StrictBool = False
    actual_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None
    requirement_ids: list[Identifier] = Field(default_factory=list, max_length=12)
    expected_result: Annotated[str, StringConstraints(max_length=1000)] = ""
    done_when: Annotated[str, StringConstraints(max_length=1000)] = ""
    estimate_basis: Annotated[str, StringConstraints(max_length=1000)] = ""
    min_minutes: Annotated[StrictInt, Field(ge=1, le=4800)] | None = None
    max_minutes: Annotated[StrictInt, Field(ge=1, le=4800)] | None = None
    depends_on: list[Identifier] = Field(default_factory=list, max_length=20)
    # Accept saved metadata for browser round trips; trusted persistence supplies it.
    actual_source: Literal["user", "measured", "mixed", "unknown"] = "unknown"
    progress_status: Literal["pending", "in_progress", "paused", "interrupted", "partial", "blocked", "awaiting_review", "completed"] = "pending"
    work_state: Literal["active", "paused", "interrupted", "stopped"] = "stopped"
    measured_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] = 0
    estimated_actual_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None
    progress_summary: Annotated[str, StringConstraints(max_length=3000)] = ""
    progress_evidence: list[Annotated[str, StringConstraints(max_length=1000)]] = Field(default_factory=list, max_length=10)
    progress_updated_at: str | None = None
    substeps: list[CardSubstep] = Field(default_factory=list, max_length=32)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: Identifier = "personal-project"
    days: Annotated[StrictInt, Field(ge=1, le=30)]
    minutes_per_day: Annotated[StrictInt, Field(ge=15, le=480)]
    tasks: Annotated[list[TaskInput], Field(min_length=1, max_length=20)] | None = None
    start_date: date | None = None
    card_revision: Annotated[StrictInt, Field(ge=0)] | None = None


class TextProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARACTERS)]


class MaterialTextRequest(TextProjectRequest):
    external_url: Annotated[str, StringConstraints(max_length=2000)] | None = None


class AdaptiveTaskInput(TaskInput):
    # Metadata is accepted for round trips, then derived from the trusted card.
    # In particular an old estimate range must not reject a new user duration.
    id: Identifier


class DayAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: Annotated[StrictInt, Field(ge=1, le=30)]
    minutes: Annotated[StrictInt, Field(ge=0, le=480)]


class SavedPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: Annotated[StrictInt, Field(ge=0)]
    card_revision: Annotated[StrictInt, Field(ge=0)]
    availability: Annotated[list[DayAvailability], Field(min_length=1, max_length=30)]
    tasks: Annotated[list[AdaptiveTaskInput], Field(min_length=1, max_length=20)]
    start_date: date | None = None
    reserve_percent: Annotated[StrictInt, Field(ge=0, le=100)] | None = None


class AssistantApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: Identifier
    card_revision: Annotated[StrictInt, Field(ge=0)]
    plan_revision: Annotated[StrictInt, Field(ge=0)]
    apply: StrictBool


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: Card
    revision: Annotated[StrictInt, Field(ge=0)]


class RateLimiter:
    def __init__(self, limit):
        self.limit = limit
        self.requests = defaultdict(deque)

    def allow(self, key):
        now = time.monotonic()
        entries = self.requests[key]
        while entries and entries[0] <= now - 60:
            entries.popleft()
        if len(entries) >= self.limit:
            return False
        entries.append(now)
        # Small local service: remove inactive client buckets rather than retain them forever.
        if len(self.requests) > 1000:
            for old_key in list(self.requests):
                if not self.requests[old_key] or self.requests[old_key][-1] <= now - 60:
                    del self.requests[old_key]
        return True


class BodyLimitMiddleware:
    """Bound multipart uploads and JSON requests by their input type."""

    def __init__(self, app, limit=16_384):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT"}:
            return await self.app(scope, receive, send)
        is_materials = scope.get("path", "").startswith("/api/assignments/") and scope["path"].endswith("/materials")
        headers = dict(scope.get("headers", []))
        is_upload = scope.get("path") == "/api/assignments" or (is_materials and headers.get(b"content-type", b"").startswith(b"multipart/form-data"))
        is_confirmation = scope.get("path", "").startswith("/api/assignments/") and scope["path"].endswith("/confirm")
        is_workspace = scope.get("path") in ("/api/assignments/from-text","/api/codex/plan") or (scope.get("path", "").startswith("/api/assignments/") and scope["path"].endswith("/plan"))
        is_text = scope.get("path") == "/api/assignments/from-text" or is_materials
        limit = (MAX_UPLOAD_BYTES + 65_536 if is_upload else MAX_TEXT_REQUEST_BYTES if is_text else 98_304 if is_confirmation or is_workspace or is_materials
                 else 32_768 if scope.get("path") == "/api/plan" else self.limit)
        body = bytearray()
        try:
            async with asyncio.timeout(30 if is_upload else 10):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        message = "Общий размер PDF превышает 10 MiB." if is_upload else f"Запрос превышает {limit // 1024} KiB."
                        return await error_response("body_too_large", message, 413)(scope, receive, send)
                    body.extend(chunk)
                    if not event.get("more_body", False):
                        break
        except TimeoutError:
            return await error_response("body_timeout", "Истекло время передачи запроса.", 408)(scope, receive, send)
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


def error_response(code, message, status_code, request_id=None):
    return JSONResponse(status_code=status_code, content={
        "error": {"code": code, "message": message},
        "request_id": request_id or uuid.uuid4().hex[:12],
    })


async def connected_request(request, operation, timeout):
    """Cancel work on disconnect; MCP context stays in its own owning task."""
    stopped = asyncio.Event()

    async def disconnected():
        while not stopped.is_set() and not await request.is_disconnected():
            await asyncio.sleep(0.2)

    work = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        async with asyncio.timeout(timeout):
            completed, _ = await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if work in completed:
                return await work
            raise AgentError("client_disconnected", "Запрос отменён пользователем.")
    except TimeoutError as exc:
        raise AgentError("request_timeout", "Истекло время обработки запроса.") from exc
    finally:
        # is_disconnected uses an AnyIO cancel scope that may absorb the
        # asyncio cancellation. The explicit signal also terminates that case.
        stopped.set()
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)


async def extract_uploaded_documents(files, timeout):
    """Bound PDF parsing in a disposable child; only the parent commits data."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "studynavigator.ingestion", "--extract",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, cwd=PROJECT_ROOT,
    )
    payload = json.dumps({"files": [{"filename": name, "data": base64.b64encode(data).decode("ascii")}
                                    for name, data in files]}).encode()
    try:
        async with asyncio.timeout(timeout):
            output, _ = await process.communicate(payload)
        result = json.loads(output)
        if "error" in result:
            error = result["error"]
            raise UploadError(error["code"], error["message"], error["status_code"])
        if process.returncode or not isinstance(result.get("documents"), list):
            raise ValueError("invalid extraction result")
        return result["documents"]
    except TimeoutError as exc:
        raise UploadError("upload_timeout", "Обработка PDF заняла слишком много времени. Сократите файл.", 408) from exc
    except UploadError:
        raise
    except (ValueError, KeyError) as exc:
        raise UploadError("invalid_pdf", "Не удалось обработать PDF. Проверьте файл и попробуйте снова.") from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


def create_app(settings=None, provider=None, analysis_provider=None):
    settings = settings or Settings.from_env()
    provider = provider or (OfflineProvider() if settings.mode == "offline" else ChatCompletionsProvider(settings))
    if analysis_provider is None:
        analysis_provider = (ChatCompletionsProvider(replace(settings, model=settings.analysis_model))
                             if settings.mode == "live" and settings.analysis_model
                             and settings.analysis_model != settings.model else provider)
    live_budget = None
    if settings.mode == "live" and settings.live_budget_rub is not None:
        live_providers = list({id(item): item for item in (provider, analysis_provider)
                               if isinstance(item, ChatCompletionsProvider)}.values())
        transports = {id(item.transport): item.transport for item in live_providers if item.transport is not None}
        if len(transports) > 1:
            raise ValueError("Budgeted live providers must share one HTTP transport")
        existing = next(iter(transports.values()), None)
        if isinstance(existing, BudgetTransport):
            if (existing.ledger_path.resolve() != settings.live_budget_path.resolve()
                    or existing.summary()["budget_rub"] != settings.live_budget_rub):
                raise ValueError("Injected budget transport differs from server budget settings")
            live_budget = existing
        elif live_providers:
            live_budget = BudgetTransport(settings.live_budget_path, budget_rub=settings.live_budget_rub,
                                          inner=existing,
                                          tokenize_payload=any("/yandexgpt-5.1" in item.model
                                                               for item in live_providers))
        for item in live_providers:
            item.transport = live_budget
    limiter = RateLimiter(settings.rate_limit_per_minute)
    capacity = asyncio.Semaphore(settings.max_concurrent_requests)
    action_proposals = {}
    knowledge_reader = KnowledgeStore(os.environ.get("STUDYNAV_KNOWLEDGE_DB_PATH"))
    codex_access = CodexAccess(settings.database_path)

    @asynccontextmanager
    async def lifespan(app):
        try:
            seed_database(settings.database_path)
            initialize_chats(settings.database_path)
            codex_access.initialize()
            async with MCPGateway(settings.database_path, timeout=settings.tool_timeout) as gateway:
                app.state.mcp_tools = len(gateway.tools)
            yield
        finally:
            if live_budget is not None:
                await live_budget.close()

    app = FastAPI(title="Project Navigator", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.provider = provider
    app.state.analysis_provider = analysis_provider
    app.state.live_budget = live_budget
    app.state.mcp_tools = 0
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(BrowserSessionMiddleware)
    app.include_router(create_codex_router(codex_access))
    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "web"), name="static")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response("invalid_request", "Проверьте поля запроса и допустимые значения.", 422)

    @app.exception_handler(CodexAccessError)
    async def codex_error(request, exc):
        response = error_response(exc.code, exc.message, exc.status_code)
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = 'Bearer realm="Project Navigator"'
        if exc.status_code == 429:
            response.headers["Retry-After"] = "60"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        if request.method == "POST" and request.url.path == "/api/assignments" and exc.status_code == 400:
            return error_response("invalid_upload", "Проверьте форму: от 1 до 3 PDF-файлов и короткое название задания.", 400)
        return error_response("not_found" if exc.status_code == 404 else "request_error", str(exc.detail), exc.status_code)

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        status = 404 if exc.code == "unknown_assignment" else 503
        return error_response(exc.code, exc.message, status)

    @app.exception_handler(CatalogError)
    async def catalogue_error(request, exc):
        status = getattr(exc, "status_code", 404 if exc.code == "unknown_assignment" else 422)
        return error_response(exc.code, exc.message, status)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        status = 429 if exc.code == "provider_rate_limit" else 504 if "timeout" in exc.code else 503
        return error_response(exc.code, exc.message, status)

    def store(request):
        return Store(settings.database_path, workspace_id=request.state.workspace_id)

    def gateway(request):
        return MCPGateway(settings.database_path, timeout=settings.tool_timeout,
                          workspace_id=request.state.workspace_id)

    def source_for(request, source_id):
        if source_id.startswith("kb-"):
            return knowledge_reader.get_source(source_id)
        if source_id.startswith("web-theory-"):
            return TheoryCache(store(request)).source(source_id) or ChatStore(store(request)).source(source_id)
        return store(request).get_source(source_id)

    @app.get("/api/assignments/{assignment_id}/chats")
    async def chat_sessions(assignment_id: Identifier, request: Request):
        return ChatStore(store(request)).list(assignment_id)

    @app.post("/api/assignments/{assignment_id}/chats", status_code=201)
    async def new_chat(assignment_id: Identifier, request: Request):
        if not check_rate(request):
            return error_response("rate_limit", "Подождите немного перед созданием нового разговора.", 429)
        return ChatStore(store(request)).create(assignment_id)

    @app.get("/api/assignments/{assignment_id}/chats/{chat_id}")
    async def read_chat(assignment_id: Identifier, chat_id: Identifier, request: Request):
        return ChatStore(store(request)).get(assignment_id, chat_id)

    @app.delete("/api/assignments/{assignment_id}/chats/{chat_id}", status_code=204)
    async def delete_chat(assignment_id: Identifier, chat_id: Identifier, request: Request):
        ChatStore(store(request)).delete(assignment_id, chat_id)
        return Response(status_code=204)

    @app.get("/api/assignments/{assignment_id}/theory")
    async def theory_overview(assignment_id: Identifier, request: Request):
        return TheoryCache(store(request)).overview(assignment_id)

    @app.post("/api/assignments/{assignment_id}/theory")
    async def theory_refresh(assignment_id: Identifier, request: Request):
        owned = store(request)
        owned.get_assignment(assignment_id)
        if not check_rate(request) or capacity.locked():
            return error_response("busy", "Подождите немного и повторите подбор теории.", 429)
        async with capacity:
            return await connected_request(request, TheoryCache(owned).refresh(assignment_id), 18)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse((PROJECT_ROOT / "web/index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": provider.mode, "version": __version__}

    @app.get("/api/status")
    async def status():
        return {"mode": provider.mode, "live_configured": settings.live_configured,
                "model": provider.model, "analysis_model": analysis_provider.model,
                "mcp_tools": app.state.mcp_tools,
                "transport": "stdio", "version": __version__}

    @app.get("/api/assignments")
    async def assignments(request: Request):
        async with gateway(request) as client:
            return await client.call("list_assignments", {})

    @app.post("/api/assignments/from-text", status_code=201)
    async def create_text_project(body: TextProjectRequest, request: Request):
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        return store(request).create_text_assignment(body.title, body.text)

    @app.get("/api/assignments/{assignment_id}/plan")
    async def read_saved_plan(assignment_id: Identifier, request: Request):
        return store(request).get_plan(assignment_id)

    @app.put("/api/assignments/{assignment_id}/plan")
    async def save_project_plan(assignment_id: Identifier, body: SavedPlanRequest, request: Request):
        from .planner import build_adaptive_plan
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        selected = store(request).get_assignment(assignment_id)["assignment"]
        if not selected["is_uploaded"]:
            raise CardError("read_only_assignment", "Сохранение доступно для вашего проекта.", 403)
        if selected["status"] != "ready" or selected["card_revision"] != body.card_revision:
            raise CardError("stale_card", "Сначала подтвердите актуальную карточку проекта.", 409)
        try:
            tasks = prepare_task_overrides(tasks_from_card(selected["card"]),
                                           [task.model_dump() for task in body.tasks])
            from .planner import preserve_progress, reconcile_user_progress
            existing = (selected.get("project_context") or {}).get("plan")
            tasks = preserve_progress(tasks, existing["input"]["tasks"] if existing else [])
            if existing and existing["card_revision"] != body.card_revision:
                previous_tasks = {task["id"]: task for task in existing["input"]["tasks"]}
                for task in tasks:
                    original = previous_tasks.get(task["id"], {})
                    for key in ("completed", "actual_minutes"):
                        if key in original:
                            task[key] = original[key]
            tasks = reconcile_user_progress(tasks, existing["input"]["tasks"] if existing else [])
            availability = [item.model_dump() for item in body.availability]
            previous = (selected.get("project_context") or {}).get("plan")
            reserve = body.reserve_percent if body.reserve_percent is not None else (
                previous["input"].get("reserve_percent", 0) if previous else 20)
            result = build_adaptive_plan(tasks, availability, start_date=body.start_date,
                                         reserve_percent=reserve, card=selected["card"])
            result["assignment_id"] = assignment_id
            result["card_revision"] = body.card_revision
            plan_input = {"tasks": tasks, "availability": availability,
                          "start_date": body.start_date.isoformat() if body.start_date else None,
                          "reserve_percent": reserve}
            return store(request).save_plan(assignment_id, revision=body.revision,
                                            card_revision=body.card_revision,
                                            plan_input=plan_input, result=result)
        except CardError:
            raise
        except ValueError:
            return error_response("invalid_plan", "Проверьте задачи, зависимости и доступное время.", 422)

    @app.get("/api/assignments/{assignment_id}")
    async def assignment(assignment_id: Identifier, request: Request):
        async with gateway(request) as client:
            return await client.call("get_assignment", {"assignment_id": assignment_id})

    @app.post("/api/assignments", status_code=201)
    async def upload_assignment(request: Request):
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        if capacity.locked():
            return error_response("busy", "Сервис занят. Повторите загрузку чуть позже.", 429)
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return error_response("invalid_upload", "Выберите PDF-файлы в форме загрузки.", 422)
        async with request.form(max_files=3, max_fields=1, max_part_size=1024) as form:
            if any(name not in {"files", "title"} for name, _ in form.multi_items()):
                return error_response("invalid_upload", "Проверьте поля загрузки.", 422)
            parts = form.getlist("files")
            title = form.get("title", "")
            if not 1 <= len(parts) <= 3 or not all(isinstance(part, UploadFile) for part in parts) or not isinstance(title, str):
                return error_response("invalid_upload", "Для задания выберите от 1 до 3 PDF-файлов.", 422)
            files, total = [], 0
            for part in parts:
                content = bytearray()
                while chunk := await part.read(65_536):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        return error_response("upload_too_large", "Общий размер PDF превышает 10 MiB.", 413)
                    content.extend(chunk)
                files.append((part.filename or "", bytes(content)))

            async def operation():
                extracted = await extract_uploaded_documents(files, settings.upload_timeout)
                return ingest_documents(settings.database_path, settings.uploads_path,
                                        request.state.workspace_id, files, title,
                                        extracted_documents=extracted)

            async with capacity:
                try:
                    return await connected_request(request, operation(), settings.upload_timeout + 5)
                except AgentError as exc:
                    return error_response(exc.code, exc.message, 499 if exc.code == "client_disconnected" else 408)
                except UploadError:
                    raise
                except Exception as exc:
                    logger.error("upload_failed kind=%s", type(exc).__name__)
                    return error_response("upload_failed", "Не удалось сохранить материалы. Попробуйте загрузить их снова.", 500)

    @app.post("/api/assignments/{assignment_id}/assistant-actions")
    async def apply_assistant_proposal(assignment_id: Identifier, body: AssistantApplyRequest, request: Request):
        from .assistant_actions import apply_assistant_actions
        store(request).get_assignment(assignment_id)
        key = (request.state.workspace_id, assignment_id, body.proposal_id)
        proposal = action_proposals.get(key)
        if not body.apply:
            raise CardError("apply_required", "Применение требует явного подтверждения.", 422)
        if proposal is None or proposal["expires"] < time.monotonic():
            action_proposals.pop(key, None)
            raise CardError("unknown_proposal", "Предложение устарело. Запросите его снова.", 409)
        if body.card_revision != proposal["card_revision"] or body.plan_revision != proposal["plan_revision"]:
            raise CardError("proposal_conflict", "Версии предложения не совпадают. Запросите его снова.", 409)
        result = apply_assistant_actions(store(request), assignment_id, proposal["proposal"], body.card_revision, body.plan_revision,
                                         materials_revision=proposal["materials_revision"], estimate_origin=proposal.get("estimate_origin", "user"))
        action_proposals.pop(key, None)
        return result

    @app.get("/api/assignments/{assignment_id}/analytics")
    async def project_analytics(assignment_id: Identifier, request: Request):
        return {"analytics": store(request).get_project_context(assignment_id)["analytics"]}

    @app.post("/api/assignments/{assignment_id}/materials", status_code=201)
    async def append_materials(assignment_id: Identifier, request: Request):
        selected = store(request).get_assignment(assignment_id)["assignment"]
        if not selected["is_uploaded"]:
            raise CardError("read_only_assignment", "Материалы можно добавлять только в свой проект.", 403)
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = MaterialTextRequest.model_validate(await request.json())
            except (ValueError, TypeError):
                return error_response("invalid_material", "Укажите название, текст до 100 000 символов и необязательную HTTP(S)-ссылку.", 422)
            return store(request).append_material_text(assignment_id, body.title, body.text, body.external_url)
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return error_response("invalid_upload", "Выберите PDF или добавьте текст материала.", 422)
        if capacity.locked():
            return error_response("busy", "Сервис занят. Повторите загрузку позже.", 429)
        async with request.form(max_files=3, max_fields=1, max_part_size=1024) as form:
            parts, title = form.getlist("files"), form.get("title", "")
            if (any(name not in {"files", "title"} for name, _ in form.multi_items()) or not 1 <= len(parts) <= 3
                    or not all(isinstance(part, UploadFile) for part in parts) or not isinstance(title, str)):
                return error_response("invalid_upload", "Выберите от 1 до 3 PDF-файлов.", 422)
            files, total = [], 0
            for part in parts:
                content = bytearray()
                while chunk := await part.read(65536):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        return error_response("upload_too_large", "Общий размер PDF превышает 10 MiB.", 413)
                    content.extend(chunk)
                files.append((part.filename or "", bytes(content)))
            async def operation():
                extracted = await extract_uploaded_documents(files, settings.upload_timeout)
                return store(request).append_material_pdfs(assignment_id, settings.uploads_path, files, title, extracted)
            async with capacity:
                try:
                    return await connected_request(request, operation(), settings.upload_timeout + 5)
                except AgentError as exc:
                    return error_response(exc.code, exc.message, 499 if exc.code == "client_disconnected" else 408)

    @app.get("/api/assignments/{assignment_id}/materials")
    async def assignment_materials(assignment_id: Identifier, request: Request,
                                   q: Annotated[str, StringConstraints(max_length=500)] = ""):
        selected = store(request).get_assignment(assignment_id)["assignment"]
        if not q.strip():
            return {"matches": selected["sources"]}
        if not check_rate(request) or capacity.locked():
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        async with capacity:
            async with gateway(request) as client:
                return await client.call("search_materials", {"query": q, "assignment_id": assignment_id, "limit": 10})

    @app.post("/api/assignments/{assignment_id}/analyze")
    async def analyze(assignment_id: Identifier, body: AnalyzeRequest, request: Request):
        selected = store(request).get_assignment(assignment_id)["assignment"]
        if not selected.get("is_uploaded"):
            raise CardError("read_only_assignment", "Подготовленный пример уже разобран. Загрузите своё задание.", 403)
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        if capacity.locked():
            return error_response("busy", "Сервис занят. Повторите разбор чуть позже.", 429)

        async def operation():
            async with gateway(request) as client:
                result = await analyze_assignment(client, analysis_provider, assignment_id)
            saved = store(request).save_card(assignment_id, result["card"],
                                             expected_revision=selected["card_revision"],
                                             expected_materials_revision=selected["materials_revision"])
            return {**result, **saved, "revision": saved["assignment"]["card_revision"]}

        try:
            async with capacity:
                return await connected_request(request, operation(), settings.request_timeout)
        except AgentError as exc:
            return error_response(exc.code, exc.message, 499 if exc.code == "client_disconnected" else 504)
        except (CardError, ProviderError, GatewayError):
            raise
        except Exception as exc:
            logger.error("analysis_failed kind=%s", type(exc).__name__)
            return error_response("analysis_failed", "Не удалось разобрать задание. Карточка не изменена; повторите попытку.", 500)

    @app.post("/api/assignments/{assignment_id}/confirm")
    async def confirm(assignment_id: Identifier, body: ConfirmRequest, request: Request):
        selected = store(request).get_assignment(assignment_id)["assignment"]
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        if not selected.get("is_uploaded"):
            raise CardError("read_only_assignment", "Подготовленный пример доступен только для чтения.", 403)
        normalized = confirm_card(body.card, selected.get("card"), selected["sources"])
        if not normalized["tasks"]:
            normalized["tasks"] = [{key: value for key, value in task.items() if key not in {"completed", "actual_minutes"}}
                                   for task in tasks_from_card(normalized)]
        saved = store(request).save_card(assignment_id, normalized, confirmed=True,
                                         expected_revision=body.revision)
        return {**saved, "revision": saved["assignment"]["card_revision"]}

    @app.delete("/api/assignments/{assignment_id}", status_code=204)
    async def delete_assignment(assignment_id: Identifier, request: Request):
        store(request).delete_assignment(assignment_id, settings.uploads_path)
        return Response(status_code=204)

    @app.get("/api/sources/{source_id}")
    async def source_json(source_id: Identifier, request: Request):
        source = source_for(request, source_id)
        if source is None:
            raise HTTPException(404, "Источник не найден.")
        return source

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    async def source_page(source_id: Identifier, request: Request):
        from .text_format import render_structured_text

        source = source_for(request, source_id)
        if source is None:
            raise HTTPException(404, "Источник не найден.")
        title, section = html.escape(source["title"]), html.escape(source.get("section") or "Фрагмент")
        excerpt = render_structured_text(source["excerpt"])
        if source.get("scope") == "web_theory":
            from urllib.parse import urlsplit
            original = source.get("external_url") or ""
            parsed = urlsplit(original)
            original = html.escape(original if parsed.scheme == "https" and parsed.hostname in {"ru.wikipedia.org", "en.wikipedia.org"} else "#", quote=True)
            return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<meta name="color-scheme" content="light dark"><script src="/static/theme.js"></script>
<link rel="stylesheet" href="/static/styles.css"></head><body><main class="source-reader">
<a href="/">← К проекту</a><p>Общая теория · Wikipedia</p><h1>{title}</h1>
<article class="structured-text">{excerpt}</article><p>Участники Википедии · CC BY-SA · выдержка из статьи.</p>
<p><a href="{original}" target="_blank" rel="noopener noreferrer">Открыть оригинал ↗</a></p>
<p>Общая теория не подтверждает условия и ответы конкретного учебника.</p></main></body></html>""")
        if source.get("scope") == "shared_research":
            original = html.escape(source.get("external_url") or "", quote=True)
            pdf = html.escape(source.get("file_url") or "", quote=True)
            authors = html.escape(str(source.get("authors") or ""))
            year = html.escape(str(source.get("year") or ""))
            doi = html.escape(source.get("doi") or "")
            return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<meta name="color-scheme" content="light dark"><script src="/static/theme.js?v=20261109-route-theme"></script>
<link rel="stylesheet" href="/static/styles.css?v=20261109-route-theme"></head><body><main class="source-reader">
<a href="/">← К проекту</a><p>Исследование · {year}</p><h1>{title}</h1><p>{authors}</p><p>DOI: {doi} · {section}</p>
<article class="structured-text">{excerpt}</article><p><a href="{pdf}" target="_blank" rel="noopener noreferrer">Открыть исходный PDF ↗</a></p>
<p><a href="{original}" target="_blank" rel="noopener noreferrer">Открыть статью у издателя ↗</a></p></main></body></html>""")
        if source.get("kind") == "text":
            return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<meta name="color-scheme" content="light dark"><script src="/static/theme.js?v=20261109-route-theme"></script>
<link rel="stylesheet" href="/static/styles.css?v=20261109-route-theme"></head><body><main class="source-reader">
<a href="/">← К проекту</a><p>Исходный текст проекта</p><h1>{title}</h1><p>{section}</p>
<article class="structured-text">{excerpt}</article></main></body></html>""")
        page = int(source["page"])
        document_url = html.escape(source.get("document_url") or "/materials/personal-project.pdf", quote=True)
        body = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — источник Project Navigator</title><meta name="color-scheme" content="light dark">
<script src="/static/theme.js?v=20261109-route-theme"></script><link rel="stylesheet" href="/static/styles.css?v=20261109-route-theme"></head>
<body><main id="source-reader" class="source-reader">
<header class="source-reader-header"><a class="source-back" href="/">← К ассистенту</a>
<p class="source-meta">Исходный материал · страница {page}</p><h1>{title}</h1>
<p class="source-section">{section}</p></header>
<article class="source-content" aria-label="Текст источника"><div class="structured-text">{excerpt}</div></article>
<footer class="source-reader-footer"><a class="source-pdf-link" href="{document_url}#page={page}" target="_blank" rel="noopener">Открыть страницу {page} исходного PDF ↗</a>
<p>Сверяйте извлечённый текст с оригиналом; интерпретация ассистента не заменяет его требования.</p></footer>
</main></body></html>"""
        return HTMLResponse(body)

    @app.get("/materials/personal-project.pdf")
    async def material_pdf():
        return FileResponse(PDF_PATH, media_type="application/pdf", filename="Личный проект.pdf", content_disposition_type="inline")

    @app.get("/research/{source_id}.pdf")
    async def research_pdf(source_id: Identifier):
        path = knowledge_reader.resolve_file(source_id)
        if path is None:
            raise HTTPException(404, "Исследование не найдено.")
        return FileResponse(path, media_type="application/pdf", filename=f"{source_id}.pdf", content_disposition_type="inline")

    @app.get("/materials/{document_id}.pdf")
    async def uploaded_pdf(document_id: Identifier, request: Request):
        document = store(request).get_document(document_id)
        if document is None:
            raise HTTPException(404, "Документ не найден.")
        path = settings.uploads_path / document["stored_name"]
        if not path.is_file():
            raise HTTPException(404, "Документ не найден.")
        return FileResponse(path, media_type="application/pdf", filename=document["filename"], content_disposition_type="inline")

    def check_rate(request):
        key = request.client.host if request.client else "local"
        return limiter.allow(key)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        selected = store(request).get_assignment(body.assignment_id)["assignment"]
        if selected["status"] != "ready":
            return error_response("assignment_not_ready", "Сначала заполните и подтвердите карточку задания.", 409)
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429, request_id)
        if capacity.locked():
            return error_response("busy", "Сервис занят. Повторите запрос чуть позже.", 429, request_id)

        chats = ChatStore(store(request))
        history = chats.history(body.assignment_id, body.chat_id) if body.chat_id else [item.model_dump() for item in body.history]
        turn_id = chats.begin(body.assignment_id, body.chat_id, body.message) if body.chat_id else None
        from .assistant_actions import resolve_duration_context, regroup_proposal
        from .conversation import troubleshooting_reply
        resolved = {"proposal": None, "clarification": None, "user_message": body.message}

        def persist_turn(**kwargs):
            if turn_id:
                try:
                    chats.finish(body.assignment_id, body.chat_id, turn_id, **kwargs)
                except CatalogError as exc:
                    if exc.code not in {"unknown_assignment", "unknown_chat"}:
                        raise

        def failure(code, message, status):
            persist_turn(error={"code": code, "message": message})
            return error_response(code, message, status, request_id)

        def local_result():
            proposal = (regroup_proposal(selected, body.message) or resolved["proposal"]) if selected.get("is_uploaded") else None
            if proposal:
                return {"answer": "Подготовлено изменение действий. Проверьте предложение и нажмите «Применить».",
                        "status": "answered", "answer_basis": "project_context", "response_origin": "local",
                        "action_proposal": proposal, "mode": provider.mode, "sources": [], "facts": [],
                        "trace": [], "usage": None, "format_repair_attempted": False}
            if resolved["clarification"]:
                return {"answer": resolved["clarification"], "status": "insufficient_data",
                        "answer_basis": "project_context", "response_origin": "local", "action_proposal": None,
                        "mode": provider.mode, "sources": [], "facts": [], "trace": [], "usage": None,
                        "format_repair_attempted": False}
            explanation = troubleshooting_reply(body.message, history)
            if explanation:
                return {"answer": explanation, "status": "answered", "answer_basis": "project_context",
                        "response_origin": "local", "action_proposal": None, "mode": provider.mode,
                        "sources": [], "facts": [], "trace": [], "usage": None, "format_repair_attempted": False}
            return None

        async def operation():
            if selected.get("is_uploaded"):
                resolved.update(resolve_duration_context(selected, body.message, history))
            result = local_result()
            if result is not None:
                return result
            async with gateway(request) as client:
                agent = StudyAgent(client, provider, max_calls=settings.max_calls,
                                   request_timeout=settings.request_timeout)
                return await agent.run(resolved["user_message"], body.assignment_id,
                                       history=[{"role": item["role"], "content": item["content"]} for item in history])

        try:
            async with capacity:
                result = await connected_request(request, operation(), settings.request_timeout)
            if result.get("action_proposal"):
                from .assistant_actions import prepare_assistant_actions
                estimate_origin = "user" if result.get("response_origin") == "local" else "estimate"
                proposal = prepare_assistant_actions(selected, result["action_proposal"], resolved["user_message"], estimate_origin=estimate_origin)
                from .assistant_actions import _preview, ActionProposal
                _, _, preview_result = _preview(selected, ActionProposal.model_validate(proposal), estimate_origin=estimate_origin)
                proposal_id = uuid.uuid4().hex
                plan = selected["project_context"]["plan"]
                card_revision, plan_revision = selected["card_revision"], plan["revision"] if plan else 0
                now = time.monotonic()
                for key in list(action_proposals):
                    if action_proposals[key]["expires"] < now:
                        del action_proposals[key]
                if len(action_proposals) >= 1000:
                    del action_proposals[next(iter(action_proposals))]
                action_proposals[(request.state.workspace_id, body.assignment_id, proposal_id)] = {
                    "proposal": proposal, "expires": now + 900, "card_revision": card_revision, "plan_revision": plan_revision,
                    "materials_revision": selected["materials_revision"], "estimate_origin": estimate_origin}
                result["action_proposal"] = {**proposal, "proposal_id": proposal_id,
                    "card_revision": card_revision, "plan_revision": plan_revision,
                    "preview_tasks": preview_result["tasks"], "required_minutes": preview_result["required_minutes"]}
            result.update(request_id=request_id, elapsed_ms=round((time.monotonic() - started) * 1000),
                          assignment_id=body.assignment_id)
            if turn_id:
                result["chat_id"] = body.chat_id
                persist_turn(result=result)
            logger.info("chat_complete id=%s mode=%s status=%s tools=%d", request_id, provider.mode,
                        result["status"], len(result["trace"]))
            return result
        except (AgentError, GatewayError) as exc:
            logger.warning("chat_failed id=%s code=%s", request_id, exc.code)
            code = 404 if exc.code == "unknown_assignment" else 429 if exc.code == "provider_rate_limit" else 504 if "timeout" in exc.code else 503
            if exc.code == "client_disconnected":
                code = 499
            return failure(exc.code, exc.message, code)
        except CardError as exc:
            return failure(exc.code, exc.message, exc.status_code)
        except asyncio.CancelledError:
            persist_turn(error={"code": "cancelled", "message": "Запрос остановлен. Можно отправить вопрос снова."})
            raise
        except Exception as exc:
            logger.error("chat_internal_error id=%s kind=%s", request_id, type(exc).__name__)
            return failure("internal_error", "Не удалось обработать запрос. Попробуйте позже.", 500)

    @app.post("/api/plan")
    async def plan(body: PlanRequest, request: Request):
        if not check_rate(request):
            return error_response("rate_limit", "Слишком много запросов. Подождите минуту.", 429)
        # Validate the selected assignment against the same catalogue used by MCP.
        try:
            selected = store(request).get_assignment(body.assignment_id)["assignment"]
            if selected["status"] != "ready":
                return error_response("assignment_not_ready", "Сначала заполните и подтвердите карточку задания.", 409)
            submitted = [task.model_dump(exclude_none=True) for task in body.tasks] if body.tasks is not None else None
            if selected.get("is_uploaded"):
                if body.card_revision != selected["card_revision"]:
                    return error_response("stale_card", "Карточка изменилась. Откройте её заново и пересчитайте план.", 409)
                tasks = prepare_task_overrides(tasks_from_card(selected["card"]), submitted)
            else:
                # One-off legacy inputs are user estimates, never document facts.
                tasks = [{**task, "estimate_source": "user", "source_ids": []} for task in submitted] if submitted is not None else None
            effective_days = body.days
            deadline = (selected.get("card") or {}).get("deadline") or {}
            if body.start_date is not None and deadline.get("date") and any(not task.get("completed", False) for task in tasks or [{}]):
                remaining_days = (date.fromisoformat(deadline["date"]) - body.start_date).days + 1
                if remaining_days < 1:
                    raise ValueError("start date is after the known deadline")
                effective_days = min(body.days, remaining_days)
            result = build_plan(days=effective_days, minutes_per_day=body.minutes_per_day,
                                tasks=tasks, start_date=body.start_date, assignment_id=body.assignment_id)
            result["card_revision"] = selected["card_revision"]
            if selected.get("card"):
                result["deadline"] = selected["card"]["deadline"]
            if effective_days != body.days:
                result["assumptions"].append(f"Горизонт ограничен известным сроком: {effective_days} дн. из выбранных {body.days}.")
            result["days"] = effective_days
            return result
        except ValueError as exc:
            if getattr(exc, "code", None) == "unknown_assignment":
                return error_response("unknown_assignment", "Учебное задание не найдено.", 404)
            return error_response("invalid_plan", "Проверьте длительности и ограничения плана.", 422)

    return app


app = create_app()
