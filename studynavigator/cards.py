"""Assignment cards with explicit document/user provenance, independent of a provider."""

from __future__ import annotations

import re
from datetime import date as CalendarDate
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, ValidationError, field_validator, model_validator, model_serializer

from .catalog import CatalogError

SourceID = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class CardError(CatalogError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(code, message)
        self.status_code = status_code


class CardField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: SourceID | None = None
    text: Text = ""
    origin: Literal["document", "user"] = "user"
    source_ids: list[SourceID] = Field(default_factory=list, max_length=5)

    @model_serializer(mode="wrap")
    def omit_absent_id(self, handler):
        result = handler(self)
        if self.id is None:
            result.pop("id", None)
        return result


class CardDeadline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str | None = None
    origin: Literal["document", "user", "unknown"] = "unknown"
    source_ids: list[SourceID] = Field(default_factory=list, max_length=5)

    @field_validator("date")
    @classmethod
    def iso_date(cls, value):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("date must be YYYY-MM-DD")
            CalendarDate.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def consistent_unknown(self):
        if self.date is None:
            if self.origin != "unknown" or self.source_ids:
                raise ValueError("an unknown deadline has no origin or sources")
        elif self.origin == "unknown":
            raise ValueError("a known deadline needs an origin")
        return self


class CardSubstep(BaseModel):
    """A concrete part of the parent's work, not a separate progress record."""
    model_config = ConfigDict(extra="forbid")
    id: SourceID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    minutes: Annotated[StrictInt, Field(ge=1, le=4800)]
    done_when: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    actual_source: Literal["user", "measured", "mixed", "unknown"] = "unknown"
    progress_summary: Annotated[str, StringConstraints(max_length=3000)] = ""
    progress_evidence: list[Annotated[str, StringConstraints(max_length=1000)]] = Field(default_factory=list, max_length=10)
    progress_updated_at: str | None = None
    completed: StrictBool = False
    actual_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None
    progress_status: Literal["pending", "in_progress", "paused", "interrupted", "partial", "blocked", "awaiting_review", "completed"] = "pending"
    work_state: Literal["active", "paused", "interrupted", "stopped"] = "stopped"
    measured_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] = 0
    estimated_actual_minutes: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None


    @model_serializer(mode="wrap")
    def omit_empty_progress(self, handler):
        result = handler(self)
        for key in ("actual_source", "progress_summary", "progress_evidence", "progress_updated_at",
                    "completed", "actual_minutes", "progress_status", "work_state",
                    "measured_minutes", "estimated_actual_minutes"):
            field = type(self).model_fields[key]
            default = field.default_factory() if field.default_factory else field.default
            if result.get(key) == default:
                result.pop(key, None)
        return result


def apportion_substeps(substeps, minutes):
    """Reserve one minute per step, then distribute the remainder by weight.

    Largest remainders break ties in the authored order. This also handles a
    user changing the parent estimate without invalidating its decomposition.
    """
    if not substeps:
        return []
    if minutes < len(substeps):
        raise ValueError("task minutes must allow at least one minute per substep; combine or reestimate substeps")
    total = sum(step.minutes for step in substeps)
    if total == minutes:
        return substeps
    budget = minutes - len(substeps)
    shares = [divmod(budget * step.minutes, total) for step in substeps]
    allocated = [1 + share[0] for share in shares]
    order = sorted(range(len(substeps)), key=lambda index: (-shares[index][1], index))
    for index in order[:minutes - sum(allocated)]:
        allocated[index] += 1
    return [step.model_copy(update={"minutes": duration}) for step, duration in zip(substeps, allocated)]


class CardTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    minutes: Annotated[StrictInt, Field(ge=1, le=4800)]
    estimate_source: Literal["estimate", "user", "document"] = "estimate"
    source_ids: list[SourceID] = Field(default_factory=list, max_length=5)
    requirement_ids: list[SourceID] = Field(default_factory=list, max_length=12)
    expected_result: Text = ""
    done_when: Text = ""
    estimate_basis: Text = ""
    min_minutes: Annotated[StrictInt, Field(ge=1, le=4800)] | None = None
    max_minutes: Annotated[StrictInt, Field(ge=1, le=4800)] | None = None
    priority: Literal["high", "medium", "low"] = "medium"
    priority_reason: Text = ""
    depends_on: list[SourceID] = Field(default_factory=list, max_length=20)
    substeps: list[CardSubstep] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def estimate_range(self):
        if len({step.id for step in self.substeps}) != len(self.substeps):
            raise ValueError("substep IDs must be unique within a task")
        self.substeps = apportion_substeps(self.substeps, self.minutes)
        if (self.min_minutes is None) != (self.max_minutes is None):
            raise ValueError("provide both estimate bounds")
        if self.min_minutes is not None and not self.min_minutes <= self.minutes <= self.max_minutes:
            raise ValueError("estimate must be within bounds")
        return self


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    goal: CardField = Field(default_factory=CardField)
    deliverable: CardField = Field(default_factory=CardField)
    requirements: list[CardField] = Field(default_factory=list, max_length=12)
    criteria: list[CardField] = Field(default_factory=list, max_length=10)
    deadline: CardDeadline = Field(default_factory=CardDeadline)
    tasks: list[CardTask] = Field(default_factory=list, max_length=20)
    questions: list[Question] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def useful_entries(self):
        if any(not item.text for item in [*self.requirements, *self.criteria]):
            raise ValueError("requirement and criterion entries must not be empty")
        if len({task.id for task in self.tasks}) != len(self.tasks):
            raise ValueError("task IDs must be unique")
        used = {item.id for item in self.requirements if item.id is not None}
        if len(used) != sum(item.id is not None for item in self.requirements):
            raise ValueError("requirement IDs must be unique")
        for item in self.requirements:
            if item.id is None:
                index = 1
                while f"req-{index}" in used:
                    index += 1
                item.id = f"req-{index}"
                used.add(item.id)
        task_ids = {task.id for task in self.tasks}
        for task in self.tasks:
            if any(ref not in used for ref in task.requirement_ids):
                raise ValueError("unknown requirement reference")
            if any(ref not in task_ids for ref in task.depends_on):
                raise ValueError("unknown dependency")
        visited, active = set(), set()
        by_id = {task.id: task for task in self.tasks}
        def visit(identifier):
            if identifier in active:
                raise ValueError("cyclic task dependencies")
            if identifier in visited:
                return
            active.add(identifier)
            for ref in by_id[identifier].depends_on:
                visit(ref)
            active.remove(identifier)
            visited.add(identifier)
        for identifier in by_id:
            visit(identifier)
        return self


def parse_card(value: Card | dict[str, Any]) -> Card:
    try:
        return Card.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        if "at least one minute per substep" in str(exc):
            raise CardError("invalid_card", "На каждый шаг нужна хотя бы одна минута. Увеличьте оценку задачи или объедините шаги.") from exc
        raise CardError("invalid_card", "Проверьте поля карточки, даты и оценки длительности.") from exc


def _source_set(available_sources) -> set[str]:
    return {source if isinstance(source, str) else source["source_id"] for source in available_sources}


def validate_card_sources(card: Card | dict[str, Any], available_sources, *, confirmed=False) -> Card:
    """Check structure and scoped references; semantic support is a separate Eval."""
    parsed = parse_card(card)
    allowed = _source_set(available_sources)
    for item in [parsed.goal, parsed.deliverable, *parsed.requirements, *parsed.criteria]:
        if any(source_id not in allowed for source_id in item.source_ids):
            raise CardError("unsupported_source", "Карточка ссылается на источник другого задания или неизвестный фрагмент.")
        if item.origin == "document" and (not item.text or not item.source_ids):
            raise CardError("missing_sources", "Сведение из документа должно иметь текст и ссылку на его страницу.")
        if item.origin == "user" and item.source_ids:
            raise CardError("invalid_provenance", "Пользовательское уточнение не является фактом документа.")
    deadline = parsed.deadline
    if any(source_id not in allowed for source_id in deadline.source_ids):
        raise CardError("unsupported_source", "Источник срока не относится к этому заданию.")
    if deadline.origin == "document" and not deadline.source_ids:
        raise CardError("missing_sources", "Для срока из документа нужен источник.")
    if deadline.origin == "user" and deadline.source_ids:
        raise CardError("invalid_provenance", "Пользовательский срок не должен ссылаться на документ.")
    for task in parsed.tasks:
        if any(source_id not in allowed for source_id in task.source_ids):
            raise CardError("unsupported_source", "Источник оценки времени не относится к этому заданию.")
        if task.estimate_source == "document" and not task.source_ids:
            raise CardError("missing_sources", "Оценка времени из документа должна иметь источник.")
        if task.estimate_source != "document" and task.source_ids:
            raise CardError("invalid_provenance", "Рекомендация или пользовательская оценка не является фактом документа.")
    if confirmed and (not parsed.goal.text or not parsed.deliverable.text):
        raise CardError("incomplete_card", "Перед подтверждением укажите цель задания и результат, который нужно сдать.")
    return parsed


def manual_card(title: str) -> dict[str, Any]:
    """An empty form, not a fabricated analysis of an arbitrary uploaded PDF."""
    return Card(title=title, questions=[
        "Какова цель задания и что нужно сдать?",
        "Какие требования и критерии указаны в материалах?",
        "Если срок известен, укажите его; иначе оставьте поле пустым.",
    ]).model_dump()


