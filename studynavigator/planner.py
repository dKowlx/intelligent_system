"""Deterministic relative planning in whole minutes. Estimates are not course facts."""

import re
from datetime import date, timedelta

PROGRESS_FIELDS = {"progress_status", "work_state", "measured_minutes", "estimated_actual_minutes", "progress_summary", "progress_evidence", "progress_updated_at", "actual_source"}


def preserve_progress(tasks, previous):
    """Keep richer progress from the persisted plan across browser round trips."""
    old = {task["id"]: task for task in previous or []}
    new_ids = {task["id"] for task in tasks}
    def has_progress(item):
        return (item.get("completed") or item.get("actual_minutes") is not None or item.get("measured_minutes")
                or item.get("estimated_actual_minutes") is not None or item.get("progress_status", "pending") != "pending"
                or item.get("progress_summary") or item.get("progress_evidence") or item.get("progress_updated_at"))
    for identifier, task in old.items():
        if identifier not in new_ids and (has_progress(task) or any(has_progress(step) for step in task.get("substeps", []))):
            raise ValueError("cannot remove work with progress")
    result = []
    for task in tasks:
        previous_task = old.get(task["id"], {})
        merged = {**{key: value for key, value in previous_task.items() if key in PROGRESS_FIELDS}, **task}
        old_steps = {step["id"]: step for step in previous_task.get("substeps", [])}
        ids = {step["id"] for step in task.get("substeps", [])}
        if previous_task.get("completed") and task.get("completed", previous_task.get("completed")) and ids - set(old_steps):
            raise ValueError("reopen completed task before adding unfinished substeps")
        if any(has_progress(step) for key, step in old_steps.items() if key not in ids):
            raise ValueError("cannot remove substep with progress")
        merged["substeps"] = [{**step, **{key: value for key, value in old_steps.get(step["id"], {}).items()
            if key in PROGRESS_FIELDS | {"completed", "actual_minutes"}}} for step in task.get("substeps", [])]
        result.append(merged)
    return result


def reconcile_user_progress(tasks, previous):
    """Apply explicit browser/chat progress edits without discarding richer facts."""
    tasks = preserve_progress(tasks, previous)
    old = {task["id"]: task for task in previous or []}
    for task in tasks:
        original = old.get(task["id"], {})
        if task.get("actual_minutes") != original.get("actual_minutes"):
            task["actual_source"] = "user" if task.get("actual_minutes") is not None else "unknown"
        if task.get("completed", False) != original.get("completed", False):
            task.update(progress_status="completed" if task.get("completed") else "pending", work_state="stopped")
            if task.get("completed"):
                for step in task.get("substeps", []):
                    step.update(completed=True, progress_status="completed", work_state="stopped")
    return tasks


def protect_reported_steps(db, assignment_id, tasks):
    """Reports remain recorded work even when no minutes were supplied."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='codex_runs'").fetchone():
        return
    ids = {(task['id'], step['id']) for task in tasks for step in task.get('substeps', [])}
    for task_id, step_id in db.execute("SELECT task_id,substep_id FROM codex_runs WHERE assignment_id=? AND substep_id IS NOT NULL AND (summary!='' OR evidence!='[]')", (assignment_id,)):
        if (task_id, step_id) not in ids:
            raise ValueError("cannot remove a reported substep")


DEFAULT_TASKS = [
    {"title": "Уточнить задачу и подготовить материалы", "minutes": 60, "estimate_source": "estimate"},
    {"title": "Реализовать MCP и AI-логику", "minutes": 180, "estimate_source": "estimate"},
    {"title": "Собрать веб-интерфейс", "minutes": 90, "estimate_source": "estimate"},
    {"title": "Провести Eval и разобрать ошибки", "minutes": 90, "estimate_source": "estimate"},
    {"title": "Развернуть сервис и проверить запуск", "minutes": 60, "estimate_source": "estimate"},
    {"title": "Оформить README и подготовить защиту", "minutes": 90, "estimate_source": "estimate"},
]


def _normalize_tasks(tasks):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
        raise ValueError("provide 1 to 20 tasks")
    normalized = []
    identifiers = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        minutes, title = task.get("minutes"), task.get("title")
        origin = task.get("estimate_source", "user")
        completed = task.get("completed", False)
        identifier = task.get("id") if task.get("id") is not None else f"task-{index + 1}"
        source_ids = task.get("source_ids", [])
        if type(minutes) is not int or not 1 <= minutes <= 4800:
            raise ValueError("task duration must be a positive integer, at most 4800 minutes")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("task title must contain 1 to 200 characters")
        if origin not in {"estimate", "user", "document"}:
            raise ValueError("invalid estimate source")
        if type(completed) is not bool:
            raise ValueError("completed must be boolean")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            raise ValueError("invalid task id")
        if identifier in identifiers:
            raise ValueError("task ids must be unique")
        if (not isinstance(source_ids, list) or len(source_ids) > 5
                or any(not isinstance(ref, str) or not ref or len(ref) > 100 for ref in source_ids)):
            raise ValueError("invalid estimate sources")
        identifiers.add(identifier)
        from .cards import CardTask
        metadata = CardTask.model_validate({key: value for key, value in task.items()
                                           if key in CardTask.model_fields} | {"id": identifier,
                                               "title": title, "minutes": minutes,
                                               "estimate_source": origin, "source_ids": source_ids}).model_dump()
        actual = task.get("actual_minutes")
        if actual is not None and (type(actual) is not int or not 0 <= actual <= 100000):
            raise ValueError("actual_minutes must be null or an integer between 0 and 100000")
        normalized.append({**{key: value for key, value in task.items() if key in PROGRESS_FIELDS}, **metadata, "actual_minutes": actual, "id": identifier, "title": title.strip(), "minutes": minutes,
                           "estimate_source": origin, "source_ids": list(dict.fromkeys(source_ids)),
                           "completed": completed})
    by_id = {task["id"]: task for task in normalized}
    ordered, active, visited = [], set(), set()
    def visit(identifier):
        if identifier in active:
            raise ValueError("cyclic task dependencies")
        if identifier in visited:
            return
        active.add(identifier)
        for ref in by_id[identifier]["depends_on"]:
            if ref not in by_id:
                raise ValueError("unknown task dependency")
            visit(ref)
        active.remove(identifier)
        visited.add(identifier)
        ordered.append(by_id[identifier])
    for identifier in by_id:
        visit(identifier)
    # Preserve submitted order here; adaptive scheduling uses topological order.
    return normalized


def prioritize_tasks(tasks):
    """Stable topological order with urgency inherited by prerequisites."""
    by_id = {task["id"]: task for task in tasks}
    ranks = {"high": 0, "medium": 1, "low": 2}
    effective = {task["id"]: ranks[task.get("priority", "medium")] for task in tasks}
    def promote(identifier, rank):
        effective[identifier] = min(effective[identifier], rank)
        for ref in by_id[identifier].get("depends_on", []):
            promote(ref, rank)
    for task in tasks:
        promote(task["id"], effective[task["id"]])
    pending, ordered, done = list(tasks), [], set()
    positions = {task["id"]: index for index, task in enumerate(tasks)}
    while pending:
        ready = [task for task in pending if set(task.get("depends_on", [])) <= done]
        if not ready:
            raise ValueError("cyclic task dependencies")
        task = min(ready, key=lambda item: (effective[item["id"]], positions[item["id"]]))
        ordered.append(task)
        done.add(task["id"])
        pending.remove(task)
    return ordered


def _field_text(value):
    if isinstance(value, dict):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return value.strip() if isinstance(value, str) else ""


def tasks_from_card(card):
    """Use proposed tasks or a visible, editable template from this card's text.

    The fallback does not interpret documents or pretend that its durations came
    from a model. The visible five-stage scaffold must be refined by the user.
    """
    if not isinstance(card, dict):
        raise ValueError("card must be an object")
    if card.get("tasks"):
        if not isinstance(card["tasks"], list) or any(not isinstance(task, dict) for task in card["tasks"]):
            raise ValueError("proposed tasks must be objects")
        # Completion belongs to the current calculation, not the persisted card.
        return _normalize_tasks([{**task, "completed": False} for task in card["tasks"]])
    requirements = card.get("requirements") or []
    if not isinstance(requirements, list):
        raise ValueError("requirements must be a list")
    entries = [(item.get("id") or f"req-{index + 1}" if isinstance(item, dict) else f"req-{index + 1}", _field_text(item))
               for index, item in enumerate(requirements)]
    entries = [(identifier, text) for identifier, text in entries if text]
    texts = [text for _, text in entries]
    if len(texts) > 19:
        raise ValueError("too many requirements to plan without omitting work")
    goal = _field_text(card.get("goal"))
    deliverable = _field_text(card.get("deliverable"))
    if not goal or not deliverable:
        raise ValueError("confirmed card needs a goal and deliverable to propose tasks")
    # This visible manual scaffold describes operations, not a semantic analysis.
    volume = max(1, len(entries))
    refs = [identifier for identifier, _ in entries]
    stages = [
        ("Разобрать объём и критерии", 30 + 10 * volume,
         f"Список из {volume} пунктов работ по цели: {goal}",
         "Для каждого пункта определены входные данные, результат и критерий проверки"),
        ("Подготовить данные и способ решения", 30 + 15 * volume,
         "Подготовленные материалы, инструменты и порядок выполнения пунктов",
         "Для каждого пункта доступны исходные данные и выбран способ выполнения"),
        ("Выполнить пункты задания", 60 * volume,
         "Черновые результаты всех пунктов задания",
         "Каждый пункт выполнен; полученные результаты сохранены"),
        ("Проверить и исправить результаты", 30 * volume,
         "Проверенные результаты и исправленные ошибки",
         "Каждый результат сверён с требованиями; ошибки устранены или явно записаны"),
        ("Собрать и проверить сдаваемый результат", 30 + 10 * volume,
         deliverable,
         "Комплект результата открывается и содержит все требуемые части"),
    ]
    result = []
    for index, (title, minutes, output, check) in enumerate(stages, 1):
        if index in (2, 3, 4) and entries:
            steps = [{"id": f"step-{index}-{n}", "title": text[:200], "minutes": 60,
                      "done_when": (f"{'Подготовлен способ выполнения' if index == 2 else 'Сохранён результат' if index == 3 else 'Выполнена проверка'}: " + text)[:1000]}
                     for n, text in enumerate(["; ".join(text for _, text in entries[offset:offset + 2]) for offset in range(0, len(entries), 2)], 1)]
        else:
            steps = [{"id": f"step-{index}", "title": title, "minutes": minutes, "done_when": check}]
        result.append({"id": f"task-{index}", "title": title, "minutes": minutes,
                       "estimate_source": "estimate", "requirement_ids": refs if index == 3 else [],
                       "expected_result": output[:1000], "done_when": check,
                       "estimate_basis": f"Ручной шаблон, не AI-анализ: {volume} пунктов; {minutes} мин на этот этап. Оценка не откалибрована по сложности и вашему темпу. Запросите AI-переоценку или уточните минуты вручную.",
                       "depends_on": [f"task-{index-1}"] if index > 1 else [], "substeps": steps})
    return _normalize_tasks(result)


def regroup_tasks(tasks):
    """Aggregate authored work in five queue groups without new dependencies.

    Original task IDs become substep IDs; estimates and requirement coverage are
    retained. Replanning work with progress needs a human migration decision.
    """
    tasks = _normalize_tasks(tasks)
    if any(task["completed"] or (task.get("actual_minutes") or 0) > 0 for task in tasks):
        from .cards import CardError
        raise CardError("regroup_has_progress", "В плане уже отмечен прогресс. Группировка изменила бы его смысл; сохраните текущие действия и изменяйте их оценки отдельно.")
    if len(tasks) < 5:
        from .cards import CardError
        raise CardError("regroup_too_few", "В плане меньше пяти действий. Сначала добавьте недостающие действия.")
    if len(tasks) == 5:
        return tasks
    tasks = prioritize_tasks(tasks)
    groups = [tasks[index * len(tasks) // 5:(index + 1) * len(tasks) // 5] for index in range(5)]
    group_ids = {task["id"]: f"group-{index+1}" for index, group in enumerate(groups) for task in group}
    result = []
    for index, group in enumerate(groups, 1):
        steps = []
        for task in group:
            if task["substeps"]:
                for step_index, step in enumerate(task["substeps"], 1):
                    steps.append({**step, "id": (f"g{index}-s{len(steps)+1}-{task['id'][:30]}-{step['id']}")[:100],
                                  "title": step["title"]})
            else:
                steps.append({"id": f"g{index}-s{len(steps)+1}", "title": task["title"], "minutes": task["minutes"],
                              "done_when": task["done_when"] or task["expected_result"] or f"Получен и проверен результат: {task['title']}"})
        if sum(task["minutes"] for task in group) > 4800 or len(steps) > 32:
            from .cards import CardError
            raise CardError("regroup_too_large", "Группа превышает 4800 минут или 32 шага. Объедините меньший объём вручную.")
        result.append({"id": f"group-{index}", "title": " / ".join(task["title"] for task in group)[:200],
                       "minutes": sum(task["minutes"] for task in group),
                       "estimate_source": "user" if all(task["estimate_source"] == "user" for task in group) else "estimate",
                       "priority": min((task["priority"] for task in group), key={"high": 0, "medium": 1, "low": 2}.get),
                       "min_minutes": sum(task["min_minutes"] for task in group) if all(task["min_minutes"] is not None for task in group) and sum(task["max_minutes"] for task in group) <= 4800 else None,
                       "max_minutes": sum(task["max_minutes"] for task in group) if all(task["max_minutes"] is not None for task in group) and sum(task["max_minutes"] for task in group) <= 4800 else None,
                       "requirement_ids": list(dict.fromkeys(ref for task in group for ref in task["requirement_ids"])),
                       "expected_result": "; ".join(task["expected_result"] or task["title"] for task in group)[:1000],
                       "done_when": "Завершены и проверены все вложенные действия",
                       "estimate_basis": "Сумма сохранённых оценок вложенных действий; группировка не меняет общий объём работы",
                       "depends_on": list(dict.fromkeys(group_ids[ref] for task in group for ref in task["depends_on"] if group_ids[ref] != f"group-{index}")),
                       "substeps": steps})
    return _normalize_tasks(result)


def prepare_task_overrides(base_tasks, overrides=None):
    """Derive provenance from trusted tasks, never from browser claims.

    All tasks stay present and in their confirmed order. Completed work is
    explicitly excluded by the scheduler; it is not silently dropped by input.
    """
    base = _normalize_tasks(base_tasks)
    if overrides is None:
        return base
    if not isinstance(overrides, list) or any(not isinstance(task, dict) for task in overrides):
        raise ValueError("tasks must be objects")
    submitted = _normalize_tasks([{key: value for key, value in task.items()
                                  if key in {"id", "title", "minutes", "completed", "actual_minutes"}}
                                 for task in overrides])
    if len(submitted) != len(base):
        raise ValueError("provide all tasks; use completed for finished work")
    by_id = {task["id"]: task for task in submitted}
    if set(by_id) != {task["id"] for task in base}:
        raise ValueError("task ids must match the confirmed card")
    result = []
    for original in base:
        update = by_id[original["id"]]
        unchanged = (update["title"] == original["title"] and update["minutes"] == original["minutes"])
        result.append({**original, "title": update["title"], "minutes": update["minutes"],
                       "completed": update["completed"], "actual_minutes": update["actual_minutes"],
                       "min_minutes": original["min_minutes"] if unchanged else None,
                       "max_minutes": original["max_minutes"] if unchanged else None,
                       "estimate_basis": original["estimate_basis"] if unchanged else "Оценка пользователя",
                       "estimate_source": original["estimate_source"] if unchanged else "user",
                       "source_ids": original["source_ids"] if unchanged else []})
    return _normalize_tasks(result)


def _task_slice(task, offset, minutes):
    """Intersect authored substeps with a parent slot; never invent work.

    A step crossing a day boundary retains its ID and completion criterion.
    Slot minutes describe only that day's effort; progress remains parent-level.
    """
    steps, position = [], 0
    for step in task.get("substeps", []):
        end = position + step["minutes"]
        overlap = min(end, offset + minutes) - max(position, offset)
        if overlap > 0:
            steps.append({**step, "minutes": overlap,
                          "total_minutes": step["minutes"],
                          "continued": offset > position,
                          "continues": offset + minutes < end})
        position = end
    return {**task, "minutes": minutes, "substeps": steps}


def build_plan(*, days, minutes_per_day, tasks=None, start_date=None, assignment_id="personal-project"):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("days must be an integer between 1 and 30")
    if type(minutes_per_day) is not int or not 15 <= minutes_per_day <= 480:
        raise ValueError("minutes_per_day must be an integer between 15 and 480")
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if start_date is not None and type(start_date) is not date:
        raise ValueError("start_date must be an ISO date")
    if start_date is not None:
        try:
            start_date + timedelta(days=days - 1)
        except OverflowError as exc:
            raise ValueError("start_date is outside the supported calendar range") from exc
    if tasks is None and assignment_id != "personal-project":
        raise ValueError("tasks for this assignment must come from its confirmed card")
    normalized = _normalize_tasks(DEFAULT_TASKS if tasks is None else tasks)
    ordered = prioritize_tasks(normalized)
    pending = [{**task, "minutes": sum(step["minutes"] for step in task["substeps"] if not step.get("completed")),
                "substeps": [step for step in task["substeps"] if not step.get("completed")]}
               if task.get("substeps") else task for task in ordered if not task["completed"]]
    pending = [task for task in pending if task["minutes"] > 0]
    required = sum(t["minutes"] for t in pending)
    available = days * minutes_per_day
    remaining = [t["minutes"] for t in pending]
    schedule = []
    cursor = 0
    for day in range(1, days + 1) if pending else []:
        budget, slots = minutes_per_day, []
        while cursor < len(pending) and budget:
            take = min(remaining[cursor], budget)
            slots.append(_task_slice(pending[cursor], pending[cursor]["minutes"] - remaining[cursor], take))
            remaining[cursor] -= take
            budget -= take
            if remaining[cursor] == 0:
                cursor += 1
        schedule.append({"day": day, "date": (start_date + timedelta(days=day - 1)).isoformat()
                         if start_date else None, "tasks": slots})
    return {
        "assignment_id": assignment_id,
        "status": "feasible" if required <= available else "insufficient_time",
        "available_minutes": available, "required_minutes": required,
        "deficit_minutes": max(0, required - available), "schedule": schedule,
        "tasks": normalized,
        "completed_task_count": len(normalized) - len(pending),
        "completed_minutes": sum(task["minutes"] for task in normalized if task["completed"]),
        "remaining_tasks": [_task_slice(task, task["minutes"] - rest, rest) for task, rest in zip(pending, remaining) if rest],
        "assumptions": ["Длительности с меткой estimate — предварительные оценки, а не требования преподавателя; user — ваши оценки.",
                        "План относительный; даты дней появляются только при явно заданной дате начала.",
                        "Этапы выполняются последовательно и могут делиться между днями."],
    }


def build_adaptive_plan(tasks, availability, start_date=None, reserve_percent=0, card=None):
    """Schedule remaining effort in dependency order; expose quality gaps separately.

    Actual time is historical effort, not evidence of remaining effort or completion.
    A duration is the user's current remaining-work estimate until marked complete.
    """
    normalized = _normalize_tasks(tasks)
    if not isinstance(availability, list) or not 1 <= len(availability) <= 30:
        raise ValueError("provide availability for 1 to 30 days")
    for index, item in enumerate(availability, 1):
        if (not isinstance(item, dict) or type(item.get("day")) is not int
                or item["day"] != index or type(item.get("minutes")) is not int
                or not 0 <= item["minutes"] <= 480):
            raise ValueError("availability must contain contiguous days and 0 to 480 minutes")
    if type(reserve_percent) is not int or not 0 <= reserve_percent <= 100:
        raise ValueError("reserve_percent must be an integer between 0 and 100")
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if start_date is not None and type(start_date) is not date:
        raise ValueError("start_date must be an ISO date")
    try:
        if start_date:
            start_date + timedelta(days=len(availability) - 1)
    except OverflowError as exc:
        raise ValueError("start_date is outside supported range") from exc
    by_id = {task["id"]: task for task in normalized}
    ordered = prioritize_tasks(normalized)
    warnings = []
    def warn(code, message, **context):
        warnings.append({"code": code, "message": message, **context})
    for task in normalized:
        context = {"task_id": task["id"]}
        if not task["expected_result"] or not task["done_when"]:
            warn("missing_result", "Укажите результат действия и проверяемый признак готовности.", **context)
        if not task["estimate_basis"]:
            warn("missing_estimate_basis", "Оценка времени не имеет объяснения объёма работы.", **context)
        if task["estimate_basis"].startswith("Ручной шаблон") or re.match(r"^(выполнить:|подготовить:|проверить результат:)", task["title"], re.I):
            warn("generic_task", "Шаблон требует содержательной декомпозиции; ссылка не доказывает покрытие.", **context)
        if task["completed"] and any(not by_id[ref]["completed"] for ref in task["depends_on"]):
            warn("inconsistent_completion", "Завершённое действие имеет незавершённую предпосылку.", **context)
    requirements = card.get("requirements", []) if isinstance(card, dict) else []
    requirement_ids = [item.get("id") or f"req-{index + 1}" for index, item in enumerate(requirements)]
    known = set(requirement_ids)
    items = []
    for task in normalized:
        if card is not None and any(ref not in known for ref in task["requirement_ids"]):
            raise ValueError("unknown requirement reference")
        if card is not None and not task["requirement_ids"]:
            warn("unlinked_task", "Действие не связано с требованием; проверьте необходимость.", task_id=task["id"])
    for identifier in requirement_ids:
        linked = [task for task in normalized if identifier in task["requirement_ids"]]
        checkable = [task for task in linked if task["expected_result"] and task["done_when"]
                     and task["estimate_basis"] and not task["estimate_basis"].startswith("Ручной шаблон") and not re.match(r"^(выполнить:|подготовить:|проверить результат:)", task["title"], re.I)]
        items.append({"requirement_id": identifier, "task_ids": [task["id"] for task in linked],
                      "linked": bool(linked), "complete_check": bool(checkable)})
        if not linked:
            warn("uncovered_requirement", "Для требования нет связанного действия.", requirement_id=identifier)
    pending = [{**task, "minutes": sum(step["minutes"] for step in task["substeps"] if not step.get("completed")),
                "substeps": [step for step in task["substeps"] if not step.get("completed")]}
               if task.get("substeps") else task for task in ordered if not task["completed"]]
    pending = [task for task in pending if task["minutes"] > 0]
    remaining = [task["minutes"] for task in pending]
    cursor, schedule = 0, []
    budgets = [item["minutes"] * (100 - reserve_percent) // 100 for item in availability]
    deadline_text = card.get("deadline", {}).get("date") if isinstance(card, dict) else None
    if deadline_text and start_date:
        deadline = date.fromisoformat(deadline_text)
        budgets = [budget if start_date + timedelta(days=index) <= deadline else 0
                   for index, budget in enumerate(budgets)]
        if start_date + timedelta(days=len(availability) - 1) > deadline:
            warn("deadline_limits_availability", "Доступное время после известного срока исключено из расчёта.")
    elif deadline_text:
        warn("deadline_not_checked", "Для проверки известного срока укажите дату начала.")
    for item, budget in zip(availability, budgets):
        slots = []
        while cursor < len(pending) and budget:
            take = min(remaining[cursor], budget)
            slots.append(_task_slice(pending[cursor], pending[cursor]["minutes"] - remaining[cursor], take))
            remaining[cursor] -= take
            budget -= take
            if remaining[cursor] == 0:
                cursor += 1
        schedule.append({"day": item["day"], "date": (start_date + timedelta(days=item["day"] - 1)).isoformat()
                         if start_date else None, "tasks": slots})
    required, available = sum(task["minutes"] for task in pending), sum(budgets)
    coverage = {"requirements_total": len(items), "linked_count": sum(item["linked"] for item in items),
                "complete_check_count": sum(item["complete_check"] for item in items), "items": items,
                "linked_requirement_ids": [item["requirement_id"] for item in items if item["linked"]],
                "uncovered_requirement_ids": [item["requirement_id"] for item in items if not item["linked"]]}
    if required > available:
        warn("insufficient_time", "Работа не помещается: увеличьте доступность или пересмотрите объём явно.")
    return {"assignment_id": "personal-project", "status": "feasible" if required <= available else "insufficient_time",
            "available_minutes": available, "raw_available_minutes": sum(item["minutes"] for item in availability),
            "reserve_percent": reserve_percent, "required_minutes": required, "deficit_minutes": max(0, required - available),
            "tasks": normalized, "schedule": schedule, "coverage": coverage, "warnings": warnings,
            "completed_task_count": sum(task["completed"] for task in normalized),
            "completed_minutes": sum(task["minutes"] for task in normalized if task["completed"]),
            "remaining_tasks": [_task_slice(task, task["minutes"] - rest, rest) for task, rest in zip(pending, remaining) if rest],
            "assumptions": ["Минуты — текущая оценка оставшейся работы; фактическое время само по себе не означает завершение.",
                            "Связь с требованием не доказывает полноту или качество результата; проверьте действия содержательно.",
                            "Резерв вычитается из каждого дня с округлением доступного времени вниз."]}
