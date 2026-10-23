"""Message and memory primitives."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Message:
    role: str
    content: str = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    def to_api_dict(self):
        data: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            data["name"] = self.name
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        return data


@dataclass
class ConversationMemory:
    system_prompt: str
    max_messages: int = 20
    _messages: List[Message] = field(default_factory=list)

    def add(self, message: Message):
        self._messages.append(message)
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]

    def add_user(self, content: str):
        self.add(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls=None):
        self.add(
            Message(
                role="assistant",
                content=content or "",
                tool_calls=tool_calls,
            )
        )

    def add_tool_result(self, tool_call_id: str, content: str):
        self.add(
            Message(
                role="tool",
                content=content,
                tool_call_id=tool_call_id,
            )
        )

    def clear(self):
        self._messages = []

    def snapshot(self):
        return [
            Message(role="system", content=self.system_prompt)
        ] + list(self._messages)

    def __len__(self):
        return len(self._messages)
