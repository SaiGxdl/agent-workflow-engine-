# storage_sqlite.py
import sqlite3
import json
from datetime import datetime
from typing import Any

DB = "runs.db"

def _get_conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        graph_id TEXT,
        initial_state TEXT,
        final_state TEXT,
        log TEXT,
        status TEXT,
        created_at TEXT,
        completed_at TEXT
    )""")
    conn.commit()
    return conn

def _get_val(obj: Any, key: str, default=None):
    """
    Helper: if obj is a dict, use obj.get(key), otherwise getattr(obj, key, default).
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _normalize_log(raw_log: Any):
    """
    Convert a log list which may contain objects with __dict__ into pure serializable types.
    If raw_log is None return empty list.
    """
    if not raw_log:
        return []
    normalized = []
    for entry in raw_log:
        if hasattr(entry, "__dict__"):
            normalized.append(entry.__dict__)
        else:
            normalized.append(entry)
    return normalized

class SQLiteStorage:
    def __init__(self):
        self.conn = _get_conn()

    def store_graph(self, graph):
        # optional: persist graphs if needed; left as no-op for now
        pass

    def reserve_run(self, run_id, graph_id, initial_state):
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO runs (run_id, graph_id, initial_state, final_state, log, status, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                graph_id,
                json.dumps(initial_state if initial_state is not None else {}),
                json.dumps({}),
                json.dumps([]),
                "running",
                now,
                None,
            ),
        )
        self.conn.commit()

    def store_run(self, run):
        """
        Persist a run (dict or object) into the DB. Accepts either:
          - run as a dict with keys: run_id, final_state, log, completed_at
          - run as an object (dataclass/namespace) with same attributes.
        """
        run_id = _get_val(run, "run_id")
        if run_id is None:
            raise ValueError("store_run: run_id is required")

        final_state = _get_val(run, "final_state", {})
        raw_log = _get_val(run, "log", []) or []
        normalized_log = _normalize_log(raw_log)

        completed_at = _get_val(run, "completed_at")
        # If completed_at is a datetime, convert to ISO; otherwise pass-through (None or str)
        if isinstance(completed_at, datetime):
            completed_at_iso = completed_at.isoformat()
        else:
            completed_at_iso = completed_at or datetime.utcnow().isoformat()

        self.conn.execute(
            "UPDATE runs SET final_state=?, log=?, status=?, completed_at=? WHERE run_id=?",
            (
                json.dumps(final_state),
                json.dumps(normalized_log),
                "completed",
                completed_at_iso,
                run_id,
            ),
        )
        self.conn.commit()

    def mark_run_failed(self, run_id, error_msg):
        self.conn.execute(
            "UPDATE runs SET status=?, final_state=? WHERE run_id=?",
            ("failed", json.dumps({"error": error_msg}), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id):
        cur = self.conn.execute(
            "SELECT run_id, graph_id, initial_state, final_state, log, status, created_at, completed_at FROM runs WHERE run_id=?",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "graph_id": row[1],
            "initial_state": json.loads(row[2]) if row[2] else {},
            "final_state": json.loads(row[3]) if row[3] else {},
            "log": json.loads(row[4]) if row[4] else [],
            "status": row[5],
            "created_at": row[6],
            "completed_at": row[7],
        }
