"""Validated plan edits inside the authenticated caller's SQLite transaction."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json

from pydantic import BaseModel, ConfigDict, Field, StrictInt, create_model
from typing import Annotated

from .cards import CardTask, CardSubstep, validate_card_sources
from .planner import build_adaptive_plan, tasks_from_card, preserve_progress, protect_reported_steps

AuthoredSubstep = create_model('AuthoredSubstep', __config__=ConfigDict(extra='forbid'), **{
    name: (field.annotation, field) for name, field in CardSubstep.model_fields.items()
    if name in {'id', 'title', 'minutes', 'done_when'}
})

# Patches retain omitted values; final CardTask validation checks the merged task.
def _patch_field(name, field):
    patch = deepcopy(field)
    if name != 'id':
        patch.default = None
        patch.default_factory = None
    return patch


TaskEdit = create_model('TaskEdit', __config__=ConfigDict(extra='forbid'), **{
    name: (list[AuthoredSubstep] if name == "substeps" else field.annotation, _patch_field(name, field))
    for name, field in CardTask.model_fields.items()
    if name not in {'estimate_source', 'source_ids'}
})


class Availability(BaseModel):
    model_config = ConfigDict(extra='forbid')
    day: Annotated[StrictInt, Field(ge=1, le=30)]
    minutes: Annotated[StrictInt, Field(ge=0, le=480)]


class PlanEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tasks: Annotated[list[TaskEdit], Field(min_length=1, max_length=20)] | None = None
    availability: Annotated[list[Availability], Field(min_length=1, max_length=30)] | None = None
    start_date: date | None = None
    reserve_percent: Annotated[StrictInt, Field(ge=0, le=100)] | None = None


def persist_plan(db, owner, saved, card, plan_input, card_changed=False):
    """Recompute and save within the caller transaction, including history."""
    assignment_id, workspace_id = owner['assignment_id'], owner['workspace_id']
    card_revision = owner['card_revision'] + int(card_changed)
    plan_revision = (saved['revision'] if saved is not None else 0) + 1
    result = build_adaptive_plan(plan_input['tasks'], plan_input['availability'],
        start_date=plan_input.get('start_date'), reserve_percent=plan_input.get('reserve_percent', 0), card=card)
    # Normalization validates metadata; retain progress fields owned by other tools.
    originals = {task['id']: task for task in plan_input['tasks']}
    normalized = []
    authored_keys = {'id', 'title', 'minutes', 'done_when'}
    for task in result['tasks']:
        original = originals[task['id']]
        steps = {step['id']: step for step in original.get('substeps', [])}
        merged_steps = []
        for step in task.get('substeps', []):
            progress = {key: value for key, value in steps.get(step['id'], {}).items()
                        if key not in authored_keys}
            merged_steps.append({'completed': False, 'actual_minutes': None, **step, **progress})
        normalized.append({**original, **task, 'substeps': merged_steps})
    plan_input['tasks'] = normalized
    result['tasks'] = deepcopy(plan_input['tasks'])
    result.update(assignment_id=assignment_id, card_revision=card_revision)
    now = datetime.now(timezone.utc).isoformat()
    if card_changed:
        db.execute('UPDATE assignments SET card_json=?,card_revision=? WHERE assignment_id=? AND workspace_id=?',
            (json.dumps(card, ensure_ascii=False), card_revision, assignment_id, workspace_id))
    db.execute('''INSERT INTO assignment_plans VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(assignment_id,workspace_id) DO UPDATE SET revision=excluded.revision,
        card_revision=excluded.card_revision,input_json=excluded.input_json,
        result_json=excluded.result_json,updated_at=excluded.updated_at''',
        (assignment_id, workspace_id, plan_revision, card_revision,
         json.dumps(plan_input, ensure_ascii=False), json.dumps(result, ensure_ascii=False), now))
    db.execute('INSERT INTO progress_history VALUES (?,?,?,?,?)',
        (assignment_id, workspace_id, plan_revision, now, json.dumps(plan_input['tasks'], ensure_ascii=False)))
    return {'card_revision': card_revision, 'plan_revision': plan_revision,
            'plan': {'revision': plan_revision, 'card_revision': card_revision,
                     'input': plan_input, 'result': result, 'stale': False}}


def edit_plan(db, owner, saved, changes):
    """Upsert authored tasks while retaining all existing progress by stable ID."""
    changes = changes if isinstance(changes, PlanEdit) else PlanEdit.model_validate(changes)
    if not changes.model_fields_set:
        raise ValueError('provide at least one plan change')
    if any(getattr(changes, key) is None for key in changes.model_fields_set - {'start_date'}):
        raise ValueError('only start_date can be null')
    card = json.loads(owner['card_json'])
    data = json.loads(saved['input_json']) if saved is not None else {
        'tasks': tasks_from_card(card), 'availability': [{'day': 1, 'minutes': 120}],
        'reserve_percent': 20, 'start_date': None}
    tasks = deepcopy(data['tasks'])
    indices = {task['id']: index for index, task in enumerate(tasks)}
    patches = changes.tasks or []
    if len({patch.id for patch in patches}) != len(patches):
        raise ValueError('duplicate task edits')
    for patch in patches:
        update = patch.model_dump(exclude_unset=True)
        existing = tasks[indices[patch.id]] if patch.id in indices else {}
        merged = {**existing, **update}
        if 'minutes' in update and update['minutes'] != existing.get('minutes'):
            if 'min_minutes' not in update and 'max_minutes' not in update:
                merged.update(min_minutes=None, max_minutes=None)
            merged.update(estimate_source='estimate', source_ids=[])
            if 'estimate_basis' not in update:
                merged['estimate_basis'] = 'Оценка подключённого ассистента; требует проверки пользователем'
        # Prevent removal of substeps with recorded work.
        if 'substeps' in update:
            retained = {step['id'] for step in update['substeps']}
            for step in existing.get('substeps', []):
                if step['id'] not in retained and (step.get('completed') or step.get('actual_minutes') is not None or step.get('progress_status', 'pending') != 'pending' or step.get('measured_minutes') or step.get('estimated_actual_minutes')):
                    raise ValueError('cannot remove substep with progress')
        authored = CardTask.model_validate({key: value for key, value in merged.items() if key in CardTask.model_fields}).model_dump()
        old_steps = {step['id']: step for step in existing.get('substeps', [])}
        authored['substeps'] = [{**step, **{key: value for key, value in old_steps.get(step["id"], {}).items() if key not in {"id", "title", "minutes", "done_when"}}} for step in authored['substeps']]
        merged = {**existing, **authored}
        if patch.id in indices:
            tasks[indices[patch.id]] = merged
        else:
            tasks.append({**merged, 'completed': False, 'actual_minutes': None})
    tasks = preserve_progress(tasks, data['tasks'])
    protect_reported_steps(db, owner['assignment_id'], tasks)
    data['tasks'] = tasks
    for key in ('availability', 'reserve_percent', 'start_date'):
        if key in changes.model_fields_set:
            value = getattr(changes, key)
            data[key] = ([item.model_dump() for item in value] if key == 'availability'
                         else value.isoformat() if isinstance(value, date) else value)
    if patches:
        card['tasks'] = [{key: value for key, value in task.items() if key in CardTask.model_fields} for task in tasks]
        # Strip substep progress from authored card while validating source references.
        for task in card['tasks']:
            task['substeps'] = [{key: value for key, value in step.items() if key in {'id','title','minutes','done_when'}} for step in task['substeps']]
        sources = [dict(row) for row in db.execute('SELECT * FROM sources WHERE assignment_id=?', (owner['assignment_id'],))]
        card = validate_card_sources(card, sources, confirmed=True).model_dump()
    return persist_plan(db, owner, saved, card, data, card_changed=bool(patches))
