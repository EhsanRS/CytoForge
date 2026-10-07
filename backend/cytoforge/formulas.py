"""Small vectorized expression language. Never evaluates Python code."""

import ast
import operator
from collections.abc import Callable

import numpy as np

FUNCTIONS = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "log10": np.log10,
    "exp": np.exp,
    "asinh": np.arcsinh,
    "min": np.minimum,
    "max": np.maximum,
    "clip": np.clip,
}
ARITY = {
    "abs": 1,
    "sqrt": 1,
    "log": 1,
    "log10": 1,
    "exp": 1,
    "asinh": 1,
    "min": 2,
    "max": 2,
    "clip": 3,
    "ch": 1,
}
BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: np.divide,
    ast.Pow: np.power,
}


def parse(expression: str) -> tuple[ast.Expression, set[str]]:
    if len(expression) > 1024:
        raise ValueError("Formula exceeds 1024 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid formula syntax") from exc
    if len(list(ast.walk(tree))) > 160:
        raise ValueError("Formula is too complex")
    dependencies: set[str] = set()

    def validate(node):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            try:
                finite = np.isfinite(float(node.value))
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("Formula constants must be finite")
            return
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            validate(node.left)
            validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            validate(node.operand)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name not in ARITY or len(node.args) != ARITY[name]:
                raise ValueError("Unknown function or wrong number of arguments")
            if name == "ch":
                if not isinstance(node.args[0], ast.Constant) or not isinstance(
                    node.args[0].value, str
                ):
                    raise ValueError('Channel references use ch("Channel name")')
                dependencies.add(node.args[0].value)
            else:
                for arg in node.args:
                    validate(arg)
            return
        raise ValueError(
            "Formulas allow arithmetic, channel references, and documented functions only"
        )

    validate(tree.body)
    return tree, dependencies


def evaluate(expression: str, channel: Callable[[str], np.ndarray], count: int) -> np.ndarray:
    tree, _ = parse(expression)

    def compute(node):
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            return BINARY[type(node.op)](compute(node.left), compute(node.right))
        if isinstance(node, ast.UnaryOp):
            value = compute(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if node.func.id == "ch":
            return channel(node.args[0].value)
        return FUNCTIONS[node.func.id](*[compute(arg) for arg in node.args])

    with np.errstate(all="ignore"):
        result = np.asarray(compute(tree.body), dtype=float)
    if result.ndim == 0:
        result = np.full(count, result.item())
    if result.shape != (count,):
        raise ValueError("Formula must produce one value per event")
    return np.where(np.isfinite(result), result, np.nan)
