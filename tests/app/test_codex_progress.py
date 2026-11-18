"""Canonical per-substep progress and explicit active time, never wall-clock time."""
import json
import sqlite3
import pytest

from studynavigator.catalog import Store, seed_database
from studynavigator.cards import manual_card
from studynavigator.codex_access import CodexAccess, CodexAccessError
from studynavigator.codex_progress import apply_progress
from studynavigator.planner import build_adaptive_plan


@pytest.fixture
def project(tmp_path):
    path = tmp_path / 'progress.sqlite'
    seed_database(path)
    store = Store(path, 'owner')
    assignment = store.create_text_assignment('Project', 'Project material')['assignment']['assignment_id']
    tasks = [{'id': 'task', 'title': 'Work', 'minutes': 30, 'substeps': [
        {'id': 'step', 'title': 'First', 'minutes': 10, 'done_when': 'Done'},
        {'id': 'other', 'title': 'Second', 'minutes': 20, 'done_when': 'Done'}]}]
    card = manual_card('Project')
    card['tasks'] = tasks
    with sqlite3.connect(path) as db:
        db.execute("UPDATE assignments SET card_json=?,card_revision=1,status='ready' WHERE assignment_id=?",
                   (json.dumps(card), assignment))
    data = {'tasks': tasks, 'availability': [{'day': 1, 'minutes': 120}], 'reserve_percent': 0, 'start_date': None}
    result = build_adaptive_plan(tasks, data['availability'], card=card)
    data['tasks'] = result['tasks']
    store.save_plan(assignment, revision=0, card_revision=1, plan_input=data, result=result)
    access = CodexAccess(path)
    access.initialize()
    def apply(action, substep='step', **kwargs):
        with access._db() as db:
            owner = db.execute('SELECT * FROM assignments WHERE assignment_id=?', (assignment,)).fetchone()
            saved = db.execute('SELECT * FROM assignment_plans WHERE assignment_id=?', (assignment,)).fetchone()
            return apply_progress(db, None, owner, saved,
                {'task_id': 'task', 'substep_id': substep, 'action': action, 'summary': 'Verified work or explicit time correction', **kwargs}, '2026-11-18T00:00:00+00:00')
    apply.access = access
    return store, assignment, apply


def test_pauses_reconnect_and_explicit_measurement(project):
    store, assignment, apply = project
    started = apply('start')['progress']
    assert started.get('actual_minutes') is None
    paused = apply('pause', time={'source': 'measured', 'basis': 'Monotonic active test stopwatch', 'minutes': 7})['progress']
    assert paused['actual_minutes'] == 7
    assert paused['measured_minutes'] == 7
    apply('resume')
    interrupted = apply('interrupt')['progress']
    assert interrupted['actual_minutes'] == 7
    apply('resume')
    completed = apply('complete', time={'source': 'measured', 'basis': 'Monotonic active test stopwatch', 'minutes': 3})['progress']
    assert completed['actual_minutes'] == 10
    assert completed['completed']
    tasks = store.get_plan(assignment)['plan']['input']['tasks']
    assert tasks[0]['substeps'][0]['actual_minutes'] == 10
    assert not tasks[0]['completed']
    assert not tasks[0]['substeps'][1]['completed']


def test_exact_user_time_estimate_and_reopen(project):
    store, assignment, apply = project
    result = apply('record', time={'source': 'user', 'minutes': 200})
    assert result['progress']['actual_minutes'] == 200
    estimated = apply('record', time={'source': 'estimate', 'minutes': 30})['progress']
    assert estimated['actual_minutes'] == 200
    assert estimated['estimated_actual_minutes'] == 30
    apply('complete', substep=None)
    reopened = apply('reopen')['plan']['input']['tasks'][0]
    assert not reopened['completed']
    assert not reopened['substeps'][0]['completed']
    assert reopened['substeps'][1]['completed']
    assert reopened['substeps'][0]['actual_minutes'] == 200


