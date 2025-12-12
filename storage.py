# storage.py
from typing import Dict, Optional, List
from models import GraphDefinition, RunState
import uuid
from datetime import datetime
import threading


class Storage:
    """In-memory storage for graphs and runs. Thread-safe for use with background tasks."""

    def __init__(self):
        self.graphs: Dict[str, GraphDefinition] = {}
        self.runs: Dict[str, RunState] = {}
        self._lock = threading.Lock()

    # -----------------------
    # Graph methods
    # -----------------------
    def store_graph(self, graph: GraphDefinition) -> str:
        """Store a graph and return its ID."""
        graph_id = str(uuid.uuid4())
        with self._lock:
            self.graphs[graph_id] = graph
        return graph_id

    def get_graph(self, graph_id: str) -> Optional[GraphDefinition]:
        """Retrieve a graph by ID."""
        with self._lock:
            return self.graphs.get(graph_id)

    # -----------------------
    # Run methods
    # -----------------------
    def store_run(self, run: RunState) -> str:
        """
        Persist the provided RunState. If run.run_id is missing/empty, generate one.
        Returns the run_id used to store the run.
        """
        # set run_id if missing
        run_id = getattr(run, "run_id", None)
        if not run_id:
            run_id = str(uuid.uuid4())
            # attempt to set attribute if RunState allows assignment
            try:
                run.run_id = run_id
            except Exception:
                # If RunState is frozen/immutable, we'll store by generated id mapping to run object
                pass

        with self._lock:
            self.runs[run_id] = run

        return run_id

    def get_run(self, run_id: str) -> Optional[RunState]:
        """Retrieve a run by ID."""
        with self._lock:
            return self.runs.get(run_id)

    def list_runs(self, graph_id: str) -> List[RunState]:
        """List all runs for a graph."""
        with self._lock:
            return [run for run in self.runs.values() if getattr(run, "graph_id", None) == graph_id]

    # -----------------------
    # Methods required for background flow
    # -----------------------
    def reserve_run(self, run_id: str, graph_id: str, initial_state: dict) -> None:
        """
        Reserve a run record so that polling can return 'running' immediately.
        Creates a RunState with minimal fields: run_id, graph_id, initial_state, status='running', created_at.
        """
        now = datetime.utcnow()
        # Create a minimal RunState. We assume RunState accepts these fields; adapt if your RunState has a different signature.
        run = RunState(
            run_id=run_id,
            graph_id=graph_id,
            initial_state=initial_state,
            final_state=None,
            log=None,
            status="running",
            created_at=now,
            completed_at=None,
        )

        with self._lock:
            self.runs[run_id] = run

    def mark_run_failed(self, run_id: str, error_msg: str) -> None:
        """
        Mark a run as failed and append the error_msg to the run log (or set it).
        """
        now = datetime.utcnow()
        with self._lock:
            existing = self.runs.get(run_id)
            if not existing:
                # If we don't have the run reserved, create a failed record anyway
                run = RunState(
                    run_id=run_id,
                    graph_id=None,
                    initial_state=None,
                    final_state=None,
                    log=f"Run failed before reservation: {error_msg}",
                    status="failed",
                    created_at=now,
                    completed_at=now,
                )
                self.runs[run_id] = run
                return

            # update fields on existing run. Try to assign; if RunState is immutable, replace with a new one
            try:
                existing.status = "failed"
                existing.log = (existing.log or "") + f"\nERROR: {error_msg}"
                existing.completed_at = now
                self.runs[run_id] = existing
            except Exception:
                # fallback: create a new RunState object (best-effort; adapt to your RunState model)
                run = RunState(
                    run_id=existing.run_id,
                    graph_id=getattr(existing, "graph_id", None),
                    initial_state=getattr(existing, "initial_state", None),
                    final_state=None,
                    log=(getattr(existing, "log", None) or "") + f"\nERROR: {error_msg}",
                    status="failed",
                    created_at=getattr(existing, "created_at", now),
                    completed_at=now,
                )
                self.runs[run_id] = run

    def update_run(self, run_id: str, run: RunState) -> None:
        """Atomically replace an existing run record (used by background worker to store completed run)."""
        with self._lock:
            self.runs[run_id] = run
