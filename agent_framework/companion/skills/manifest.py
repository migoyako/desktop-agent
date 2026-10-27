"""Strict parser for the small YAML subset allowed in SKILL.md."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple

from ..models import AgentMode


EFFECT_KINDS = ("read", "network", "write", "external")
LIST_FIELDS = ("tools", "modes", "references")
REQUIRED_FIELDS = ("name", "description", "effectKind")
ALL_FIELDS = (
    "name",
    "description",
    "tools",
    "effectKind",
    "modes",
    "references",
)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

MAX_DESCRIPTION_CHARS = 240
MAX_BODY_CHARS = 8000
MAX_SKILL_FILE_BYTES = 128 * 1024
MAX_REFERENCE_BYTES = 256 * 1024


class SkillManifestError(ValueError):
    def __init__(self, message, code="invalid_skill"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    tools: Tuple[str, ...]
    effect_kind: str
    modes: Optional[Tuple[str, ...]]
    references: Tuple[str, ...]
    body: str


def _parse_scalar(value, field):
    value = value.strip()
    if not value:
        raise SkillManifestError(
            "字段 {} 不能为空。".format(field),
            "invalid_manifest",
        )
    if value[0] in ("'", '"'):
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise SkillManifestError(
                "字段 {} 的引号没有闭合。".format(field),
                "invalid_manifest",
            )
        return value[1:-1].strip()
    return value


def _parse_inline_list(value, field):
    value = value.strip()
    if not value:
        return []
    items = []
    current = []
    quote = None
    for char in value:
        if quote is not None:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if quote is not None:
        raise SkillManifestError(
            "字段 {} 的引号没有闭合。".format(field),
            "invalid_manifest",
        )
    items.append("".join(current).strip())
    if any(not item for item in items):
        raise SkillManifestError(
            "字段 {} 不能包含空项。".format(field),
            "invalid_manifest",
        )
    return items


def _parse_frontmatter(lines):
    fields: Dict[str, object] = {}
    current_list = None
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace():
            if current_list is None:
                raise SkillManifestError(
                    "只支持顶层字段与列表项。",
                    "invalid_manifest",
                )
            if not stripped.startswith("- "):
                raise SkillManifestError(
                    "列表项必须以 '- ' 开头。",
                    "invalid_manifest",
                )
            fields[current_list].append(
                _parse_scalar(stripped[2:], current_list)
            )
            continue
        if ":" not in line:
            raise SkillManifestError(
                "无法解析的 frontmatter 行：{}".format(stripped),
                "invalid_manifest",
            )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if key not in ALL_FIELDS:
            raise SkillManifestError(
                "未知字段：{}".format(key),
                "invalid_manifest",
            )
        if key in fields:
            raise SkillManifestError(
                "字段重复：{}".format(key),
                "invalid_manifest",
            )
        current_list = None
        if key in LIST_FIELDS:
            if not value:
                fields[key] = []
                current_list = key
            elif value.startswith("["):
                if not value.endswith("]"):
                    raise SkillManifestError(
                        "字段 {} 的列表没有闭合。".format(key),
                        "invalid_manifest",
                    )
                fields[key] = _parse_inline_list(value[1:-1], key)
            else:
                fields[key] = _parse_inline_list(value, key)
        else:
            fields[key] = _parse_scalar(value, key)
    return fields


def _dedupe(values, label):
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    if len(result) > 64:
        raise SkillManifestError(
            "{} 项过多。".format(label),
            "invalid_manifest",
        )
    return tuple(result)


def parse_manifest(skill_id, text):
    if not NAME_RE.match(skill_id or ""):
        raise SkillManifestError(
            "技能目录名只能是小写字母、数字、下划线或短横线。",
            "invalid_skill_id",
        )
    text = (text or "").lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillManifestError(
            "SKILL.md 必须以 --- 开头的 frontmatter。",
            "invalid_manifest",
        )
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        raise SkillManifestError(
            "SKILL.md 的 frontmatter 没有闭合。",
            "invalid_manifest",
        )
    fields = _parse_frontmatter(lines[1:closing])
    for required in REQUIRED_FIELDS:
        if required not in fields:
            raise SkillManifestError(
                "缺少字段：{}".format(required),
                "invalid_manifest",
            )
    name = str(fields["name"])
    if not NAME_RE.match(name):
        raise SkillManifestError(
            "技能 name 只能是小写字母、数字、下划线或短横线。",
            "invalid_manifest",
        )
    if name != skill_id:
        raise SkillManifestError(
            "技能 name 必须与目录名一致。",
            "invalid_manifest",
        )
    description = str(fields["description"]).strip()
    if not description:
        raise SkillManifestError(
            "技能描述不能为空。",
            "invalid_manifest",
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillManifestError(
            "技能描述超过 {} 字符。".format(MAX_DESCRIPTION_CHARS),
            "invalid_manifest",
        )
    effect_kind = str(fields["effectKind"]).strip()
    if effect_kind not in EFFECT_KINDS:
        raise SkillManifestError(
            "effectKind 必须是 {}。".format(" / ".join(EFFECT_KINDS)),
            "invalid_manifest",
        )
    tools = _dedupe(fields.get("tools") or [], "tools")
    for tool in tools:
        if not TOOL_RE.match(tool):
            raise SkillManifestError(
                "工具名不合法：{}".format(tool),
                "invalid_manifest",
            )
    references = _dedupe(fields.get("references") or [], "references")
    for reference in references:
        if not REFERENCE_RE.match(reference) or "/" in reference:
            raise SkillManifestError(
                "参考文件必须是 references/ 下的单个文件名。",
                "invalid_reference",
            )
        lowered = reference.lower()
        if not (lowered.endswith(".md") or lowered.endswith(".txt")):
            raise SkillManifestError(
                "参考文件只支持 .md 与 .txt。",
                "invalid_reference",
            )
    modes = None
    if "modes" in fields:
        raw_modes = _dedupe(fields["modes"], "modes")
        if not raw_modes:
            raise SkillManifestError(
                "modes 不能为空；省略时按风险推导。",
                "invalid_manifest",
            )
        valid = {mode.value for mode in AgentMode}
        for mode in raw_modes:
            if mode not in valid:
                raise SkillManifestError(
                    "未知模式：{}".format(mode),
                    "invalid_manifest",
                )
        modes = raw_modes
    body = "\n".join(lines[closing + 1:]).strip()
    if not body:
        raise SkillManifestError(
            "技能正文不能为空。",
            "invalid_manifest",
        )
    if len(body) > MAX_BODY_CHARS:
        raise SkillManifestError(
            "技能正文超过 {} 字符。".format(MAX_BODY_CHARS),
            "invalid_manifest",
        )
    return SkillManifest(
        name=name,
        description=description,
        tools=tools,
        effect_kind=effect_kind,
        modes=modes,
        references=references,
        body=body,
    )
