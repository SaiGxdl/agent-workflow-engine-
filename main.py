# main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone
from typing import Any
import uuid
import asyncio
import os
import logging

from models import (
    CreateGraphRequest,
    CreateGraphResponse,
    RunGraphRequest,
    RunGraphResponse,
    GraphDefinition as GraphDefinitionModel,
    RunState,
)

# Use SQLite for both graphs and runs
from storage_sqlite import SQLiteStorage

# Node and engine imports
from engine import WorkflowEngine
from nodes import (
    extract_functions,
    check_complexity,
    detect_issues,
    suggest_improvements,
    end_node,
)
from logging_setup import configure_logging

# -----------------------------
# Logging
# -----------------------------
configure_logging("INFO")
logger = logging.getLogger(__name__)
logger.info("Starting Agent Workflow Engine")

# -----------------------------
# FastAPI App
# -----------------------------
app = FastAPI(title="Agent Workflow Engine", version="0.1.0")

# -----------------------------
# Storage Setup (SQLite for both graphs and runs)
# -----------------------------
storage_graphs = SQLiteStorage()
storage_runs = SQLiteStorage()

# Optional override: store graphs somewhere else via env var later if needed
USE_SQLITE_FOR_GRAPHS = os.getenv("USE_SQLITE_FOR_GRAPHS", "1") == "1"
if not USE_SQLITE_FOR_GRAPHS:
    # If you later implement a separate graph store, you can override here.
    pass

# -----------------------------
# Node registry and engine init
# -----------------------------
NODE_FUNCTIONS = {
    "extract_functions": extract_functions,
    "check_complexity": check_complexity,
    "detect_issues": detect_issues,
    "suggest_improvements": suggest_improvements,
    "end_node": end_node,
}

engine = WorkflowEngine(NODE_FUNCTIONS)

