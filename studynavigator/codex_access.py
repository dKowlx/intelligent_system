"""Project-scoped, revocable credentials and human-reviewed agent work."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import sqlite3
import uuid

from .catalog import Store, CatalogError
from .planner import build_adaptive_plan


class CodexAccessError(ValueError):
    def __init__(self, code, message, status_code=409):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fail(code, message, status=409):
    raise CodexAccessError(code, message, status)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _payload(value):
    return _digest(json.dumps(value, sort_keys=True, ensure_ascii=False))


class CodexAccess:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS codex_connections (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
                    label TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT);
                CREATE INDEX IF NOT EXISTS codex_connections_project ON codex_connections(workspace_id,assignment_id);
                CREATE TABLE IF NOT EXISTS codex_runs (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, assignment_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, task_title TEXT NOT NULL, connection_id TEXT NOT NULL,
                    card_revision INTEGER NOT NULL, plan_revision INTEGER NOT NULL,
                    status TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    claim_request_id TEXT NOT NULL, claim_digest TEXT NOT NULL,
                    report_request_id TEXT, report_digest TEXT,
                    UNIQUE(connection_id,claim_request_id));
                CREATE UNIQUE INDEX IF NOT EXISTS codex_active_task ON codex_runs(workspace_id,assignment_id,task_id)
                    WHERE status IN ('in_progress','awaiting_review','blocked');
                CREATE INDEX IF NOT EXISTS codex_runs_project ON codex_runs(workspace_id,assignment_id,created_at);
                CREATE TABLE IF NOT EXISTS codex_report_requests (
                    connection_id TEXT NOT NULL, request_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    digest TEXT NOT NULL, response_json TEXT NOT NULL,
                    PRIMARY KEY(connection_id,request_id));
                CREATE INDEX IF NOT EXISTS codex_report_requests_run ON codex_report_requests(run_id);
            ''')

    def _owner(self, db, workspace_id, assignment_id):
        if not workspace_id:
            _fail('unknown_assignment', 'Проект не найден.', 404)
        row = db.execute('SELECT * FROM assignments WHERE assignment_id=? AND workspace_id=? AND demo=0',
                         (assignment_id, workspace_id)).fetchone()
        if row is None:
            _fail('unknown_assignment', 'Проект не найден.', 404)
        return row

    @staticmethod
    def _connection(row):
        return {key: row[key] for key in ('id','label','created_at','expires_at','revoked_at')}

    @staticmethod
    def _run(row):
        result = {key: row[key] for key in ('id','task_id','task_title','connection_id','card_revision',
                  'plan_revision','status','summary','created_at','updated_at')}
        result['evidence'] = json.loads(row['evidence'])
        return result

    def create_connection(self, workspace_id, assignment_id, label, expires_days=7):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100 or type(expires_days) is not int or not 1 <= expires_days <= 30:
            _fail('invalid_arguments', 'Проверьте название и срок подключения.', 422)
        token = 'pn_' + secrets.token_urlsafe(32)
        now = _now()
        with self._db() as db:
            self._owner(db, workspace_id, assignment_id)
            count = db.execute('SELECT count(*) FROM codex_connections WHERE workspace_id=? AND assignment_id=? AND revoked_at IS NULL AND expires_at>?', (workspace_id,assignment_id,now)).fetchone()[0]
            if count >= 5:
                _fail('connection_limit', 'Можно создать не более пяти активных подключений.')
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO codex_connections VALUES (?,?,?,?,?,?,?,NULL)', (identifier,workspace_id,assignment_id,label.strip(),_digest(token),now,(datetime.now(timezone.utc)+timedelta(days=expires_days)).isoformat()))
            row = db.execute('SELECT * FROM codex_connections WHERE id=?', (identifier,)).fetchone()
            return {'connection': self._connection(row), 'token': token}

    def list_connections(self, workspace_id, assignment_id):
        with self._db() as db:
            self._owner(db,workspace_id,assignment_id)
            return [self._connection(row) for row in db.execute('SELECT * FROM codex_connections WHERE workspace_id=? AND assignment_id=? ORDER BY created_at DESC', (workspace_id,assignment_id))]

    def revoke_connection(self, workspace_id, assignment_id, connection_id):
        with self._db() as db:
            self._owner(db,workspace_id,assignment_id)
            row = db.execute('SELECT * FROM codex_connections WHERE id=? AND workspace_id=? AND assignment_id=?', (connection_id,workspace_id,assignment_id)).fetchone()
            if row is None:
                _fail('unknown_connection','Подключение не найдено.',404)
            db.execute('UPDATE codex_connections SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (_now(),connection_id))
            db.execute("UPDATE codex_runs SET status='dismissed',updated_at=? WHERE connection_id=? AND status IN ('in_progress','blocked','awaiting_review')", (_now(),connection_id))
            return self._connection(db.execute('SELECT * FROM codex_connections WHERE id=?',(connection_id,)).fetchone())

    def _context(self, db, context):
        row = db.execute('SELECT * FROM codex_connections WHERE id=?', (context.get('connection_id'),)).fetchone()
        if row is None or row['revoked_at'] or row['expires_at'] <= _now():
            _fail('invalid_token','Подключение недействительно.',401)
        if any(context.get(k) != row[k] for k in ('workspace_id','assignment_id')):
            _fail('invalid_token','Подключение недействительно.',401)
        self._owner(db,row['workspace_id'],row['assignment_id'])
        return row

    def authenticate(self, token):
        if not isinstance(token,str) or not token.startswith('pn_') or len(token)>128:
            _fail('invalid_token','Подключение недействительно.',401)
        with self._db() as db:
            row = db.execute('SELECT * FROM codex_connections WHERE token_hash=?', (_digest(token),)).fetchone()
            if row is None:
                _fail('invalid_token','Подключение недействительно.',401)
            context = {'connection_id':row['id'],'workspace_id':row['workspace_id'],'assignment_id':row['assignment_id']}
            self._context(db,context)
            return context

    def get_project(self, context):
        with self._db() as db:
            row = self._context(db,context)
            owner = self._owner(db,row['workspace_id'],row['assignment_id'])
            assignment = {k:owner[k] for k in ('assignment_id','title','status','card_revision','materials_revision')}
            card = json.loads(owner['card_json']) if owner['card_json'] else None
            assignment['sources'] = [dict(r) for r in db.execute('SELECT source_id,title,page,section,url FROM sources WHERE assignment_id=? ORDER BY rowid LIMIT 100',(row['assignment_id'],))]
            saved = db.execute('SELECT * FROM assignment_plans WHERE assignment_id=? AND workspace_id=?',(row['assignment_id'],row['workspace_id'])).fetchone()
            # Keep tasks exactly once. The saved plan is authoritative for progress;
            # authored card tasks are available only before the first plan exists.
            assignment['card'] = ({key:value for key,value in card.items()
                                   if key != 'tasks' or saved is None} if card else None)
            if card is None:
                assignment['requirements'] = [json.loads(r[0]) for r in db.execute('SELECT body FROM requirements WHERE assignment_id=? LIMIT 100',(row['assignment_id'],))]
            plan = None
            if saved is not None:
                data, result = json.loads(saved['input_json']), json.loads(saved['result_json'])
                metrics = ('status','available_minutes','raw_available_minutes','required_minutes',
                           'deficit_minutes','completed_task_count','completed_minutes','reserve_percent')
                plan = {'revision':saved['revision'],'card_revision':saved['card_revision'],
                        'input':{key:data[key] for key in ('tasks','availability','start_date','reserve_percent') if key in data},
                        'result':{key:result[key] for key in metrics if key in result},
                        'stale':saved['card_revision']!=owner['card_revision']}
            runs = [self._run(run) for run in db.execute(
                'SELECT * FROM codex_runs WHERE workspace_id=? AND assignment_id=? ORDER BY created_at DESC LIMIT 20',
                (row['workspace_id'],row['assignment_id']))]
            return {'assignment':assignment,'plan':plan,'runs':runs}

    def search_materials(self, context, query, limit=5):
        with self._db() as db:
            row = self._context(db,context)
            try:
                return Store(self.db_path,row['workspace_id']).search_materials(query,limit,assignment_id=row['assignment_id'])
            except CatalogError as exc:
                _fail(exc.code,exc.message,422)

    @staticmethod
    def _request(request_id):
        if not isinstance(request_id,str) or not 1 <= len(request_id) <= 100:
            _fail('invalid_arguments','Укажите идентификатор запроса до 100 символов.',422)

    def _task(self, db, workspace_id, assignment_id, task_id, card_revision, plan_revision):
        owner = self._owner(db,workspace_id,assignment_id)
        plan = db.execute('SELECT * FROM assignment_plans WHERE assignment_id=? AND workspace_id=?',(assignment_id,workspace_id)).fetchone()
        if (type(card_revision) is not int or type(plan_revision) is not int or owner['status']!='ready' or owner['card_revision']!=card_revision or plan is None or plan['revision']!=plan_revision or plan['card_revision']!=card_revision):
            _fail('stale_plan','Карточка или план изменились. Обновите проект.')
        data = json.loads(plan['input_json'])
        tasks = {task['id']:task for task in data.get('tasks',[])}
        task = tasks.get(task_id)
        if task is None or task.get('completed'):
            _fail('invalid_task','Действие отсутствует или уже завершено.')
        if any(not tasks.get(dep,{}).get('completed') for dep in task.get('depends_on',[])):
            _fail('dependencies_pending','Сначала завершите предшествующие действия.')
        return owner,plan,data,task

    def claim_task(self, context, task_id, card_revision, plan_revision, request_id):
        self._request(request_id)
        digest = _payload([task_id,card_revision,plan_revision])
        with self._db() as db:
            row = self._context(db,context)
            old = db.execute('SELECT * FROM codex_runs WHERE connection_id=? AND claim_request_id=?',(row['id'],request_id)).fetchone()
            if old:
                if old['claim_digest'] != digest:
                    _fail('request_conflict','Идентификатор запроса уже использован.')
                return self._run(old)
            _,_,_,task = self._task(db,row['workspace_id'],row['assignment_id'],task_id,card_revision,plan_revision)
            identifier,now = uuid.uuid4().hex,_now()
            try:
                db.execute('INSERT INTO codex_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (identifier,row['workspace_id'],row['assignment_id'],task_id,task['title'],row['id'],card_revision,plan_revision,'in_progress','','[]',now,now,request_id,digest,None,None))
            except sqlite3.IntegrityError:
                _fail('task_claimed','Действие уже занято подключением.')
            return self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(identifier,)).fetchone())

    def report_task(self, context, run_id, status, summary, evidence, request_id):
        self._request(request_id)
        if status not in ('awaiting_review','blocked') or not isinstance(summary,str) or len(summary)>3000 or not isinstance(evidence,list) or len(evidence)>10 or any(not isinstance(x,str) or len(x)>1000 for x in evidence):
            _fail('invalid_arguments','Проверьте статус, описание и подтверждения результата.',422)
        digest = _payload([run_id,status,summary,evidence])
        with self._db() as db:
            row = self._context(db,context)
            run = db.execute('SELECT * FROM codex_runs WHERE id=? AND connection_id=?',(run_id,row['id'])).fetchone()
            if run is None:
                _fail('unknown_run','Работа не найдена.',404)
            previous = db.execute('SELECT * FROM codex_report_requests WHERE connection_id=? AND request_id=?',
                                  (row['id'],request_id)).fetchone()
            if previous is not None:
                if previous['run_id'] != run_id or previous['digest'] != digest:
                    _fail('request_conflict','Идентификатор запроса уже использован.')
                return json.loads(previous['response_json'])
            if run['status'] not in ('in_progress','blocked'):
                _fail('invalid_run_state','Работа уже передана на проверку или закрыта.')
            db.execute('UPDATE codex_runs SET status=?,summary=?,evidence=?,report_request_id=?,report_digest=?,updated_at=? WHERE id=?',(status,summary,json.dumps(evidence,ensure_ascii=False),request_id,digest,_now(),run_id))
            response = self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(run_id,)).fetchone())
            db.execute('INSERT INTO codex_report_requests VALUES (?,?,?,?,?)',
                       (row['id'],request_id,run_id,digest,json.dumps(response,ensure_ascii=False)))
            return response

    def list_runs(self, workspace_id, assignment_id):
        with self._db() as db:
            self._owner(db,workspace_id,assignment_id)
            return [self._run(r) for r in db.execute('SELECT * FROM codex_runs WHERE workspace_id=? AND assignment_id=? ORDER BY created_at DESC LIMIT 100',(workspace_id,assignment_id))]

    def _owned_run(self, db, workspace_id, assignment_id, run_id):
        self._owner(db,workspace_id,assignment_id)
        run = db.execute('SELECT * FROM codex_runs WHERE id=? AND workspace_id=? AND assignment_id=?',(run_id,workspace_id,assignment_id)).fetchone()
        if run is None:
            _fail('unknown_run','Работа не найдена.',404)
        return run

    def accept_run(self, workspace_id, assignment_id, run_id, card_revision, plan_revision):
        with self._db() as db:
            run = self._owned_run(db,workspace_id,assignment_id,run_id)
            if run['status']!='awaiting_review':
                _fail('invalid_run_state','Работа не ожидает проверки.')
            if (run['card_revision'],run['plan_revision'])!=(card_revision,plan_revision):
                _fail('stale_plan','Версии работы и плана не совпадают.')
            owner,plan,data,task = self._task(db,workspace_id,assignment_id,run['task_id'],card_revision,plan_revision)
            if task['title']!=run['task_title']:
                _fail('stale_plan','Действие изменилось.')
            task['completed']=True
            result=build_adaptive_plan(data['tasks'],data['availability'],start_date=data.get('start_date'),reserve_percent=data.get('reserve_percent',0),card=json.loads(owner['card_json']))
            result.update(assignment_id=assignment_id,card_revision=card_revision)
            now=_now()
            # Same optimistic revision and history contract as Store.save_plan, in the run transaction.
            db.execute('UPDATE assignment_plans SET revision=?,input_json=?,result_json=?,updated_at=? WHERE assignment_id=? AND workspace_id=?',(plan_revision+1,json.dumps(data,ensure_ascii=False),json.dumps(result,ensure_ascii=False),now,assignment_id,workspace_id))
            db.execute('INSERT INTO progress_history VALUES (?,?,?,?,?)',(assignment_id,workspace_id,plan_revision+1,now,json.dumps(data['tasks'],ensure_ascii=False)))
            db.execute("UPDATE codex_runs SET status='accepted',updated_at=? WHERE id=?",(now,run_id))
            return {'run':self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(run_id,)).fetchone()),'plan':{'revision':plan_revision+1,'card_revision':card_revision,'input':data,'result':result,'stale':False}}

    def dismiss_run(self, workspace_id, assignment_id, run_id):
        with self._db() as db:
            run=self._owned_run(db,workspace_id,assignment_id,run_id)
            if run['status']=='accepted':
                _fail('invalid_run_state','Принятая работа уже завершена.')
            db.execute("UPDATE codex_runs SET status='dismissed',updated_at=? WHERE id=?",(_now(),run_id))
            return self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(run_id,)).fetchone())
