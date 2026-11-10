"""File metadata index and the read-only file tools (desktop agent S2).

The index holds names, paths and ``stat()`` numbers — never file contents. A search
answer is a handful of candidates, not a directory listing, because keeping the model
context small is the reason a local index exists.

Two rules shape everything below:

* Only directories the user authorized are walked, and only through the same
  ``WorkspaceGuard`` rules the read step uses, so an indexed path is always a path
  that can be resolved later.
* Reading is metered by a ``grant_leases`` lease signed for the turn: revoke the
  directory or run out of operations and the next call refuses, in this turn, without
  waiting for a restart.

本轮只到"服务层 + 工具"：界面与 ``/api/files*`` 按计划书 R5 等 Pixso 画板放行。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat as stat_module
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .access_control import (
    CAPABILITY_FS_READ,
    CAPABILITY_FS_SEARCH,
    GRANT_ACTIVE,
    AccessControlError,
    AccessControlService,
    AccessGrant,
)
from .path_safety import (
    BLOCKED_NAMES,
    PathSafetyError,
    WorkspaceGuard,
    path_key,
    relative_parts,
    relative_path_key,
)
from .store import (
    INDEX_FAILED,
    INDEX_MAX_DEPTH,
    INDEX_MAX_FILES,
    INDEX_READY,
    INDEX_RUNNING,
    INDEX_SKIP_DIRECTORY_NAMES,
    INDEX_STALE,
    CompanionStore,
)


# §13.4：find_files 最多返回 5 个候选；§4.2 要求多命中时给最相关的几条并让用户选。
MAX_CANDIDATES = 5
DEFAULT_CANDIDATES = 5
CANDIDATE_SCAN_LIMIT = 400

READ_LEASE_OPERATIONS = 8
READ_LEASE_TTL_SECONDS = 300

READ_CHUNK_CHARS = 8000
READ_DEFAULT_CHARS = 4000
READ_MAX_BYTES = 2 * 1024 * 1024
LIVE_SCAN_MAX_FILES = 2000
CATALOG_BATCH = 500

# 授权后不立刻开扫，先等这么一会儿。连着换几个工作目录时，被换掉的目录不该各扫一遍；
# 而扫描线程唯一会写的东西就是数据库——数据目录正在被删（迁移、恢复、清理临时目录）
# 时，等这一小段就足够让它先消失，线程一看库不在原地了就直接退出。
INDEX_START_DELAY_SECONDS = 0.75

_SKIP_DIRECTORIES = frozenset(
    name.casefold() for name in (INDEX_SKIP_DIRECTORY_NAMES | BLOCKED_NAMES)
)

# 只有这些扩展名按文本读。没有名单时"能不能读"就变成对任意二进制的猜测，
# 而一次猜错就是把字节塞进模型上下文。
DECODABLE_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv",
        ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".conf", ".log", ".xml", ".svg", ".properties", ".po", ".srt",
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css",
        ".scss", ".less", ".sql", ".sh", ".bash", ".zsh", ".ps1",
        ".bat", ".cmd", ".c", ".h", ".cpp", ".hpp", ".cc", ".go", ".rs",
        ".java", ".kt", ".rb", ".php", ".swift", ".lua", ".vim",
    }
)

# UTF-8 优先（本项目写出的文件都是），GBK 兜底（中文 Windows 桌面存量文件常见）。
_TEXT_ENCODINGS = ("utf-8", "gbk")

# 这些状态下可以直接用已索引的数据回答；其余状态说明这一轮还没扫完或扫不动，
# 检索退化成有上限的实时扫描。running 在里面是因为它只可能是"本机另一个在跑的扫描"
# 或"上次中断的残迹"——后者由启动自愈重扫（见 runtime.resume_file_index）。
USABLE_INDEX_STATUSES = (INDEX_READY, INDEX_RUNNING, INDEX_STALE)

REF_PREFIX_LIVE = "live:"


class FileIndexError(RuntimeError):
    def __init__(self, message, code="file_index_error"):
        super().__init__(message)
        self.code = code


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def utc_stamp(seconds_from_now: int = 0) -> str:
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=int(seconds_from_now)))


def file_mtime(value: float) -> str:
    """Store modification time as a sortable UTC stamp.

    Lexicographic order only matches chronological order while every stamp has the
    same shape, which is why search sorts ``mtime DESC`` inside SQL.
    """
    return _iso(datetime.fromtimestamp(float(value), timezone.utc))


def file_extension(path) -> str:
    """扩展名的小写形式，不带点——索引、候选和 ``ext`` 过滤共用这一个写法。"""
    return str(Path(path).suffix.lower()).lstrip(".")


def is_skippable_directory(name) -> bool:
    return str(name or "").casefold() in _SKIP_DIRECTORIES


def relative_to_root(path: Path, root: Path) -> str:
    """POSIX-style path relative to the granted directory; never an absolute path."""
    return Path(path).relative_to(root).as_posix()


def is_reparse_info(info) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def read_text_bytes(data: bytes) -> str:
    """Decode a local text file, or refuse instead of returning mojibake."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for encoding in _TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FileIndexError(
        "这个文件不是 UTF-8 或 GBK 文本，按二进制处理，不读进上下文。",
        "undecodable",
    )


