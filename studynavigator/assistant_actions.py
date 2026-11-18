"""Validated previews only; persistence is an explicit HTTP user action."""

import re
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .cards import CardError, CardTask, validate_card_sources
from .planner import build_adaptive_plan, tasks_from_card, regroup_tasks

PROGRESS_REPORT_RULES = """Просьба «заполни», «запиши» или «отметь» после отчёта о работе — запрос обновить прогресс.
Сопоставь описанную работу с конкретным существующим действием по смыслу; при неоднозначности уточни.
Потраченное время записывай как actual_minutes, не меняй плановую оценку minutes.
Если пользователь сообщает о завершении действия, предложи complete; если работа ещё идёт —
update с actual_minutes без отметки завершения. Не распределяй общий факт времени между
несколькими действиями без уточнения. Предложение требует явного применения пользователем.
"""


class ActionOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add", "update", "complete", "regroup"]
    task_id: str = Field(min_length=1, max_length=100)
    task: CardTask | None = None
    changes: dict = Field(default_factory=dict)
    actual_minutes: StrictInt | None = Field(default=None, ge=0, le=100000)


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1000)
    operations: list[ActionOperation] = Field(min_length=1, max_length=20)


def explicit_edit_intent(message):
    from .estimation import is_reestimate_request
    if is_reestimate_request(message):
        return True
    return bool(re.search(r"\b(добавь|добавить|измени|изменить|поменяй|поменять|сгруппируй|сгруппировать|обнови|отметь|отметить|заверши|завершить|поставь|сделай|сделать|подстрой|скорректируй|увеличь|сократи|перенеси|приоритизируй|расставь|запиши|заполни)\b", message, re.I))



def regroup_proposal(assignment, message):
    if not re.fullmatch(r"\s*сгруппир(?:овать|уй)\s+(?:в\s+)?5\s+действий[.!]?\s*", message, re.I):
        return None
    return prepare_assistant_actions(assignment, {"summary": "Сгруппировать существующую работу в 5 действий, сохранив общий бюджет времени",
        "operations": [{"op": "regroup", "task_id": "all"}]}, message)


def duration_proposal(assignment, message):
    """Recognize only unambiguous user time edits; documents never enter here.

    A minimum is a floor, not permission to shorten a longer estimate. IDs or
    exact titles select one task; bulk requests require an explicit quantifier.
    Ambiguous, quoted and multi-instruction messages stay with the agent.
    """
    # This parser edits parent actions only. Reported instructions and finer
    # scopes require clarification rather than borrowing their authorization.
    if re.search(r"подшаг|подзадач|субзадач|цитир|ассистент|\b(?:он|она|модель|бот)\s+(?:сказал|сказала|просил|просила|предложил|предложила)|\b(?:сказано|написано)\b", message, re.I):
        return None
    if not explicit_edit_intent(message) or any(char in message for char in ['"', "'", '«', '»', '\n', ';']):
        return None
    original_message = message
    # A reason such as "за 60 я не успею" describes the old budget; it is
    # neither another requested amount nor a negated edit. Accept only this
    # narrow explanatory clause, never arbitrary text after "потому что".
    clauses = re.split(r"\s*,?\s*потому\s+что\s+", message, maxsplit=1, flags=re.I)
    if len(clauses) == 2 and re.fullmatch(
        r"(?:я\s+)?(?:за\s+\d{1,4}\s*(?:минут(?:у|ы)?|мин|час(?:а|ов)?)?\s+)?"
        r"(?:я\s+)?не\s+успе(?:ю|ваю|ваем|ем)[.!?]?\s*", clauses[1], re.I
    ):
        message = clauses[0]
    if re.search(r"\bне\s+(?:меняй|подстраивай|корректируй|поменяй)|\bили\b|документ|инструкци|удали|добавь|заверши|отметь|\bкроме\b|\bтолько\b|\bне\s+все|\bчерез\b|(?:увелич|сократ|уменьш)\w*\s+на", message, re.I):
        return None
    if re.search(r"\bне\b(?!\s+(?:менее|меньше)\b)|\b(?:отмена|отмени|нельзя)\b|срок|дедлайн|доступност|\bв\s+день\b", message, re.I):
        return None
    if not re.search(r"врем|длитель|час|минут", message, re.I):
        return None
    # Consume ranges as one expression, so the hyphen cannot turn 3–4 into 4.
    amounts = list(re.finditer(r"(?<![\w.,-])(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?\s*(час(?:а|ов)?|ч\b|минут(?:а|ы)?|мин\b)", message, re.I))
    if len(amounts) != 1:
        return None
    amount = amounts[0]
    factor = 60 if amount[3].lower().startswith('ч') else 1
    minimum = int(amount[1]) * factor
    minutes = int(amount[2] or amount[1]) * factor
    if not 1 <= minimum <= minutes <= 4800 or re.search(r"[-−]\s*$", message[:amount.start()]):
        return None
    saved = (assignment.get('project_context') or {}).get('plan')
    tasks = saved['result']['tasks'] if saved else tasks_from_card(assignment.get('card') or {})
    bulk = bool(re.search(r"\b(кажд(?:ое|ую|ого|ой|ый|ым|ому)|все(?:х|м)?|всем)\b", message, re.I))
    selected = tasks if bulk else [task for task in tasks if
        re.search(r'(?<![\w-])' + re.escape(task['id']) + r'(?![\w-])', message, re.I)
        or task['title'].casefold() in message.casefold()]
    if not selected or (not bulk and len(selected) != 1):
        return None
    floor = bool(re.search(r"минимум|не\s+менее|не\s+меньше|хотя\s+бы", message, re.I))
    operations = [{'op': 'update', 'task_id': task['id'],
                   'changes': {'minutes': max(task['minutes'], minutes) if floor else minutes,
                               **({'min_minutes': minimum, 'max_minutes': minutes} if amount[2] and not floor else {})}}
                  for task in selected]
    return prepare_assistant_actions(assignment, {
        'summary': (f"Диапазон {minimum}–{minutes} мин; для плана выбрана верхняя граница {minutes} мин на {'каждое действие' if bulk else selected[0]['title']}"
                    if amount[2] and not floor else f"{'Не менее' if floor else 'По'} {minutes} мин на {'каждое действие' if bulk else selected[0]['title']}"),
        'operations': operations}, original_message)


