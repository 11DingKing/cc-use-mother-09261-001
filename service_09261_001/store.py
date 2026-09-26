"""SQLite 持久化仓储：原始材料、结论版本与幂等键。

所有表仅插入不更新，同一数据库文件被重新打开后即可恢复全部历史版本。
"""
import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conclusion_versions (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    target TEXT NOT NULL,
    statement TEXT NOT NULL,
    material_ids TEXT NOT NULL,
    digest TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    status INTEGER NOT NULL,
    body TEXT NOT NULL
);
"""


class SQLiteStore:
    """基于 SQLite 的仓储；单连接加锁，可被多线程 HTTP 服务共享。"""

    def __init__(self, path=":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ---- 原始材料 ----
    def add_material(self, row):
        with self._lock:
            self._db.execute(
                "INSERT INTO materials(id,title,kind,content,registered_by,registered_at,sha256)"
                " VALUES(?,?,?,?,?,?,?)",
                (row["id"], row["title"], row["kind"], row["content"],
                 row["registered_by"], row["registered_at"], row["sha256"]))
            self._db.commit()

    def get_material(self, material_id):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        return dict(row) if row else None

    def list_materials(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM materials ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # ---- 结论版本 ----
    def add_conclusion_version(self, row):
        with self._lock:
            self._db.execute(
                "INSERT INTO conclusion_versions"
                "(id,version,target,statement,material_ids,digest,created_by,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (row["id"], row["version"], row["target"], row["statement"],
                 json.dumps(row["material_ids"], ensure_ascii=False),
                 row["digest"], row["created_by"], row["created_at"]))
            self._db.commit()

    @staticmethod
    def _conclusion(row):
        data = dict(row)
        data["material_ids"] = json.loads(data["material_ids"])
        return data

    def get_conclusion_version(self, conclusion_id, version):
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM conclusion_versions WHERE id=? AND version=?",
                (conclusion_id, version)).fetchone()
        return self._conclusion(row) if row else None

    def latest_version(self, conclusion_id):
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(version) AS v FROM conclusion_versions WHERE id=?",
                (conclusion_id,)).fetchone()
        return row["v"] if row and row["v"] is not None else None

    def list_latest_conclusions(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT c.* FROM conclusion_versions c"
                " JOIN (SELECT id, MAX(version) AS v FROM conclusion_versions GROUP BY id) t"
                " ON c.id=t.id AND c.version=t.v ORDER BY c.id").fetchall()
        return [self._conclusion(r) for r in rows]

    # ---- 幂等键 ----
    def get_idempotency(self, key):
        with self._lock:
            row = self._db.execute(
                "SELECT status, body FROM idempotency_keys WHERE key=?", (key,)).fetchone()
        return {"status": row["status"], "body": json.loads(row["body"])} if row else None

    def save_idempotency(self, key, status, body):
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO idempotency_keys(key,status,body) VALUES(?,?,?)",
                (key, status, json.dumps(body, ensure_ascii=False)))
            self._db.commit()
