# engine.py
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Callable, Optional
from datetime import datetime, timezone
import time
import copy
import pprint
import ast

# --- Helper to safely test for AST node classes across Python versions ---
def ast_class(name):
    """Return ast.<name> if present, else None."""
    return getattr(ast, name, None)

# --- Safe condition evaluator using AST (cross-version compatible) ---
class SafeConditionEvaluator:
    @staticmethod
    def evaluate(condition: str, state: Dict[str, Any]) -> bool:
        """
        Safely evaluate a limited expression language for workflow branching.
        Supported:
          - boolean ops: and, or, not
          - comparisons: ==, !=, <, <=, >, >=
          - parentheses
          - numeric and string literals (ast.Constant)
          - state['key'] and state.get('key', default)
        Disallowed: arbitrary names, attribute access (except state.get), arbitrary calls.
        """
        if not isinstance(condition, str):
            raise ValueError("Condition must be a string")
        src = condition.strip()
        if src.lower() == "true":
            return True
        if src.lower() == "false":
            return False

        try:
            parsed = ast.parse(src, mode="eval")
            node = parsed.body
        except SyntaxError as e:
            raise ValueError(f"Syntax error in condition: {e}")

        # helpers for available AST classes (may be None on some Python versions)
        AST_Num = ast_class("Num")
        AST_Str = ast_class("Str")
        AST_Index = ast_class("Index")
        AST_Constant = ast_class("Constant")

        def is_instance(n, cls):
            """Safe isinstance: cls may be None (not present in this version)."""
            if cls is None:
                return False
            return isinstance(n, cls)

        def eval_node(n):
            # Constants (modern Python uses ast.Constant)
            if AST_Constant and isinstance(n, AST_Constant):
                return n.value
            if AST_Num and is_instance(n, AST_Num):
                return n.n
            if AST_Str and is_instance(n, AST_Str):
                return n.s

            # Name: allow True/False if they appear as names (rare)
            if isinstance(n, ast.Name):
                if n.id in ("True", "False"):
                    return True if n.id == "True" else False
                raise ValueError(f"Use of name '{n.id}' not allowed in condition")

            # Boolean ops
            if isinstance(n, ast.BoolOp):
                vals = [eval_node(v) for v in n.values]
                if isinstance(n.op, ast.And):
                    return all(vals)
                if isinstance(n.op, ast.Or):
                    return any(vals)
                raise ValueError("Unsupported boolean operator")

            # Unary ops (not)
            if isinstance(n, ast.UnaryOp):
                if isinstance(n.op, ast.Not):
                    return not eval_node(n.operand)
                raise ValueError("Unsupported unary operator")

            # Comparisons
            if isinstance(n, ast.Compare):
                left = eval_node(n.left)
                for op, comparator in zip(n.ops, n.comparators):
                    right = eval_node(comparator)
                    if isinstance(op, ast.Eq):
                        ok = left == right
                    elif isinstance(op, ast.NotEq):
                        ok = left != right
                    elif isinstance(op, ast.Lt):
                        ok = left < right
                    elif isinstance(op, ast.LtE):
                        ok = left <= right
                    elif isinstance(op, ast.Gt):
                        ok = left > right
                    elif isinstance(op, ast.GtE):
                        ok = left >= right
                    else:
                        raise ValueError("Unsupported comparison operator")
                    if not ok:
                        return False
                    left = right
                return True

            # Subscript: state['key']
            if isinstance(n, ast.Subscript):
                # Ensure pattern state[<key>] only
                if isinstance(n.value, ast.Name) and n.value.id == "state":
                    # slice shape differs with Python versions
                    idx = n.slice
                    # older ASTs used ast.Index wrapper
                    if AST_Index and is_instance(idx, AST_Index):
                        idx = idx.value
                    key = eval_node(idx)
                    return state.get(key)
                raise ValueError("Only state[...] subscripting is allowed")

            # Call: allow only state.get(key[, default])
            if isinstance(n, ast.Call):
                func = n.func
                # func must be state.get
                if isinstance(func, ast.Attribute):
                    if isinstance(func.value, ast.Name) and func.value.id == "state" and func.attr == "get":
                        # allow 1 or 2 args
                        if not (1 <= len(n.args) <= 2):
                            raise ValueError("state.get() accepts 1 or 2 args")
                        key = eval_node(n.args[0])
                        default = eval_node(n.args[1]) if len(n.args) == 2 else None
                        return state.get(key, default)
                raise ValueError("Only state.get(...) calls are allowed")

            # ast.Index wrapper fallback for odd ASTs
            if AST_Index and is_instance(n, AST_Index):
                return eval_node(n.value)

            # Tuple of values
            if isinstance(n, ast.Tuple):
                return tuple(eval_node(e) for e in n.elts)

            raise ValueError(f"Unsupported AST node in condition: {type(n).__name__}")

        result = eval_node(node)
        if not isinstance(result, bool):
            return bool(result)
        return result

