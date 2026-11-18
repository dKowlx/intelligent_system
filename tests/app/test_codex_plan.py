"""Plan mutation is atomic and preserves progress across edits."""
import json
import sqlite3
import pytest
from pydantic import ValidationError
from studynavigator.catalog import seed_database, Store
from studynavigator.cards import manual_card
from studynavigator.codex_plan import edit_plan, PlanEdit
from studynavigator.planner import preserve_progress

@pytest.fixture
def project(tmp_path):
    path = tmp_path/'plan.sqlite'
    seed_database(path)
    store = Store(path, 'owner')
    identifier = store.create_text_assignment('Project', 'Data')['assignment']['assignment_id']
    card = manual_card('Project')
    card['goal']['text'] = 'Build project'
    card['deliverable']['text'] = 'Working result'
    card['tasks'] = [{'id':'first','title':'Build','minutes':30,'substeps':[
        {'id':'step','title':'Build item','minutes':30,'done_when':'Item exists'}]}]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE assignments SET card_json=?,card_revision=1,status='ready' WHERE assignment_id=?", (json.dumps(card), identifier))
    return path, store, identifier

def mutate(path, identifier, changes):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        owner = db.execute('SELECT * FROM assignments WHERE assignment_id=?', (identifier,)).fetchone()
        saved = db.execute('SELECT * FROM assignment_plans WHERE assignment_id=?', (identifier,)).fetchone()
        return edit_plan(db, owner, saved, changes)

def test_edit_add_dependencies_and_preserve_progress(project):
    path,store,identifier = project
    first = mutate(path,identifier,{'availability':[{'day':1,'minutes':180}]})
    assert first['card_revision'] == 1
    with sqlite3.connect(path) as db:
        data = first['plan']['input']
        data['tasks'][0].update(completed=True,actual_minutes=42,progress_status='completed')
        data['tasks'][0]['substeps'][0].update(completed=True,measured_minutes=10,progress_summary='done')
        db.execute('UPDATE assignment_plans SET input_json=? WHERE assignment_id=?',(json.dumps(data),identifier))
    result = mutate(path,identifier,{'tasks':[
        {'id':'first','minutes':60,'priority':'high'},
        {'id':'next','title':'Verify','minutes':20,'depends_on':['first']}]})
    tasks = result['plan']['input']['tasks']
    assert result['card_revision']==2 and result['plan_revision']==2
    assert tasks[0]['actual_minutes']==42 and tasks[0]['completed']
    assert tasks[0]['substeps'][0]['measured_minutes']==10
    assert tasks[0]['substeps'][0]['progress_summary']=='done'
    assert tasks[1]['depends_on']==['first']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM progress_history').fetchone()[0]==2

def test_invalid_dependency_rolls_back(project):
    path,store,identifier=project
    mutate(path,identifier,{'availability':[{'day':1,'minutes':30}]})
    before=store.get_plan(identifier)
    with pytest.raises(ValueError):
        mutate(path,identifier,{'tasks':[{'id':'first','depends_on':['missing']}]})
    assert store.get_plan(identifier)==before
    assert store.get_assignment(identifier)['assignment']['card_revision']==1

def test_progress_not_editable_and_substep_deletion_rejected(project):
    with pytest.raises(ValidationError):
        PlanEdit.model_validate({'tasks':[{'id':'first','completed':True}]})
    with pytest.raises(ValidationError):
        PlanEdit.model_validate({'tasks':[{'id':'first','substeps':[{'id':'s','title':'S','minutes':1,'done_when':'Yes','completed':True}]}]})
    with pytest.raises(ValueError):
        preserve_progress([{'id':'first','substeps':[]}],[{'id':'first','substeps':[{'id':'step','actual_minutes':2}]}])

def test_completed_steps_reduce_remaining_effort(project):
    path,store,identifier=project
    first=mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    with sqlite3.connect(path) as db:
        data=first['plan']['input']
        data['tasks'][0]['substeps'][0]['completed']=True
        db.execute('UPDATE assignment_plans SET input_json=? WHERE assignment_id=?',(json.dumps(data),identifier))
    result=mutate(path,identifier,{'reserve_percent':10})
    assert result['plan']['result']['required_minutes']==0
    assert all(not day['tasks'] for day in result['plan']['result']['schedule'])


def test_confirm_cannot_delete_recorded_zero_step(project):
    from studynavigator.cards import CardError
    path,store,identifier=project
    first=mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    with sqlite3.connect(path) as db:
        data=first['plan']['input']
        data['tasks'][0]['substeps'][0]['actual_minutes']=0
        db.execute('UPDATE assignment_plans SET input_json=? WHERE assignment_id=?',(json.dumps(data),identifier))
    card=store.get_assignment(identifier)['assignment']['card']
    card['tasks'][0]['substeps']=[]
    with pytest.raises(CardError) as raised:
        store.save_card(identifier,card,confirmed=True,expected_revision=1)
    assert raised.value.code=='progress_conflict'
    assert store.get_assignment(identifier)['assignment']['card_revision']==1


