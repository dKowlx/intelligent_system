"""Offline structural guarantees plus a human-authored research-project reference."""
import copy
import json
from pathlib import Path

import pytest

from studynavigator.cards import CardError, confirm_card, parse_card
from studynavigator.planner import build_adaptive_plan, prepare_task_overrides, tasks_from_card


@pytest.fixture
def reference():
    return json.loads(Path('evals/app/project-reference.json').read_text())


def test_substantive_reference_retains_all_work_and_evidence(reference):
    card = parse_card(reference['card']).model_dump()
    result = build_adaptive_plan(tasks_from_card(card), reference['availability'], card=card)
    expected = reference['expected']
    for key in ('required_minutes', 'available_minutes', 'deficit_minutes'):
        assert result[key] == expected[key]
    assert result['coverage']['linked_count'] == result['coverage']['complete_check_count'] == 4
    assert result['schedule'][1]['tasks'] == []
    sequence = list(dict.fromkeys(task['id'] for day in result['schedule'] for task in day['tasks']))
    assert sequence == expected['task_order']
    assert sum(task['minutes'] for day in result['schedule'] for task in day['tasks']) == 450
    assert result['remaining_tasks'][0]['id'] == 'report'
    assert result['remaining_tasks'][0]['minutes'] == 20
    # Rubric is independent of scheduler output, and includes rejected outputs.
    assert all(item['accept'] and item['reject'] for item in reference['rubric'])


def test_omissions_extras_and_generic_links_are_distinct(reference):
    card = reference['card']
    tasks = copy.deepcopy(card['tasks'])
    tasks[2]['requirement_ids'] = []
    tasks[0].update(title='Выполнить: интервью', expected_result='', done_when='', estimate_basis='')
    tasks.append(dict(id='extra', title='Развернуть сайт', minutes=60))
    result = build_adaptive_plan(tasks, reference['availability'], card=card)
    assert result['coverage']['uncovered_requirement_ids'] == ['req-synthesis']
    assert result['coverage']['linked_count'] == 3
    codes = {(w['code'], w.get('task_id')) for w in result['warnings']}
    assert ('unlinked_task', 'extra') in codes
    assert ('generic_task', 'guide') in codes
    assert ('missing_result', 'guide') in codes
    assert ('missing_estimate_basis', 'guide') in codes
    # A lone generic link cannot become a checked requirement.
    tasks[1]['requirement_ids'] = []
    assert build_adaptive_plan(tasks, reference['availability'], card=card)['coverage']['complete_check_count'] == 2


@pytest.mark.parametrize('mutation', ['unknown', 'cycle', 'range', 'unknown-requirement'])
def test_invalid_task_graph_ranges_and_references_rejected(reference, mutation):
    card = copy.deepcopy(reference['card'])
    if mutation == 'unknown': card['tasks'][0]['depends_on'] = ['missing']
    if mutation == 'cycle': card['tasks'][0]['depends_on'] = ['report']
    if mutation == 'range': card['tasks'][0]['max_minutes'] = 20
    if mutation == 'unknown-requirement': card['tasks'][0]['requirement_ids'] = ['missing']
    with pytest.raises(CardError): parse_card(card)


def test_ids_stable_during_reorder_and_confirmation_preserves_metadata(reference):
    card = reference['card']
    for item in card['requirements']: item.pop('id')
    for task in card['tasks']: task['requirement_ids'] = []
    draft = parse_card(card).model_dump()
    proposed = copy.deepcopy(draft)
    proposed['requirements'].reverse()
    proposed['tasks'][0]['minutes'] = 50
    confirmed = confirm_card(proposed, draft, [])
    assert [r['id'] for r in confirmed['requirements']] == ['req-4','req-3','req-2','req-1']
    assert confirmed['tasks'][0]['expected_result'] == draft['tasks'][0]['expected_result']
    assert confirmed['tasks'][0]['min_minutes'] is None
    assert confirmed['tasks'][0]['estimate_source'] == 'user'


def test_browser_overrides_keep_trusted_links_and_clear_obsolete_estimate(reference):
    tasks = tasks_from_card(reference['card'])
    overrides = copy.deepcopy(tasks)
    overrides[0].update(minutes=10, actual_minutes=80, requirement_ids=['forged'], depends_on=['missing'],
                        expected_result='forged', min_minutes=100, max_minutes=2)
    result = prepare_task_overrides(tasks, overrides)
    assert result[0]['requirement_ids'] == tasks[0]['requirement_ids']
    assert result[0]['expected_result'] == tasks[0]['expected_result']
    assert result[0]['actual_minutes'] == 80
    assert result[0]['min_minutes'] is result[0]['max_minutes'] is None
    assert result[0]['estimate_basis'] == 'Оценка пользователя'
    assert result[1]['actual_minutes'] is None


