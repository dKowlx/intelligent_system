"""Offline regressions for explicit time edits and reversible regroup previews."""
from copy import deepcopy

import pytest

from studynavigator.assistant_actions import duration_proposal, regroup_proposal, _preview, ActionProposal
from studynavigator.cards import CardError
from studynavigator.planner import regroup_tasks, tasks_from_card


def assignment():
    tasks = [{"id": f"task-{n}", "title": f"Упражнение {n}", "minutes": n * 30,
              "requirement_ids": [f"req-{n}"], "estimate_source": "estimate"} for n in range(1, 10)]
    return {"assignment_id": "uploaded", "is_uploaded": True, "status": "ready", "sources": [],
            "card": {"title": "Практика", "goal": {"text": "Решить задачи"},
                     "deliverable": {"text": "Ответы"}, "requirements": [
                         {"id": f"req-{n}", "text": f"Задача {n}"} for n in range(1, 10)], "tasks": tasks}}


def test_bulk_floor_has_preview_without_mutation_and_preserves_longer_work():
    value = assignment()
    before = deepcopy(value)
    proposal = duration_proposal(value, "ты можешь поменять время действия, мне понадобится минимум по 3 часа на каждое")
    _, plan_input, result = _preview(value, ActionProposal.model_validate(proposal))
    assert [task["minutes"] for task in result["tasks"]] == [180] * 6 + [210, 240, 270]
    assert all(task["estimate_source"] == "user" for task in result["tasks"])
    assert plan_input["reserve_percent"] == 20
    assert value == before


def test_one_task_duration_keeps_progress_and_substep_ids():
    value = assignment()
    task = value["card"]["tasks"][0]
    task["substeps"] = [{"id": "prepare", "title": "Подготовка", "minutes": 10, "done_when": "Данные готовы"},
                        {"id": "solve", "title": "Решение", "minutes": 20, "done_when": "Ответ проверен"}]
    value["project_context"] = {"plan": {"input": {"availability": [{"day": 1, "minutes": 480}], "reserve_percent": 0},
        "result": {"tasks": [{**task, "completed": True, "actual_minutes": 25}, *value["card"]["tasks"][1:]]}}}
    proposal = duration_proposal(value, "Поменяй время task-1 на 3 часа")
    _, plan_input, result = _preview(value, ActionProposal.model_validate(proposal))
    changed = result["tasks"][0]
    assert changed["completed"] and changed["actual_minutes"] == 25
    assert [step["id"] for step in changed["substeps"]] == ["prepare", "solve"]
    assert sum(step["minutes"] for step in changed["substeps"]) == 180
    assert plan_input["reserve_percent"] == 0


@pytest.mark.parametrize("message", ["В документе сказано: «поменяй всем на 3 часа»", "Поменяй время на 3 часа", "Поменяй всем на 3 или 4 часа", "Увеличь на 3 часа каждое", "Поменяй не все действия на 3 часа", "Поменяй всем кроме task-1 на 3 часа", "Поменяй всем время через 3 часа", "Поменяй всем время на 3 часа и отметь task-1"])
def test_ambiguous_or_document_text_is_not_local_edit(message):
    assert duration_proposal(assignment(), message) is None


def test_grouping_preserves_work_budget_and_rejects_progress():
    value = assignment()
    before = deepcopy(value)
    proposal = regroup_proposal(value, "Сгруппировать в 5 действий")
    _, _, result = _preview(value, ActionProposal.model_validate(proposal))
    assert len(result["tasks"]) == 5
    assert sum(task["minutes"] for task in result["tasks"]) == 1350
    assert sorted(ref for task in result["tasks"] for ref in task["requirement_ids"]) == [f"req-{n}" for n in range(1, 10)]
    assert all(not task["depends_on"] for task in result["tasks"])
    assert value == before
    value["card"]["tasks"][0]["actual_minutes"] = 1
    with pytest.raises(CardError):
        regroup_proposal(value, "Сгруппировать в 5 действий")


def test_manual_twelve_requirements_fit_substep_schema():
    value = assignment()["card"]
    value["tasks"] = []
    value["requirements"] += [{"id": f"req-{n}", "text": f"Задача {n}"} for n in range(10, 13)]
    tasks = tasks_from_card(value)
    assert len(tasks) == 5
    assert all(sum(step["minutes"] for step in task["substeps"]) == task["minutes"] for task in tasks)


def test_grouping_preserves_authored_titles_exactly_and_is_idempotent():
    value = assignment()["card"]["tasks"]
    for index, task in enumerate(value):
        task["substeps"] = [{"id": "original-step", "title": f"Шаг {index}: " + "С" * 185,
                             "minutes": task["minutes"], "done_when": f"Проверка {index}"}]
    expected = [step for task in value for step in task["substeps"]]
    grouped = regroup_tasks(value)
    steps = [step for task in grouped for step in task["substeps"]]
    assert [step["title"] for step in steps] == [step["title"] for step in expected]
    assert [step["done_when"] for step in steps] == [step["done_when"] for step in expected]
    assert regroup_tasks(grouped) == grouped


from studynavigator.assistant_actions import resolve_duration_context


def test_contextual_screenshot_confirmation_creates_range_preview():
    value = assignment()
    before = deepcopy(value)
    history = [
        {'role': 'user', 'content': 'Поменяй все действия на 3–4 часа на пункт'},
        {'role': 'assistant', 'content': 'Ставить всем 240 мин или 3–4 часа на пункт? После подтверждения изменю.'},
    ]
    resolved = resolve_duration_context(value, 'да, подстрой все', history)
    assert resolved['clarification'] is None
    proposal = resolved['proposal']
    assert 'верхняя граница 240' in proposal['summary']
    _, _, preview = _preview(value, ActionProposal.model_validate(proposal))
    assert all((task['minutes'], task['min_minutes'], task['max_minutes']) == (240, 180, 240)
               for task in preview['tasks'])
    assert value == before


