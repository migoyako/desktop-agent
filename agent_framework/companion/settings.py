"""Application settings shared by every front end.

The row is a single JSON blob so adding a field never needs a migration; the
field table below is the only place that knows types, ranges and defaults.
Secrets stay out of this store — the API key lives in ``config/keys.json`` and
only a masked form is ever handed to a front end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class FieldSpec:
    key: str
    group: str
    kind: str
    default: Any
    minimum: float = 0.0
    maximum: float = 0.0
    choices: Tuple[str, ...] = ()


FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec("model", "model", "str", "qwen-plus"),
    FieldSpec("base_url", "model", "str", ""),
    FieldSpec("temperature", "model", "float", 0.7, 0.0, 2.0),
    FieldSpec("top_p", "model", "float", 1.0, 0.0, 1.0),
    FieldSpec("max_tokens", "model", "int", 4096, 1, 32768),
    FieldSpec("context_tokens", "model", "int", 32000, 4000, 2000000),
    FieldSpec("max_tool_rounds", "model", "int", 3, 1, 8),
    FieldSpec("max_history_messages", "model", "int", 20, 2, 200),
    FieldSpec("offline", "model", "bool", False),
    FieldSpec("model_contexts", "model", "strmap", {}),
    FieldSpec("memory_top_k", "memory", "int", 5, 1, 20),
    FieldSpec("memory_score_threshold", "memory", "float", 0.0, 0.0, 1.0),
    FieldSpec("memory_enabled", "memory", "bool", True),
    # The wording is what the model is told; the keys are what gets stored, so a
    # rename here has to travel with OUTPUT_FORMATS.
    FieldSpec(
        "output_format",
        "memory",
        "str",
        "default",
        choices=("default", "concise", "bullets", "detailed"),
    ),
    FieldSpec(
        "tool_permission",
        "advanced",
        "str",
        "balanced",
        choices=("strict", "balanced", "relaxed"),
    ),
)

OUTPUT_FORMATS: Dict[str, str] = {
    "default": "",
    "concise": "尽量用最短的段落回答，先给结论，不复述我的问题。",
    "bullets": "回答以要点列表为主，一条一个信息点；不需要列表时可以正常成段。",
    "detailed": "给出足够详细的说明，包含理由与取舍，必要时举例。",
}

BY_KEY: Dict[str, FieldSpec] = {field.key: field for field in FIELDS}

# Upstream /models replies rarely carry a window size, so the dialog needs a
# built-in table to prefill 上下文窗口. It is a convenience default only: every
# value stays user-editable, and an unknown model must be typed in by hand.
MODEL_WINDOWS: Dict[str, int] = {
    "deepseek-chat": 65536,
    "deepseek-reasoner": 65536,
    "glm-4-plus": 131072,
    "gpt-4o": 131072,
    "gpt-4o-mini": 131072,
    "kimi-k2": 131072,
    "moonshot-v1-128k": 131072,
    "moonshot-v1-32k": 32768,
    "moonshot-v1-8k": 8192,
    "qwen-long": 1000000,
    "qwen-max": 32768,
    "qwen-plus": 131072,
}


def catalog_window(model) -> int:
    """Known context window for ``model``; 0 means the user has to say."""
    name = str(model or "").strip().lower()
    if not name:
        return 0
    if name in MODEL_WINDOWS:
        return MODEL_WINDOWS[name]
    # Versioned ids ("gpt-4o-2024-08-06") inherit their family's window, but a
    # family must not match a longer id ("qwen-max" vs "qwen-max-lite" is fine,
    # "gpt-4o" vs "gpt-4o-mini" must not shadow it).
    best = ""
    for family in MODEL_WINDOWS:
        if not name.startswith(family):
            continue
        if name[len(family)] not in "-._/":
            continue
        if len(family) > len(best):
            best = family
    return MODEL_WINDOWS[best] if best else 0


class SettingError(ValueError):
    """A single field the caller is not allowed to store."""


def defaults() -> Dict[str, Any]:
    result = {}
    for field in FIELDS:
        result[field.key] = _copy(field.default)
    return result


def groups() -> Dict[str, List[str]]:
    view: Dict[str, List[str]] = {}
    for field in FIELDS:
        view.setdefault(field.group, []).append(field.key)
    return view


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _coerce(field: FieldSpec, value: Any, strict: bool) -> Any:
    if field.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if strict:
            raise SettingError("{} 必须是布尔值。".format(field.key))
        return _copy(field.default)
    if field.kind == "int":
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            if strict:
                raise SettingError("{} 必须是整数。".format(field.key))
            return _copy(field.default)
        return _clamp(field, number, strict)
    if field.kind == "float":
        try:
            number = float(value)
        except (TypeError, ValueError):
            if strict:
                raise SettingError("{} 必须是数字。".format(field.key))
            return _copy(field.default)
        return _clamp(field, round(number, 4), strict)
    if field.kind == "str":
        text = "" if value is None else str(value).strip()
        if field.choices:
            # An empty choice is "not set", never a selectable value: both paths
            # resolve it to the default so the stored value matches the picker.
            if not text:
                return _copy(field.default)
            if text not in field.choices:
                if strict:
                    raise SettingError(
                        "{} 只能是：{}。".format(field.key, "、".join(field.choices))
                    )
                return _copy(field.default)
        return text
    if field.kind == "strmap":
        if not isinstance(value, dict):
            if strict:
                raise SettingError("{} 必须是对象。".format(field.key))
            return {}
        clean = {}
        for name, item in value.items():
            try:
                clean[str(name)] = int(round(float(item)))
            except (TypeError, ValueError):
                if strict:
                    raise SettingError(
                        "{} 的 {} 必须是整数。".format(field.key, name)
                    )
        return clean
    raise SettingError("未知的设置类型 {}".format(field.kind))


def _clamp(field: FieldSpec, number, strict: bool):
    if number < field.minimum or number > field.maximum:
        if strict:
            raise SettingError(
                "{} 需要在 {} – {} 之间。".format(
                    field.key, _plain(field.minimum), _plain(field.maximum)
                )
            )
        return max(field.minimum, min(field.maximum, number))
    return number


def _plain(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def normalize(values) -> Dict[str, Any]:
    """Lenient read path: unknown keys drop, bad values fall back to defaults."""
    result = defaults()
    if not isinstance(values, dict):
        return result
    for field in FIELDS:
        if field.key not in values:
            continue
        result[field.key] = _coerce(field, values[field.key], strict=False)
    return result


def validate_update(current, patch) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Strict write path used by the HTTP surface.

    Returns the merged settings plus one error entry per rejected key; a
    partially valid patch still applies the keys that were valid.
    """
    merged = normalize(current)
    errors: List[Dict[str, str]] = []
    if not isinstance(patch, dict):
        return merged, [{"key": "", "error": "请求内容必须是对象。"}]
    for key, value in patch.items():
        field = BY_KEY.get(key)
        if field is None:
            errors.append({"key": str(key), "error": "不支持的设置项。"})
            continue
        try:
            merged[key] = _coerce(field, value, strict=True)
        except SettingError as error:
            errors.append({"key": key, "error": str(error)})
    return merged, errors


def mask_secret(value) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    head = text[:3] if text[2:3] == "-" else text[:2]
    return "{}•••…{}".format(head, text[-4:])
