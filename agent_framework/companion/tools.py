"""Mode-aware tool registry for the companion runtime."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from .models import ActionProposal, RiskLevel
from .policy import ASK, DENY, decide_tool_policy


ToolHandler = Callable[[Dict[str, Any]], "ToolResult"]


class ToolTimeout(RuntimeError):
    pass


def _run_with_timeout(handler, arguments, timeout):
    if not timeout or timeout <= 0:
        return handler(arguments)
    outcome = queue.Queue(maxsize=1)
    state = {"done": False}

    def worker():
        try:
            value = handler(arguments)
            state["done"] = True
            outcome.put((True, value))
        except BaseException as error:
            state["done"] = True
            outcome.put((False, error))

    thread = threading.Thread(
        target=worker,
        name="companion-tool",
        daemon=True,
    )
    thread.start()
    try:
        ok, value = outcome.get(timeout=timeout)
    except queue.Empty:
        if state["done"]:
            try:
                ok, value = outcome.get_nowait()
            except queue.Empty:
                ok, value = False, RuntimeError("工具结果未送达队列。")
        else:
            raise ToolTimeout()
    if not ok:
        raise value
    return value


@dataclass
class ToolResult:
    ok: bool
    content: str
    proposal: Optional[ActionProposal] = None
    code: str = ""
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def success(cls, content, proposal=None, data=None):
        return cls(
            ok=True,
            content=content,
            proposal=proposal,
            data=data,
        )

    @classmethod
    def failure(cls, content, code="tool_error", data=None):
        return cls(ok=False, content=content, code=code, data=data)


@dataclass
class CompanionTool:
    name: str
    description: str
    parameters: Dict[str, Any]
    risk: RiskLevel
    handler: ToolHandler
    timeout: float = 10.0

    def as_openai_tool(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class CompanionToolRegistry:
    """Filters tools by mode and enforces the confirmation invariant."""

    def __init__(self, tools=None, policy_tier=None):
        self.policy_tier = policy_tier
        self._tools: Dict[str, CompanionTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: CompanionTool):
        if tool.name in self._tools:
            raise ValueError("工具已存在：{}".format(tool.name))
        self._tools[tool.name] = tool
        return self

    def names(self) -> List[str]:
        return sorted(self._tools)

    def risks(self) -> Dict[str, RiskLevel]:
        return {name: tool.risk for name, tool in self._tools.items()}

    def available(self, mode) -> List[CompanionTool]:
        return [
            self._tools[name]
            for name in self.names()
            if decide_tool_policy(mode, self._tools[name].risk, self.policy_tier) != DENY
        ]

    def as_openai_tools(self, mode) -> List[Dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self.available(mode)]

    def execute(self, name, arguments, mode) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                "未知工具：{}".format(name),
                "unknown_tool",
            )
        decision = decide_tool_policy(mode, tool.risk, self.policy_tier)
        if decision == DENY:
            return ToolResult.failure(
                "当前模式或权限档位不允许使用工具：{}".format(name),
                "mode_denied",
            )
        if not isinstance(arguments, dict):
            return ToolResult.failure(
                "工具参数必须是 JSON 对象。",
                "invalid_arguments",
            )
        try:
            result = _run_with_timeout(tool.handler, arguments, tool.timeout)
        except ToolTimeout:
            return ToolResult.failure(
                "工具 {} 执行超时（超过 {:.1f} 秒）。".format(
                    name,
                    tool.timeout,
                ),
                "tool_timeout",
            )
        except Exception as error:
            return ToolResult.failure(
                "工具 {} 执行失败：{}".format(name, error),
                "tool_error",
            )
        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                "工具 {} 返回了无效结果。".format(name),
                "invalid_result",
            )
        if decision == ASK and result.ok and result.proposal is None:
            return ToolResult.failure(
                "该操作需要用户确认，但工具没有生成提案。",
                "confirmation_missing",
            )
        return result
