"""SQLite 持久化仓储：材料、结论版本与证据引用全部落盘，重启后可恢复。

只追加、不改写：材料仅允许核验状态流转，结论逐版本插入，旧版本永久保留。
"""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials(
  material_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  material_hash TEXT NOT NULL,
  state TEXT NOT NULL,
  registered_by TEXT NOT NULL,
  registered_at TEXT NOT NULL,
  verified_by TEXT,
  verified_at TEXT,
  idempotency_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS conclusions(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  conclusion_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  prev_chain_hash TEXT NOT NULL,
  chain_hash TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  UNIQUE(conclusion_id, version)
);
CREATE TABLE IF NOT EXISTS conclusion_materials(
  conclusion_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  material_id TEXT NOT NULL,
  material_hash TEXT NOT NULL,
  PRIMARY KEY(conclusion_id, version, material_id)
);
"""


class SQLiteStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self.db.close()

    # ---- 材料 ----

    def add_material(self, rec):
        with self._lock:
            try:
                self.db.execute(
                    "INSERT INTO materials(material_id,kind,title,content,material_hash,state,"
                    "registered_by,registered_at,verified_by,verified_at,idempotency_key)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["material_id"], rec["kind"], rec["title"], rec["content"],
                     rec["material_hash"], rec["state"], rec["registered_by"],
                     rec["registered_at"], rec["verified_by"], rec["verified_at"],
                     rec["idempotency_key"]))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def get_material(self, material_id):
        with self._lock:
            row = self.db.execute("SELECT * FROM materials WHERE material_id=?",
                                  (material_id,)).fetchone()
            return dict(row) if row else None

    def material_by_key(self, key):
        with self._lock:
            row = self.db.execute("SELECT * FROM materials WHERE idempotency_key=?",
                                  (key,)).fetchone()
            return dict(row) if row else None

    def list_materials(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM materials ORDER BY material_id").fetchall()
            return [dict(r) for r in rows]

    def set_material_state(self, material_id, state, verified_by, verified_at):
        with self._lock:
            try:
                self.db.execute(
                    "UPDATE materials SET state=?, verified_by=?, verified_at=? WHERE material_id=?",
                    (state, verified_by, verified_at, material_id))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    # ---- 结论 ----

    def add_conclusion(self, rec, materials):
        """结论版本与其证据引用同事务写入，保证链上记录完整。"""
        with self._lock:
            try:
                self.db.execute(
                    "INSERT INTO conclusions(conclusion_id,version,content,prev_chain_hash,"
                    "chain_hash,created_by,created_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",
                    (rec["conclusion_id"], rec["version"], rec["content"],
                     rec["prev_chain_hash"], rec["chain_hash"], rec["created_by"],
                     rec["created_at"], rec["idempotency_key"]))
                self.db.executemany(
                    "INSERT INTO conclusion_materials(conclusion_id,version,material_id,material_hash)"
                    " VALUES(?,?,?,?)",
                    [(rec["conclusion_id"], rec["version"], mid, mhash) for mid, mhash in materials])
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def get_conclusion(self, conclusion_id, version):
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM conclusions WHERE conclusion_id=? AND version=?",
                (conclusion_id, version)).fetchone()
            return dict(row) if row else None

    def conclusion_by_key(self, key):
        with self._lock:
            row = self.db.execute("SELECT * FROM conclusions WHERE idempotency_key=?",
                                  (key,)).fetchone()
            return dict(row) if row else None

    def latest_version(self, conclusion_id):
        with self._lock:
            row = self.db.execute("SELECT MAX(version) v FROM conclusions WHERE conclusion_id=?",
                                  (conclusion_id,)).fetchone()
            return row["v"] if row and row["v"] is not None else None

    def list_latest_conclusions(self):
        with self._lock:
            rows = self.db.execute(
                "SELECT c.* FROM conclusions c JOIN ("
                " SELECT conclusion_id, MAX(version) v FROM conclusions GROUP BY conclusion_id"
                ") t ON c.conclusion_id=t.conclusion_id AND c.version=t.v"
                " ORDER BY c.conclusion_id").fetchall()
            return [dict(r) for r in rows]

    def conclusions_in_order(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM conclusions ORDER BY seq").fetchall()
            return [dict(r) for r in rows]

    def conclusion_materials(self, conclusion_id, version):
        with self._lock:
            rows = self.db.execute(
                "SELECT material_id, material_hash FROM conclusion_materials"
                " WHERE conclusion_id=? AND version=? ORDER BY material_id",
                (conclusion_id, version)).fetchall()
            return [dict(r) for r in rows]

    def latest_chain_hash(self):
        with self._lock:
            row = self.db.execute(
                "SELECT chain_hash FROM conclusions ORDER BY seq DESC LIMIT 1").fetchone()
            return row["chain_hash"] if row else None