def scan_directory(
    root,
    max_files: int = INDEX_MAX_FILES,
    max_depth: int = INDEX_MAX_DEPTH,
    should_stop=None,
    skip_paths: Sequence[str] = (),
) -> Tuple[List[Dict], Dict]:
    """Walk one granted directory under the file/depth/skip caps.

    ``truncated`` matters as much as the counts: reconciliation removes indexed files
    that are gone, and that inference is only sound after a complete walk. A walk
    that hit a cap reports what it saw and leaves the rest of the index alone.

    ``skip_paths`` names subtrees (or single files) to leave out entirely — matched on
    the absolute normalized path, because the thing being excluded is a location, not
    a filename the user chose.
    """
    root = Path(root)
    skip_keys = {
        os.path.normcase(os.path.abspath(str(item)))
        for item in skip_paths
        if str(item or "").strip()
    }
    skip_prefixes = tuple(key + os.sep for key in skip_keys)

    def is_private(abs_path: str) -> bool:
        """命中排除项本身，或落在它的子树里——排除一个目录就该排除它的全部内容。"""
        return abs_path in skip_keys or abs_path.startswith(skip_prefixes)

    entries: List[Dict] = []
    directories = 0
    skipped_directories = 0
    skipped_entries = 0
    truncated = False
    stopped = False
    error = ""
    stack: List[Tuple[Path, int]] = [(root, 0)]
    while stack and not truncated and not stopped:
        if should_stop is not None and should_stop():
            stopped = True
            break
        current, depth = stack.pop()
        try:
            with os.scandir(str(current)) as iterator:
                children = list(iterator)
        except OSError as scan_error:
            error = "{}：{}".format(Path(current).name, scan_error)
            continue
        directories += 1
        for child in children:
            name = child.name
            try:
                info = child.stat(follow_symlinks=False)
            except OSError:
                skipped_entries += 1
                continue
            is_dir = stat_module.S_ISDIR(info.st_mode)
            if not is_dir and not stat_module.S_ISREG(info.st_mode):
                skipped_entries += 1
                continue
            if is_skippable_directory(name):
                skipped_directories += 1
                continue
            if child.is_symlink() or is_reparse_info(info):
                skipped_entries += 1
                continue
            child_path = Path(child.path)
            if is_private(os.path.normcase(os.path.abspath(child.path))):
                # 按位置排除，不是按名字：被排掉的是"代理自己的数据目录"这种确定位置。
                if is_dir:
                    skipped_directories += 1
                else:
                    skipped_entries += 1
                continue
            try:
                rel_path = relative_to_root(child_path, root)
            except ValueError:
                skipped_entries += 1
                continue
            if len(relative_parts(rel_path)) > INDEX_MAX_DEPTH:
                # 索引里的路径必须还能被 WorkspaceGuard.resolve() 接受，
                # 那一步自己就卡在 MAX_PATH_DEPTH 上，这里不能超出它。
                skipped_entries += 1
                continue
            if is_dir:
                if depth + 1 > max_depth:
                    # 深度上限是稳定规则，不是"没扫完"：同一批文件下一次扫还会在
                    # 同一层被跳过，所以对账仍然可信。只有撞上文件数上限才是半截扫描。
                    skipped_directories += 1
                    continue
                stack.append((child_path, depth + 1))
                continue
            if len(entries) >= max_files:
                truncated = True
                break
            identity = info
            if int(info.st_dev) == 0 and int(info.st_ino) == 0:
                # Windows 的 scandir 缓存不填 st_dev/st_ino（都是 0），那不是一个文件身份。
                # 读取那一步要拿它比对，所以这里补一次真实 lstat；POSIX 上不会走到这。
                try:
                    identity = os.lstat(child.path)
                except OSError:
                    skipped_entries += 1
                    continue
            entries.append(
                {
                    "name": name,
                    "rel_path": rel_path,
                    "parent_key": relative_path_key(str(Path(rel_path).parent)),
                    "ext": file_extension(child_path),
                    "size_bytes": int(info.st_size),
                    "mtime": file_mtime(info.st_mtime),
                    "volume_id": str(identity.st_dev),
                    "file_id": str(identity.st_ino),
                }
            )
    return entries, {
        "files": len(entries),
        "directories": directories,
        "skipped_directories": skipped_directories,
        "skipped_entries": skipped_entries,
        "truncated": truncated,
        "stopped": stopped,
        "error": error,
    }


def index_reason(term: str, name: str, rel_path: str, ext: str) -> str:
    """§4.2 的"命中原因"：候选为什么浮上来，用一句人话说清。"""
    needle = relative_path_key(term)
    if not needle:
        return "目录列举"
    name_key = relative_path_key(name)
    if needle == name_key:
        return "文件名完全匹配"
    if needle in name_key:
        return "文件名匹配"
    if needle in relative_path_key(rel_path):
        return "所在路径匹配"
    if needle.strip(".") == relative_path_key(ext).strip("."):
        return "文件类型匹配"
    return "索引命中"