def test_web_round_trip_preserves_step_measurements():
    previous=[{'id':'task','substeps':[{'id':'step','completed':False,'actual_minutes':0,
               'measured_minutes':4,'progress_status':'partial','progress_summary':'Draft'}],
               'measured_minutes':4,'actual_source':'mixed'}]
    submitted=[{'id':'task','substeps':[{'id':'step','title':'Edited','minutes':30,
                'done_when':'Saved','completed':False,'actual_minutes':None}]}]
    result=preserve_progress(submitted,previous)
    assert result[0]['actual_source']=='mixed'
    assert result[0]['substeps'][0]['actual_minutes']==0
    assert result[0]['substeps'][0]['progress_summary']=='Draft'

@pytest.mark.parametrize('changes', [
    {'tasks':[{'id':'first','title':'x'*201}]},
    {'tasks':[{'id':'first','minutes':True}]},
    {'tasks':[{'id':'first','depends_on':['bad ref']}]},
    {'tasks':[{'id':'first','estimate_source':'document'}]},
    {'tasks':[{'id':'first','progress_status':'completed'}]},
    {'tasks':[{'id':'first','substeps':[{'id':'s','title':'S','minutes':1,'done_when':'Yes','actual_minutes':1}]}]},
])
def test_patch_schema_rejects_invalid_authored_input(changes):
    with pytest.raises(ValidationError):
        PlanEdit.model_validate(changes)


def test_cannot_remove_recorded_substep_via_mcp(project):
    path,store,identifier=project
    first=mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    with sqlite3.connect(path) as db:
        data=first['plan']['input']
        data['tasks'][0]['substeps'][0]['actual_minutes']=0
        db.execute('UPDATE assignment_plans SET input_json=? WHERE assignment_id=?',(json.dumps(data),identifier))
    before=store.get_plan(identifier)
    with pytest.raises(ValueError):
        mutate(path,identifier,{'tasks':[{'id':'first','substeps':[]}]})
    assert store.get_plan(identifier)==before

def test_web_confirm_and_replan_preserve_parent_progress(tmp_path):
    from fastapi.testclient import TestClient
    from studynavigator.config import Settings
    from studynavigator.main import create_app
    from test_project_workspace import NoModel, create_project, plan_body
    settings=Settings(database_path=tmp_path/'web.sqlite',rate_limit_per_minute=1000)
    with TestClient(create_app(settings=settings,provider=NoModel())) as browser:
        assignment=create_project(browser)
        identifier=assignment['assignment_id']
        body=plan_body(assignment)
        body['tasks'][0].update(completed=True,actual_minutes=45)
        saved=browser.put(f'/api/assignments/{identifier}/plan',json=body)
        assert saved.status_code==200,saved.text
        card=assignment['card']
        card['tasks'][0]['title']='Edited title'
        confirmed=browser.post(f'/api/assignments/{identifier}/confirm',json={
            'revision':assignment['card_revision'],'card':card})
        assert confirmed.status_code==200,confirmed.text
        body=plan_body(confirmed.json()['assignment'],revision=1)
        saved=browser.put(f'/api/assignments/{identifier}/plan',json=body)
        assert saved.status_code==200,saved.text
        task=saved.json()['plan']['input']['tasks'][0]
        assert task['completed'] and task['actual_minutes']==45
        assert task['progress_status']=='completed' and task['actual_source']=='user'


def test_plan_only_connection_cannot_inject_nested_progress(project):
    from studynavigator.codex_access import CodexAccess, CodexAccessError
    path,store,identifier=project
    mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    access=CodexAccess(path)
    access.initialize()
    issued=access.create_connection('owner',identifier,'Planner only',scopes=['read','plan'],completion_mode='review')
    context=access.authenticate(issued['token'])
    before=store.get_plan(identifier)
    forbidden={'completed':True,'actual_minutes':30,'measured_minutes':30,
               'estimated_actual_minutes':30,'progress_status':'completed',
               'work_state':'active','progress_summary':'done','progress_evidence':['result'],
               'progress_updated_at':'2026-11-18T00:00:00Z','actual_source':'measured',
               'status':'completed','time':{'source':'measured','minutes':30}}
    for identifier_kind in ('step','new-step'):
        for key,value in forbidden.items():
            body={'request_id':f'{identifier_kind}-{key}','card_revision':1,'plan_revision':1,
                  'reason':'Try bypass','changes':{'tasks':[{'id':'first','substeps':[
                      {'id':identifier_kind,'title':'Build item','minutes':30,'done_when':'Exists',key:value}]}]}}
            with pytest.raises(CodexAccessError) as rejected:
                access.mutate(context,'plan',body)
            assert rejected.value.code=='invalid_change'
            assert store.get_plan(project[2])==before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM progress_history WHERE assignment_id=?",(identifier,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM codex_activity WHERE assignment_id=? AND operation='update_plan'",(identifier,)).fetchone()[0]==0


