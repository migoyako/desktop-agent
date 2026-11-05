"""Configuration loading for the agent framework."""

from dataclasses import dataclass
import json
import os


DEFAULT_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "keys.json",
)

# Tests and a second data directory need a different credential file without
# rebuilding the whole app; the default stays the repo-relative path.
CONFIG_PATH_ENV = "COMPANION_KEYS_FILE"


def config_path():
    return os.getenv(CONFIG_PATH_ENV) or DEFAULT_CONFIG_FILE


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_config(values, path=None):
    """Merge ``values`` into the credential file.

    Only the keys that are given are touched, so hand-written entries survive;
    an empty value removes that key. The file holds the API key, so it is
    replaced atomically and its contents are never returned or logged here.
    """
    target = path or config_path()
    try:
        current = _read_json(target)
    except (ValueError, OSError):
        current = {}
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in dict(values or {}).items():
        text = "" if value is None else str(value).strip()
        if text:
            merged[str(key)] = text
        else:
            merged.pop(str(key), None)
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temp = "{}.tmp".format(target)
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(merged, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, target)
    return sorted(merged)


@dataclass
class AgentConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "qwen-plus"
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    max_tool_rounds: int = 4
    max_history_messages: int = 20
    offline: bool = False

    @classmethod
    def from_env(cls, config_file=None):
        values = _read_json(config_file or config_path())
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
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            top_p=_number(values, os.getenv("AGENT_TOP_P"), "TOP_P", 1.0),
            max_tokens=int(
                _number(values, os.getenv("AGENT_MAX_TOKENS"), "MAX_TOKENS", 4096)
            ),
        )

    @property
    def can_use_remote_llm(self):
        return bool(self.api_key and self.base_url)


def _number(values, env_value, key, fallback):
    raw = values.get(key)
    if raw is None:
        raw = env_value
    if raw is None:
        return fallback
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback
