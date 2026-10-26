"""Stable persona-pack loading for the desktop companion."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional
import uuid

from .textseg import segment_tokens


PERSONA_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PERSONA_FILES = ("persona.json", "persona.md", "facts.md", "worldbook.md")
MAX_FILE_BYTES = 64 * 1024
MAX_PACK_BYTES = 256 * 1024
DEFAULT_PERSONA_ID = "remielle"


class PersonaError(ValueError):
    """Raised when a persona pack is missing or malformed."""


@dataclass(frozen=True)
class WorldbookEntry:
    title: str
    content: str


@dataclass(frozen=True)
class Persona:
    persona_id: str
    name: str
    version: str
    prompt_text: str
    facts_text: str
    metadata: Dict[str, Any]
    stable_hash: str
    worldbook_text: str = ""
    file_sizes: Dict[str, int] = field(default_factory=dict)

    @property
    def stable_prefix(self) -> str:
        return (
            "## 角色人设（稳定层）\n"
            "{}\n\n"
            "## 角色背景事实（稳定层，不是用户记忆，也不是共同经历）\n"
            "{}"
        ).format(self.prompt_text.strip(), self.facts_text.strip()).strip()

    @property
    def worldbook_entries(self) -> List[WorldbookEntry]:
        return parse_worldbook(self.worldbook_text)

    def info(self) -> Dict[str, Any]:
        source = self.metadata.get("source")
        if not isinstance(source, dict):
            source = {}
        license_info = self.metadata.get("license")
        if not isinstance(license_info, dict):
            license_info = {}
        return {
            "id": self.persona_id,
            "name": self.name,
            "version": self.version,
            "stable_hash": self.stable_hash,
            "source": source,
            "license": license_info,
            "worldbook_entries": len(self.worldbook_entries),
            "worldbook_chars": len(self.worldbook_text),
            "files": dict(self.file_sizes),
            "total_bytes": sum(self.file_sizes.values()),
            "loaded": True,
        }


def _read_text(path: Path, label: str) -> str:
    if path.is_symlink():
        raise PersonaError("人设包 {} 不能是符号链接。".format(label))
    if not path.is_file():
        raise PersonaError("人设包缺少 {}。".format(label))
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise PersonaError(
            "人设包 {} 超过 {} 字节上限。".format(label, MAX_FILE_BYTES)
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise PersonaError("人设包 {} 读取失败：{}".format(label, error)) from error


def _load_metadata(path: Path) -> Dict[str, Any]:
    raw = _read_text(path, "persona.json")
    try:
        metadata = json.loads(raw)
    except ValueError as error:
        raise PersonaError("persona.json 不是有效 JSON：{}".format(error)) from error
    if not isinstance(metadata, dict):
        raise PersonaError("persona.json 顶层必须是对象。")
    return metadata


def _stable_hash(
    persona_id: str,
    name: str,
    version: str,
    prompt_text: str,
    facts_text: str,
    metadata: Dict[str, Any],
) -> str:
    source = metadata.get("source")
    if not isinstance(source, dict):
        source = {}
    payload = {
        "id": persona_id,
        "name": name,
        "version": version,
        "canon_cutoff": source.get("canon_cutoff", ""),
        "prompt": prompt_text.strip(),
        "facts": facts_text.strip(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_pack(root: Path, persona_id: str) -> Persona:
    metadata = _load_metadata(root / "persona.json")
    name = metadata.get("name")
    version = metadata.get("version")
    if not isinstance(name, str) or not name.strip():
        raise PersonaError("persona.json 缺少 name。")
    if not isinstance(version, str) or not version.strip():
        raise PersonaError("persona.json 缺少 version。")

    prompt_text = _read_text(root / "persona.md", "persona.md").strip()
    if not prompt_text:
        raise PersonaError("persona.md 不能为空。")
    facts_path = root / "facts.md"
    facts_text = (
        _read_text(facts_path, "facts.md").strip()
        if facts_path.is_file()
        else ""
    )
    worldbook_path = root / "worldbook.md"
    worldbook_text = (
        _read_text(worldbook_path, "worldbook.md").strip()
        if worldbook_path.is_file()
        else ""
    )
    file_sizes = {}
    for filename in PERSONA_FILES:
        path = root / filename
        if path.is_file():
            file_sizes[filename] = path.stat().st_size

    return Persona(
        persona_id=persona_id,
        name=name.strip(),
        version=version.strip(),
        prompt_text=prompt_text,
        facts_text=facts_text,
        metadata=metadata,
        stable_hash=_stable_hash(
            persona_id,
            name.strip(),
            version.strip(),
            prompt_text,
            facts_text,
            metadata,
        ),
        worldbook_text=worldbook_text,
        file_sizes=file_sizes,
    )


def load_persona(
    data_dir,
    persona_id: str = DEFAULT_PERSONA_ID,
) -> Optional[Persona]:
    """Load a local persona pack, or return None when it does not exist."""
    if not persona_id or not PERSONA_ID.fullmatch(persona_id):
        raise PersonaError("人设 ID 只能包含小写字母、数字、下划线和短横线。")
    root = (Path(data_dir) / "personas" / persona_id).resolve()
    expected = (Path(data_dir) / "personas").resolve()
    if root.parent != expected:
        raise PersonaError("人设路径越界。")
    if not root.is_dir():
        return None
    return _load_pack(root, persona_id)


def _metadata_persona_id(metadata: Dict[str, Any], source: Path) -> str:
    value = metadata.get("id")
    if value is not None:
        if not isinstance(value, str) or not PERSONA_ID.fullmatch(value):
            raise PersonaError(
                "persona.json 的 id 只能包含小写字母、数字、下划线和短横线。"
            )
        return value
    if PERSONA_ID.fullmatch(source.name):
        return source.name
    raise PersonaError("persona.json 缺少合法 id，且目录名不能作为 id。")


def preview_persona_pack(source_dir, data_dir=None) -> Dict[str, Any]:
    """Inspect a local pack before copying it into the data directory."""
    raw_source = Path(source_dir)
    if raw_source.is_symlink():
        raise PersonaError("人设包目录不能是符号链接。")
    source = raw_source.resolve()
    if not source.is_dir():
        raise PersonaError("人设包目录不存在。")

    metadata = _load_metadata(source / "persona.json")
    persona_id = _metadata_persona_id(metadata, source)
    persona = _load_pack(source, persona_id)

    files = dict(persona.file_sizes)
    total = sum(files.values())
    if total > MAX_PACK_BYTES:
        raise PersonaError(
            "人设包总大小超过 {} 字节上限。".format(MAX_PACK_BYTES)
        )

    ignored = [
        child.name
        for child in source.iterdir()
        if child.is_file() and child.name not in PERSONA_FILES
    ]
    warnings = []
    if ignored:
        warnings.append(
            "将忽略 {} 个非人设文本文件（不导入图片、语音或模型）。".format(
                len(ignored)
            )
        )
    if source.name != persona_id:
        warnings.append("导入后目录名将使用 id“{}”。".format(persona_id))

    target_exists = False
    if data_dir is not None:
        target = Path(data_dir) / "personas" / persona_id
        target_exists = target.exists()

    info = persona.info()
    info.update(
        {
            "files": files,
            "total_bytes": total,
            "warnings": warnings,
            "target_exists": target_exists,
        }
    )
    return info


def import_persona_pack(source_dir, data_dir, overwrite=False) -> Persona:
    """Copy only persona text files into ``data/personas/<id>``."""
    raw_source = Path(source_dir)
    if raw_source.is_symlink():
        raise PersonaError("人设包目录不能是符号链接。")
    source = raw_source.resolve()
    preview = preview_persona_pack(source, data_dir)
    persona_id = preview["id"]

    personas_root = (Path(data_dir) / "personas").resolve()
    personas_root.mkdir(parents=True, exist_ok=True)
    target = (personas_root / persona_id).resolve()
    if target.parent != personas_root:
        raise PersonaError("人设路径越界。")
    if target.is_symlink():
        raise PersonaError("目标人设目录不能是符号链接。")
    if target.exists() and not overwrite:
        raise PersonaError("已存在同 ID 的人设包，请确认是否覆盖。")

    temp = Path(
        tempfile.mkdtemp(prefix=".import-", dir=str(personas_root))
    )
    backup = None
    try:
        for name in PERSONA_FILES:
            src = source / name
            if src.is_symlink():
                raise PersonaError("人设包 {} 不能是符号链接。".format(name))
            if src.is_file():
                shutil.copyfile(src, temp / name)
        _load_pack(temp, persona_id)
        if target.exists():
            backup = personas_root / ".backup-{}-{}".format(
                persona_id,
                uuid.uuid4().hex[:8],
            )
            target.rename(backup)
        temp.rename(target)
    except Exception:
        if backup is not None and backup.exists() and not target.exists():
            backup.rename(target)
        shutil.rmtree(temp, ignore_errors=True)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)

    imported = load_persona(data_dir, persona_id)
    if imported is None:
        raise PersonaError("人设包导入后无法加载。")
    return imported


WORLDBOOK_HEADING = re.compile(r"^##\s+(.+?)\s*$")
WORLDBOOK_STOPWORDS = {
    "如何",
    "什么",
    "为什么",
    "怎么",
    "怎样",
    "是否",
    "可以",
    "一下",
    "介绍",
    "说说",
    "告诉",
    "这个",
    "那个",
    "今天",
    "现在",
    "帮我",
    "请问",
    "知道",
}


def parse_worldbook(text: str) -> List[WorldbookEntry]:
    text = (text or "").strip()
    if not text:
        return []
    entries: List[WorldbookEntry] = []
    title = ""
    body: List[str] = []

    def flush():
        content = "\n".join(body).strip()
        if title and content:
            entries.append(WorldbookEntry(title=title, content=content))

    for line in text.splitlines():
        match = WORLDBOOK_HEADING.match(line)
        if match:
            flush()
            title = match.group(1).strip()
            body = []
        elif title:
            body.append(line)
        elif line.strip() and not line.startswith("# "):
            title = "概述"
            body = [line]
    flush()
    if not entries:
        content = "\n".join(
            line for line in text.splitlines() if not line.startswith("# ")
        ).strip()
        if content:
            entries.append(WorldbookEntry(title="概述", content=content))
    return entries


def select_worldbook(
    entries: List[WorldbookEntry],
    query: str,
    limit: int = 2,
    max_chars: int = 1200,
) -> List[WorldbookEntry]:
    tokens = {
        token
        for token in segment_tokens(query)
        if len(token) >= 2 and token not in WORLDBOOK_STOPWORDS
    }
    if not tokens or not entries:
        return []
    ranked = []
    for index, entry in enumerate(entries):
        title_tokens = set(segment_tokens(entry.title))
        body_tokens = set(segment_tokens(entry.content))
        score = sum(2 for token in tokens if token in title_tokens)
        score += sum(1 for token in tokens if token in body_tokens)
        if score:
            ranked.append((score, index, entry))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: List[WorldbookEntry] = []
    used = 0
    for _, _, entry in ranked:
        if len(selected) >= max(1, limit):
            break
        title_size = len(entry.title)
        body_size = len(entry.content)
        if selected and used + title_size + body_size > max_chars:
            continue
        if not selected and title_size + body_size > max_chars:
            allowance = max(0, max_chars - title_size)
            entry = WorldbookEntry(
                title=entry.title,
                content=entry.content[:allowance].rstrip() + "…",
            )
            body_size = len(entry.content)
        selected.append(entry)
        used += title_size + body_size
    return selected


def list_personas(data_dir) -> List[Persona]:
    root = Path(data_dir) / "personas"
    if not root.is_dir():
        return []
    personas = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not PERSONA_ID.fullmatch(child.name):
            continue
        try:
            persona = load_persona(data_dir, child.name)
        except PersonaError:
            continue
        if persona is not None:
            personas.append(persona)
    return personas
