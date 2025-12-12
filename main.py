# main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone
import uuid
import asyncio
import os
import logging
from typing import Any, Dict, List

from models import (
    CreateGraphResponse,
    RunGraphRequest,
    RunGraphResponse,
    GraphDefinition as GraphDefinitionModel,
    RunState,
)

# Storage backend (ensure storage_sqlite implements required API)
from storage_sqlite import SQLiteStorage

# Node functions and engine
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
# If you want an in-memory store for debugging, replace these with an InMemoryStorage instance.
storage_graphs: SQLiteStorage = SQLiteStorage()
storage_runs: SQLiteStorage = SQLiteStorage()

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
    Accept a flexible graph payload, normalize it, validate with Pydantic, and store.
    Expected incoming shape:
    {
      "nodes": { "<name>": {"func": "func_name", ...}, ... },
      "edges": { "<name>": ["target1", "target2"], ... },
      "conditions": { "src->dst": "condition", ... },
      "start_node": "<name>"
    }
    """
    try:
        payload = await request.json()
    except Exception:
        logger.exception("Invalid JSON payload in create_graph")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Basic normalization / defaults
    nodes = payload.get("nodes") or {}
    edges = payload.get("edges") or {}
    conditions = payload.get("conditions") or {}
    start_node = payload.get("start_node")

    # Basic type checks - return 400 for bad shapes
    if not isinstance(nodes, dict):
        raise HTTPException(status_code=400, detail="`nodes` must be an object/dictionary")
    if not isinstance(edges, dict):
        raise HTTPException(status_code=400, detail="`edges` must be an object/dictionary")
    if not isinstance(conditions, dict):
        raise HTTPException(status_code=400, detail="`conditions` must be an object/dictionary")

    # Ensure start_node exists
    if not start_node:
        if nodes:
            start_node = next(iter(nodes.keys()))
            logger.info("No start_node provided; defaulting to first node: %s", start_node)
        else:
            start_node = "start"
            nodes[start_node] = {}
            logger.info("No nodes provided; created default start node: %s", start_node)

    if start_node not in nodes:
        nodes[start_node] = {}
        logger.info("start_node '%s' was not present in nodes; created an empty node.", start_node)

    # Normalize nodes: ensure dict and default func
    for name, nodeval in list(nodes.items()):
        if nodeval is None or not isinstance(nodeval, dict):
            nodes[name] = {}
            nodeval = nodes[name]
        if "func" not in nodeval or not isinstance(nodeval.get("func"), str) or nodeval.get("func") == "":
            nodes[name]["func"] = name

    # Validate edges and auto-create referenced nodes if missing
    for src, out_edges in list(edges.items()):
        if src not in nodes:
            nodes[src] = {"func": src}
            logger.warning("Auto-created node '%s' referenced in edges.", src)

        if out_edges is None:
            edges[src] = []
            out_edges = edges[src]
        if not isinstance(out_edges, list):
            raise HTTPException(status_code=400, detail=f"Edges for node '{src}' must be a list")

        for tgt in out_edges:
            if not isinstance(tgt, str):
                raise HTTPException(status_code=400, detail=f"Edge targets must be string node names (bad target on '{src}')")
            if tgt not in nodes:
                nodes[tgt] = {"func": tgt}
                logger.warning("Auto-created node '%s' referenced as an edge target.", tgt)

    # Build minimal nodes dict for Pydantic model (avoid extra-key validation issues)
    try:
        minimal_nodes: Dict[str, Dict[str, str]] = {n: {"func": nodes[n]["func"]} for n in nodes}
        graph = GraphDefinitionModel(
            nodes=minimal_nodes,
            edges=edges,
            conditions=conditions,
            start_node=start_node,
        )
    except Exception as e:
        logger.exception("GraphDefinitionModel validation failed in create_graph")
        raise HTTPException(status_code=400, detail=f"Graph schema validation failed: {str(e)}")

    # Persist graph (offload to thread if storage may block)
    try:
        graph_id = await asyncio.to_thread(storage_graphs.store_graph, graph)
    except Exception as e:
        logger.exception("Failed to store graph in create_graph")
        raise HTTPException(status_code=500, detail=f"Failed to store graph: {str(e)}")

    return CreateGraphResponse(graph_id=graph_id)


@app.post("/graph/run", response_model=RunGraphResponse)
async def run_graph(request: RunGraphRequest):
    """Run a workflow graph synchronously and return final state + log."""
    graph = await asyncio.to_thread(storage_graphs.get_graph, request.graph_id)
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

    try:
        await asyncio.to_thread(storage_runs.store_run, run)
    except Exception as e:
        logger.exception("Failed to store run")
        raise HTTPException(status_code=500, detail=f"Failed to store run: {e}")

    # Convert log entries to pydantic-friendly form if they are dataclasses
    try:
        pydantic_log = []
        for entry in log:
            if hasattr(entry, "to_pydantic"):
                pydantic_log.append(entry.to_pydantic())
            else:
                pydantic_log.append(entry)
    except Exception:
        pydantic_log = log

    return RunGraphResponse(run_id=run.run_id, final_state=final_state, log=pydantic_log)


async def _execute_and_store(graph: Any, initial_state: dict, run_id: str):
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
    graph = await asyncio.to_thread(storage_graphs.get_graph, request.graph_id)
    if not graph:
        raise HTTPException(status_code=404, detail="Graph not found")

    run_id = str(uuid.uuid4())

    # Reserve run in storage (offload to thread)
    try:
        await asyncio.to_thread(storage_runs.reserve_run, run_id, request.graph_id, request.initial_state)
    except Exception as e:
        logger.exception("Failed to reserve run")
        raise HTTPException(status_code=500, detail=f"Failed to reserve run: {e}")

    # Schedule background task (BackgroundTasks will run after response)
    bg.add_task(_execute_and_store, graph, request.initial_state, run_id)

    return {"run_id": run_id, "status": "started"}


@app.get("/graph/state/{run_id}")
def get_run_state(run_id: str):
    """Return run state from storage (prefer pydantic serialization if available)."""
    run = storage_runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    try:
        if hasattr(run, "to_pydantic"):
            return run.to_pydantic()
        if hasattr(run, "dict"):
            return run.dict()
        return run
    except Exception:
        logger.exception("Failed to serialize run for run_id=%s", run_id)
        return {
            "run_id": getattr(run, "run_id", run_id),
            "status": getattr(run, "status", "unknown"),
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