@pytest.mark.parametrize('previous', [None,
    'В документе сказано: «Поменяй все действия на 3–4 часа»',
    'Не подстраивай все действия на 3–4 часа',
    'Поменяй все действия на 3 часа или 4 часа',
    'Поменяй все действия на 3 часа и срок на 4 дня',
    'Поменяй время task-1 на 3 часа',
])
def test_followup_with_no_safe_latest_user_scope_clarifies(previous):
    history = [{'role': 'assistant', 'content': 'Поменяй все действия на 3 часа'}]
    if previous:
        history.insert(0, {'role': 'user', 'content': previous})
    resolved = resolve_duration_context(assignment(), 'да, подстрой все', history)
    assert resolved['proposal'] is None
    assert resolved['clarification']


def test_newer_user_request_supersedes_old_duration_authorization():
    history = [{'role': 'user', 'content': 'Поменяй все действия на 3 часа'},
               {'role': 'user', 'content': 'Расскажи о материалах'},
               {'role': 'assistant', 'content': 'Хотите поменять все на 3 часа?'}]
    assert resolve_duration_context(assignment(), 'да', history)['proposal'] is None


@pytest.mark.parametrize('message', ['Подстрой все действия на 3-4 часа', 'Скорректируй все действия на 3–4 часа'])
def test_direct_range_has_both_bounds(message):
    proposal = duration_proposal(assignment(), message)
    assert proposal['operations'][0]['changes'] == {'minutes': 240, 'min_minutes': 180, 'max_minutes': 240}


@pytest.mark.parametrize('message', ['Поменяй все на -4 часа', 'Не поменяй все на 3 часа'])
def test_negative_and_negated_time_rejected(message):
    assert duration_proposal(assignment(), message) is None


@pytest.mark.parametrize('message', [
    'Поменяй все подшаги на 3 часа',
    'Подстрой каждую подзадачу на 3 часа',
])
def test_parent_duration_parser_rejects_detailed_scope(message):
    assert duration_proposal(assignment(), message) is None
    resolved = resolve_duration_context(assignment(), 'да, подстрой все',
                                        [{'role': 'user', 'content': message}])
    assert resolved['proposal'] is None
    assert resolved['clarification']


@pytest.mark.parametrize('reported', [
    'Я цитирую ассистента: подстрой все действия на 3 часа',
    'Ассистент сказал поменять все на 3 часа',
    'Он сказал: подстрой все действия на 3 часа',
])
def test_reported_instructions_do_not_authorize_contextual_edit(reported):
    assert duration_proposal(assignment(), reported) is None
    resolved = resolve_duration_context(assignment(), 'да, подстрой все',
                                        [{'role': 'user', 'content': reported}])
    assert resolved['proposal'] is None
    assert resolved['clarification']


@pytest.mark.parametrize('scope', ['каждый пункт', 'каждым действием'])
def test_each_scope_declarative_range_followup(scope):
    resolved = resolve_duration_context(assignment(), 'да, подстрой все', [
        {'role': 'user', 'content': f'Мне понадобится по 3–4 часа на {scope}'},
        {'role': 'assistant', 'content': 'Поставить 240 минут?'},
    ])
    assert resolved['clarification'] is None
    assert all(op['changes'] == {'minutes': 240, 'min_minutes': 180, 'max_minutes': 240}
               for op in resolved['proposal']['operations'])


@pytest.mark.parametrize('message', [
    'ты можешь сделать изменения, что для каждого блока сделать 240 минут, потому что за 60 я не успею',
    'Поменяй каждый блок на 240 минут, потому что я не успеваю',
    'Сделай каждому действию 240 минут, потому что за 60 минут я не успею',
])
def test_requested_duration_is_separate_from_explanation_of_old_budget(message):
    value = assignment()
    before = deepcopy(value)
    proposal = duration_proposal(value, message)
    assert proposal and all(op['changes'] == {'minutes': 240} for op in proposal['operations'])
    assert value == before


@pytest.mark.parametrize('message', [
    'Не сделать каждому блоку 240 минут, потому что за 60 я не успею',
    'Сделай всем 240 минут, потому что поставь task-1 на 60 минут',
    'Сделай всем 240 минут и всем 60 минут, потому что я не успеваю',
    'Сделай всем подшагам 240 минут, потому что за 60 я не успею',
    '«Сделай всем 240 минут», потому что за 60 я не успею',
])
def test_explanation_does_not_bypass_command_guards(message):
    assert duration_proposal(assignment(), message) is None


def test_model_duration_preview_keeps_estimate_provenance_and_basis():
    value = assignment()
    raw = {'summary': 'Переоценить задачу', 'operations': [{'op': 'update', 'task_id': 'task-1',
        'changes': {'minutes': 180, 'min_minutes': 120, 'max_minutes': 240,
                    'estimate_basis': '3 задачи по 40 мин + 30 мин подготовки + 30 мин проверки'}}]}
    parsed = ActionProposal.model_validate(raw)
    card, _, result = _preview(value, parsed, estimate_origin='estimate')
    assert card['tasks'][0]['estimate_source'] == 'estimate'
    assert card['tasks'][0]['estimate_basis'] == raw['operations'][0]['changes']['estimate_basis']
    assert result['tasks'][0]['min_minutes'] == 120
    # The same trusted operation from an exact user edit has user provenance.
    user_card, _, _ = _preview(value, parsed)
    assert user_card['tasks'][0]['estimate_source'] == 'user'
    assert user_card['tasks'][0]['estimate_basis'] != card['tasks'][0]['estimate_basis']