def candidate_score(term: str, name: str, rel_path: str) -> int:
    needle = relative_path_key(term)
    if not needle:
        return 4
    name_key = relative_path_key(name)
    if name_key == needle:
        return 0
    if name_key.startswith(needle):
        return 1
    if needle in name_key:
        return 2
    if needle in relative_path_key(rel_path):
        return 3
    return 4


def candidate_from_row(
    row: Dict, grant: AccessGrant, reason: str, from_index: bool = True
) -> Dict:
    """§4.2 的候选形状：够用户挑一个文件，但不把整棵目录树交出去。"""
    return {
        "file_ref": str(row["file_ref"]),
        "name": str(row["name"]),
        "grant_id": str(grant.grant_id),
        "directory": Path(grant.path).name or str(grant.path),
        "rel_path": str(row["rel_path"]),
        "ext": str(row.get("ext") or ""),
        "size_bytes": int(row.get("size_bytes") or 0),
        "mtime": str(row.get("mtime") or ""),
        "reason": reason,
        "from_index": bool(from_index),
    }


def rank_candidates(term: str, candidates: List[Dict]) -> List[Dict]:
    """按相关度分档，档内按修改时间从新到旧（两次稳定排序）。"""
    candidates.sort(key=lambda item: str(item.get("mtime") or ""), reverse=True)
    candidates.sort(
        key=lambda item: candidate_score(
            term, str(item.get("name") or ""), str(item.get("rel_path") or "")
        )
    )
    return candidates


