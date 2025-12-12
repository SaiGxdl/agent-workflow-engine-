from __future__ import annotations  # MUST be first

# models.py

from pydantic import BaseModel, Field
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import uuid

# ----------------------------------------------------
# Helper: Create timezone-aware ISO timestamps
# ----------------------------------------------------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------
# Pydantic Models (API layer)
# ----------------------------------------------------

class NodeDefinition(BaseModel):
    func: str


class GraphDefinition(BaseModel):
    nodes: Dict[str, NodeDefinition]
    edges: Dict[str, List[str]]
    conditions: Dict[str, str]
    start_node: str


class LogEntryPydantic(BaseModel):
    step: int
    node_name: str
    entry_state: Dict[str, Any]
    exit_state: Dict[str, Any]
    decision: List[str] = Field(default_factory=list)
    duration: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"from_attributes": True}


class RunStatePydantic(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    graph_id: str
    initial_state: Dict[str, Any]
    final_state: Optional[Dict[str, Any]] = None
    log: List[LogEntryPydantic] = Field(default_factory=list)
    status: str = "running"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CreateGraphRequest(BaseModel):
    nodes: Dict[str, NodeDefinition]
    edges: Dict[str, List[str]]
    conditions: Dict[str, str]
    start_node: str


class RunGraphRequest(BaseModel):
    graph_id: str
    initial_state: Dict[str, Any]


class CreateGraphResponse(BaseModel):
    graph_id: str


class RunGraphResponse(BaseModel):
    run_id: str
    final_state: Dict[str, Any]
    log: List[LogEntryPydantic]


# ----------------------------------------------------
# Dataclasses (internal runtime state for engine)
# ----------------------------------------------------

@dataclass
class LogEntry:
    step: int
    node_name: str
    entry_state: Dict[str, Any]
    exit_state: Dict[str, Any]
    decision: List[str] = field(default_factory=list)
    duration: Optional[float] = None
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = now_utc_iso()

    def to_pydantic(self) -> LogEntryPydantic:
        # SAFE conversion: timestamp → datetime
        if self.timestamp is None:
            ts_dt = datetime.now(timezone.utc)
        elif isinstance(self.timestamp, str):
            ts_dt = datetime.fromisoformat(self.timestamp)
        else:
            ts_dt = self.timestamp  # already datetime

        return LogEntryPydantic(
            step=self.step,
            node_name=self.node_name,
            entry_state=self.entry_state,
            exit_state=self.exit_state,
            decision=list(self.decision),
            duration=self.duration,
            timestamp=ts_dt,
        )

    @staticmethod
    def from_pydantic(p: LogEntryPydantic) -> "LogEntry":
        return LogEntry(
            step=p.step,
            node_name=p.node_name,
            entry_state=p.entry_state,
            exit_state=p.exit_state,
            decision=list(p.decision),
            duration=p.duration,
            timestamp=p.timestamp.isoformat(),
        )


@dataclass
class RunState:
    run_id: str
    graph_id: Optional[str] = None
    initial_state: Optional[Dict[str, Any]] = None
    final_state: Optional[Dict[str, Any]] = None
    log: List[LogEntry] = field(default_factory=list)
    status: str = "running"
    created_at: Optional[str] = None
    completed_at: Optional[str] = None

    def __post_init__(self):
        if not self.run_id:
            self.run_id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = now_utc_iso()

    def to_pydantic(self) -> RunStatePydantic:
        # SAFE conversion: created_at
        if self.created_at is None:
            created_dt = datetime.now(timezone.utc)
        elif isinstance(self.created_at, str):
            created_dt = datetime.fromisoformat(self.created_at)
        else:
            created_dt = self.created_at

        # SAFE conversion: completed_at
        if self.completed_at is None:
            completed_dt = None
        elif isinstance(self.completed_at, str):
            completed_dt = datetime.fromisoformat(self.completed_at)
        else:
            completed_dt = self.completed_at

        return RunStatePydantic(
            run_id=self.run_id,
            graph_id=self.graph_id or "",
            initial_state=self.initial_state or {},
            final_state=self.final_state,
            log=[le.to_pydantic() for le in self.log],
            status=self.status,
            created_at=created_dt,
            completed_at=completed_dt,
        )

    @staticmethod
    def from_pydantic(p: RunStatePydantic) -> "RunState":
        return RunState(
            run_id=p.run_id,
            graph_id=p.graph_id,
            initial_state=p.initial_state,
            final_state=p.final_state,
            log=[LogEntry.from_pydantic(le) for le in p.log],
            status=p.status,
            created_at=p.created_at.isoformat(),
            completed_at=(p.completed_at.isoformat() if p.completed_at else None),
        )


# ----------------------------------------------------
# Helper
# ----------------------------------------------------
def make_runstate_from_graph(graph_id: str, initial_state: Dict[str, Any]) -> RunState:
    return RunState(
        run_id=str(uuid.uuid4()),
        graph_id=graph_id,
        initial_state=initial_state,
        final_state=None,
        log=[],
        status="running",
        created_at=now_utc_iso(),
    )
