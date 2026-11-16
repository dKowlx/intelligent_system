"""Offline regression checks for authored decomposition and exact daily effort."""
from copy import deepcopy

import pytest

from studynavigator.cards import CardError, CardTask, parse_card
from studynavigator.planner import build_adaptive_plan, build_plan, prepare_task_overrides


def task(minutes=50):
    return dict(id='interviews', title='Подготовить интервью', minutes=minutes,
                expected_result='Гайд интервью', done_when='Гайд содержит пять вопросов',
                estimate_basis='Пять разделов по десять минут',
                substeps=[dict(id=f'step-{i}', title=title, minutes=10, done_when=criterion)
                          for i, (title, criterion) in enumerate([
                              ('Выбрать участников', 'Записаны критерии и три кандидата'),
                              ('Сформулировать цель интервью', 'Записан один исследовательский вопрос'),
                              ('Написать вопросы о текущем опыте', 'Составлены три открытых вопроса'),
                              ('Добавить вопросы о трудностях', 'Составлены два вопроса о конкретном случае'),
                              ('Проверить гайд на коллеге', 'Убраны наводящие вопросы по результату прогона'),
                          ], 1)])


def availability(*minutes):
    return [dict(day=i, minutes=value) for i, value in enumerate(minutes, 1)]


def assert_exact_slots(result):
    for day in result['schedule']:
        for slot in day['tasks']:
            if slot['substeps']:
                assert sum(step['minutes'] for step in slot['substeps']) == slot['minutes']
                assert all(0 < step['minutes'] <= step['total_minutes'] for step in slot['substeps'])


def test_five_authored_points_are_real_work_with_criteria():
    result = build_adaptive_plan([task()], availability(50))
    steps = result['schedule'][0]['tasks'][0]['substeps']
    assert len(steps) == 5
    assert [s['title'] for s in steps] == [s['title'] for s in task()['substeps']]
    assert all(s['done_when'] and not s['continued'] and not s['continues'] for s in steps)
    assert result['required_minutes'] == result['available_minutes'] == 50
    assert_exact_slots(result)


def test_step_crosses_days_without_restarting_or_filling_zero_day():
    result = build_adaptive_plan([task()], availability(15, 0, 35, 30))
    assert result['schedule'][1]['tasks'] == result['schedule'][3]['tasks'] == []
    first = result['schedule'][0]['tasks'][0]['substeps']
    third = result['schedule'][2]['tasks'][0]['substeps']
    assert [(s['id'], s['minutes']) for s in first] == [('step-1', 10), ('step-2', 5)]
    assert first[1]['continues'] and not first[1]['continued']
    assert third[0]['id'] == 'step-2' and third[0]['minutes'] == 5
    assert third[0]['continued'] and third[0]['total_minutes'] == 10
    assert third[0]['done_when'] == first[1]['done_when']
    assert sum(s['minutes'] for s in first + third) == 50
    assert_exact_slots(result)


def test_short_horizon_preserves_unfinished_steps_and_deficit():
    result = build_adaptive_plan([task()], availability(13, 0))
    assert result['status'] == 'insufficient_time' and result['deficit_minutes'] == 37
    rest = result['remaining_tasks'][0]
    assert rest['minutes'] == sum(s['minutes'] for s in rest['substeps']) == 37
    assert rest['substeps'][0]['id'] == 'step-2'
    assert rest['substeps'][0]['minutes'] == 7 and rest['substeps'][0]['continued']
    assert result['schedule'][1]['tasks'] == []
    assert_exact_slots(result)


def test_changed_parent_estimate_scales_steps_and_keeps_parent_progress():
    original = task()
    overrides = [dict(id='interviews', title=original['title'], minutes=17,
                      completed=False, actual_minutes=12)]
    updated = prepare_task_overrides([original], overrides)[0]
    assert sum(s['minutes'] for s in updated['substeps']) == 17
    assert [s['minutes'] for s in updated['substeps']] == [4, 4, 3, 3, 3]
    assert updated['estimate_source'] == 'user' and updated['actual_minutes'] == 12
    assert original == task()  # authored card was not mutated
    result = build_adaptive_plan([updated], availability(8, 9))
    assert_exact_slots(result)
    overrides[0]['completed'] = True
    completed = build_adaptive_plan(prepare_task_overrides([original], overrides), availability(17))
    assert completed['required_minutes'] == 0 and completed['completed_task_count'] == 1
    assert completed['schedule'][0]['tasks'] == []
    assert len(completed['tasks'][0]['substeps']) == 5