async def analyze_assignment(gateway, provider, assignment_id: str) -> dict[str, Any]:
    result = await gateway.call("get_assignment", {"assignment_id": assignment_id})
    assignment = result["assignment"]
    if assignment.get("assignment_id") != assignment_id:
        raise CardError("invalid_assignment", "Получено другое задание.")
    if not assignment.get("is_uploaded"):
        raise CardError("read_only_assignment", "Подготовленный пример уже разобран. Загрузите своё задание.", 403)
    if provider.mode == "offline":
        card = manual_card(assignment["title"])
        return {"card": card, "mode": "offline", "usage": None,
                "trace": list(gateway.trace), "analysis_kind": "manual",
                "notice": "Модель не подключена. Заполните карточку вручную по своим материалам; автоматический AI-разбор не выполнялся."}
    # Previously confirmed user edits are not evidence for a new document analysis.
    context = {key: assignment[key] for key in ("assignment_id", "title", "sources")}
    generated = await provider.analyze(context)
    if not isinstance(generated, dict) or not isinstance(generated.get("card"), dict):
        raise CardError("invalid_card", "Модель вернула карточку в неподдерживаемом формате.")
    parsed = validate_card_sources(generated["card"], assignment.get("sources", []))
    # The model received documents, not newly supplied user constraints.
    if any(item.origin == "user" and item.text for item in [parsed.goal, parsed.deliverable, *parsed.requirements, *parsed.criteria]):
        raise CardError("invalid_provenance", "Модель не может выдавать свои предположения за пользовательские уточнения.")
    if parsed.deadline.origin == "user":
        raise CardError("invalid_provenance", "Модель не может придумывать пользовательский срок.")
    if any(task.estimate_source == "user" for task in parsed.tasks):
        raise CardError("invalid_provenance", "Предложенная моделью длительность является оценкой, а не вводом пользователя.")
    return {"card": parsed.model_dump(), "mode": provider.mode,
            "usage": generated.get("usage"), "trace": list(gateway.trace),
            "analysis_kind": "model", "notice": "Проверьте извлечённые требования и источники перед подтверждением."}


def confirm_card(proposed: Card | dict[str, Any], draft: dict[str, Any] | None,
                 available_sources) -> dict[str, Any]:
    """Only unchanged, server-issued document facts retain document provenance."""
    parsed = parse_card(proposed)
    old = parse_card(draft) if draft is not None else None
    allowed = _source_set(available_sources)
    # Reject foreign IDs even when an edit is about to lose document provenance.
    all_items = [parsed.goal, parsed.deliverable, *parsed.requirements, *parsed.criteria,
                 parsed.deadline, *parsed.tasks]
    if any(ref not in allowed for item in all_items for ref in item.source_ids):
        raise CardError("unsupported_source", "В карточке есть источник другого задания или неизвестный фрагмент.")

    def normalize(item: CardField, candidates: list[CardField]) -> CardField:
        unchanged = any(previous.origin == "document" and previous.text == item.text
                        and previous.source_ids == item.source_ids for previous in candidates)
        return item if item.origin == "document" and unchanged else CardField(id=item.id, text=item.text)

    changed_scope = old is None or any([
        parsed.goal.text != old.goal.text,
        parsed.deliverable.text != old.deliverable.text,
        {item.id: item.text for item in parsed.requirements} != {item.id: item.text for item in old.requirements},
    ])
    parsed.goal = normalize(parsed.goal, [old.goal] if old else [])
    parsed.deliverable = normalize(parsed.deliverable, [old.deliverable] if old else [])
    parsed.requirements = [normalize(item, old.requirements if old else []) for item in parsed.requirements]
    parsed.criteria = [normalize(item, old.criteria if old else []) for item in parsed.criteria]
    if parsed.deadline.date is None:
        parsed.deadline = CardDeadline()
    elif not (old and old.deadline.origin == "document" and parsed.deadline.origin == "document"
              and parsed.deadline.date == old.deadline.date and parsed.deadline.source_ids == old.deadline.source_ids):
        parsed.deadline = CardDeadline(date=parsed.deadline.date, origin="user")

    if changed_scope and old and parsed.tasks == old.tasks:
        parsed.tasks = []
    else:
        normalized_tasks = []
        for task in parsed.tasks:
            previous = next((item for item in old.tasks if item.id == task.id), None) if old else None
            unchanged = previous is not None and all(
                getattr(task, key) == getattr(previous, key)
                for key in ("title", "minutes", "estimate_source", "source_ids", "estimate_basis", "min_minutes", "max_minutes"))
            if not unchanged:
                data = task.model_dump()
                data.update(estimate_source="user", source_ids=[])
                if previous is not None and task.minutes != previous.minutes:
                    data.update(min_minutes=None, max_minutes=None, estimate_basis="Оценка пользователя")
                task = CardTask(**data)
            normalized_tasks.append(task)
        parsed.tasks = normalized_tasks
    parsed.questions = []
    return validate_card_sources(parsed, available_sources, confirmed=True).model_dump()
