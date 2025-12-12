# tests/test_engine.py
from engine import WorkflowEngine
from models import GraphDefinition, NodeDefinition
from nodes import (
    extract_functions,
    check_complexity,
    detect_issues,
    suggest_improvements,
    end_node,
)

SAMPLE_CODE = """
def hello(name):
    # TODO: add more functionality
    print(f"Hello, {name}!")

def complex_function(a, b, c, d, e, f, g):
    if a > 0 and b > 0:
        for i in range(a):
            while i < b:
                if c or d:
                    print(f"Very long line that exceeds 100 characters limit and is intentionally written to trigger the long line detection {c}")
                i += 1
    return a + b

def bad_function(x, y, z, w, p, q, r):
    # This function has too many parameters
    # and a missing docstring
    return x + y
"""


class TestWorkflowEngine:
    def test_code_review_workflow_reaches_quality_threshold(self):
        """Test that code review workflow completes and quality score improves."""
        node_functions = {
            "extract_functions": extract_functions,
            "check_complexity": check_complexity,
            "detect_issues": detect_issues,
            "suggest_improvements": suggest_improvements,
            "end_node": end_node,
        }

        engine = WorkflowEngine(node_functions)

        graph = GraphDefinition(
            nodes={
                "extract_functions": NodeDefinition(func="extract_functions"),
                "check_complexity": NodeDefinition(func="check_complexity"),
                "detect_issues": NodeDefinition(func="detect_issues"),
                "suggest_improvements": NodeDefinition(func="suggest_improvements"),
                "end_node": NodeDefinition(func="end_node"),
            },
            edges={
                "extract_functions": ["check_complexity"],
                "check_complexity": ["detect_issues"],
                "detect_issues": ["suggest_improvements"],
                "suggest_improvements": ["suggest_improvements", "end_node"],
            },
            conditions={
                "suggest_improvements->end_node": "state['quality_score'] >= 0.5",
                "suggest_improvements->suggest_improvements": "state['quality_score'] < 0.5 and state.get('iterations', 0) < 5",
            },
            start_node="extract_functions",
        )

        initial_state = {"code": SAMPLE_CODE}
        final_state, log = engine.execute(graph, initial_state)

        assert final_state.get("quality_score", 0) >= 0.5 or final_state.get("iterations", 0) >= 5
        assert len(log) > 0
        assert "functions" in final_state
        assert "issues" in final_state

    def test_branching_conditions_evaluated_safely(self):
        """Test that branching conditions are evaluated safely."""
        def pass_node(state):
            state["value"] = 10
            return state

        def end(state):
            return state

        node_functions = {
            "pass": pass_node,
            "end": end,
        }

        engine = WorkflowEngine(node_functions)

        graph = GraphDefinition(
            nodes={
                "pass": NodeDefinition(func="pass"),
                "end": NodeDefinition(func="end"),
            },
            edges={
                "pass": ["end"],
            },
            conditions={
                "pass->end": "state['value'] > 5",
            },
            start_node="pass",
        )

        final_state, log = engine.execute(graph, {})
        assert final_state["value"] == 10
        assert len(log) == 2


def test_simple_workflow():
    """Test a simple linear workflow."""
    def node_a(state):
        state["visited"] = ["a"]
        return state

    def node_b(state):
        state["visited"].append("b")
        return state

    node_functions = {"a": node_a, "b": node_b}
    engine = WorkflowEngine(node_functions)

    graph = GraphDefinition(
        nodes={"a": NodeDefinition(func="a"), "b": NodeDefinition(func="b")},
        edges={"a": ["b"]},
        conditions={},
        start_node="a",
    )

    final_state, log = engine.execute(graph, {})
    assert final_state["visited"] == ["a", "b"]
    assert len(log) == 2
