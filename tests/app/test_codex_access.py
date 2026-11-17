"""Real SQLite isolation, review and optimistic revision checks."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from studynavigator.catalog import Store, seed_database
from studynavigator.cards import manual_card
from studynavigator.codex_access import CodexAccess, CodexAccessError
from studynavigator.planner import build_adaptive_plan


@pytest.fixture
def setup(tmp_path):
    path=tmp_path/'access.sqlite'
    seed_database(path)
    store=Store(path,'owner')
    assignment=store.create_text_assignment('Project','ORION private material')['assignment']['assignment_id']
    tasks=[{'id':'first','title':'Prepare result','minutes':30,'completed':False},
           {'id':'second','title':'Verify result','minutes':20,'completed':False,'depends_on':['first']}]
    card=manual_card('Project')
    card['tasks']=tasks
    with sqlite3.connect(path) as db:
        db.execute("UPDATE assignments SET card_json=?,card_revision=1,status='ready' WHERE assignment_id=?",(json.dumps(card),assignment))
    data={'tasks':tasks,'availability':[{'day':1,'minutes':120}], 'reserve_percent':0,'start_date':None}
    result=build_adaptive_plan(tasks,data['availability'],card=card)
    data['tasks']=result['tasks']
    store.save_plan(assignment,revision=0,card_revision=1,plan_input=data,result=result)
    access=CodexAccess(path)
    access.initialize()
    connection=access.create_connection('owner',assignment,'Laptop')
    context=access.authenticate(connection['token'])
    return path,store,assignment,access,connection,context


def error(code, function, *args):
    with pytest.raises(CodexAccessError) as caught:
        function(*args)
    assert caught.value.code==code


def test_scope_secrets_and_revoke(setup):
    path,store,project,access,connection,context=setup
    assert connection['token'].startswith('pn_')
    with sqlite3.connect(path) as db:
        assert connection['token'] not in repr(db.execute('SELECT * FROM codex_connections').fetchall())
    error('unknown_assignment',access.create_connection,'other',project,'Bad')
    error('unknown_assignment',access.create_connection,'owner','personal-project','Bad')
    error('unknown_assignment',access.list_connections,'other',project)
    error('invalid_token',access.get_project,{**context,'assignment_id':'personal-project'})
    own=access.get_project(context)
    assert own['assignment']['assignment_id']==project
    assert 'brief' not in own['assignment']
    assert 'excerpt' not in own['assignment']['sources'][0]
    other=Store(path,'other').create_text_assignment('Other','ORION hidden secret')['assignment']['assignment_id']
    matches=access.search_materials(context,'ORION')['matches']
    assert matches and all(m['assignment_id']==project for m in matches)
    access.revoke_connection('owner',project,connection['connection']['id'])
    error('invalid_token',access.authenticate,connection['token'])
    error('invalid_token',access.get_project,context)


def test_expiry_owner_disappearance_and_limit(setup):
    path,store,project,access,connection,context=setup
    for i in range(4):
        access.create_connection('owner',project,str(i))
    error('connection_limit',access.create_connection,'owner',project,'sixth')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE codex_connections SET expires_at='2000-01-01' WHERE id=?",(context['connection_id'],))
    error('invalid_token',access.authenticate,connection['token'])
    replacement=access.create_connection('owner',project,'replacement')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignments SET workspace_id=? WHERE assignment_id=?',('different',project))
    error('unknown_assignment',access.authenticate,replacement['token'])
    error('unknown_assignment',access.list_connections,'owner',project)


def test_review_only_completion_and_retry(setup):
    path,store,project,access,connection,ctx=setup
    error('dependencies_pending',access.claim_task,ctx,'second',1,1,'dependency')
    error('stale_plan',access.claim_task,ctx,'first',1,2,'stale')
    run=access.claim_task(ctx,'first',1,1,'claim')
    assert access.claim_task(ctx,'first',1,1,'claim')==run
    error('request_conflict',access.claim_task,ctx,'second',1,1,'claim')
    error('invalid_arguments',access.report_task,ctx,run['id'],'completed','done',[],'bad')
    other=access.authenticate(access.create_connection('owner',project,'Other')['token'])
    error('unknown_run',access.report_task,other,run['id'],'blocked','No',[],'report')
    reported=access.report_task(ctx,run['id'],'awaiting_review','Done',['result.txt'],'report')
    assert access.report_task(ctx,run['id'],'awaiting_review','Done',['result.txt'],'report')==reported
    error('request_conflict',access.report_task,ctx,run['id'],'blocked','Changed',[],'report')
    assert not store.get_plan(project)['plan']['input']['tasks'][0]['completed']
    error('stale_plan',access.accept_run,'owner',project,run['id'],1,2)
    accepted=access.accept_run('owner',project,run['id'],1,1)
    assert accepted['run']['status']=='accepted'
    assert store.get_plan(project)['plan']['input']['tasks'][0]['completed']
    assert store.get_plan(project)['plan']['revision']==2
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM progress_history WHERE assignment_id=?',(project,)).fetchone()[0]==2
    assert access.claim_task(ctx,'second',1,2,'next')['status']=='in_progress'


def test_collision_dismiss_stale_and_revoked_report(setup):
    path,store,project,access,connection,ctx=setup
    other=access.authenticate(access.create_connection('owner',project,'Other')['token'])
    def claim(pair):
        context,request=pair
        try:
            return access.claim_task(context,'first',1,1,request)
        except CodexAccessError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(claim,[(ctx,'one'),(other,'two')]))
    assert sum(isinstance(r,dict) for r in results)==1
    assert 'task_claimed' in results
    run=next(r for r in results if isinstance(r,dict))
    claiming=ctx if run['connection_id']==ctx['connection_id'] else other
    error('unknown_assignment',access.dismiss_run,'intruder',project,run['id'])
    access.dismiss_run('owner',project,run['id'])
    error('invalid_run_state',access.report_task,claiming,run['id'],'blocked','',[],'late')
    run=access.claim_task(ctx,'first',1,1,'new')
    access.report_task(ctx,run['id'],'awaiting_review','done',[],'ready')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignments SET card_revision=2 WHERE assignment_id=?',(project,))
    error('stale_plan',access.accept_run,'owner',project,run['id'],1,1)
    access.revoke_connection('owner',project,ctx['connection_id'])
    error('invalid_token',access.report_task,ctx,run['id'],'blocked','',[],'revoked')


def test_cross_project_writes_and_invalid_report_are_atomic(setup):
    path,store,project,access,connection,ctx=setup
    foreign=Store(path,'another-owner').create_text_assignment('Foreign','secret')['assignment']['assignment_id']
    foreign_connection=access.create_connection('another-owner',foreign,'Foreign connection')
    foreign_context=access.authenticate(foreign_connection['token'])
    run=access.claim_task(ctx,'first',1,1,'start')
    error('unknown_connection',access.revoke_connection,'owner',project,foreign_connection['connection']['id'])
    error('unknown_run',access.dismiss_run,'another-owner',foreign,run['id'])
    error('unknown_run',access.accept_run,'another-owner',foreign,run['id'],1,1)
    error('unknown_run',access.report_task,foreign_context,run['id'],'awaiting_review','Stolen',[],'foreign')
    for status,summary,evidence in [('accepted','',[]),('awaiting_review','x'*3001,[]),
                                     ('blocked','',['x']*11),('blocked','',['x'*1001]),
                                     ('blocked','',[{'path':'unsafe'}]),('blocked',None,[])]:
        error('invalid_arguments',access.report_task,ctx,run['id'],status,summary,evidence,'invalid')
    unchanged=access.list_runs('owner',project)[0]
    assert unchanged['status']=='in_progress' and unchanged['summary']=='' and unchanged['evidence']==[]
    assert not store.get_plan(project)['plan']['input']['tasks'][0]['completed']
    assert access.authenticate(foreign_connection['token'])==foreign_context
    access.report_task(ctx,run['id'],'awaiting_review','Done',[],'valid')
    access.accept_run('owner',project,run['id'],1,1)
    error('invalid_run_state',access.report_task,ctx,run['id'],'blocked','Undo',[],'after-accept')
    assert store.get_plan(project)['plan']['input']['tasks'][0]['completed']


def test_card_edit_and_expiry_prevent_writes(setup):
    path,store,project,access,connection,ctx=setup
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignments SET card_revision=2 WHERE assignment_id=?',(project,))
    error('stale_plan',access.claim_task,ctx,'first',1,1,'old-card')
    error('stale_plan',access.claim_task,ctx,'first',2,1,'old-plan')
    assert access.list_runs('owner',project)==[]
    with sqlite3.connect(path) as db:
        db.execute('UPDATE assignments SET card_revision=1 WHERE assignment_id=?',(project,))
    run=access.claim_task(ctx,'first',1,1,'live')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE codex_connections SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(ctx['connection_id'],))
    error('invalid_token',access.report_task,ctx,run['id'],'awaiting_review','Late',[],'expired-report')
    error('invalid_token',access.claim_task,ctx,'first',1,1,'expired-claim')
    assert access.list_runs('owner',project)[0]['status']=='in_progress'
    assert not store.get_plan(project)['plan']['input']['tasks'][0]['completed']


def test_failed_plan_rebuild_rolls_back_acceptance(setup, monkeypatch):
    path,store,project,access,connection,ctx=setup
    run=access.claim_task(ctx,'first',1,1,'start')
    access.report_task(ctx,run['id'],'awaiting_review','Done',[],'report')
    def fail(*args,**kwargs):
        raise ValueError('planner failed')
    monkeypatch.setattr('studynavigator.codex_access.build_adaptive_plan',fail)
    with pytest.raises(ValueError,match='planner failed'):
        access.accept_run('owner',project,run['id'],1,1)
    assert access.list_runs('owner',project)[0]['status']=='awaiting_review'
    plan=store.get_plan(project)['plan']
    assert plan['revision']==1 and not plan['input']['tasks'][0]['completed']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM progress_history WHERE assignment_id=?',(project,)).fetchone()[0]==1


def test_report_replay_survives_newer_reports_and_restart(setup):
    path,store,project,access,connection,ctx=setup
    run=access.claim_task(ctx,'first',1,1,'claim')
    first=access.report_task(ctx,run['id'],'blocked','Original',['first.txt'],'A')
    second=access.report_task(ctx,run['id'],'blocked','Newer',['second.txt'],'B')
    restarted=CodexAccess(path)
    restarted.initialize()
    assert restarted.report_task(ctx,run['id'],'blocked','Original',['first.txt'],'A')==first
    assert restarted.list_runs('owner',project)[0]==second
    error('request_conflict',restarted.report_task,ctx,run['id'],'awaiting_review','Changed',[],'A')
    restarted.dismiss_run('owner',project,run['id'])
    next_run=restarted.claim_task(ctx,'first',1,1,'next-claim')
    error('request_conflict',restarted.report_task,ctx,next_run['id'],'blocked','Original',['first.txt'],'A')
    # Replaying does not bypass current credentials even though the response is persisted.
    restarted.revoke_connection('owner',project,ctx['connection_id'])
    error('invalid_token',restarted.report_task,ctx,run['id'],'blocked','Original',['first.txt'],'A')


def test_project_projection_has_tasks_once_and_bounded_recent_runs(setup):
    path,store,project,access,connection,ctx=setup
    for index in range(23):
        run=access.claim_task(ctx,'first',1,1,f'claim-{index}')
        access.report_task(ctx,run['id'],'blocked',f'Result {index}',[f'proof-{index}'],f'report-{index}')
        access.dismiss_run('owner',project,run['id'])
    other_project=store.create_text_assignment('Other','Other materials')['assignment']['assignment_id']
    foreign=access.create_connection('owner',other_project,'Other')
    result=access.get_project(ctx)
    assert 'tasks' not in result['assignment']['card']
    assert len(result['plan']['input']['tasks'])==2
    assert all(not isinstance(value,(dict,list)) for value in result['plan']['result'].values())
    assert 'schedule' not in result['plan']['result']
    assert 'tasks' not in result['plan']['result']
    assert len(result['runs'])==20
    assert result['runs'][0]['summary']=='Result 22'
    assert result['runs'][0]['evidence']==['proof-22']
    assert result['runs'][-1]['summary']=='Result 3'
    # With no saved plan, retain authored tasks as the only available task source.
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM assignment_plans WHERE assignment_id=?',(project,))
    without_plan=access.get_project(ctx)
    assert without_plan['plan'] is None
    assert len(without_plan['assignment']['card']['tasks'])==2
    assert access.get_project(access.authenticate(foreign['token']))['runs']==[]
