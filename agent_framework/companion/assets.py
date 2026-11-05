"""Read-only view of the user's character assets under ``data/assets/<persona_id>/``.

Nothing here writes to the asset directory except one deliberate exception,
``import_avatar``: an explicit user upload copied into that persona's own
``avatar.*`` slot. Everything else stays file-presence driven, so "no file" and
"bad file" both mean "no asset" rather than falling back to a built-in figure
(D58).
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from .persona import PERSONA_ID

AVATAR_NAMES = ("avatar.png", "avatar.webp", "avatar.jpg")
BACKGROUND_NAMES = ("background.png", "background.webp", "background.jpg")
MAX_AVATAR_BYTES = 8 * 1024 * 1024
MAX_BACKGROUND_BYTES = 12 * 1024 * 1024
MAX_LIVE2D_BYTES = 1024 * 1024
MAX_LIVE2D_FILE_BYTES = 32 * 1024 * 1024
MAX_LIVE2D_TOTAL_BYTES = 96 * 1024 * 1024
LIVE2D_ENTRY = Path("live2d") / "model3.json"
LIVE2D_ROOT = Path("live2d")
LIVE2D_MODEL_SUFFIX = ".model3.json"
MAX_LIVE2D_MODELS = 64
MESH_ENTRY = Path("avatar.mavatar")
MAX_MESH_BYTES = 128 * 1024 * 1024
MAX_MESH_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MESH_ENTRY_BYTES = 64 * 1024 * 1024
MAX_MESH_ENTRIES = 200
MESH_EXPRESSIONS = (
    "neutral",
    "smile",
    "shy",
    "surprise",
    "halfLidded",
    "angry",
    "sad",
    "wink",
)

IMAGE_SUFFIXES = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".webp": "webp"}
LIVE2D_MEDIA_TYPES = {
    ".json": "application/json",
    ".moc3": "application/octet-stream",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
}


@dataclass(frozen=True)
class ImageAsset:
    kind: str
    path: Path
    size: int
    stamp: int


@dataclass(frozen=True)
class Live2dEntry:
    path: Path
    root: Path
    size: int
    valid: bool
    reason: str = ""
    expressions: Tuple[str, ...] = ()
    motions: Tuple[str, ...] = ()
    motion_groups: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    model_path: str = ""


@dataclass(frozen=True)
class MeshAvatarEntry:
    path: Path
    size: int
    stamp: int
    valid: bool
    name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CharacterAssets:
    avatar: Optional[ImageAsset]
    live2d: Optional[Live2dEntry]
    background: Optional[ImageAsset]
    mesh: Optional[MeshAvatarEntry] = None


def asset_root(data_dir, persona_id) -> Optional[Path]:
    """``data/assets/<persona_id>`` when it is a real directory, else None."""
    if not persona_id or not PERSONA_ID.fullmatch(str(persona_id)):
        return None
    root = Path(data_dir) / "assets" / str(persona_id)
    if root.is_symlink() or not root.is_dir():
        return None
    return root


def _head_signature(head, kind: str) -> bool:
    if kind == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if kind == "jpg":
        return head.startswith(b"\xff\xd8\xff")
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _signature_ok(path: Path, kind: str) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    return _head_signature(head, kind)


def _image(root: Path, names, limit: int) -> Optional[ImageAsset]:
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        kind = path.suffix.lstrip(".").lower()
        if info.st_size <= 0 or info.st_size > limit:
            continue
        if not _signature_ok(path, kind):
            continue
        return ImageAsset(
            kind=kind,
            path=path,
            size=info.st_size,
            stamp=int(info.st_mtime),
        )
    return None


def _avatar(root: Path) -> Optional[ImageAsset]:
    return _image(root, AVATAR_NAMES, MAX_AVATAR_BYTES)


def _background(root: Path) -> Optional[ImageAsset]:
    return _image(root, BACKGROUND_NAMES, MAX_BACKGROUND_BYTES)


class AssetImportError(ValueError):
    """The uploaded file cannot become this persona's avatar."""


