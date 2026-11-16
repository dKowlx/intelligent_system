"""Persisted project conversations, scoped by the existing browser workspace."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import uuid

from .cards import CardError


SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_owner ON chat_sessions(workspace_id,assignment_id,updated_at);
CREATE TABLE IF NOT EXISTS chat_turns (
    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_message TEXT NOT NULL, reply_json TEXT, error_json TEXT, status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_turn_order ON chat_turns(chat_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS chat_one_pending ON chat_turns(chat_id) WHERE status='pending';
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def initialize_chats(db_path):
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        # Startup recovery: never leave a conversation locked after a process crash.
        db.execute("UPDATE chat_turns SET status='failed',error_json=? WHERE status='pending'", (json.dumps({
            "code": "request_interrupted", "message": "Ответ прерван перезапуском сервиса. Отправьте вопрос снова."}, ensure_ascii=False),))


class ChatStore:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.store.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def authorize(self, assignment_id, chat_id):
        self.store.get_assignment(assignment_id)
        with self.connection() as db:
            row = db.execute("SELECT * FROM chat_sessions WHERE id=? AND assignment_id=? AND workspace_id=?",
                             (chat_id, assignment_id, self.store.workspace_id)).fetchone()
        if row is None:
            raise CardError("unknown_chat", "Разговор не найден.", 404)
        return row

    @staticmethod
    def summary(row, count):
        return {**{key: row[key] for key in ("id", "title", "created_at", "updated_at")}, "message_count": count}

    def list(self, assignment_id):
        self.store.get_assignment(assignment_id)
        with self.connection() as db:
            rows = db.execute("""SELECT s.*,COALESCE(SUM(CASE WHEN t.id IS NULL THEN 0 WHEN t.status='pending' THEN 1 ELSE 2 END),0) AS messages
                FROM chat_sessions s LEFT JOIN chat_turns t ON t.chat_id=s.id
                WHERE s.assignment_id=? AND s.workspace_id=? GROUP BY s.id ORDER BY s.updated_at DESC,s.id DESC""",
                (assignment_id, self.store.workspace_id)).fetchall()
        return {"sessions": [self.summary(row, row["messages"]) for row in rows]}

    def create(self, assignment_id):
        self.store.get_assignment(assignment_id)
        identifier, timestamp = uuid.uuid4().hex, now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM chat_sessions WHERE assignment_id=? AND workspace_id=?",
                               (assignment_id, self.store.workspace_id)).fetchone()[0]
            if count >= 50:
                raise CardError("chat_limit", "Сохранено 50 разговоров. Удалите ненужный разговор, чтобы создать новый.", 409)
            db.execute("INSERT INTO chat_sessions VALUES (?,?,?,?,?,?)",
                       (identifier, assignment_id, self.store.workspace_id, "Новый разговор", timestamp, timestamp))
        return {"session": {"id": identifier, "title": "Новый разговор", "created_at": timestamp, "updated_at": timestamp, "message_count": 0}}

    def get(self, assignment_id, chat_id):
        row = self.authorize(assignment_id, chat_id)
        with self.connection() as db:
            turns = db.execute("SELECT * FROM chat_turns WHERE chat_id=? ORDER BY created_at,id", (chat_id,)).fetchall()
        messages = []
        for turn in turns:
            messages.append({"id": turn["id"] + "-user", "role": "user", "content": turn["user_message"], "created_at": turn["created_at"]})
            if turn["reply_json"]:
                result = json.loads(turn["reply_json"])
                messages.append({"id": turn["id"] + "-assistant", "role": "assistant", "content": result["answer"],
                                 "result": result, "created_at": turn["created_at"]})
            elif turn["error_json"]:
                error = json.loads(turn["error_json"])
                messages.append({"id": turn["id"] + "-assistant", "role": "assistant", "content": error["message"],
                                 "error": error, "created_at": turn["created_at"]})
            else:
                messages.append({"id": turn["id"] + "-assistant", "role": "assistant", "content": "Ответ ещё формируется. Откройте разговор немного позже.",
                                 "pending": True, "created_at": turn["created_at"]})
        return {"session": self.summary(row, len(messages)), "messages": messages}

    def history(self, assignment_id, chat_id):
        messages = self.get(assignment_id, chat_id)["messages"]
        return [{"role": m["role"], "content": m["content"][:500],
                 **({"error_code": m["error"]["code"]} if m.get("error") else {})}
                for m in messages if not m.get("pending")][-6:]

    def begin(self, assignment_id, chat_id, message):
        self.authorize(assignment_id, chat_id)
        turn_id, timestamp = uuid.uuid4().hex, now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM chat_turns WHERE chat_id=? AND status='pending'", (chat_id,)).fetchone():
                raise CardError("chat_busy", "В этом разговоре ещё формируется ответ. Дождитесь его или откройте новый разговор.", 409)
            count = db.execute("SELECT COUNT(*) FROM chat_turns WHERE chat_id=?", (chat_id,)).fetchone()[0]
            if count >= 100:
                raise CardError("chat_full", "В этом разговоре уже 100 вопросов. Продолжите в новом разговоре.", 409)
            db.execute("INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?)", (turn_id, chat_id, message, None, None, "pending", timestamp))
            db.execute("UPDATE chat_sessions SET updated_at=?,title=CASE WHEN ?=0 THEN ? ELSE title END WHERE id=?",
                       (timestamp, count, " ".join(message.split())[:80], chat_id))
        return turn_id

    def finish(self, assignment_id, chat_id, turn_id, *, result=None, error=None):
        self.authorize(assignment_id, chat_id)
        snapshot = None
        if result is not None:
            snapshot = {key: result[key] for key in ("answer", "status", "answer_basis", "response_origin", "mode", "sources", "facts") if key in result}
            snapshot["action_proposal"] = None
            if result.get("action_proposal"):
                snapshot["answer"] += "\n\nПредложение из истории. Для изменения плана запросите новое предложение."
            snapshot = json.dumps(snapshot, ensure_ascii=False)
        with self.connection() as db:
            db.execute("UPDATE chat_turns SET reply_json=?,error_json=?,status=? WHERE id=? AND chat_id=? AND status='pending'",
                       (snapshot, json.dumps(error, ensure_ascii=False) if error else None, "complete" if result else "failed", turn_id, chat_id))
            db.execute("UPDATE chat_sessions SET updated_at=? WHERE id=?", (now(), chat_id))

    def delete(self, assignment_id, chat_id):
        self.authorize(assignment_id, chat_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM chat_turns WHERE chat_id=? AND status='pending'", (chat_id,)).fetchone():
                raise CardError("chat_busy", "Дождитесь завершения ответа перед удалением разговора.", 409)
            db.execute("DELETE FROM chat_sessions WHERE id=? AND workspace_id=?", (chat_id, self.store.workspace_id))

    def source(self, source_id):
        """Keep historical citations readable after the short-lived web cache expires."""
        with self.connection() as db:
            row = db.execute("""SELECT j.value FROM chat_turns t JOIN chat_sessions s ON s.id=t.chat_id
                JOIN assignments a ON a.assignment_id=s.assignment_id, json_each(t.reply_json,'$.sources') j
                WHERE s.workspace_id=? AND (a.workspace_id IS NULL OR a.workspace_id=?)
                AND json_extract(j.value,'$.source_id')=? AND json_extract(j.value,'$.scope')='web_theory'
                ORDER BY t.created_at DESC LIMIT 1""", (self.store.workspace_id, self.store.workspace_id, source_id)).fetchone()
        return json.loads(row[0]) if row else None
