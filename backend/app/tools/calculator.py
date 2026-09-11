"""Safe calculator (AST-restricted evaluator, no eval/exec/imports)."""
from __future__ import annotations

import ast
import math
import operator

from .base import Tool, ToolContext, ToolResult

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin,
    "acos": math.acos, "atan": math.atan, "floor": math.floor, "ceil": math.ceil,
    "abs": abs, "min": min, "max": max, "round": round, "pow": pow,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e9):
            raise ValueError("exponent/operand too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ValueError(f"disallowed expression element: {ast.dump(node)[:80]}")


def safe_eval(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


class CalculatorTool(Tool):
    key = "calculator"
    display_name = "Calculator"
    description = (
        "Evaluate an arithmetic/mathematical expression exactly. Supports + - * / // % **, "
        "parentheses, pi/e and functions sqrt, log, log10, exp, sin/cos/tan, floor, ceil, abs, round."
    )
    category = "mathematics"
    enabled_by_default = True
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. 'sqrt(2)*pi + log10(1000)'"},
        },
        "required": ["expression"],
    }

    def available(self) -> bool:
        return True

    async def run(self, arguments, ctx: ToolContext) -> ToolResult:
        expr = str(arguments.get("expression", "")).strip()
        if not expr:
            return ToolResult(ok=False, error="empty expression")
        if len(expr) > 1000:
            return ToolResult(ok=False, error="expression too long")
        try:
            value = safe_eval(expr)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, error=f"could not evaluate: {exc}")
        return ToolResult(ok=True, output=f"{expr} = {value:g}", data={"value": value})


class DateTimeTool(Tool):
    key = "datetime"
    display_name = "Current date/time"
    description = "Return the current UTC date and time. Call whenever the question depends on 'now' or today."
    category = "general"
    enabled_by_default = True
    parameters = {"type": "object", "properties": {}}

    def available(self) -> bool:
        return True

    async def run(self, arguments, ctx: ToolContext) -> ToolResult:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return ToolResult(ok=True, output=now.strftime("%Y-%m-%d %H:%M:%S UTC (%A)"))