def import_avatar(data_dir, persona_id, filename, payload) -> Dict:
    """Store an uploaded picture as ``avatar.<kind>`` in the persona's own slot.

    The only write this module performs: the source file is never touched, and
    nothing outside the three ``avatar.*`` names in this slot is removed.
    """
    text_id = str(persona_id or "")
    if not PERSONA_ID.fullmatch(text_id):
        raise AssetImportError("人设 ID 不合法，找不到素材槽位。")
    root = Path(data_dir) / "assets" / text_id
    if root.parent.is_symlink() or root.is_symlink():
        raise AssetImportError("素材目录不能是符号链接。")
    kind = IMAGE_SUFFIXES.get(Path(str(filename or "")).suffix.lower())
    if kind is None:
        raise AssetImportError("头像只支持 .png、.jpg 与 .webp。")
    data = bytes(payload or b"")
    if not data:
        raise AssetImportError("没有读到文件内容。")
    if len(data) > MAX_AVATAR_BYTES:
        raise AssetImportError(
            "头像超过 {:.0f} MiB，请压缩后再导入。".format(MAX_AVATAR_BYTES / 1048576)
        )
    if not _head_signature(data[:16], kind):
        raise AssetImportError("文件内容与扩展名不符，看起来不是有效图片。")
    root.mkdir(parents=True, exist_ok=True)
    target = root / "avatar.{}".format(kind)
    # A half-written avatar still resolves as an image slot, so the swap is atomic.
    temp = root / "avatar.{}.tmp".format(kind)
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    except OSError as error:
        try:
            temp.unlink()
        except OSError:
            pass
        raise AssetImportError("写入头像失败：{}".format(error))
    removed = []
    for name in AVATAR_NAMES:
        if name == target.name:
            continue
        stale = root / name
        if stale.is_symlink() or not stale.is_file():
            continue
        try:
            stale.unlink()
            removed.append(name)
        except OSError:
            removed.append(name)
    asset = _avatar(root)
    if asset is None:
        raise AssetImportError("头像已写入但读不出来，请检查文件是否完整。")
    return {"asset": asset, "removed": removed, "persona_id": text_id}