def test_completed_work_excluded_actual_not_subtracted_and_dependencies_ordered(reference):
    tasks = tasks_from_card(reference['card'])
    tasks[0].update(completed=True, actual_minutes=70)
    tasks[1]['actual_minutes'] = 20
    result = build_adaptive_plan(list(reversed(tasks)), [{'day':1,'minutes':480}], reserve_percent=10)
    assert result['required_minutes'] == 425
    assert result['available_minutes'] == 432
    assert result['tasks'][-1]['actual_minutes'] == 70
    assert result['schedule'][0]['tasks'][0]['id'] == 'interviews'
    tasks[1]['completed'] = True
    tasks[0]['completed'] = False
    assert 'inconsistent_completion' in {w['code'] for w in build_adaptive_plan(tasks,[{'day':1,'minutes':0}])['warnings']}


@pytest.mark.parametrize('availability', [[], [{'day':2,'minutes':60}], [{'day':1,'minutes':481}], [{'day':1,'minutes':True}]])
def test_availability_validation(reference, availability):
    with pytest.raises(ValueError): build_adaptive_plan(reference['card']['tasks'], availability)


def test_fallback_is_explicitly_incomplete_not_quality_analysis():
    card = {'goal':{'text':'Исследовать'}, 'deliverable':{'text':'Отчёт'},
            'requirements':[{'id':'req-a','text':'Пять интервью'}]}
    result = build_adaptive_plan(tasks_from_card(card),[{'day':1,'minutes':0}],card=card)
    assert result['coverage']['linked_count'] == 1
    assert result['coverage']['complete_check_count'] == 0
    assert 'generic_task' in {w['code'] for w in result['warnings']}
    assert result['required_minutes'] == result['deficit_minutes'] == 215


def test_deadline_excludes_late_capacity_and_unknown_start_warns(reference):
    card = copy.deepcopy(reference['card'])
    card['deadline'] = {'date':'2027-01-01','origin':'user','source_ids':[]}
    result = build_adaptive_plan(card['tasks'], [{'day':1,'minutes':60},{'day':2,'minutes':480}],
                                 start_date='2027-01-01', card=card)
    assert result['available_minutes'] == 60
    assert result['schedule'][1]['tasks'] == []
    assert result['deficit_minutes'] == 410
    assert 'deadline_limits_availability' in {w['code'] for w in result['warnings']}
    unknown_start = build_adaptive_plan(card['tasks'], [{'day':1,'minutes':480}], card=card)
    assert 'deadline_not_checked' in {w['code'] for w in unknown_start['warnings']}


def test_complete_check_is_not_completed_work(reference):
    result = build_adaptive_plan(reference['card']['tasks'], [{'day':1,'minutes':0}], card=reference['card'])
    assert result['coverage']['complete_check_count'] == 4
    assert result['completed_task_count'] == 0
    assert result['deficit_minutes'] == 470


def test_reordering_requirements_preserves_unchanged_tasks(reference):
    draft = parse_card(reference['card']).model_dump()
    proposed = copy.deepcopy(draft)
    proposed['requirements'].reverse()
    confirmed = confirm_card(proposed, draft, [])
    assert confirmed['tasks'] == draft['tasks']
    assert confirmed['requirements'][0]['id'] == 'req-report'


def test_new_manual_tasks_keep_explicit_basis_and_range(reference):
    confirmed = confirm_card(reference['card'], None, [])
    assert confirmed['tasks'][0]['estimate_basis'] == reference['card']['tasks'][0]['estimate_basis']
    assert confirmed['tasks'][0]['min_minutes'] == 30
    assert confirmed['tasks'][0]['max_minutes'] == 60
    assert confirmed['tasks'][0]['estimate_source'] == 'user'


def test_legacy_plan_respects_new_dependencies():
    from studynavigator.planner import build_plan
    result = build_plan(days=1, minutes_per_day=60, tasks=[
        {'id':'child','title':'Child','minutes':10,'depends_on':['parent']},
        {'id':'parent','title':'Parent','minutes':10}])
    assert [task['id'] for task in result['schedule'][0]['tasks']] == ['parent','child']