def test_parent_estimate_too_small_requires_explicit_reestimate():
    with pytest.raises(ValueError, match='at least one minute per substep'):
        CardTask.model_validate(task(4))
    with pytest.raises(CardError, match='На каждый шаг нужна хотя бы одна минута'):
        parse_card(dict(title='Исследование', tasks=[task(4)]))


def test_plan_override_cannot_replace_confirmed_substeps():
    original = task()
    forged = deepcopy(original)
    forged['substeps'] = [dict(id='fake', title='Несогласованная работа', minutes=50, done_when='Готово')]
    result = prepare_task_overrides([original], [forged])
    assert result[0]['substeps'] == original['substeps']


def test_normalized_estimates_have_exact_sum_and_stable_rounding():
    value = task(53)
    normalized = CardTask.model_validate(value).model_dump()
    assert [step['minutes'] for step in normalized['substeps']] == [11, 11, 11, 10, 10]
    assert CardTask.model_validate(normalized).model_dump() == normalized


def test_http_task_round_trip_accepts_authored_substeps_without_enforcing_old_sum():
    from studynavigator.main import AdaptiveTaskInput
    value = task()
    value['minutes'] = 17
    parsed = AdaptiveTaskInput.model_validate(value).model_dump()
    # HTTP metadata is accepted for round trips; trusted card normalization owns
    # the new estimate, not a stale or browser-authored decomposition.
    assert parsed['substeps'] == value['substeps']
    updated = prepare_task_overrides([task()], [parsed])[0]
    assert sum(step['minutes'] for step in updated['substeps']) == 17


def test_legacy_card_does_not_get_invented_steps():
    legacy = task()
    del legacy['substeps']
    card = parse_card(dict(title='Исследование', tasks=[legacy])).model_dump()
    assert card['tasks'][0]['substeps'] == []
    result = build_adaptive_plan(card['tasks'], availability(15, 35))
    assert all(slot['substeps'] == [] for day in result['schedule'] for slot in day['tasks'])
    assert result['required_minutes'] == 50


def test_relative_planner_uses_same_slicing_contract():
    result = build_plan(days=2, minutes_per_day=25, tasks=[task()])
    assert_exact_slots(result)
    assert result['schedule'][1]['tasks'][0]['substeps'][0]['continued']


@pytest.mark.parametrize('change', ['duplicate', 'empty-check', 'zero', 'too-many', 'bool'])
def test_invalid_substeps_rejected(change):
    value = task()
    if change == 'duplicate': value['substeps'][1]['id'] = 'step-1'
    if change == 'empty-check': value['substeps'][0]['done_when'] = ' '
    if change == 'zero': value['substeps'][0]['minutes'] = 0
    if change == 'bool': value['substeps'][0]['minutes'] = True
    if change == 'too-many':
        value['substeps'] += [dict(id=f'extra-{i}', title='Раздел', minutes=1, done_when='Проверен') for i in range(32)]
    with pytest.raises(CardError):
        parse_card(dict(title='Исследование', tasks=[value]))


def test_dependencies_and_reserve_do_not_move_substeps_ahead_of_parent():
    prerequisite = deepcopy(task())
    next_task = dict(id='analysis', title='Сопоставить ответы', minutes=20,
                     depends_on=['interviews'], priority='high', substeps=[
                         dict(id='codes', title='Разметить ответы', minutes=10, done_when='Каждый ответ имеет код'),
                         dict(id='themes', title='Сгруппировать коды', minutes=10, done_when='Выделены темы')])
    result = build_adaptive_plan([next_task, prerequisite], availability(40, 60), reserve_percent=25)
    assert [slot['id'] for day in result['schedule'] for slot in day['tasks']] == ['interviews', 'interviews', 'analysis']
    assert result['available_minutes'] == 75
    assert_exact_slots(result)
