"""Restricted table formulas over named, immutable columns; no Python evaluation."""

import ast
import operator
from collections.abc import Callable

import numpy as np

from .formulas import BINARY, FUNCTIONS

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
    "ifelse": 3,
    "coalesce": 2,
    "mean": 1,
    "median": 1,
    "sum": 1,
    "sd": 1,
    "n": 1,
    "ceil": 1,
    "floor": 1,
    "sin": 1,
    "cos": 1,
    "tan": 1,
    "sinh": 1,
    "cosh": 1,
    "tanh": 1,
    "asin": 1,
    "acos": 1,
    "atan": 1,
}
TABLE_FUNCTIONS = {
    **FUNCTIONS,
    **{
        name: getattr(np, name)
        for name in ("ceil", "floor", "sin", "cos", "tan", "sinh", "cosh", "tanh")
    },
    "asin": np.arcsin,
    "acos": np.arccos,
    "atan": np.arctan,
}
COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def parse_table_formula(expression):
    if not expression or len(expression) > 2048:
        raise ValueError("Table formulas require 1–2048 characters")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid table formula syntax") from exc
    if len(list(ast.walk(tree))) > 200:
        raise ValueError("Table formula is too complex")
    references = set()

    def literal(node):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or not node.value:
            raise ValueError("Column and control references require quoted names")
        return node.value

    def validate(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool, int, float)):
            if isinstance(node.value, (int, float)):
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
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
            validate(node.operand)
            return
        if isinstance(node, ast.Compare) and all(type(op) in COMPARE for op in node.ops):
            for operand in [node.left, *node.comparators]:
                validate(operand)
            return
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for operand in node.values:
                validate(operand)
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name in {"row", "offset"} and len(node.args) == 2:
                references.add(literal(node.args[0]))
                index = node.args[1]
                sign = (
                    -1 if isinstance(index, ast.UnaryOp) and isinstance(index.op, ast.USub) else 1
                )
                if isinstance(index, ast.UnaryOp) and isinstance(index.op, (ast.UAdd, ast.USub)):
                    index = index.operand
                if (
                    not isinstance(index, ast.Constant)
                    or type(index.value) is not int
                    or not -50000 <= sign * index.value <= 50000
                    or (name == "row" and sign * index.value < 1)
                ):
                    raise ValueError(
                        "Row references require a bounded literal integer; rows start at one"
                    )
                return
            if name in {"col", "control"} and len(node.args) == (1 if name == "col" else 2):
                references.add(literal(node.args[0]))
                if name == "control":
                    literal(node.args[1])
                return
            if (name in ARITY and len(node.args) == ARITY[name]) or (
                name in {"min", "max"} and 2 <= len(node.args) <= 32
            ):
                for arg in node.args:
                    validate(arg)
                return
        raise ValueError(
            "Table formulas allow arithmetic, comparisons, columns and listed functions"
        )

    validate(tree.body)
    return tree, references


def numeric(values):
    try:
        return np.asarray(values, dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError("Use a numeric metadata column for arithmetic or aggregation") from exc


def evaluate_table_formula(
    expression: str, column: Callable, control: Callable, count: int
) -> np.ndarray:
    tree, _ = parse_table_formula(expression)

    def compute(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            return BINARY[type(node.op)](numeric(compute(node.left)), numeric(compute(node.right)))
        if isinstance(node, ast.UnaryOp):
            value = compute(node.operand)
            if isinstance(node.op, ast.Not):
                return np.logical_not(value)
            return -numeric(value) if isinstance(node.op, ast.USub) else numeric(value)
        if isinstance(node, ast.Compare):
            left = compute(node.left)
            result = True
            for op, right_node in zip(node.ops, node.comparators, strict=True):
                right = compute(right_node)
                result = np.logical_and(result, COMPARE[type(op)](left, right))
                left = right
            return result
        if isinstance(node, ast.BoolOp):
            result = compute(node.values[0])
            for child in node.values[1:]:
                result = (np.logical_and if isinstance(node.op, ast.And) else np.logical_or)(
                    result, compute(child)
                )
            return result
        name = node.func.id
        if name == "col":
            return column(node.args[0].value)
        if name == "control":
            return control(node.args[0].value, node.args[1].value)
        if name in {"row", "offset"}:
            values = numeric(column(node.args[0].value))
            index = int(compute(node.args[1]))
            if name == "row":
                return values[index - 1] if index <= len(values) else np.nan
            result = np.full(count, np.nan)
            positions = np.arange(count) + index
            valid = (positions >= 0) & (positions < len(values))
            result[valid] = values[positions[valid]]
            return result
        args = [compute(child) for child in node.args]
        if name == "ifelse":
            return np.where(args[0], args[1], args[2])
        if name == "coalesce":
            return np.where(np.isfinite(numeric(args[0])), args[0], args[1])
        if name in {"mean", "median", "sum", "sd", "n"}:
            values = numeric(args[0])
            values = values[np.isfinite(values)]
            if name == "n":
                return len(values)
            if not len(values) or (name == "sd" and len(values) < 2):
                return np.nan
            # Normalize to avoid overflow when a finite average/SD is representable.
            scale = max(float(np.abs(values).max()), 1e-300)
            if name == "sd":
                return np.std(values / scale, ddof=1) * scale
            function = {"mean": np.mean, "median": np.median, "sum": np.sum}[name]
            return function(values / scale) * scale
        if name in {"min", "max"}:
            function = np.minimum if name == "min" else np.maximum
            result = numeric(args[0])
            for value in args[1:]:
                result = function(result, numeric(value))
            return result
        return TABLE_FUNCTIONS[name](*[numeric(value) for value in args])

    with np.errstate(all="ignore"):
        result = numeric(compute(tree.body))
    if result.ndim == 0:
        result = np.full(count, result.item())
    if result.shape != (count,):
        raise ValueError("A table formula must produce one numeric value per row")
    return np.where(np.isfinite(result), result, np.nan)