# -----------------------------
# Routes
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <h1>Agent Workflow Engine</h1>
    <p>API is running. Visit <a href="/docs">/docs</a> or <a href="/health">/health</a>.</p>
    """


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/graph/create", response_model=CreateGraphResponse)
async def create_graph(request: Request):
    """
    Robust create_graph:
      - Accepts raw JSON payload (so we can normalize before Pydantic)
      - Normalizes types/keys: nodes, edges, conditions, start_node
      - Ensures start_node exists (auto-create if needed)
      - Ensures each node is a dict and has minimal required keys:
          - 'func' (defaults to node name)
          - 'type' (defaults to 'task')
          - 'params' (defaults to {})
      - Ensures edges are lists and reference existing nodes (auto-create minimal nodes if needed)
      - Attempts to construct GraphDefinitionModel and store it; returns helpful error if still fails
    """
    try:
        payload = await request.json()
    except Exception:
        logger.exception("Invalid JSON payload in create_graph")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Normalize base keys
    nodes = payload.get("nodes") or {}
    edges = payload.get("edges") or {}
    conditions = payload.get("conditions") or {}
    start_node = payload.get("start_node")

    # Sanity type checks
    if not isinstance(nodes, dict):
        raise HTTPException(status_code=400, detail="`nodes` must be an object/dictionary")
    if not isinstance(edges, dict):
        raise HTTPException(status_code=400, detail="`edges` must be an object/dictionary")
    if not isinstance(conditions, dict):
        raise HTTPException(status_code=400, detail="`conditions` must be an object/dictionary")

    # Ensure start_node exists; choose fallback if missing
    if not start_node:
        if nodes:
            start_node = next(iter(nodes.keys()))
            logger.info("No start_node provided; defaulting to first node: %s", start_node)
        else:
            start_node = "start"
            nodes[start_node] = {}
            logger.info("No nodes provided; created default start node: %s", start_node)

    # If start_node not in nodes, create a minimal node for it
    if start_node not in nodes:
        nodes[start_node] = {}
        logger.info("start_node '%s' was not present in nodes; created an empty node.", start_node)

    # Normalize and auto-fill each node
    for name, nodeval in list(nodes.items()):
        # Normalize non-dict node representations to dict
        if nodeval is None or not isinstance(nodeval, dict):
            nodes[name] = {}
            nodeval = nodes[name]

        # Ensure minimal required keys exist & have reasonable default types
        # func: required by your models (default to node name)
        if "func" not in nodeval or nodeval.get("func") in (None, ""):
            nodes[name]["func"] = name

        # type: default to "task" if missing or not a string
        if "type" not in nodeval or not isinstance(nodeval.get("type"), str):
            nodes[name]["type"] = "task"

        # params: default to empty dict
        if "params" not in nodeval or not isinstance(nodeval.get("params"), dict):
            nodes[name]["params"] = {}

    # Validate & normalize edges: ensure target nodes exist; edges must be list
    for src, out_edges in list(edges.items()):
        if src not in nodes:
            # forgive referencing unknown node by making a minimal node
            nodes[src] = {"func": src, "type": "task", "params": {}}
            logger.warning("Auto-created node '%s' referenced in edges.", src)

        # edges must be list
        if out_edges is None:
            edges[src] = []
            out_edges = edges[src]
        if not isinstance(out_edges, list):
            raise HTTPException(status_code=400, detail=f"Edges for node '{src}' must be a list")

        # ensure each referenced edge target exists (auto-create minimal node if needed)
        for tgt in out_edges:
            if not isinstance(tgt, str):
                raise HTTPException(status_code=400, detail=f"Edge targets must be string node names (bad target on '{src}')")
            if tgt not in nodes:
                nodes[tgt] = {"func": tgt, "type": "task", "params": {}}
                logger.warning("Auto-created node '%s' referenced as an edge target.", tgt)

    # Now construct typed GraphDefinitionModel and store it
    try:
        graph = GraphDefinitionModel(
            nodes=nodes,
            edges=edges,
            conditions=conditions,
            start_node=start_node,
        )
    except Exception as e:
        # Validation failed despite normalization: return user-friendly message
        logger.exception("GraphDefinitionModel validation failed in create_graph")
        raise HTTPException(status_code=400, detail=f"Graph schema validation failed: {str(e)}")

    # Store graph
    try:
        graph_id = storage_graphs.store_graph(graph)
    except Exception as e:
        logger.exception("Failed to store graph in create_graph")
        # storage failure is server-side — return 500 with a clear message
        raise HTTPException(status_code=500, detail=f"Failed to store graph: {str(e)}")

    return CreateGraphResponse(graph_id=graph_id)


@app.post("/graph/run", response_model=RunGraphResponse)
async def run_graph(request: RunGraphRequest):
    """Run a graph synchronously."""
    graph = storage_graphs.get_graph(request.graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")

    try:
        final_state, log = await asyncio.to_thread(engine.execute, graph, request.initial_state)
    except Exception as e:
        logger.exception("Execution failed")
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}")

    run = RunState(
        run_id=str(uuid.uuid4()),
        graph_id=request.graph_id,
        initial_state=request.initial_state,
        final_state=final_state,
        log=log,
        status="completed",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )

    await asyncio.to_thread(storage_runs.store_run, run)

    return RunGraphResponse(run_id=run.run_id, final_state=final_state, log=log)


async def _execute_and_store(graph, initial_state, run_id: str):
    """Background executor for async runs."""
    try:
        logger.info("Background run started: %s", run_id)
        final_state, log = await asyncio.to_thread(engine.execute, graph, initial_state)

        run = RunState(
            run_id=run_id,
            graph_id=getattr(graph, "graph_id", None),
            initial_state=initial_state,
            final_state=final_state,
            log=log,
            status="completed",
            created_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        await asyncio.to_thread(storage_runs.store_run, run)
        logger.info("Background run completed: %s", run_id)

    except Exception as e:
        logger.exception("Background run failed: %s", run_id)
        try:
            await asyncio.to_thread(storage_runs.mark_run_failed, run_id, str(e))
        except Exception:
            logger.exception("Failed to mark run failed: %s", run_id)


@app.post("/graph/run-async")
async def run_graph_async(request: RunGraphRequest, bg: BackgroundTasks):
    """Start async run and return immediately with run_id."""
    graph = storage_graphs.get_graph(request.graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")

    run_id = str(uuid.uuid4())

    storage_runs.reserve_run(run_id, request.graph_id, request.initial_state)

    bg.add_task(_execute_and_store, graph, request.initial_state, run_id)

    return {"run_id": run_id, "status": "started"}


@app.get("/graph/state/{run_id}")
def get_run_state(run_id: str):
    """Return run state from storage (dict)."""
    run = storage_runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
