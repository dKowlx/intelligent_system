"""Canonical progress updates inside the caller's SQLite transaction.

Only explicit measured active work counts: opening a session, idle time and
reconnecting never manufacture actual time. Estimates remain separate facts.
"""
from typing import Annotated, Literal
import json
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class ProgressTime(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['measured', 'user', 'estimate', 'unknown']
    minutes: Annotated[StrictInt, Field(ge=0, le=100000)] | None = None
    measurement: Literal['bridge_timer', 'external'] | None = None
    basis: Annotated[StrictStr, Field(max_length=1000)] | None = None


class ProgressBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: StrictStr
    substep_id: StrictStr | None = None
    action: Literal['start', 'resume', 'pause', 'interrupt', 'record', 'complete', 'reopen']
    run_id: StrictStr | None = None
    summary: Annotated[StrictStr, Field(max_length=3000)] = ''
    evidence: list[Annotated[StrictStr, Field(max_length=1000)]] = Field(default_factory=list, max_length=10)
    time: ProgressTime | None = None


def _fail(code, message):
    from .codex_access import CodexAccessError
    raise CodexAccessError(code, message)


def get_target(data, task_id, substep_id=None):
    task = next((t for t in data.get('tasks', []) if t['id'] == task_id), None)
    if task is None:
        _fail('invalid_task', 'Действие не найдено.')
    if substep_id is None:
        return task
    step = next((s for s in task.get('substeps', []) if s['id'] == substep_id), None)
    if step is None:
        _fail('invalid_substep', 'Подшаг не найден.')
    return step


def apply_progress(db, connection, owner, saved, body, now):
    from .codex_plan import persist_plan
    from .codex_access import CodexAccessError
    try:
        command = ProgressBody.model_validate(body)
    except ValueError as exc:
        raise CodexAccessError('invalid_arguments', 'Проверьте параметры прогресса.', 422) from exc
    data = json.loads(saved['input_json'])
    if command.action in ('complete', 'reopen', 'record') and not command.summary.strip():
        _fail('invalid_arguments', 'Опишите результат, причину исправления или записываемое время.')
    if connection is not None and command.action == 'complete' and not any(item.strip() for item in command.evidence):
        _fail('evidence_required', 'Для автоматического завершения укажите проверяемое подтверждение.')
    task = get_target(data, command.task_id)
    target = get_target(data, command.task_id, command.substep_id)
    connection_id = connection['id'] if connection is not None else None
    # A parent claim conflicts with every child; independent substeps can be worked on separately.
    runs = db.execute('''SELECT * FROM codex_runs WHERE workspace_id=? AND assignment_id=?
        AND task_id=? AND status IN ('in_progress','blocked','partial','awaiting_review')''',
        (owner['workspace_id'], owner['assignment_id'], command.task_id)).fetchall()
    for run in runs:
        step = run['substep_id'] if 'substep_id' in run.keys() else None
        overlapping = step is None or command.substep_id is None or step == command.substep_id
        if overlapping and connection_id is not None and run['connection_id'] != connection_id:
            _fail('task_claimed', 'Действие или подшаг занят другим подключением.')
    related_run = None
    if command.run_id:
        run = db.execute('SELECT * FROM codex_runs WHERE id=? AND workspace_id=? AND assignment_id=?',
                         (command.run_id, owner['workspace_id'], owner['assignment_id'])).fetchone()
        if run is None or run['task_id'] != command.task_id or (
                connection_id is not None and run['connection_id'] != connection_id):
            _fail('unknown_run', 'Работа не найдена.')
        if run['status'] not in ('in_progress', 'blocked', 'partial', 'awaiting_review'):
            _fail('invalid_run_state', 'Работа уже закрыта.')
        related_run = run
        step = run['substep_id'] if 'substep_id' in run.keys() else None
        if step != command.substep_id:
            _fail('invalid_substep', 'Работа относится к другому подшагу.')
    elif connection_id is not None:
        related_run = next((run for run in runs if run['connection_id'] == connection_id and
            (run['substep_id'] if 'substep_id' in run.keys() else None) == command.substep_id), None)
    if command.action in ('start', 'resume', 'complete'):
        tasks = {t['id']: t for t in data['tasks']}
        if any(not tasks.get(dep, {}).get('completed') for dep in task.get('depends_on', [])):
            _fail('dependencies_pending', 'Сначала завершите предшествующие действия.')
    state = target.get('work_state', 'stopped')
    if command.action == 'resume' and state not in ('paused', 'interrupted', 'active'):
        _fail('invalid_work_state', 'Возобновить можно начатую, приостановленную или прерванную работу.')
    if command.action == 'start' and state == 'active':
        _fail('invalid_work_state', 'Работа уже начата.')
    if command.action in ('pause', 'interrupt') and state != 'active':
        _fail('invalid_work_state', 'Сначала начните работу.')
    if command.action in ('start', 'resume') and target.get('completed'):
        _fail('invalid_work_state', 'Сначала откройте завершённое действие повторно.')
    recorded = command.time
    if recorded is not None:
        if recorded.source != 'unknown' and recorded.minutes is None:
            _fail('invalid_arguments', 'Укажите минуты для известного источника времени.')
        if recorded.source == 'unknown' and recorded.minutes is not None:
            _fail('invalid_arguments', 'Неизвестное время не содержит минут.')
        if recorded.source == 'measured':
            if not recorded.basis or not recorded.basis.strip():
                _fail('invalid_arguments', 'Для измеренного времени укажите основание измерения.')
            if state != 'active' or command.action in ('start', 'resume', 'reopen'):
                _fail('invalid_work_state', 'Измеренное время принимается только для активной работы.')
            measured = target.get('measured_minutes', 0) + recorded.minutes
            actual = (target.get('actual_minutes') or 0) + recorded.minutes
            if actual > 100000 or measured > 100000:
                _fail('invalid_arguments', 'Суммарное время превышает допустимый предел.')
            previous_source = target.get('actual_source', 'unknown')
            source = 'mixed' if target.get('actual_minutes') is not None and previous_source != 'measured' else 'measured'
            target.update(actual_minutes=actual, measured_minutes=measured, actual_source=source)
        elif recorded.source == 'user':
            target.update(actual_minutes=recorded.minutes, actual_source='user')
        elif recorded.source == 'estimate':
            target['estimated_actual_minutes'] = recorded.minutes
        else:
            target.update(actual_minutes=None, actual_source='unknown')
    if command.action in ('start', 'resume'):
        target.update(work_state='active', progress_status='in_progress')
    elif command.action in ('pause', 'interrupt'):
        target.update(work_state='paused' if command.action == 'pause' else 'interrupted',
                      progress_status='paused' if command.action == 'pause' else 'interrupted')
    elif command.action == 'complete':
        target.update(completed=True, work_state='stopped', progress_status='completed')
        if command.substep_id is None:
            for step in task.get('substeps', []):
                step.update(completed=True, work_state='stopped', progress_status='completed')
    elif command.action == 'reopen':
        target.update(completed=False, work_state='stopped', progress_status='pending')
        if command.substep_id is not None:
            task.update(completed=False, progress_status='pending', work_state='stopped')
    if command.summary:
        target['progress_summary'] = command.summary
    if command.evidence:
        target['progress_evidence'] = command.evidence
    target['progress_updated_at'] = now
    response = persist_plan(db, owner, saved, json.loads(owner['card_json']), data)
    if related_run is not None and command.action in ('complete', 'reopen'):
        if connection_id is not None:
            db.execute('UPDATE codex_runs SET status=?,summary=?,evidence=?,updated_at=? WHERE id=?',
                       ('accepted' if command.action == 'complete' else 'dismissed', command.summary,
                        json.dumps(command.evidence, ensure_ascii=False), now, related_run['id']))
        else:
            # Owner acceptance preserves the original submitted report.
            db.execute('UPDATE codex_runs SET status=?,updated_at=? WHERE id=?',
                       ('accepted' if command.action == 'complete' else 'dismissed', now, related_run['id']))
    if command.action == 'complete' and (connection_id is None and not command.run_id or command.substep_id is None):
        for run in runs:
            step = run['substep_id'] if 'substep_id' in run.keys() else None
            if (connection_id is None or run['connection_id'] == connection_id) and (command.substep_id is None or step is None or step == command.substep_id):
                db.execute('UPDATE codex_runs SET status=?,updated_at=? WHERE id=?',
                           ('accepted', now, run['id']))
    return {**response, 'task_id': command.task_id, 'substep_id': command.substep_id, 'progress': target}
