# nodes.py
import ast
from typing import Dict, Any, List
from tools import TOOLS  # ensure this module exists and exposes required helpers


def extract_functions(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Robustly extract function definitions from state['code'].
    - Prefer AST parsing (FunctionDef / AsyncFunctionDef).
    - If AST finds nothing, fall back to a simple regex to detect `def name(` lines.
    - Return state with 'functions' list of dicts: {name, source, line_start}.
    """
    import re

    code = state.get("code", "") or ""
    functions: List[Dict[str, Any]] = []

    if not isinstance(code, str) or code.strip() == "":
        state["functions"] = functions
        return state

    # 1) Try AST-based extraction
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, getattr(ast, "AsyncFunctionDef", type(None)))):
                # try to get source segment if possible
                source = ""
                try:
                    source = ast.get_source_segment(code, node) or ""
                except Exception:
                    if hasattr(node, "lineno") and hasattr(node, "end_lineno"):
                        lines = code.splitlines()
                        start = max(0, node.lineno - 1)
                        end = node.end_lineno
                        source = "\n".join(lines[start:end])
                    else:
                        source = ""

                functions.append({
                    "name": getattr(node, "name", "<lambda>"),
                    "source": source,
                    "line_start": getattr(node, "lineno", None),
                })
    except Exception:
        # If AST parsing fails unexpectedly, continue to regex fallback below
        functions = []

    # 2) Regex fallback if AST found nothing
    if not functions:
        # simple regex to find "def <name>(...):"
        pattern = re.compile(r'^\s*def\s+([A-Za-z_]\w*)\s*\(', re.MULTILINE)
        matches = list(pattern.finditer(code))
        if matches:
            lines = code.splitlines()
            for m in matches:
                name = m.group(1)
                start_pos = m.start()
                line_no = code.count("\n", 0, start_pos) + 1
                src_lines = []
                for i in range(line_no - 1, len(lines)):
                    src_lines.append(lines[i])
                    if lines[i].strip() == "":
                        break
                source = "\n".join(src_lines).rstrip("\n")
                functions.append({
                    "name": name,
                    "source": source,
                    "line_start": line_no,
                })

    state["functions"] = functions
    return state






def check_complexity(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compute complexity score for each function."""
    functions = state.get("functions", [])

    for func in functions:
        count = 1 + TOOLS["count_control_flow"](func.get("source", ""))
        func["complexity_score"] = count

    return state


def detect_issues(state: Dict[str, Any]) -> Dict[str, Any]:
    """Detect code issues: long lines, TODOs, missing docstrings, too many params."""
    functions = state.get("functions", [])
    issues = []

    for func in functions:
        func_issues = []

        src = func.get("source", "")

        # Long lines
        long_lines = TOOLS["detect_long_lines"](src, 100)
        if long_lines:
            func_issues.append(f"Long lines: {long_lines}")

        # TODOs
        todos = TOOLS["detect_todos"](src)
        if todos:
            func_issues.append(f"TODO comments: {todos}")

        # Missing docstring
        if TOOLS["detect_missing_docstrings"](src):
            func_issues.append("Missing docstring")

        # Too many parameters
        params = TOOLS["count_parameters"](src)
        if params > 5:
            func_issues.append(f"Too many parameters: {params}")

        func["issues"] = func_issues
        issues.extend([(func["name"], issue) for issue in func_issues])

    state["issues"] = issues
    return state


def suggest_improvements(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate suggestions and compute quality score."""
    functions = state.get("functions", [])
    suggestions = []
    total_issues = len(state.get("issues", []))

    for func in functions:
        func_suggestions = []

        if func.get("complexity_score", 0) > 10:
            func_suggestions.append("Break function into smaller pieces (high complexity)")

        if func.get("issues"):
            func_suggestions.append(f"Address {len(func['issues'])} code issues")

        func["suggestions"] = func_suggestions
        suggestions.append({"function": func["name"], "suggestions": func_suggestions})

    max_possible_issues = len(functions) * 5
    quality_score = 1.0 - (total_issues / max_possible_issues) if max_possible_issues > 0 else 1.0
    quality_score = max(0.0, min(1.0, quality_score))

    state["quality_score"] = quality_score
    state["suggestions"] = suggestions
    state["iterations"] = state.get("iterations", 0) + 1

    return state


def end_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final node - returns the state as-is."""
    return state