def resolve_duration_context(assignment, message, history=None):
    """Resolve a short confirmation only against the latest user turn.

    Assistant text and older superseded requests never supply authorization.
    The resolved message is suitable for the existing explicit-intent validator.
    """
    result = {"proposal": None, "user_message": message, "clarification": None}
    direct = duration_proposal(assignment, message)
    if direct:
        result["proposal"] = direct
        return result
    followup = bool(re.fullmatch(
        r"\s*(?:да[,!]?\s*)?(?:подстрой|скорректируй|поменяй|измени)(?:\s+(?:все|всё|всем))?[.!]?\s*|\s*(?:да|давай|согласен|подтверждаю)[.!]?\s*",
        message, re.I))
    if not followup:
        return result
    recent_user = next((item.get("content", "") for item in reversed(list(history or [])[-6:])
                        if isinstance(item, dict) and item.get("role") == "user"), "")
    # A declarative user estimate can authorize a later imperative confirmation.
    # Keep the original scope and all exclusions intact for the strict parser.
    candidate = recent_user if explicit_edit_intent(recent_user) else "Подстрой " + recent_user
    proposal = duration_proposal(assignment, candidate) if recent_user else None
    if proposal and re.search(r"\bвсе(?:м)?\b|всё", message, re.I) and not re.search(r"\b(?:все(?:х|м)?|кажд(?:ое|ую|ого|ой|ый|ым|ому))\b", recent_user, re.I):
        proposal = None
    if proposal:
        result.update(proposal=proposal, user_message=candidate)
    else:
        result["clarification"] = "Уточните, какие действия изменить и сколько минут или часов выделить на каждое."
    return result