# --- Models ---
@dataclass
class LogEntry:
    step: int
    node_name: str
    entry_state: Dict[str, Any]
    exit_state: Dict[str, Any]
    decision: List[str]
    duration: Optional[float] = None  # seconds
    timestamp: Optional[str] = None   # ISO UTC string

@dataclass
class GraphDefinition:
    # tests pass a mapping of nodes (dict) so allow a generic mapping
    nodes: Dict[str, Any]
    edges: Dict[str, List[str]]
    conditions: Dict[str, str]
    start_node: str

# --- WorkflowEngine ---
class WorkflowEngine:
    """Minimal agent workflow engine with safe condition evaluation and per-node timing."""
    
    def __init__(self, node_functions: Dict[str, Callable]):
        self.node_functions = node_functions
    
    def execute(
        self,
        graph: GraphDefinition,
        initial_state: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[LogEntry]]:
        """Execute a workflow graph and return final state and execution log."""
        state = copy.deepcopy(initial_state)
        log: List[LogEntry] = []
        current_node = graph.start_node
        step = 0
        
        # Prevent infinite loops
        max_steps = 1000
        
        while current_node is not None and step < max_steps:
            if current_node not in graph.nodes:
                raise ValueError(f"Unknown node: {current_node}")
            
            step += 1
            entry_state = copy.deepcopy(state)
            
            # Execute node
            if current_node not in self.node_functions:
                raise ValueError(f"Node function not found: {current_node}")
            
            try:
                start = time.perf_counter()
                state = self.node_functions[current_node](state)
                duration = time.perf_counter() - start
            except Exception as e:
                raise RuntimeError(f"Error executing node {current_node}: {str(e)}")
            
            exit_state = copy.deepcopy(state)
            next_nodes = graph.edges.get(current_node, [])
            
            if not next_nodes:
                decision = []
                log.append(LogEntry(
                    step=step,
                    node_name=current_node,
                    entry_state=entry_state,
                    exit_state=exit_state,
                    decision=decision,
                    duration=duration,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                break
            
            decision = []
            for next_node in next_nodes:
                condition_key = f"{current_node}->{next_node}"
                if condition_key in graph.conditions:
                    try:
                        if SafeConditionEvaluator.evaluate(graph.conditions[condition_key], state):
                            decision.append(next_node)
                    except ValueError as e:
                        raise ValueError(f"Invalid condition for {condition_key}: {str(e)}")
                else:
                    decision.append(next_node)
            
            log.append(LogEntry(
                step=step,
                node_name=current_node,
                entry_state=entry_state,
                exit_state=exit_state,
                decision=decision,
                duration=duration,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
            
            # choose first decision (sequential)
            current_node = decision[0] if decision else None
        
        if step >= max_steps:
            raise RuntimeError(f"Workflow exceeded maximum steps ({max_steps})")
        
        return state, log

# Demo driver guarded by main to avoid running in tests
if __name__ == "__main__":
    def start_node(state):
        time.sleep(0.02)
        state['counter'] = state.get('counter', 0) + 1
        return state

    def branch_node(state):
        time.sleep(0.01)
        return state

    def end_node_local(state):
        time.sleep(0.005)
        state['done'] = True
        return state

    graph = GraphDefinition(
        nodes={"start": None, "branch": None, "end": None},
        edges={"start": ["branch"], "branch": ["end"]},
        conditions={},
        start_node="start"
    )

    engine = WorkflowEngine(node_functions={
        "start": start_node,
        "branch": branch_node,
        "end": end_node_local,
    })

    final_state, log_entries = engine.execute(graph, initial_state={})
    pp = pprint.PrettyPrinter(indent=2, width=120)
    print("Final state:")
    pp.pprint(final_state)
    print("\nExecution log entries:")
    for entry in log_entries:
        pp.pprint(asdict(entry))