def _reference(parent: Path, value, suffixes) -> Optional[Path]:
    """A model3.json reference confined to ``parent``; None if missing/unsafe."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    raw = parent / value
    if raw.is_symlink() or not raw.is_file():
        return None
    try:
        target = raw.resolve()
        target.relative_to(parent.resolve())
    except (OSError, ValueError):
        return None
    # Name-tail match, not Path.suffix: Cubism clip files end in
    # ".motion3.json" and only the last dot would be considered.
    lowered = target.name.lower()
    if not any(lowered.endswith(item) for item in suffixes):
        return None
    return target


def _expression_names(references: dict) -> Tuple[str, ...]:
    items = references.get("Expressions")
    names = []
    if not isinstance(items, list):
        return ()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("Name")
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return tuple(names)


def _motion_groups(parent: Path, references: dict) -> Tuple[str, ...]:
    """Group names that actually hold at least one clip file inside ``parent``.

    ``idle`` is included: the stage needs to tell content gestures apart from
    the standby group, and a manifest that only declares ``idle`` must still
    count as having motions.
    """
    items = references.get("Motions")
    names = []
    if not isinstance(items, dict):
        return ()
    for name, clips in items.items():
        if not isinstance(name, str) or not name.strip() or name.strip() in names:
            continue
        if not isinstance(clips, list):
            continue
        usable = any(
            isinstance(clip, dict)
            and _reference(parent, clip.get("File"), {".motion3.json"}) is not None
            for clip in clips
        )
        if usable:
            names.append(name.strip())
    return tuple(names)


def _motion_group_map(payload: dict, known: Tuple[str, ...]) -> Dict[str, Tuple[str, ...]]:
    """Cubism ``Groups`` entries with Target=="Motion", keyed by group name.

    Only names that resolve to real motion groups survive, so a stale label in
    the manifest cannot point the stage at a clip that no longer exists.
    """
    items = payload.get("Groups")
    result: Dict[str, Tuple[str, ...]] = {}
    if not isinstance(items, list):
        return result
    lookup = {name.lower(): name for name in known}
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("Target") or "").lower() != "motion":
            continue
        label = item.get("Name")
        if not isinstance(label, str) or not label.strip():
            continue
        ids = item.get("Ids")
        if not isinstance(ids, list):
            continue
        names = []
        for value in ids:
            resolved = lookup.get(str(value or "").strip().lower())
            if resolved and resolved not in names:
                names.append(resolved)
        if names:
            result[label.strip()] = tuple(names)
    return result


def _live2d_candidates(root: Path) -> Tuple[Tuple[Path, str], ...]:
    """Model manifests below ``live2d/`` in a stable, path-safe order.

    The legacy root entry stays exclusive when present. Otherwise Cubism packs
    commonly arrive inside their own directory, with names such as
    ``DeepSeek/character.model3.json``.
    """
    live_root = root / LIVE2D_ROOT
    if live_root.is_symlink() or not live_root.is_dir():
        return ()
    exact = live_root / "model3.json"
    if not exact.is_symlink() and exact.is_file():
        return ((exact, "model3.json"),)

    found = []
    for current, dirs, files in os.walk(live_root, followlinks=False):
        directory = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if not (directory / name).is_symlink()
        )
        for name in sorted(files):
            lowered = name.lower()
            if lowered != "model3.json" and not lowered.endswith(LIVE2D_MODEL_SUFFIX):
                continue
            path = directory / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(live_root).as_posix()
                path.resolve().relative_to(live_root.resolve())
            except (OSError, ValueError):
                continue
            found.append((path, relative))

    found.sort(key=lambda item: (len(item[1].split("/")), item[1].casefold()))
    return tuple(found[:MAX_LIVE2D_MODELS])


def _live2d_entry(
    path: Path, relative: str
) -> Live2dEntry:
    def invalid(reason: str, size: int = 0) -> Live2dEntry:
        return Live2dEntry(
            path,
            path.parent,
            size,
            False,
            reason,
            model_path=relative,
        )

    try:
        size = path.stat().st_size
    except OSError:
        return invalid("model3.json 不可读。")
    if size <= 0 or size > MAX_LIVE2D_BYTES:
        return invalid("model3.json 为空或超过 1 MB 上限。", size)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return invalid("model3.json 不是合法 JSON。", size)
    if not isinstance(payload, dict):
        return invalid("model3.json 顶层必须是对象。", size)
    references = payload.get("FileReferences")
    if not isinstance(references, dict):
        return invalid("model3.json 缺少 FileReferences。", size)
    moc = references.get("Moc")
    moc_path = _reference(path.parent, moc, {".moc3"})
    if moc_path is None:
        return invalid(
            "FileReferences.Moc 缺失、越界或不是 .moc3 文件。", size
        )
    try:
        with moc_path.open("rb") as handle:
            magic = handle.read(4)
        if magic != b"MOC3":
            return invalid(".moc3 文件头无效。", size)
        total = size + moc_path.stat().st_size
    except OSError:
        return invalid("模型文件不可读。", size)
    if moc_path.stat().st_size > MAX_LIVE2D_FILE_BYTES:
        return invalid("单个模型文件超过 32 MB 上限。", size)
    textures = references.get("Textures")
    if not isinstance(textures, list) or not textures:
        return invalid("model3.json 缺少 FileReferences.Textures。", size)
    for item in textures:
        texture = _reference(path.parent, item, IMAGE_SUFFIXES)
        if texture is None:
            return invalid("贴图引用缺失、越界或类型不受支持。", size)
        if not _signature_ok(texture, IMAGE_SUFFIXES[texture.suffix.lower()]):
            return invalid("贴图格式无效。", size)
        try:
            file_size = texture.stat().st_size
        except OSError:
            return invalid("贴图不可读。", size)
        if file_size > MAX_LIVE2D_FILE_BYTES:
            return invalid("单个贴图超过 32 MB 上限。", size)
        total += file_size
    if total > MAX_LIVE2D_TOTAL_BYTES:
        return invalid("模型素材总大小超过 96 MB 上限。", size)
    motions = _motion_groups(path.parent, references)
    return Live2dEntry(
        path,
        path.parent,
        size,
        True,
        "",
        _expression_names(references),
        motions,
        _motion_group_map(payload, motions),
        relative,
    )


def _live2d(root: Path) -> Optional[Live2dEntry]:
    first_invalid = None
    for path, relative in _live2d_candidates(root):
        entry = _live2d_entry(path, relative)
        if entry.valid:
            return entry
        if first_invalid is None:
            first_invalid = entry
    return first_invalid


def _mesh_name_ok(name: str) -> bool:
    if not name or name.endswith("/") or name.startswith("/"):
        return False
    if "\\" in name or "\x00" in name or ":" in name.split("/")[0]:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


def _mesh_json(archive: zipfile.ZipFile, name: str):
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (KeyError, OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _mesh_png(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        with archive.open(name) as handle:
            head = handle.read(16)
    except (KeyError, OSError, RuntimeError):
        return False
    return head.startswith(b"\x89PNG\r\n\x1a\n")


def _mesh_rect(value) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return False
    return value[0] >= 0 and value[1] >= 0 and value[2] > 0 and value[3] > 0


def _mesh_layer_files(value, prefix: str, names) -> Optional[str]:
    """Validate a layers map and return the first missing/bad file, if any."""
    if not isinstance(value, dict) or not value:
        return "缺少图层表"
    for key, rect in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            return "图层名不合法"
        if not _mesh_rect(rect):
            return "图层 {}.png 的矩形不合法".format(key)
        if "{}{}.png".format(prefix, key) not in names:
            return "缺少图层 {}.png".format(key)
    return None


def _mesh(root: Path) -> Optional[MeshAvatarEntry]:
    path = root / MESH_ENTRY
    if path.is_symlink() or not path.is_file():
        return None
    try:
        info = path.stat()
    except OSError:
        return None
    entry = MeshAvatarEntry(path, info.st_size, int(info.st_mtime), False)
    if info.st_size <= 0:
        return MeshAvatarEntry(path, info.st_size, entry.stamp, False, "", ".mavatar 为空。")
    if info.st_size > MAX_MESH_BYTES:
        return MeshAvatarEntry(path, info.st_size, entry.stamp, False, "", ".mavatar 超过 128 MB 上限。")

    def invalid(reason):
        return MeshAvatarEntry(
            path, info.st_size, entry.stamp, False, entry.name, reason
        )

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos:
                return invalid(".mavatar 归档为空。")
            if len(infos) > MAX_MESH_ENTRIES:
                return invalid("归档文件数超过 {} 上限。".format(MAX_MESH_ENTRIES))
            names = [item.filename for item in infos]
            if any(not _mesh_name_ok(name) for name in names):
                return invalid("归档包含不安全路径。")
            if len(set(names)) != len(names):
                return invalid("归档包含重复文件。")
            total = 0
            for item in infos:
                if item.flag_bits & 0x1:
                    return invalid("归档包含加密文件。")
                if item.compress_type != zipfile.ZIP_STORED:
                    return invalid("归档必须使用存储压缩（stored）。")
                if item.file_size > MAX_MESH_ENTRY_BYTES:
                    return invalid("单个文件超过 64 MB 上限。")
                total += item.file_size
            if total > MAX_MESH_TOTAL_BYTES:
                return invalid("归档解压后超过 512 MB 上限。")
            files = set(names)
            for required in ("avatar.json", "rig.json", "built/layers.json"):
                if required not in files:
                    return invalid("缺少 {}。".format(required))
            for image in ("built/base.png", "built/hairmask.png"):
                if image not in files:
                    return invalid("缺少 {}。".format(image))
                if not _mesh_png(archive, image):
                    return invalid("{} 不是有效 PNG。".format(image))

            manifest = _mesh_json(archive, "avatar.json")
            if manifest is None:
                return invalid("avatar.json 不是合法 JSON 对象。")
            if manifest.get("format") != "mesh-avatar":
                return invalid("avatar.json.format 必须是 mesh-avatar。")
            if manifest.get("version") != 1:
                return invalid("不支持的 .mavatar 版本。")
            name = manifest.get("name")
            if not isinstance(name, str) or not name.strip():
                return invalid("avatar.json.name 缺失。")
            name = name.strip()

            rig = _mesh_json(archive, "rig.json")
            if rig is None:
                return invalid("rig.json 不是合法 JSON 对象。")
            if rig.get("version") != 1:
                return invalid("rig.json.version 必须是 1。")

            layers = _mesh_json(archive, "built/layers.json")
            if layers is None:
                return invalid("built/layers.json 不是合法 JSON 对象。")
            size = layers.get("size")
            if (
                not isinstance(size, list)
                or len(size) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)
            ):
                return invalid("built/layers.json.size 必须是 [宽度, 高度]。")
            problem = _mesh_layer_files(layers.get("layers"), "built/", files)
            if problem:
                return invalid(problem)
            eyes = layers.get("eyes")
            if not isinstance(eyes, list) or len(eyes) != 2:
                return invalid("built/layers.json.eyes 必须是两条眼线。")
            for eye in eyes:
                if not isinstance(eye, dict):
                    return invalid("built/layers.json.eyes 含非法条目。")
                x0, x1 = eye.get("x0"), eye.get("x1")
                if not (
                    isinstance(x0, (int, float))
                    and isinstance(x1, (int, float))
                    and x0 < x1
                ):
                    return invalid("built/layers.json.eyes 的 x0 必须小于 x1。")
                for key in ("top", "bot"):
                    curve = eye.get(key)
                    if (
                        not isinstance(curve, list)
                        or len(curve) != 24
                        or not all(
                            isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in curve
                        )
                    ):
                        return invalid("built/layers.json.eyes.{} 必须是 24 个数。".format(key))

            if "built/sprites/sprites.json" in files:
                sprites = _mesh_json(archive, "built/sprites/sprites.json")
                if sprites is None:
                    return invalid("built/sprites/sprites.json 不是合法 JSON 对象。")
                if sprites.get("version") not in (None, 1):
                    return invalid("不支持的 sprites 版本。")
                problem = _mesh_layer_files(
                    sprites.get("layers"), "built/sprites/", files
                )
                if problem:
                    return invalid(problem)

            if "built/overlays/overlays.json" in files:
                overlays = _mesh_json(archive, "built/overlays/overlays.json")
                if overlays is None:
                    return invalid("built/overlays/overlays.json 不是合法 JSON 对象。")
                items = overlays.get("overlays")
                if not isinstance(items, list):
                    return invalid("built/overlays/overlays.json.overlays 必须是数组。")
                for item in items:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("name"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]+", item["name"])
                        or not isinstance(item.get("file"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]+\.png", item["file"])
                    ):
                        return invalid("overlay 条目必须包含安全的 name 与 file。")
                    if "built/overlays/" + item["file"] not in files:
                        return invalid("缺少 overlay 文件 {}。".format(item["file"]))
    except zipfile.BadZipFile:
        return invalid("不是有效的 .mavatar 归档。")
    except (OSError, RuntimeError, ValueError):
        return invalid(".mavatar 不可读或已损坏。")
    return MeshAvatarEntry(path, info.st_size, entry.stamp, True, name, "")


def live2d_file(root: Path, relative: str) -> Optional[Tuple[Path, str]]:
    """Resolve a model-relative request under ``live2d/`` to ``(path, media)``."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    segments = relative.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in segments):
        return None
    if any(":" in part or "\x00" in part for part in segments):
        return None
    media = LIVE2D_MEDIA_TYPES.get(Path(relative).suffix.lower())
    if media is None:
        return None
    raw = root / relative
    if raw.is_symlink() or not raw.is_file():
        return None
    try:
        target = raw.resolve()
        target.relative_to(root.resolve())
        size = target.stat().st_size
    except (OSError, ValueError):
        return None
    if size <= 0 or size > MAX_LIVE2D_FILE_BYTES:
        return None
    return target, media