def test_chat_apply_preserves_rich_progress_and_sets_user_fact(project):
    from studynavigator.assistant_actions import apply_assistant_actions
    path,store,identifier=project
    initial=mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    data=initial['plan']['input']
    data['tasks'][0].update(progress_status='paused',work_state='paused',measured_minutes=4,
                            actual_minutes=4,actual_source='measured',progress_summary='Draft')
    data['tasks'][0]['substeps'][0].update(actual_minutes=0,progress_summary='Step report')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignment_plans SET input_json=?,result_json=? WHERE assignment_id=?',
            (json.dumps(data),json.dumps({'tasks':data['tasks']}),identifier))
    result=apply_assistant_actions(store,identifier,{'summary':'Finish','operations':[
        {'op':'complete','task_id':'first','actual_minutes':7}]},1,1)
    task=result['plan']['input']['tasks'][0]
    assert task['progress_status']=='completed' and task['work_state']=='stopped'
    assert task['actual_source']=='user' and task['actual_minutes']==7 and task['measured_minutes']==4
    assert task['progress_summary']=='Draft'
    assert task['substeps'][0]['completed'] and task['substeps'][0]['actual_minutes']==0
    assert task['substeps'][0]['progress_summary']=='Step report'


@pytest.mark.parametrize('progress',[{'estimated_actual_minutes':0},{'progress_summary':'Reported'},
                                    {'progress_evidence':['result.txt']}])
def test_cannot_remove_substep_with_zero_estimate_or_report(progress):
    previous=[{'id':'task','substeps':[{'id':'step',**progress}]}]
    with pytest.raises(ValueError):
        preserve_progress([{'id':'task','substeps':[]}],previous)


def test_completed_parent_requires_reopen_before_new_step(project):
    path,store,identifier=project
    initial=mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    data=initial['plan']['input']
    data['tasks'][0]['completed']=True
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignment_plans SET input_json=? WHERE assignment_id=?',(json.dumps(data),identifier))
    with pytest.raises(ValueError,match='reopen'):
        mutate(path,identifier,{'tasks':[{'id':'first','substeps':[
            {'id':'step','title':'Build','minutes':20,'done_when':'Exists'},
            {'id':'new','title':'Extra','minutes':10,'done_when':'Exists'}]}]})


def test_report_without_minutes_cannot_be_removed(project):
    from studynavigator.codex_access import CodexAccess
    path,store,identifier=project
    mutate(path,identifier,{'availability':[{'day':1,'minutes':100}]})
    access=CodexAccess(path)
    access.initialize()
    issued=access.create_connection('owner',identifier,'Reporter',scopes=['read','reports'])
    context=access.authenticate(issued['token'])
    run=access.claim_task(context,'first',1,1,'claim',substep_id='step')
    access.report_task(context,run['id'],'partial','Draft exists',['draft.txt'],'report',expected_updated_at=run['updated_at'])
    with pytest.raises(ValueError,match='reported'):
        mutate(path,identifier,{'tasks':[{'id':'first','substeps':[]}]})


def test_new_saved_task_metadata_round_trips_but_is_not_browser_authority():
    from studynavigator.main import AdaptiveTaskInput
    from studynavigator.planner import prepare_task_overrides
    canonical={'id':'task','title':'Build','minutes':30,'completed':False,'actual_minutes':2,
               'progress_status':'paused','work_state':'paused','measured_minutes':2,'actual_source':'measured',
               'estimated_actual_minutes':0,'progress_summary':'Saved note','progress_evidence':['draft'],
               'progress_updated_at':'2026-11-18T00:00:00+00:00'}
    submitted=AdaptiveTaskInput.model_validate({**canonical,'progress_status':'completed','measured_minutes':999}).model_dump()
    normalized=prepare_task_overrides([canonical],[submitted])
    normalized=preserve_progress(normalized,[canonical])
    assert normalized[0]['progress_status']=='paused'
    assert normalized[0]['measured_minutes']==2
    assert normalized[0]['estimated_actual_minutes']==0
    assert normalized[0]['progress_summary']=='Saved note'
