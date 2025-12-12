# condition_evaluator.py
import ast
from typing import Dict, Any, Optional


class SafeConditionEvaluator:
    """
    Safely evaluate small condition expressions like "state['x'] > 5" or
    "state.get('k', 0) < 5". Only a very small, explicit subset of AST nodes is allowed.
    """

    ALLOWED_COMPARE_OPS = (ast.Gt, ast.Lt, ast.GtE, ast.LtE, ast.Eq, ast.NotEq)
    ALLOWED_BOOLEAN_OPS = (ast.And, ast.Or)
    ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
    ALLOWED_UNARYOPS = (ast.USub, ast.UAdd, ast.Not)

    # ------------------------------------------------------------
    # CONSTANT DETECTION (robust across Python versions)
    # ------------------------------------------------------------
    @staticmethod
    def _is_constant_node(node: Optional[ast.AST]) -> bool:
        """
        Returns True for literal constants (string, number, boolean, None).
        Supports all Python AST variations.
        """
        if node is None:
            return False

        if isinstance(node, ast.Constant):
            return True

        # Legacy Python < 3.8 types (Num/Str/NameConstant)
        Num = getattr(ast, "Num", None)
        Str = getattr(ast, "Str", None)
        NameConstant = getattr(ast, "NameConstant", None)
        if Num and isinstance(node, Num):
            return True
        if Str and isinstance(node, Str):
            return True
        if NameConstant and isinstance(node, NameConstant):
            return True

        # Allow unary constants like -1
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return SafeConditionEvaluator._is_constant_node(node.operand)

        return False

    # ------------------------------------------------------------
    # UNIVERSAL NODE UNWRAPPER (Subscript.slice + Call args)
    # ------------------------------------------------------------
    @staticmethod
    def _unwrap(node: ast.AST) -> ast.AST:
        """
        Removes wrapper nodes like ast.Index, ast.Slice; unwraps .value or .slice.
        Normalizes AST across Python versions, returning an AST node (not a raw Python value).
        """
        # ast.Index(value=...) wrapper (older ASTs)
        if node.__class__.__name__ == "Index" and hasattr(node, "value"):
            return node.value

        # ast.Slice(lower=..., upper=...) - try to return relevant part if present
        if node.__class__.__name__ == "Slice":
            # prefer .lower if it's present and not None
            if hasattr(node, "lower") and node.lower is not None:
                return node.lower
            if hasattr(node, "value") and node.value is not None:
                return node.value
            return node

        # If node is a Constant already, keep it
        if isinstance(node, ast.Constant):
            return node

        return node

    @staticmethod
    def _get_subscript_key(sub: ast.Subscript) -> Optional[ast.AST]:
        """
        Extracts key in state['x'], handling old & new AST formats.
        Always returns an AST node (or None), never a raw Python value.
        """
        slice_obj = getattr(sub, "slice", None)
        if slice_obj is None:
            return None

        # If the slice itself is a Constant node (py3.9+), return it
        if isinstance(slice_obj, ast.Constant):
            return slice_obj

        # Unwrap common wrappers (Index, Slice, etc.)
        inner = SafeConditionEvaluator._unwrap(slice_obj)

        # If inner is a Constant AST node, return it directly
        if isinstance(inner, ast.Constant):
            return inner

        # Some variants use .value on wrapper nodes (avoid returning raw value)
        # If inner has attribute 'value' and that .value is an AST node, return it.
        if hasattr(inner, "value") and isinstance(getattr(inner, "value"), ast.AST):
            return getattr(inner, "value")

        # If inner has attribute 'slice' and that is an AST node, return it.
        if hasattr(inner, "slice") and isinstance(getattr(inner, "slice"), ast.AST):
            return getattr(inner, "slice")

        # As a last attempt, if inner itself is an AST node, return it
        if isinstance(inner, ast.AST):
            return inner

        # Could not extract AST node for key
        return None

    # ------------------------------------------------------------
    # AST VALIDATION
    # ------------------------------------------------------------
    @staticmethod
    def _validate_ast(node: ast.AST) -> None:
        """Recursively validate AST node types and structure."""

        # Boolean operations
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, SafeConditionEvaluator.ALLOWED_BOOLEAN_OPS):
                raise TypeError("Boolean operator not allowed")
            for v in node.values:
                SafeConditionEvaluator._validate_ast(v)
            return

        # Binary operations
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, SafeConditionEvaluator.ALLOWED_BINOPS):
                raise TypeError("Binary operator not allowed")
            SafeConditionEvaluator._validate_ast(node.left)
            SafeConditionEvaluator._validate_ast(node.right)
            return

        # Unary operations
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, SafeConditionEvaluator.ALLOWED_UNARYOPS):
                raise TypeError("Unary operator not allowed")
            SafeConditionEvaluator._validate_ast(node.operand)
            return

        # Comparisons
        if isinstance(node, ast.Compare):
            SafeConditionEvaluator._validate_ast(node.left)
            for comp in node.comparators:
                SafeConditionEvaluator._validate_ast(comp)
            for op in node.ops:
                if not isinstance(op, SafeConditionEvaluator.ALLOWED_COMPARE_OPS):
                    raise TypeError("Comparison operator not allowed")
            return

        # Subscript: state['x']
        if isinstance(node, ast.Subscript):
            # Must be state [...]
            if not isinstance(node.value, ast.Name) or node.value.id != "state":
                raise TypeError("Only state[...] is allowed")

            key_node = SafeConditionEvaluator._get_subscript_key(node)

            if key_node is None or not SafeConditionEvaluator._is_constant_node(key_node):
                raise TypeError("State subscript must be a constant key, e.g. state['x']")

            return

        # state.get('k', default)
        if isinstance(node, ast.Call):
            func = node.func

            # Must be state.get
            if not isinstance(func, ast.Attribute):
                raise TypeError("Only state.get(...) calls are allowed")
            if not isinstance(func.value, ast.Name) or func.value.id != "state":
                raise TypeError("Only state.get(...) calls are allowed")
            if func.attr != "get":
                raise TypeError("Only state.get(...) calls are allowed")

            if node.keywords:
                raise TypeError("Keyword arguments not allowed in state.get(...)")

            if len(node.args) not in (1, 2):
                raise TypeError("state.get expects 1 or 2 args")

            # Args must be constants (AST nodes)
            for arg in node.args:
                arg_node = SafeConditionEvaluator._unwrap(arg)
                # If arg_node is a wrapper with .value that is an AST node, use that
                if hasattr(arg_node, "value") and isinstance(getattr(arg_node, "value"), ast.AST):
                    arg_node = getattr(arg_node, "value")
                if not SafeConditionEvaluator._is_constant_node(arg_node):
                    raise TypeError("Arguments to state.get(...) must be constants")
            return

        # Allowed name: only "state"
        if isinstance(node, ast.Name):
            if node.id != "state":
                raise TypeError("Only 'state' may appear in conditions")
            return

        # Constant literals
        if SafeConditionEvaluator._is_constant_node(node):
            return

        raise TypeError(f"Disallowed AST node: {type(node).__name__}")

    # ------------------------------------------------------------
    # EVALUATION
    # ------------------------------------------------------------
    @staticmethod
    def evaluate(condition: str, state: Dict[str, Any]) -> bool:
        """
        Safely evaluate condition with no builtins, only 'state' in scope.
        """
        try:
            tree = ast.parse(condition, mode="eval")
            SafeConditionEvaluator._validate_ast(tree.body)
            return bool(
                eval(
                    compile(tree, "<condition>", "eval"),
                    {"__builtins__": {}},
                    {"state": state},
                )
            )
        except Exception as e:
            raise ValueError(f"Invalid or unsafe condition: {condition}. Error: {e}")