def _preview(assignment, proposal, *, estimate_origin="user"):
    if estimate_origin not in {"user", "estimate"}:
        raise ValueError("invalid estimate origin")
    card = deepcopy(assignment.get("card") or {})
    saved = (assignment.get("project_context") or {}).get("plan")
    if not assignment.get("is_uploaded") or assignment.get("status") != "ready":
        raise CardError("read_only_assignment", "Сначала подтвердите карточку своего проекта.", 409)
    if saved and saved.get("stale"):
        raise CardError("stale_card", "Сначала пересчитайте устаревший план.", 409)
    previous_tasks = (saved.get("input", {}).get("tasks") or saved.get("result", {}).get("tasks", [])) if saved else []
    tasks = deepcopy(previous_tasks if saved else tasks_from_card(card))
    by_id = {task["id"]: task for task in tasks}
    editable = set(CardTask.model_fields) - {"id", "estimate_source", "source_ids"}
    editable |= {"completed", "actual_minutes"}
    for op in proposal.operations:
        if op.op == "regroup":
            if len(proposal.operations) != 1 or op.task_id != "all" or op.task or op.changes or op.actual_minutes is not None:
                raise ValueError("invalid regroup")
            tasks = regroup_tasks(tasks)
            by_id = {task["id"]: task for task in tasks}
        elif op.op == "add":
            if op.task is None or op.task_id != op.task.id or op.task_id in by_id or op.changes or op.actual_minutes is not None:
                raise ValueError("invalid add")
            task = op.task.model_dump()
            if task["estimate_source"] != "estimate" or task["source_ids"]:
                raise ValueError("new estimates are not document facts")
            task.update(completed=False, actual_minutes=None)
            tasks.append(task)
            by_id[op.task_id] = task
        else:
            if op.task_id not in by_id or op.task is not None:
                raise ValueError("unknown task")
            task = by_id[op.task_id]
            if op.op == "complete":
                if op.changes:
                    raise ValueError("complete has no changes")
                task["completed"] = True
                if op.actual_minutes is not None:
                    task["actual_minutes"] = op.actual_minutes
            else:
                if not op.changes or set(op.changes) - editable or op.actual_minutes is not None:
                    raise ValueError("invalid update fields")
                task.update(op.changes)
                if "minutes" in op.changes:
                    task.update(estimate_source=estimate_origin, source_ids=[],
                                min_minutes=op.changes.get("min_minutes"), max_minutes=op.changes.get("max_minutes"),
                                estimate_basis=(op.changes.get("estimate_basis") or "Рекомендация ассистента; проверьте по фактическим затратам"
                                                if estimate_origin == "estimate" else "Оценка изменена по запросу пользователя"))
    from .planner import reconcile_user_progress
    tasks = reconcile_user_progress(tasks, previous_tasks)
    card["tasks"] = [{key: value for key, value in task.items() if key in CardTask.model_fields} for task in tasks]
    parsed = validate_card_sources(card, assignment.get("sources", []), confirmed=True).model_dump()
    plan_input = deepcopy(saved["input"]) if saved else {"availability": [{"day": 1, "minutes": 0}], "reserve_percent": 20, "start_date": None}
    plan_input["tasks"] = tasks
    result = build_adaptive_plan(tasks, plan_input["availability"], plan_input.get("start_date"),
                                 plan_input.get("reserve_percent", 0), parsed)
    result["assignment_id"] = assignment["assignment_id"]
    return parsed, plan_input, result


def prepare_assistant_actions(assignment, proposal, user_message, *, estimate_origin="user"):
    try:
        if not explicit_edit_intent(user_message):
            raise ValueError("no explicit user edit request")
        parsed = ActionProposal.model_validate(proposal)
        _preview(assignment, parsed, estimate_origin=estimate_origin)
        return parsed.model_dump(exclude_none=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise CardError("invalid_action_proposal", "Предложение не прошло проверку. Укажите действие и существующие задачи явно.") from exc


def apply_assistant_actions(store, assignment_id, proposal, card_revision, plan_revision, materials_revision=None, *, estimate_origin="user"):
    assignment = store.get_assignment(assignment_id)["assignment"]
    if assignment["card_revision"] != card_revision:
        raise CardError("card_conflict", "Карточка изменилась. Повторите запрос.", 409)
    current_plan = (assignment.get("project_context") or {}).get("plan")
    if (current_plan["revision"] if current_plan else 0) != plan_revision:
        raise CardError("plan_conflict", "План изменился. Повторите запрос.", 409)
    try:
        card, plan_input, result = _preview(assignment, ActionProposal.model_validate(proposal), estimate_origin=estimate_origin)
    except (ValueError, TypeError, KeyError) as exc:
        raise CardError("invalid_action_proposal", "Предложение больше нельзя применить.") from exc
    return store.commit_assistant_actions(assignment_id, card=card, plan_input=plan_input,
        result=result, card_revision=card_revision, plan_revision=plan_revision, materials_revision=materials_revision)
