"""Project-scoped credentials, work reports and versioned collaboration."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import sqlite3
import uuid

from .catalog import Store, CatalogError


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
                CREATE INDEX IF NOT EXISTS codex_runs_project ON codex_runs(workspace_id,assignment_id,created_at);
                CREATE TABLE IF NOT EXISTS codex_report_requests (
                    connection_id TEXT NOT NULL, request_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    digest TEXT NOT NULL, response_json TEXT NOT NULL,
                    PRIMARY KEY(connection_id,request_id));
                CREATE INDEX IF NOT EXISTS codex_report_requests_run ON codex_report_requests(run_id);
            ''')

            # NULL grants identify keys created before permissions were selectable.
            self._add_column(db, "codex_connections", "scopes", "TEXT")
            self._add_column(db, "codex_connections", "completion_mode", "TEXT NOT NULL DEFAULT 'review'")
            self._add_column(db, "codex_runs", "substep_id", "TEXT")
            db.execute("DROP INDEX IF EXISTS codex_active_task")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS codex_active_target ON codex_runs
                (workspace_id,assignment_id,task_id,COALESCE(substep_id,''))
                WHERE status IN ('in_progress','awaiting_review','blocked','partial')""")
            db.execute("""CREATE TABLE IF NOT EXISTS codex_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, actor TEXT NOT NULL,
                connection_id TEXT, operation TEXT NOT NULL, reason TEXT NOT NULL, task_id TEXT, substep_id TEXT,
                details TEXT NOT NULL, created_at TEXT NOT NULL, card_revision INTEGER, plan_revision INTEGER,
                request_id TEXT, digest TEXT, response_json TEXT,
                UNIQUE(assignment_id, connection_id, request_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS codex_activity_project ON codex_activity(assignment_id,id)")

    @staticmethod
    def _add_column(db, table, column, declaration):
        if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _scopes(row):
        return json.loads(row["scopes"]) if row["scopes"] is not None else ["read", "reports"]

    def _require(self, row, scope):
        if scope not in self._scopes(row):
            _fail("permission_denied", "Для этого ключа не разрешено выбранное действие.", 403)

    @staticmethod
    def _audit(db, assignment_id, actor, operation, reason, *, connection_id=None, task_id=None,
               substep_id=None, details=None, revisions=(None, None), request_id=None, digest=None, response=None):
        db.execute("""INSERT INTO codex_activity
            (assignment_id,actor,connection_id,operation,reason,task_id,substep_id,details,created_at,
             card_revision,plan_revision,request_id,digest,response_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (assignment_id,actor,connection_id,operation,reason,task_id,substep_id,json.dumps(details or {}, ensure_ascii=False),
             _now(),*revisions,request_id,digest,json.dumps(response, ensure_ascii=False) if response is not None else None))

    def activity(self, workspace, assignment_id, include_runs=False):
        with self._db() as db:
            owner = self._owner(db,workspace,assignment_id)
            saved = db.execute("SELECT revision FROM assignment_plans WHERE assignment_id=?", (assignment_id,)).fetchone()
            last = db.execute("SELECT max(id) FROM codex_activity WHERE assignment_id=?", (assignment_id,)).fetchone()[0]
            state = {"card_revision":owner["card_revision"], "plan_revision":saved[0] if saved else 0,
                     "activity_version":last or 0}
            if include_runs:
                state["runs"] = [self._run(r) for r in db.execute("SELECT * FROM codex_runs WHERE assignment_id=? ORDER BY created_at DESC LIMIT 100",(assignment_id,))]
                events = db.execute("""SELECT id,actor,operation,reason,task_id,substep_id,details,created_at,
                    card_revision,plan_revision FROM codex_activity WHERE assignment_id=? ORDER BY id DESC LIMIT 80""",(assignment_id,))
                state["audit"] = [{**dict(event),"details":json.loads(event["details"])} for event in events]
            return state

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
        return {**{key: row[key] for key in ('id','label','created_at','expires_at','revoked_at','completion_mode')},
                'scopes': CodexAccess._scopes(row)}

    @staticmethod
    def _run(row):
        result = {key: row[key] for key in ('id','task_id','task_title','connection_id','card_revision',
                  'plan_revision','status','summary','created_at','updated_at','substep_id')}
        result['evidence'] = json.loads(row['evidence'])
        return result

    def create_connection(self, workspace_id, assignment_id, label, expires_days=7, scopes=None, completion_mode='review'):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100 or type(expires_days) is not int or not 1 <= expires_days <= 30:
            _fail('invalid_arguments', 'Проверьте название и срок подключения.', 422)
        if scopes is not None and (not isinstance(scopes,list) or "read" not in scopes
                or len(set(scopes)) != len(scopes) or not set(scopes) <= {"read","reports","progress","plan"}):
            _fail("invalid_scopes", "Проверьте права подключения.", 422)
        if completion_mode not in ("review","auto") or (completion_mode=="auto" and "progress" not in (scopes or [])):
            _fail("invalid_completion_mode", "Для автозавершения разрешите изменение прогресса.", 422)
        token = 'pn_' + secrets.token_urlsafe(32)
        now = _now()
        with self._db() as db:
            self._owner(db, workspace_id, assignment_id)
            count = db.execute('SELECT count(*) FROM codex_connections WHERE workspace_id=? AND assignment_id=? AND revoked_at IS NULL AND expires_at>?', (workspace_id,assignment_id,now)).fetchone()[0]
            if count >= 5:
                _fail('connection_limit', 'Можно создать не более пяти активных подключений.')
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO codex_connections (id,workspace_id,assignment_id,label,token_hash,created_at,expires_at,revoked_at,scopes,completion_mode) VALUES (?,?,?,?,?,?,?,NULL,?,?)', (identifier,workspace_id,assignment_id,label.strip(),_digest(token),now,(datetime.now(timezone.utc)+timedelta(days=expires_days)).isoformat(), json.dumps(scopes) if scopes is not None else None, completion_mode))
            row = db.execute('SELECT * FROM codex_connections WHERE id=?', (identifier,)).fetchone()
            self._audit(db, assignment_id, 'Владелец', 'connect', label, details={'scopes':self._scopes(row),'completion_mode':completion_mode})
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
            if row['revoked_at']:
                return self._connection(row)
            db.execute('UPDATE codex_connections SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (_now(),connection_id))
            db.execute("UPDATE codex_runs SET status='dismissed',updated_at=? WHERE connection_id=? AND status IN ('in_progress','blocked','awaiting_review','partial')", (_now(),connection_id))
            self._audit(db, assignment_id, 'Владелец', 'revoke', row['label'])
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
                from .analytics import summarize
                plan['analytics'] = summarize(data.get('tasks', []))
            runs = [self._run(run) for run in db.execute(
                'SELECT * FROM codex_runs WHERE workspace_id=? AND assignment_id=? ORDER BY created_at DESC LIMIT 20',
                (row['workspace_id'],row['assignment_id']))]
            return {'assignment':assignment,'plan':plan,'runs':runs,'connection':self._connection(row)}

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

    @staticmethod
    def _release_expired(db, assignment_id):
        now = _now()
        db.execute("""UPDATE codex_runs SET status='dismissed', updated_at=?
            WHERE assignment_id=? AND status IN ('in_progress','awaiting_review','blocked','partial')
            AND connection_id IN (SELECT id FROM codex_connections WHERE assignment_id=? AND expires_at<=?)""",
            (now, assignment_id, assignment_id, now))

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

    def claim_task(self, context, task_id, card_revision, plan_revision, request_id, substep_id=None):
        self._request(request_id)
        digest = _payload([task_id,card_revision,plan_revision] + ([substep_id] if substep_id is not None else []))
        with self._db() as db:
            row = self._context(db,context)
            self._require(row, "reports")
            if substep_id is not None and row["scopes"] is None:
                _fail("permission_denied", "Для работы с подшагами выпустите ключ с явными правами.", 403)
            old = db.execute('SELECT * FROM codex_runs WHERE connection_id=? AND claim_request_id=?',(row['id'],request_id)).fetchone()
            if old:
                if old['claim_digest'] != digest:
                    _fail('request_conflict','Идентификатор запроса уже использован.')
                return self._run(old)
            _,_,_,task = self._task(db,row['workspace_id'],row['assignment_id'],task_id,card_revision,plan_revision)
            if substep_id is not None:
                step = next((s for s in task.get("substeps",[]) if s["id"] == substep_id), None)
                if step is None or step.get("completed"):
                    _fail("invalid_substep","Подшаг отсутствует или уже завершён.")
            identifier,now = uuid.uuid4().hex,_now()
            self._release_expired(db, row['assignment_id'])
            conflict = db.execute("""SELECT 1 FROM codex_runs
                WHERE assignment_id=? AND task_id=? AND status IN ('in_progress','awaiting_review','blocked','partial')
                AND (substep_id IS NULL OR ? IS NULL OR substep_id=?) LIMIT 1""",
                (row['assignment_id'], task_id, substep_id, substep_id)).fetchone()
            if conflict:
                _fail('task_claimed','Действие или подшаг уже занят подключением.')
            try:
                db.execute('''INSERT INTO codex_runs (id,workspace_id,assignment_id,task_id,task_title,connection_id,card_revision,plan_revision,status,summary,evidence,created_at,updated_at,claim_request_id,claim_digest,report_request_id,report_digest,substep_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (identifier,row['workspace_id'],row['assignment_id'],task_id,task['title'],row['id'],card_revision,plan_revision,'in_progress','','[]',now,now,request_id,digest,None,None,substep_id))
            except sqlite3.IntegrityError:
                _fail('task_claimed','Действие уже занято подключением.')
            self._audit(db,row['assignment_id'],row['label'],'claim',task['title'],task_id=task_id,substep_id=substep_id)
            return self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(identifier,)).fetchone())

    def report_task(self, context, run_id, status, summary, evidence, request_id, expected_updated_at=None):
        self._request(request_id)
        if status not in ('awaiting_review','blocked','partial') or not isinstance(summary,str) or len(summary)>3000 or not isinstance(evidence,list) or len(evidence)>10 or any(not isinstance(x,str) or len(x)>1000 for x in evidence):
            _fail('invalid_arguments','Проверьте статус, описание и подтверждения результата.',422)
        digest = _payload([run_id,status,summary,evidence] + ([expected_updated_at] if expected_updated_at else []))
        with self._db() as db:
            row = self._context(db,context)
            self._require(row, "reports")
            if status == "partial" and row["scopes"] is None:
                _fail("permission_denied", "Выпустите ключ с явными правами для частичных отчётов.", 403)
            run = db.execute('SELECT * FROM codex_runs WHERE id=? AND connection_id=?',(run_id,row['id'])).fetchone()
            if run is None:
                _fail('unknown_run','Работа не найдена.',404)
            previous = db.execute('SELECT * FROM codex_report_requests WHERE connection_id=? AND request_id=?',
                                  (row['id'],request_id)).fetchone()
            if previous is not None:
                if previous['run_id'] != run_id or previous['digest'] != digest:
                    _fail('request_conflict','Идентификатор запроса уже использован.')
                return json.loads(previous['response_json'])
            if run['status'] not in ('in_progress','blocked','partial'):
                _fail('invalid_run_state','Работа уже передана на проверку или закрыта.')
            if ((expected_updated_at is not None and expected_updated_at != run['updated_at'])
                    or (row['scopes'] is not None and run['report_request_id'] and not expected_updated_at)):
                _fail('stale_report','Отчёт изменился. Перечитайте работу и передайте её updated_at.')
            db.execute('UPDATE codex_runs SET status=?,summary=?,evidence=?,report_request_id=?,report_digest=?,updated_at=? WHERE id=?',(status,summary,json.dumps(evidence,ensure_ascii=False),request_id,digest,_now(),run_id))
            response = self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(run_id,)).fetchone())
            db.execute('INSERT INTO codex_report_requests VALUES (?,?,?,?,?)',
                       (row['id'],request_id,run_id,digest,json.dumps(response,ensure_ascii=False)))
            self._audit(db,row['assignment_id'],row['label'],status,summary,task_id=run['task_id'],substep_id=run['substep_id'],details={'evidence':evidence})
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
            owner, saved, _, _ = self._task(db,workspace_id,assignment_id,run['task_id'],card_revision,plan_revision)
            from .codex_progress import apply_progress
            apply_progress(db, None, owner, saved, {"task_id":run["task_id"],"substep_id":run["substep_id"],
                "run_id":run_id,"action":"complete","summary":run["summary"],"evidence":json.loads(run["evidence"])}, _now())
            db.execute("UPDATE codex_runs SET status='accepted',updated_at=? WHERE id=?",(_now(),run_id))
            self._audit(db,assignment_id,"Владелец","accept",run["summary"],task_id=run["task_id"],substep_id=run["substep_id"])
            current = db.execute("SELECT * FROM assignment_plans WHERE assignment_id=?", (assignment_id,)).fetchone()
            return {"run":self._run(self._owned_run(db,workspace_id,assignment_id,run_id)),
                    "plan":{"revision":current["revision"],"card_revision":card_revision,"stale":False,
                            "input":json.loads(current["input_json"]), "result":json.loads(current["result_json"])}}

    def dismiss_run(self, workspace_id, assignment_id, run_id):
        with self._db() as db:
            run=self._owned_run(db,workspace_id,assignment_id,run_id)
            if run['status'] in ('accepted','completed','finished'):
                _fail('invalid_run_state','Принятая работа уже завершена.')
            if run['status'] == 'dismissed':
                return self._run(run)
            db.execute("UPDATE codex_runs SET status='dismissed',updated_at=? WHERE id=?",(_now(),run_id))
            self._audit(db,assignment_id,"Владелец","dismiss",run["task_title"],task_id=run["task_id"],substep_id=run["substep_id"])
            return self._run(db.execute('SELECT * FROM codex_runs WHERE id=?',(run_id,)).fetchone())

    def mutate(self, context, operation, body, *, owner_workspace=None, assignment_id=None, response_headers=None):
        """Authorize, compare versions, apply, and record the receipt under one SQLite write lock."""
        from .codex_progress import apply_progress
        from .codex_plan import edit_plan
        from .cards import CardError

        self._request(body["request_id"])
        # The local stopwatch enriches the wire request. After a bridge restart
        # the original command has the same intent but no unsaved stopwatch.
        intent = body
        if operation == "progress" and (body.get("time") or {}).get("measurement") == "bridge_timer":
            intent = {key: value for key, value in body.items() if key != "time"}
        digest = _payload([operation, intent])
        with self._db() as db:
            if context is not None:
                connection = self._context(db, context)
                self._require(connection, operation)
                if operation == "progress" and body["action"] == "complete" and connection["completion_mode"] != "auto":
                    _fail("review_required", "Передайте результат на проверку владельцу.", 403)
                assignment_id = connection["assignment_id"]
                workspace = connection["workspace_id"]
                actor, ledger_key = connection["label"], connection["id"]
            else:
                if operation != "progress" or owner_workspace is None:
                    _fail("permission_denied", "Недостаточно прав.", 403)
                workspace, connection = owner_workspace, None
                actor, ledger_key = "Владелец", "owner"

            owner = self._owner(db,workspace,assignment_id)
            receipt = db.execute("""SELECT digest,response_json FROM codex_activity
                WHERE assignment_id=? AND connection_id=? AND request_id=?""",
                (assignment_id,ledger_key,body["request_id"])).fetchone()
            if receipt:
                if receipt["digest"] != digest:
                    _fail("request_conflict", "Этот идентификатор уже использован для другого изменения.")
                if response_headers is not None:
                    response_headers["Navigator-Replayed"] = "1"
                return json.loads(receipt["response_json"])

            saved = db.execute("SELECT * FROM assignment_plans WHERE assignment_id=? AND workspace_id=?",
                               (assignment_id,workspace)).fetchone()
            if (saved is None or owner["status"] != "ready" or owner["card_revision"] != body["card_revision"]
                    or saved["revision"] != body["plan_revision"] or saved["card_revision"] != owner["card_revision"]):
                _fail("stale_plan", "Сохраните актуальный план и перечитайте его версии.")
            try:
                if operation == "plan":
                    reason = body["reason"]
                    details = body["changes"]
                    outcome = edit_plan(db, owner, saved, details)
                    action = "update_plan"
                else:
                    self._release_expired(db, assignment_id)
                    command = {k:v for k,v in body.items() if k not in ("request_id","card_revision","plan_revision")}
                    outcome = apply_progress(db, connection, owner, saved, command, _now())
                    action = body["action"]
                    reason = body.get("summary") or ""
                    details = {k:body[k] for k in ("time","evidence","run_id") if body.get(k) is not None}
            except CodexAccessError:
                raise
            except (CardError, ValueError, TypeError) as exc:
                _fail("invalid_change", "Проверьте поля, зависимости, границы времени и сохранённый прогресс.", 422)

            revisions = (outcome["card_revision"], outcome["plan_revision"])
            response = {"card_revision":revisions[0], "plan_revision":revisions[1], "operation":action}
            if operation == "progress":
                response["progress"] = outcome["progress"]
                # A report on this target can be reviewed against the revisions just saved.
                # Do not refresh stale runs originating from an earlier plan.
                if connection is not None:
                    db.execute("""UPDATE codex_runs SET plan_revision=? WHERE connection_id=? AND task_id=?
                        AND substep_id IS ? AND plan_revision=? AND card_revision=?
                        AND status IN ('in_progress','blocked','awaiting_review','partial')""",
                        (revisions[1],ledger_key,body["task_id"],body.get("substep_id"),body["plan_revision"],revisions[0]))
            self._audit(db,assignment_id,actor,action,reason,connection_id=ledger_key,task_id=body.get("task_id"),
                        substep_id=body.get("substep_id"),details=details,revisions=revisions,request_id=body["request_id"],
                        digest=digest,response=response)
            return response