def scan_assets(data_dir, persona_id) -> CharacterAssets:
    root = asset_root(data_dir, persona_id)
    if root is None:
        return CharacterAssets(avatar=None, live2d=None, background=None, mesh=None)
    return CharacterAssets(
        avatar=_avatar(root),
        live2d=_live2d(root),
        background=_background(root),
        mesh=_mesh(root),
    )


def public_view(assets: CharacterAssets) -> dict:
    def image(item):
        if item is None:
            return None
        return {"kind": item.kind, "size": item.size, "stamp": item.stamp}

    live2d = assets.live2d
    stamp = 0
    if live2d is not None:
        try:
            stamp = int(live2d.path.stat().st_mtime)
        except OSError:
            stamp = 0
    mesh = assets.mesh
    return {
        "avatar": image(assets.avatar),
        "background": image(assets.background),
        "live2d": (
            None
            if live2d is None
            else {
                "size": live2d.size,
                "valid": live2d.valid,
                "reason": live2d.reason,
                "expressions": list(live2d.expressions),
                "motions": list(live2d.motions),
                "motion_groups": {
                    key: list(value) for key, value in live2d.motion_groups.items()
                },
                "model_path": live2d.model_path,
                "stamp": stamp,
            }
        ),
        "mesh": (
            None
            if mesh is None
            else {
                "name": mesh.name,
                "size": mesh.size,
                "valid": mesh.valid,
                "reason": mesh.reason,
                "expressions": list(MESH_EXPRESSIONS),
                "stamp": mesh.stamp,
            }
        ),
    }


