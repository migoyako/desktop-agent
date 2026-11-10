"""Shared directory identity and path rules for every local file capability.

Note writing, the authorization centre and the later file executors have to agree
on what "inside the granted directory" means. A second copy of these checks is
exactly how two of them start disagreeing, so the rules live here once.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Dict, List, Optional


class PathSafetyError(RuntimeError):
    """A path or directory failed a safety rule before anything was touched."""


# Names that may never appear as a path segment *inside* a granted directory.
BLOCKED_NAMES = {
    ".git",
    ".codex",
    ".agents",
    ".ssh",
    ".aws",
    ".env",
    "node_modules",
}

# 不是路径、只能交给 shell 解析的目标前缀。
SHELL_TARGET_PREFIXES = ("shell:", "shell::", "::")

# Directories that hold credentials. Unlike BLOCKED_NAMES these are judged against
# the authorized path itself: a grant whose own path sits inside a credential
# store needs the user's explicit override, and the override is recorded.
SECRET_DIRECTORY_NAMES = {
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    ".kube",
    ".docker",
    ".codex",
}

MAX_PATH_DEPTH = 8


def sensitive_component(path) -> str:
    """Return the credential-store segment in ``path``, or an empty string."""
    for part in Path(str(path)).parts:
        if str(part).casefold() in SECRET_DIRECTORY_NAMES:
            return str(part)
    return ""


def is_home_directory(path) -> bool:
    try:
        return os.path.normcase(str(Path(path).resolve())) == os.path.normcase(
            str(Path.home().resolve())
        )
    except OSError:
        return False


def is_reparse_point(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(value, "st_file_attributes", 0) & 0x400)


def assert_not_link(path: Path):
    if path.is_symlink() or is_reparse_point(path):
        raise PathSafetyError("不允许访问符号链接或目录联接：{}".format(path))


def resource_identity(root) -> Dict:
    """Canonical path plus volume and file identity of a directory."""
    path = Path(root).resolve()
    value = os.stat(path, follow_symlinks=False)
    return {
        "root": str(path),
        "device": str(value.st_dev),
        "file_id": str(value.st_ino),
    }


def identity_digest(snapshot: Dict) -> str:
    """Short stable digest of a stored identity, for audit lines and proposals."""
    value = snapshot or {}
    raw = "|".join(
        str(value.get(key) or "") for key in ("root", "device", "file_id")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def app_ref(target_key) -> str:
    """Stable row id for one launch target, derived from its normalized key.

    Recomputing the catalog must not hand the user a different ``app_id`` for the
    same program, or every rescan would orphan the allow-list grant they registered.
    """
    return "app-" + hashlib.sha256(
        str(target_key or "").encode("utf-8")
    ).hexdigest()[:16]


def is_shell_target(target) -> bool:
    """``shell:回收站`` / ``:: {GUID}`` 这类 shell 命名空间标识，不是文件系统路径。

    大小写不敏感：注册表和快捷方式都给过 ``SHELL:::{...}`` 这种写法，而它和
    ``shell:::{...}`` 指的是同一个入口，必须落成同一个键。
    """
    text = str(target or "").strip().lower()
    return text.startswith(SHELL_TARGET_PREFIXES)


def app_target_key(target) -> str:
    """One launch target's row key.

    ``shell:回收站`` 这类命名空间标识不是路径，按路径归一化会把它们变成一个假的绝对
    路径；反过来真实 exe 又必须走 ``path_key``，不然大小写与分隔符一换就找不到同一条
    授权。发现和启动两头都用这个函数，键才对得上。
    """
    text = str(target or "").strip()
    if not text:
        return ""
    if is_shell_target(text):
        return os.path.normcase(text)
    return path_key(text)


def resource_identity_valid(snapshot: Dict) -> bool:
    if not snapshot or not snapshot.get("root"):
        return False
    root = Path(snapshot["root"])
    if not root.exists() or not root.is_dir():
        return False
    try:
        current = resource_identity(root)
    except OSError:
        return False
    return (
        current["root"] == snapshot.get("root")
        and current["device"] == str(snapshot.get("device"))
        and current["file_id"] == str(snapshot.get("file_id"))
    )


def path_key(root) -> str:
    """Case-insensitive Windows key used to find one directory's grant row."""
    return os.path.normcase(os.path.abspath(str(root)))