def test_idle_measurements_rejected_without_revision(project):
    store, assignment, apply = project
    revision = store.get_plan(assignment)['plan']['revision']
    with pytest.raises(CodexAccessError, match='Измеренное время'):
        apply('record', time={'source': 'measured', 'basis': 'Monotonic active test stopwatch', 'minutes': 40})
    assert store.get_plan(assignment)['plan']['revision'] == revision
    with pytest.raises(CodexAccessError):
        apply('pause')
    with pytest.raises(CodexAccessError):
        apply('record', time={'source': 'user', 'minutes': True})


def test_time_provenance_and_no_time_from_paused_state(project):
    _, _, apply = project
    apply('record', time={'source': 'user', 'minutes': 200})
    apply('start')
    target = apply('pause', time={'source': 'measured', 'basis': 'Monotonic active test stopwatch', 'minutes': 5})['progress']
    assert target['actual_minutes'] == 205
    assert target['actual_source'] == 'mixed'
    with pytest.raises(CodexAccessError):
        apply('record', time={'source': 'measured', 'basis': 'Monotonic active test stopwatch', 'minutes': 5})
    target = apply('record', time={'source': 'user', 'minutes': 200})['progress']
    assert target['actual_minutes'] == 200 and target['actual_source'] == 'user'
    target = apply('record', time={'source': 'unknown'})['progress']
    assert target['actual_minutes'] is None and target['actual_source'] == 'unknown'


def test_analytics_child_time_not_counted_twice():
    from studynavigator.analytics import build_analytics
    task = {'id': 'a', 'title': 'A', 'minutes': 30,
            'substeps': [{'id': 's', 'title': 'S', 'minutes': 10, 'actual_minutes': 200},
                        {'id': 'u', 'title': 'U', 'minutes': 20}]}
    result = build_analytics([task], [])
    assert result['totals']['actual_minutes'] == 200
    assert result['tasks'][0]['actual_source'] == 'substeps'
    assert result['tasks'][0]['actual_partial']
    task['actual_minutes'] = 220
    result = build_analytics([task], [])
    assert result['totals']['actual_minutes'] == 220
    assert not result['tasks'][0]['actual_partial']
    assert result['tasks'][0]['substeps'][0]['actual_minutes'] == 200


def test_agent_completion_consolidates_claim_and_rejects_dismissed_run(project):
    store, assignment, apply = project
    access = apply.access
    connection = access.create_connection('owner', assignment, 'Agent',
        scopes=['read', 'reports', 'progress'], completion_mode='auto')
    context = access.authenticate(connection['token'])
    run = access.claim_task(context, 'task', 1, 1, 'claim', substep_id='step')
    body = {'task_id': 'task', 'substep_id': 'step', 'action': 'complete',
            'summary': 'Implemented and verified result', 'evidence': ['pytest passed: focused test'],
            'card_revision': 1, 'plan_revision': 1, 'request_id': 'complete'}
    response = access.mutate(context, 'progress', body)
    assert response['progress']['completed']
    assert access.list_runs('owner', assignment)[0]['summary'] == body['summary']
    assert access.list_runs('owner', assignment)[0]['evidence'] == body['evidence']
    assert access.list_runs('owner', assignment)[0]['status'] == 'accepted'
    # A repeated acknowledged request cannot apply a second revision.
    assert access.mutate(context, 'progress', body) == response
    run = access.claim_task(context, 'task', 1, 2, 'other-claim', substep_id='other')
    access.dismiss_run('owner', assignment, run['id'])
    with pytest.raises(CodexAccessError) as caught:
        access.mutate(context, 'progress', {**body, 'run_id': run['id'], 'substep_id': 'other',
            'plan_revision': 2, 'request_id': 'dismissed-complete'})
    assert caught.value.code == 'invalid_run_state'
    assert not store.get_plan(assignment)['plan']['input']['tasks'][0]['substeps'][1]['completed']


def test_resume_after_process_loss_adds_no_elapsed_time(project):
    _, _, apply = project
    apply('start')
    target = apply('resume')['progress']
    assert target.get('actual_minutes') is None
    assert target.get('measured_minutes', 0) == 0
    with pytest.raises(CodexAccessError, match='основание'):
        apply('record', time={'source': 'measured', 'minutes': 200})
