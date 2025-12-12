# storage_sqlite.py
from __future__ import annotations

import sqlite3
import json
import threading
import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pathlib import Path

from models import (
    RunState,
    RunStatePydantic,
    LogEntryPydantic,
    GraphDefinition as GraphDefinitionModel,
)

logger = logging.getLogger(__name__)
DB_DEFAULT = Path("runs.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStorage:
    def __init__(self, db_path: str | Path = DB_DEFAULT):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graphs (
                        graph_id TEXT PRIMARY KEY,
                        created_at TEXT,
                        data TEXT
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        graph_id TEXT,
                        created_at TEXT,
                        status TEXT,
                        data TEXT
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # -----------------------
    # Graphs
    # -----------------------
    def store_graph(self, graph: GraphDefinitionModel | dict) -> str:
        """
        Store a GraphDefinitionModel or dict and return generated graph_id.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                graph_id = f"g-{uuid.uuid4().hex}"
                created_at = _now_iso()

                # normalize to plain dict
                try:
                    if hasattr(graph, "model_dump"):
                        graph_dict = graph.model_dump()
                    elif hasattr(graph, "dict"):
                        graph_dict = graph.dict()
                    else:
                        graph_dict = dict(graph)
                except Exception:
                    # fallback: if it's already a dict-like
                    graph_dict = graph if isinstance(graph, dict) else {}

                data_text = json.dumps(graph_dict, default=str)
                cur.execute(
                    "INSERT OR REPLACE INTO graphs(graph_id, created_at, data) VALUES (?, ?, ?)",
                    (graph_id, created_at, data_text),
                )
                conn.commit()
                return graph_id
            except Exception as e:
                logger.exception("store_graph failed: %s", e)
                raise
            finally:
                conn.close()

    def get_graph(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the graph dict (attempt to return a Pydantic model if possible).
        Main code can accept dicts too; we return dict on failure.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT data FROM graphs WHERE graph_id = ?", (graph_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data_text = row["data"]
                graph_dict = json.loads(data_text)
                # Try to make a pydantic model; if that fails, return dict
                try:
                    graph_model = GraphDefinitionModel(**graph_dict)
                    return graph_model
                except Exception:
                    logger.debug("Returning raw graph dict (could not reconstruct Pydantic model).")
                    return graph_dict
            except Exception:
                logger.exception("get_graph failed")
                raise
            finally:
                conn.close()

    # -----------------------
    # Runs
    # -----------------------
    def reserve_run(self, run_id: str, graph_id: str, initial_state: Dict[str, Any]) -> None:
        """
        Reserve a run row so clients can poll while execution is pending.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                created_at = _now_iso()
                run_record = {
                    "run_id": run_id,
                    "graph_id": graph_id,
                    "initial_state": initial_state,
                    "final_state": None,
                    "log": [],
                    "status": "running",
                    "created_at": created_at,
                    "completed_at": None,
                }
                cur.execute(
                    "INSERT OR REPLACE INTO runs(run_id, graph_id, created_at, status, data) VALUES (?, ?, ?, ?, ?)",
                    (run_id, graph_id, created_at, "running", json.dumps(run_record, default=str)),
                )
                conn.commit()
            except Exception:
                logger.exception("reserve_run failed")
                raise
            finally:
                conn.close()

    def store_run(self, run: RunState | Dict[str, Any]) -> str:
        """
        Persist a completed run. Accepts RunState dataclass, Pydantic model, or plain dict.
        Returns run_id.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                # Normalize run -> plain serializable dict (`run_dict`)
                run_dict: Dict[str, Any]
                if hasattr(run, "to_pydantic"):
                    p = run.to_pydantic()
                    # p may be a Pydantic model instance
                    if hasattr(p, "model_dump"):
                        run_dict = p.model_dump()
                    elif hasattr(p, "dict"):
                        run_dict = p.dict()
                    else:
                        run_dict = dict(p)
                elif hasattr(run, "model_dump"):
                    run_dict = run.model_dump()
                elif hasattr(run, "dict"):
                    run_dict = run.dict()
                elif isinstance(run, dict):
                    run_dict = run
                else:
                    # fallback best-effort
                    run_dict = {}
                    if hasattr(run, "__dict__"):
                        run_dict.update({k: getattr(run, k) for k in vars(run)})

                # ensure required fields
                run_id = run_dict.get("run_id") or f"r-{uuid.uuid4().hex}"
                created_at = run_dict.get("created_at") or _now_iso()
                status = run_dict.get("status", "completed")
                graph_id = run_dict.get("graph_id")

                # Ensure log entries are serializable (convert pydantic/dataclass objects)
                serializable_log: List[Any] = []
                raw_log = run_dict.get("log", []) or []
                for e in raw_log:
                    try:
                        # 1) to_pydantic()
                        if hasattr(e, "to_pydantic"):
                            lp = e.to_pydantic()
                            serializable_log.append(lp.model_dump() if hasattr(lp, "model_dump") else lp.dict())
                            continue

                        # 2) pydantic model_dump/dict
                        if hasattr(e, "model_dump"):
                            serializable_log.append(e.model_dump())
                            continue
                        if hasattr(e, "dict"):
                            serializable_log.append(e.dict())
                            continue

                        # 3) plain dict
                        if isinstance(e, dict):
                            serializable_log.append(e)
                            continue

                        # 4) dataclass-like: try to read common attributes
                        log_entry_candidates = {}
                        for attr in ("step", "node_name", "entry_state", "exit_state", "decision", "duration", "timestamp"):
                            if hasattr(e, attr):
                                log_entry_candidates[attr] = getattr(e, attr)
                        if log_entry_candidates:
                            # ensure timestamp serialization
                            ts = log_entry_candidates.get("timestamp")
                            if hasattr(ts, "isoformat"):
                                log_entry_candidates["timestamp"] = ts.isoformat()
                            serializable_log.append(log_entry_candidates)
                            continue

                        # 5) last resort: string representation
                        serializable_log.append({"entry": str(e)})
                    except Exception:
                        # ensure we never blow up serialization
                        serializable_log.append({"error": "failed to serialize log entry", "repr": str(e)})

                run_dict["log"] = serializable_log

                data_text = json.dumps(run_dict, default=str)

                cur = conn.cursor()
                cur.execute(
                    "INSERT OR REPLACE INTO runs(run_id, graph_id, created_at, status, data) VALUES (?, ?, ?, ?, ?)",
                    (run_id, graph_id, created_at, status, data_text),
                )
                conn.commit()
                return run_id
            except Exception:
                logger.exception("store_run failed")
                raise
            finally:
                conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a run record. Returns a dict with run data. If you want a RunState dataclass,
        call RunState.from_pydantic externally after conversion.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data_text = row["data"]
                run_dict = json.loads(data_text) if data_text else {}
                # try to surface final_state/log directly at top-level for convenience
                if isinstance(run_dict, dict):
                    # Ensure created_at/completed_at are present
                    if "created_at" not in run_dict:
                        run_dict["created_at"] = row.get("created_at")
                    if "status" not in run_dict:
                        run_dict["status"] = row.get("status")
                return run_dict
            except Exception:
                logger.exception("get_run failed")
                raise
            finally:
                conn.close()

    def mark_run_failed(self, run_id: str, error_message: str) -> None:
        """Mark run as failed and attach an error message in data."""
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,))
                row = cur.fetchone()
                if not row:
                    return
                run_dict = json.loads(row["data"]) if row["data"] else {}
                run_dict["status"] = "failed"
                run_dict.setdefault("final_state", {})
                run_dict["final_state"]["error"] = error_message
                run_dict["completed_at"] = _now_iso()

                cur.execute(
                    "UPDATE runs SET status = ?, data = ? WHERE run_id = ?",
                    ("failed", json.dumps(run_dict, default=str), run_id),
                )
                conn.commit()
            except Exception:
                logger.exception("mark_run_failed failed")
                raise
            finally:
                conn.close()