def relative_path_key(value) -> str:
    """Key for a path relative to a granted directory: one separator, one case.

    The index stores this and only this, so a scan that finds ``Notes/a.md`` and a
    search that asks for ``notes\\A.MD`` land on the same row. Absolute paths never
    get here — those keep going through ``path_key``.
    """
    normalized = os.path.normcase(str(value or "").replace("\\", "/").strip("/"))
    # ``Path("a.md").parent`` 是 "."，它指的就是授权目录自己。顶层文件必须和
    # parent_key="" 的查询落在同一个键上，否则"列出顶层"永远列不到东西。
    return "" if normalized == "." else normalized


def relative_parts(rel_path) -> List[str]:
    """Split a stored relative path into its segments, rejecting anything absolute."""
    normalized = str(rel_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return []
    pure = PureWindowsPath(normalized)
    if pure.is_absolute() or pure.drive:
        raise PathSafetyError("文件路径必须相对于授权目录。")
    parts = [part for part in normalized.split("/") if part]
    if any(part in ("", ".", "..") for part in parts):
        raise PathSafetyError("文件路径包含越界或不允许的名称。")
    return parts


class WorkspaceGuard:
    """Restricts file actions to one explicitly granted directory.

    ``allowed_suffixes`` is deliberately not a note rule here; callers that only
    accept certain file types enforce that themselves (see ``NoteGuard``), so a
    generic file executor cannot inherit the note whitelist by accident.
    """

    def __init__(self, root):
        raw_root = Path(root)
        if not raw_root.is_absolute():
            raise PathSafetyError("工作目录必须是绝对路径。")
        text = str(raw_root)
        if text.startswith(("\\\\", "//")):
            raise PathSafetyError("不允许授权网络共享目录。")
        if raw_root == Path(raw_root.anchor):
            raise PathSafetyError("不能授权磁盘根目录。")
        if not raw_root.exists() or not raw_root.is_dir():
            raise PathSafetyError("授权目录不存在或不是文件夹。")
        for part in (raw_root, *raw_root.parents):
            assert_not_link(part)
        self.root = raw_root.resolve()

    def resolve(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        pure = PureWindowsPath(normalized)
        if pure.is_absolute() or pure.drive:
            raise PathSafetyError("文件路径必须相对于授权目录。")
        parts = normalized.split("/")
        if not parts or len(parts) > MAX_PATH_DEPTH:
            raise PathSafetyError("文件路径层级过多。")
        for part in parts:
            if (
                part in ("", ".", "..")
                or part.rstrip(" .") != part
                or part.casefold() in BLOCKED_NAMES
                or part.casefold().startswith(".env")
                or any(char in part for char in ':< >"|?*'.replace(" ", ""))
                or any(ord(char) < 32 for char in part)
                or (
                    hasattr(PureWindowsPath(part), "is_reserved")
                    and PureWindowsPath(part).is_reserved()
                )
            ):
                raise PathSafetyError("文件路径包含越界或不允许的名称。")
        target = self.root.joinpath(*parts)
        current = self.root
        for part in parts[:-1]:
            current = current / part
            if current.exists():
                assert_not_link(current)
                if not current.is_dir():
                    raise PathSafetyError("父路径不是文件夹。")
        parent = target.parent
        while parent != self.root:
            if parent.exists():
                assert_not_link(parent)
            parent = parent.parent
        try:
            common = os.path.commonpath(
                [str(self.root), str(target.resolve(strict=False))]
            )
        except ValueError as error:
            raise PathSafetyError("文件路径超出授权目录。") from error
        if os.path.normcase(common) != os.path.normcase(str(self.root)):
            raise PathSafetyError("文件路径超出授权目录。")
        if target.exists():
            assert_not_link(target)
        return target


def check_grantable_directory(root, allow_sensitive: bool = False) -> WorkspaceGuard:
    """Validate a directory the user is about to authorize.

    The credential-store gate runs here rather than at read time so an unauthorized
    path never becomes a grant row at all; ``allow_sensitive`` is the explicit
    override the caller has to ask for, and it is recorded on the row.
    """
    guard = WorkspaceGuard(root)
    if not allow_sensitive:
        component = sensitive_component(guard.root)
        if component:
            raise PathSafetyError(
                "「{}」是存放凭据的目录，默认不授权。"
                "确实需要时请明确勾选覆盖风险后重新授权。".format(component)
            )
        if is_home_directory(guard.root):
            raise PathSafetyError(
                "不能把整个用户主目录作为授权目录，请只授权其中的项目目录。"
            )
    return guard
