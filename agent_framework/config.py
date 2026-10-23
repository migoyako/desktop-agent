"""Configuration loading for the agent framework."""

from dataclasses import dataclass
import json
import os


DEFAULT_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "keys.json",
)


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


@dataclass
class AgentConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "qwen-plus"
    temperature: float = 0.7
    max_tool_rounds: int = 4
    max_history_messages: int = 20
    offline: bool = False

    @classmethod
    def from_env(cls, config_file=DEFAULT_CONFIG_FILE):
        values = _read_json(config_file)
        api_key = (
            values.get("OPENAI_API_KEY")
            or values.get("DATABASE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DATABASE_API_KEY")
            or ""
        )
        base_url = (
            values.get("OPENAI_BASE_URL")
            or values.get("DATABASE_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("DATABASE_BASE_URL")
            or ""
        )
        model = (
            values.get("MODEL_NAME")
            or os.getenv("AGENT_MODEL")
            or os.getenv("MODEL_NAME")
            or "qwen-plus"
        )
        return cls(api_key=api_key, base_url=base_url, model=model)

    @property
    def can_use_remote_llm(self):
        return bool(self.api_key and self.base_url)
