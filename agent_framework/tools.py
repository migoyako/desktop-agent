"""Tool registration and built-in tools."""

import ast
from dataclasses import dataclass
from datetime import datetime
import json
import math
import operator
from typing import Any, Callable, Dict, List, Optional


ToolHandler = Callable[..., str]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def as_openai_tool(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec):
        if tool.name in self._tools:
            raise ValueError("工具已存在: {}".format(tool.name))
        self._tools[tool.name] = tool
        return self

    def as_openai_tools(self) -> List[Dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self._tools.values()]

    def names(self):
        return sorted(self._tools)

    def execute(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        if name not in self._tools:
            return "错误：未知工具 {}".format(name)
        try:
            result = self._tools[name].handler(**(arguments or {}))
            return str(result)
        except Exception as error:
            return "工具 {} 执行失败：{}".format(name, error)


_ALLOWED_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_ALLOWED_CONSTANTS = {"pi": math.pi, "e": math.e}


def _eval_expression(node):
    if isinstance(node, ast.Expression):
        return _eval_expression(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and node.id in _ALLOWED_CONSTANTS:
        return _ALLOWED_CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY_OPERATORS:
        left = _eval_expression(node.left)
        right = _eval_expression(node.right)
        return _ALLOWED_BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY_OPERATORS:
        return _ALLOWED_UNARY_OPERATORS[type(node.op)](
            _eval_expression(node.operand)
        )
    raise ValueError("表达式包含不支持的语法")


def calculate(expression: str) -> str:
    """Safely evaluate a small arithmetic expression."""
    tree = ast.parse(expression, mode="eval")
    return str(_eval_expression(tree))


def current_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def echo_text(text: str) -> str:
    return text


def build_default_tools():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="calculate",
            description="计算加减乘除、取余和幂等简单数学表达式。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "例如 12 * (3 + 4)",
                    }
                },
                "required": ["expression"],
            },
            handler=calculate,
        )
    )
    registry.register(
        ToolSpec(
            name="current_time",
            description="获取当前本地日期和时间。",
            parameters={"type": "object", "properties": {}},
            handler=lambda: current_time(),
        )
    )
    registry.register(
        ToolSpec(
            name="echo_text",
            description="原样返回文本，便于测试工具调用。",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_text,
        )
    )
    return registry