def dedupe_candidates(candidates: List[Dict]) -> List[Dict]:
    seen = set()
    unique = []
    for item in candidates:
        key = (item["grant_id"], relative_path_key(item["rel_path"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


class FileIndexService:
    """Build and query the metadata index for the directories the user granted."""

    def __init__(
        self,
        store: CompanionStore,
        access: Optional[AccessControlService] = None,
        max_files: int = INDEX_MAX_FILES,
        max_depth: int = INDEX_MAX_DEPTH,
    ):
        self.store = store
        self.access = access or AccessControlService(store)
        self.max_files = max(1, int(max_files))
        self.max_depth = max(1, min(int(max_depth), INDEX_MAX_DEPTH))
        self._lock = threading.Lock()
        self._running: Dict[str, bool] = {}
        self._threads: List[threading.Thread] = []
        self._stops: Dict[str, threading.Event] = {}
        self._shutdown = threading.Event()

    # -- 索引 ---------------------------------------------------------------

    def grants_with_capability(self, capability: str) -> List[AccessGrant]:
        return [
            grant
            for grant in self.access.list_grants(status=GRANT_ACTIVE)
            if grant.has_capability(capability)
        ]

    def is_agent_data_directory(self, path) -> bool:
        """这个授权目录就是代理自己的数据目录。

        那棵树里只有库文件、备份、导出、工具结果和人设素材——它们要么正在被
        ``_private_paths`` 逐条排除，要么本就不该变成一个可搜索的目录。给它建索引
        只会让后台线程反复写它想索引的那个库。
        """
        return path_key(path) == path_key(self.store.database_path.parent)

    def indexable_grants(
        self, capability: str = CAPABILITY_FS_SEARCH
    ) -> List[AccessGrant]:
        """已授权、含该能力，且目录身份仍与授权时一致。"""
        usable = []
        for grant in self.grants_with_capability(capability):
            if self.is_agent_data_directory(grant.canonical_root):
                continue
            okay, _ = self.access.check_identity(grant)
            if okay:
                usable.append(grant)
        return usable

    def is_indexing(self, grant_id) -> bool:
        with self._lock:
            return bool(self._running.get(str(grant_id)))

    def _private_paths(self) -> Tuple[str, ...]:
        """代理自己的数据目录不进索引（库文件、备份、导出、工具结果都在里面）。

        用户授权的目录常常就包含它——把 ``companion.db`` 和备份变成"可搜到的文件"，
        等于把内部数据交给模型挑；索引它们也毫无意义，那正是本次扫描自己的写目标。
        """
        database = Path(self.store.database_path)
        return (str(database), str(database.parent))

    def index_grant(self, grant_id) -> Dict:
        """扫描一个授权目录，并把结果对账进目录索引。"""
        grant = self.access.get_grant(grant_id)
        if grant is None:
            raise FileIndexError("找不到这条授权。", "grant_missing")
        if grant.status != GRANT_ACTIVE:
            raise FileIndexError("这条授权已撤销，不再索引。", "grant_revoked")
        usable, reason = self.access.check_identity(grant)
        if not usable:
            self.store.save_file_index_state(
                grant.grant_id,
                {
                    "status": INDEX_FAILED,
                    "files_indexed": self.store.count_file_catalog(grant.grant_id),
                    "files_skipped": 0,
                    "last_error": reason,
                    "finished_at": utc_stamp(),
                },
            )
            raise FileIndexError(reason, "grant_unusable")
        started = utc_stamp()
        self.store.save_file_index_state(
            grant.grant_id,
            {
                "status": INDEX_RUNNING,
                "files_indexed": self.store.count_file_catalog(grant.grant_id),
                "files_skipped": 0,
                "last_error": "",
                "started_at": started,
                "finished_at": "",
            },
        )
        entries, stats = scan_directory(
            Path(grant.canonical_root),
            max_files=self.max_files,
            max_depth=self.max_depth,
            should_stop=lambda: self._grant_gone(grant.grant_id),
            skip_paths=self._private_paths(),
        )
        written = 0
        for start in range(0, len(entries), CATALOG_BATCH):
            written += self.store.upsert_file_catalog(
                grant.grant_id, entries[start:start + CATALOG_BATCH]
            )
        removed = 0
        reconciled = not stats["truncated"] and not stats["stopped"]
        if reconciled:
            scanned = {relative_path_key(item["rel_path"]) for item in entries}
            stale = [
                key
                for key in self.store.list_file_catalog_keys(grant.grant_id)
                if key not in scanned
            ]
            removed = self.store.mark_file_catalog_deleted(
                stale, grant_id=grant.grant_id
            )
        failed = bool(stats["error"]) and not entries
        state = self.store.save_file_index_state(
            grant.grant_id,
            {
                "status": INDEX_FAILED if failed else INDEX_READY,
                "files_indexed": written,
                "files_skipped": int(stats["skipped_entries"])
                + int(stats["skipped_directories"]),
                "last_error": stats["error"],
                "started_at": started,
                "finished_at": utc_stamp(),
            },
        )
        state.update(
            {
                "grant_id": grant.grant_id,
                "truncated": stats["truncated"],
                "stopped": stats["stopped"],
                "reconciled": reconciled,
                "directories": stats["directories"],
                "removed": removed,
            }
        )
        return state

    def _store_available(self) -> bool:
        """数据文件是否还在原地。后台扫描唯一的写目标就是它，没了就该收手——
        继续下去只会在一个正在被删除（迁移、恢复、清理临时目录）的库上开连接。"""
        return not self._shutdown.is_set() and Path(self.store.database_path).is_file()

    def schedule_index(self, grant_id) -> bool:
        """后台建索引：授权一个目录不该让当前对话等着扫完才能继续。

        同一目录正在扫、或还卡在延迟窗口里时再调用只返回 False，不排队——目录内容还会
        变，下一次授权、启动或显式对账会重扫。
        """
        key = str(grant_id)
        if self._shutdown.is_set():
            return False
        grant = self.access.get_grant(key)
        if grant is None or self.is_agent_data_directory(grant.canonical_root):
            return False
        with self._lock:
            if self._running.get(key):
                return False
            self._running[key] = True
            stop_event = threading.Event()
            self._stops[key] = stop_event

        def worker():
            try:
                if stop_event.wait(INDEX_START_DELAY_SECONDS):
                    return
                if not self._store_available():
                    return
                self.index_grant(grant_id)
            except Exception:
                # 失败已经写进 file_index_state；后台线程再抛只会留下一段没人看的回溯。
                pass
            finally:
                with self._lock:
                    self._running.pop(key, None)
                    self._stops.pop(key, None)
                    self._threads = [item for item in self._threads if item.is_alive()]

        thread = threading.Thread(
            target=worker,
            name="companion-file-index-{}".format(key[:12]),
            daemon=True,
        )
        with self._lock:
            self._threads.append(thread)
        thread.start()
        return True

    def shutdown(self, timeout: float = 5.0) -> None:
        """取消还没开跑的扫描并等线程退出：删库、迁移或退出前先把后台安静下来。"""
        self._shutdown.set()
        with self._lock:
            events = list(self._stops.values())
            threads = [item for item in self._threads if item.is_alive()]
        for event in events:
            event.set()
        deadline = time.monotonic() + max(0.0, float(timeout))
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            thread.join(timeout=remaining)

    def wait_for_index(self, timeout: float = 15.0) -> int:
        """等后台扫描收尾，返回仍在跑的线程数（对账路径与用例用）。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                threads = [item for item in self._threads if item.is_alive()]
            if not threads:
                return 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return len(threads)
            for thread in threads:
                thread.join(timeout=remaining)

    def index_status(self, grant_id) -> Dict:
        state = self.store.get_file_index_state(grant_id)
        if state is None:
            return {
                "grant_id": str(grant_id),
                "status": "idle",
                "files_indexed": 0,
                "files_skipped": 0,
                "last_error": "",
                "indexed_at": "",
            }
        state = dict(state)
        state["indexed_at"] = state.get("finished_at") or state.get("updated_at") or ""
        return state

    def mark_stale(self, grant_id) -> None:
        """文件在读取时对不上：标 stale，让下一次检索重扫，而不是继续信旧行。"""
        state = self.store.get_file_index_state(grant_id) or {}
        if state.get("status") == INDEX_RUNNING and self.is_indexing(grant_id):
            return
        self.store.save_file_index_state(grant_id, dict(state, status=INDEX_STALE))

    def reconcile(self, grant_id) -> Dict:
        """目录重新接上或事件不连续时重扫（§4.2 freshness）。"""
        return self.index_grant(grant_id)

    def _grant_gone(self, grant_id) -> bool:
        """撤销立刻停扫：用户说不许再看之后，扫描器不该继续往索引里写。

        数据库不在原地也按同样的方式收手——扫描到一半的目录树可能正是被删掉的临时目录，
        这时候多写一次都是往一个正在消失的库里开连接。
        """
        if not self._store_available():
            return True
        grant = self.access.get_grant(grant_id)
        return grant is None or grant.status != GRANT_ACTIVE

    # -- 检索 ---------------------------------------------------------------

    def search(
        self,
        term: str = "",
        grants: Optional[Sequence[AccessGrant]] = None,
        limit: int = DEFAULT_CANDIDATES,
        ext: str = "",
        modified_after: str = "",
        parent_key: Optional[str] = None,
    ) -> Dict:
        """先查索引；索引没建好的目录退化成有上限的实时扫描。

        每次检索都重新读授权表：撤销发生在上一轮，这一轮 ``file_catalog`` 里的目录行
        还在，也不能浮出候选。
        """
        chosen = list(grants) if grants is not None else self.indexable_grants()
        cap = max(1, min(int(limit), MAX_CANDIDATES))
        candidates: List[Dict] = []
        indexed_grants: List[str] = []
        live_grants: List[str] = []
        matched_capped = False
        for grant in chosen:
            state = self.store.get_file_index_state(grant.grant_id) or {}
            if state.get("status") in USABLE_INDEX_STATUSES:
                indexed_grants.append(grant.grant_id)
                rows = self.store.search_file_catalog(
                    [grant.grant_id],
                    term=term,
                    ext=ext,
                    modified_after=modified_after,
                    limit=CANDIDATE_SCAN_LIMIT,
                )
                if len(rows) >= CANDIDATE_SCAN_LIMIT:
                    matched_capped = True
                for row in rows:
                    candidates.append(
                        candidate_from_row(
                            row,
                            grant,
                            index_reason(
                                term,
                                str(row.get("name") or ""),
                                str(row.get("rel_path") or ""),
                                str(row.get("ext") or ""),
                            ),
                        )
                    )
                continue
            live_grants.append(grant.path)
            candidates.extend(self._live_candidates(grant, term, ext, modified_after))
        if parent_key is not None:
            wanted = relative_path_key(parent_key)
            candidates = [
                item
                for item in candidates
                if relative_path_key(str(Path(item["rel_path"]).parent)) == wanted
            ]
        ranked = rank_candidates(term, dedupe_candidates(candidates))
        return {
            "candidates": ranked[:cap],
            "matched": len(ranked),
            # 取候选时每个目录最多读 CANDIDATE_SCAN_LIMIT 行。撞到它时 matched 是
            # 一个下限而不是总数——说成总数，模型就会宣称"这个目录里只有这些文件"。
            "matched_capped": matched_capped,
            "limit": cap,
            "indexed_grants": indexed_grants,
            "live_scan_grants": live_grants,
        }

    def _live_candidates(
        self,
        grant: AccessGrant,
        term: str,
        ext: str = "",
        modified_after: str = "",
    ) -> List[Dict]:
        """索引没就绪时的兜底扫描：上限更低，候选也说明自己是实时扫出来的。"""
        try:
            entries, _ = scan_directory(
                Path(grant.canonical_root),
                max_files=LIVE_SCAN_MAX_FILES,
                max_depth=self.max_depth,
                skip_paths=self._private_paths(),
            )
        except OSError:
            return []
        needle = relative_path_key(term)
        wanted_ext = str(ext or "").lower().lstrip(".")
        after = str(modified_after or "").strip()
        found: List[Dict] = []
        for entry in entries:
            if needle and needle not in relative_path_key(entry["rel_path"]):
                continue
            if wanted_ext and str(entry["ext"]).lstrip(".") != wanted_ext:
                continue
            if after and str(entry["mtime"]) < after:
                continue
            row = dict(entry)
            # 引用里放真实相对路径而不是规范化后的键：读取那一步要把引用交给
            # WorkspaceGuard.resolve()，在非 Windows 文件系统上大小写是会要命的。
            row["file_ref"] = REF_PREFIX_LIVE + entry["rel_path"]
            found.append(
                candidate_from_row(
                    row,
                    grant,
                    "实时扫描（这个目录还没建好索引）",
                    from_index=False,
                )
            )
        return rank_candidates(term, found)[:MAX_CANDIDATES]

    # -- 读取 ---------------------------------------------------------------

    def resolve_reference(
        self,
        file_ref,
        capability: str = CAPABILITY_FS_READ,
    ) -> Tuple[Optional[Dict], Optional[AccessGrant], str]:
        """把 ``file_ref`` 换成（元数据行、授权、拒绝理由），全程不接受路径参数。

        能力名由调用方给：读取要 ``fs-read``，移动与回收站删除要 ``fs-write``/
        ``fs-delete``。引用本身只是"索引里的某一行"，它不代表任何一种权限。
        """
        text = str(file_ref or "").strip()
        if not text:
            return None, None, "缺少文件引用 file_ref。"
        if text.startswith(REF_PREFIX_LIVE):
            return self._resolve_live_ref(
                text[len(REF_PREFIX_LIVE):], capability
            )
        row = self.store.get_file_catalog_entry(text)
        if row is None:
            return None, None, "没找到这个文件引用，索引可能已重建，请重新搜索。"
        grant = self.access.get_grant(row["grant_id"])
        if grant is None:
            return None, None, "这条文件引用的授权记录不存在。"
        if grant.status != GRANT_ACTIVE:
            return None, None, "这个目录的授权已撤销，文件不再可读。"
        if not grant.has_capability(capability):
            return None, None, "这条授权不包含 {} 能力。".format(capability)
        usable, reason = self.access.check_identity(grant)
        if not usable:
            return None, None, reason
        return row, grant, ""

    def _resolve_live_ref(
        self,
        rel_path,
        capability: str = CAPABILITY_FS_READ,
    ) -> Tuple[Optional[Dict], Optional[AccessGrant], str]:
        rel_path = str(rel_path or "").strip("/")
        if not rel_path:
            return None, None, "实时引用缺少相对路径。"
        for grant in self.indexable_grants(capability):
            try:
                target = WorkspaceGuard(grant.canonical_root).resolve(rel_path)
            except PathSafetyError:
                continue
            if not target.is_file():
                continue
            info = target.lstat()
            return (
                {
                    "file_ref": REF_PREFIX_LIVE + rel_path,
                    "name": target.name,
                    "rel_path": relative_to_root(target, Path(grant.canonical_root)),
                    "ext": file_extension(target),
                    "size_bytes": int(info.st_size),
                    "mtime": file_mtime(info.st_mtime),
                    "volume_id": str(info.st_dev),
                    "file_id": str(info.st_ino),
                },
                grant,
                "",
            )
        # 引用没命中时先分清两种情况：文件确实在某条已授权目录里、只是那条授权不含
        # 这个能力，还是它真的不在任何授权目录里。把前者说清楚，用户才知道要去补授权，
        # 而不是以为搜索出错了。
        for grant in self.access.list_grants(status=GRANT_ACTIVE):
            if grant.has_capability(capability):
                continue
            try:
                candidate = WorkspaceGuard(grant.canonical_root).resolve(rel_path)
            except PathSafetyError:
                continue
            if candidate.is_file():
                return None, None, (
                    "这个文件在授权目录 {} 里，但那条授权不包含 {} 能力。".format(
                        grant.grant_id, capability
                    )
                )
        return None, None, "这个实时引用不在任何可读的授权目录里，或文件已不存在。"

    def read(self, file_ref, offset: int = 0, limit: int = READ_DEFAULT_CHARS) -> Dict:
        """读一段文本；读之前先复核文件还在、还是那个文件（§4.2 freshness）。"""
        row, grant, reason = self.resolve_reference(file_ref)
        if row is None or grant is None:
            raise FileIndexError(reason or "文件引用无法解析。", "file_ref_unresolved")
        try:
            target = WorkspaceGuard(grant.canonical_root).resolve(
                str(row["rel_path"]).replace("\\", "/")
            )
        except PathSafetyError as error:
            raise FileIndexError(str(error), "path_rejected") from error
        if not target.exists():
            self.mark_stale(grant.grant_id)
            raise FileIndexError(
                "文件已不存在，这个目录的索引已标记为待重建。", "file_missing"
            )
        info = target.lstat()
        if not stat_module.S_ISREG(info.st_mode):
            raise FileIndexError("目标不是普通文件。", "not_a_file")
        if target.is_symlink() or is_reparse_info(info):
            raise FileIndexError("不允许读取符号链接文件。", "reparse_point")
        recorded = (
            str(row.get("volume_id") or ""),
            str(row.get("file_id") or ""),
        )
        if all(part not in ("", "0") for part in recorded) and (
            str(info.st_dev) != recorded[0] or str(info.st_ino) != recorded[1]
        ):
            # 有些卷拿不到 inode（0/0），那时比身份只会误报"被替换"；这时仍靠
            # 存在性与下面的大小/修改时间对账。
            self.mark_stale(grant.grant_id)
            raise FileIndexError(
                "文件身份与索引不一致（可能已被替换），已标记待重建，请重新搜索。",
                "identity_changed",
            )
        if int(info.st_size) > READ_MAX_BYTES:
            raise FileIndexError(
                "文件超过 {} 字节，只按元数据处理，不整体读入。".format(READ_MAX_BYTES),
                "too_large",
            )
        suffix = Path(str(row["name"])).suffix.lower()
        if suffix and suffix not in DECODABLE_SUFFIXES:
            raise FileIndexError(
                "「{}」不在已支持的文本类型里，只返回元数据。".format(suffix),
                "unsupported_type",
            )
        try:
            data = target.read_bytes()
        except OSError as error:
            raise FileIndexError("读取失败：{}".format(error), "read_failed") from error
        if b"\x00" in data[:4096]:
            raise FileIndexError("文件看起来是二进制内容，不读进上下文。", "binary")
        text = read_text_bytes(data)
        start = max(0, int(offset))
        size = max(1, min(int(limit), READ_CHUNK_CHARS))
        chunk = text[start:start + size]
        return {
            "file_ref": str(row["file_ref"]),
            "name": str(row["name"]),
            "rel_path": str(row["rel_path"]),
            "grant_id": grant.grant_id,
            "directory": Path(grant.path).name or str(grant.path),
            "ext": file_extension(str(row["name"])),
            "size_bytes": int(info.st_size),
            "mtime": file_mtime(info.st_mtime),
            "text": chunk,
            "offset": start,
            "chars_returned": len(chunk),
            "total_chars": len(text),
            "next_offset": start + len(chunk),
            "truncated": start + len(chunk) < len(text),
        }


class TurnReadBroker:
    """本轮的读取租约：每个目录、每种能力一枚，逐次扣预算。

    租约在第一枚读取类工具真正要用时才签，而不是在回合开始——没碰文件的回合不该在
    ``grant_leases`` 里留下一行。每次调用过一枚操作，而 ``use_lease`` 每次都重读授权
    状态、能力集合和目录身份，所以中途撤销在下一句就生效。
    """

    def __init__(
        self,
        index: FileIndexService,
        task_id: str,
        access: Optional[AccessControlService] = None,
        max_operations: int = READ_LEASE_OPERATIONS,
        ttl_seconds: int = READ_LEASE_TTL_SECONDS,
    ):
        self.index = index
        self.access = access or index.access
        self.task_id = str(task_id or "")
        self.max_operations = max(1, int(max_operations))
        self.ttl_seconds = int(ttl_seconds)
        self._leases: Dict[Tuple[str, str], str] = {}

    def lease_for(self, grant_id, capability) -> str:
        key = (str(grant_id), str(capability))
        existing = self._leases.get(key)
        if existing:
            return existing
        lease = self.access.issue_lease(
            key[0],
            self.task_id,
            key[1],
            ttl_seconds=self.ttl_seconds,
            max_operations=self.max_operations,
        )
        self._leases[key] = lease.lease_id
        return lease.lease_id

    def spend(self, grant_id, capability) -> Dict:
        if not self.task_id:
            raise AccessControlError("缺少任务标识，无法签发读取租约。", "no_task")
        lease = self.access.use_lease(
            self.lease_for(grant_id, capability), task_id=self.task_id
        )
        used = int(lease.operations_used)
        return {
            "lease_id": lease.lease_id,
            "grant_id": str(grant_id),
            "capability": str(capability),
            "operations_used": used,
            "remaining_operations": max(0, self.max_operations - used),
        }

    def search(self, term: str = "", **kwargs) -> Dict:
        """检索：按目录逐个扣 fs-search 预算，预算耗尽的目录本轮不参与检索。

        所有目录都被拒绝时抛错，不返回空命中——空命中会被模型读成"这个文件不存在"，
        而真相是"这一轮不许再搜了"。
        """
        if not self.task_id:
            raise AccessControlError("缺少任务标识，无法签发检索租约。", "no_task")
        chosen = list(kwargs.pop("grants", None) or [])
        if not chosen:
            chosen = self.index.indexable_grants(CAPABILITY_FS_SEARCH)
        charged: List[AccessGrant] = []
        refused: List[str] = []
        refusal: Optional[AccessControlError] = None
        searchable = [
            grant for grant in chosen if grant.has_capability(CAPABILITY_FS_SEARCH)
        ]
        for grant in searchable:
            try:
                self.spend(grant.grant_id, CAPABILITY_FS_SEARCH)
                charged.append(grant)
            except AccessControlError as error:
                refusal = refusal or error
                refused.append(
                    "{}：{}".format(Path(grant.path).name or grant.path, error)
                )
        limit = max(1, int(kwargs.get("limit") or DEFAULT_CANDIDATES))
        if not charged:
            if searchable and refusal is not None:
                raise refusal
            return {
                "candidates": [],
                "matched": 0,
                "matched_capped": False,
                "limit": limit,
                "indexed_grants": [],
                "live_scan_grants": [],
                "refused": ["没有可检索的授权目录。"],
            }
        result = self.index.search(term=term, grants=charged, **kwargs)
        if refused:
            result["refused"] = refused
        return result

    def read(self, file_ref: str, **kwargs) -> Dict:
        """先扣预算再读：预算用尽时连文件都不该被打开。"""
        row, grant, reason = self.index.resolve_reference(file_ref)
        if grant is None or row is None:
            raise FileIndexError(reason or "文件引用无法解析。", "file_ref_unresolved")
        budget = self.spend(grant.grant_id, CAPABILITY_FS_READ)
        result = self.index.read(file_ref=file_ref, **kwargs)
        result["lease"] = budget
        return result

    def close(self) -> int:
        if not self.task_id:
            return 0
        return self.access.release_task(self.task_id)


def read_index_state(connection) -> Dict:
    """只读地描述索引进度，供 doctor 使用。

    和授权检查一样：自检绝不写库。一个"顺手把状态改成 ready"的自检，
    会把"这个目录早就打不开了"看成"现在又好了"。
    """
    try:
        grant_rows = connection.execute(
            """
            SELECT grant_id, path, capabilities, status, volume_id, file_id
            FROM access_grants
            WHERE resource_kind = 'directory'
            ORDER BY status, updated_at DESC
            """
        ).fetchall()
    except sqlite3.Error:
        return {
            "available": False,
            "reason": "access_grants 表不存在（数据库尚未迁移到 v13）",
        }
    try:
        count_rows = {
            str(row["grant_id"]): (int(row["active"]), int(row["gone"]))
            for row in connection.execute(
                """
                SELECT grant_id,
                       SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN status = 'deleted' THEN 1 ELSE 0 END) AS gone
                FROM file_catalog
                GROUP BY grant_id
                """
            ).fetchall()
        }
    except sqlite3.Error:
        return {
            "available": False,
            "reason": "file_catalog 表不存在（数据库尚未迁移到 v14）",
        }
    try:
        states = {
            str(row["grant_id"]): dict(row)
            for row in connection.execute(
                """
                SELECT grant_id, status, files_indexed, files_skipped,
                       last_error, started_at, finished_at, updated_at
                FROM file_index_state
                """
            ).fetchall()
        }
    except sqlite3.Error:
        states = {}
    # 数据目录自己不是"待索引的目录"（见 is_agent_data_directory）：从连接自己的
    # 库文件位置推出来，否则自检会一直Warn 一个永远扫不完的目录。
    data_key = ""
    try:
        for row in connection.execute("PRAGMA database_list").fetchall():
            if str(row[1]) == "main" and row[2]:
                data_key = path_key(Path(str(row[2])).parent)
                break
    except sqlite3.Error:
        data_key = ""
    directories = []
    for row in grant_rows:
        value = dict(row)
        try:
            capabilities = sorted(json.loads(value.get("capabilities") or "[]"))
        except ValueError:
            capabilities = []
        state = states.get(str(value["grant_id"]), {})
        active, gone = count_rows.get(str(value["grant_id"]), (0, 0))
        own_data = bool(data_key) and bool(value["path"]) and path_key(value["path"]) == data_key
        entry = {
            "grant_id": str(value["grant_id"]),
            "path": str(value["path"] or ""),
            "grant_status": str(value["status"] or ""),
            "capabilities": capabilities,
            "searchable": CAPABILITY_FS_SEARCH in capabilities and not own_data,
            "own_data": own_data,
            "status": str(state.get("status") or "idle"),
            "files_indexed": int(state.get("files_indexed") or 0),
            "files_skipped": int(state.get("files_skipped") or 0),
            "catalog_rows": active,
            "deleted_rows": gone,
            "last_error": str(state.get("last_error") or ""),
            "indexed_at": str(
                state.get("finished_at") or state.get("updated_at") or ""
            ),
            "directory_present": False,
        }
        if entry["grant_status"] == GRANT_ACTIVE and entry["path"]:
            try:
                entry["directory_present"] = Path(value["path"]).is_dir()
            except OSError:
                entry["directory_present"] = False
        directories.append(entry)
    live = [item for item in directories if item["grant_status"] == GRANT_ACTIVE]
    searchable = [item for item in live if item["searchable"]]
    return {
        "available": True,
        "reason": "",
        "directories": directories,
        "granted_count": len(live),
        "searchable_count": len(searchable),
        "never_indexed": len(
            [item for item in searchable if item["status"] in ("idle", "")]
        ),
        "failed": len([item for item in searchable if item["status"] == INDEX_FAILED]),
        "stale": len([item for item in searchable if item["status"] == INDEX_STALE]),
        "missing_directories": len(
            [item for item in searchable if not item["directory_present"]]
        ),
        "catalog_rows": sum(item["catalog_rows"] for item in directories),
        "deleted_rows": sum(item["deleted_rows"] for item in directories),
    }


def index_state_report(database_path) -> Dict:
    """以只读方式打开数据库并描述索引状态。"""
    uri = "{}?mode=ro".format(Path(database_path).resolve().as_uri())
    connection = sqlite3.connect(uri, uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        return read_index_state(connection)
    finally:
        connection.close()