def character_assets_check(
    data_dir, persona_id, overlay_visible
) -> Tuple[str, str]:
    """Doctor verdict: an enabled overlay without a usable model is a failure."""
    assets = scan_assets(data_dir, persona_id)
    avatar = assets.avatar
    live2d = assets.live2d
    mesh = assets.mesh
    avatar_detail = "none" if avatar is None else "{}({}B)".format(
        avatar.kind, avatar.size
    )
    if live2d is None:
        live2d_detail = "missing: 缺少 live2d/model3.json（也支持 live2d/*.model3.json）"
    elif live2d.valid:
        live2d_detail = "ok({}, {}B, {} expressions, {} motions)".format(
            live2d.model_path, live2d.size, len(live2d.expressions), len(live2d.motions)
        )
    else:
        live2d_detail = "invalid: {}".format(live2d.reason)
    if mesh is None:
        mesh_detail = "missing: 缺少 avatar.mavatar"
    elif mesh.valid:
        mesh_detail = "ok({}B, {})".format(mesh.size, mesh.name)
    else:
        mesh_detail = "invalid: {}".format(mesh.reason)
    detail = "persona={} avatar={} live2d={} mesh={}".format(
        persona_id, avatar_detail, live2d_detail, mesh_detail
    )
    # Live2D wins when its model file exists; an invalid Live2D model does not
    # silently fall through to the .mavatar carrier.
    if live2d is not None:
        usable = live2d.valid
    else:
        usable = mesh is not None and mesh.valid
    if usable:
        return "ok", detail
    if overlay_visible:
        return "fail", detail + "；桌面浮窗已开启，但没有可用 Live2D 模型或 .mavatar"
    return "warn", detail + "；桌面浮窗未开启"
