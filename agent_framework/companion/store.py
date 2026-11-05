"""SQLite persistence for companion state and execution audit records."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    ActionProposal,
    ActionResult,
    AffectRecord,
    APPROVAL_PENDING,
    AgentMode,
    EXECUTOR_NOTE,
    ExperienceRecord,
    FORWARD_UNKNOWN,
    MemoryRecord,
    ProactiveEvent,
    ProactiveState,
    ProposalStatus,
    ReminderRecord,
    RiskLevel,
    TaskCard,
    WORK_EXECUTING,
    WORK_NORMAL,
    WORK_PLAN_DISCUSSING,
    WORK_PLAN_REVIEW,
    WorkReceipt,
    WorkTask,
    new_id,
    utc_now,
)
from .textseg import fts_match_query, fts_segment
from .redaction import redact_text
from .path_safety import path_key, resource_identity
from . import settings as app_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS action_proposals (
    action_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    intent TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    raw_arguments TEXT NOT NULL,
    normalized_arguments TEXT NOT NULL,
    expected_state TEXT NOT NULL,
    risk TEXT NOT NULL,
    target_summary TEXT NOT NULL,
    reversible INTEGER NOT NULL,
    user_visible_plan TEXT NOT NULL,
    confirmation_digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_proposals_pending
    ON action_proposals(session_id, status, created_at);

CREATE TABLE IF NOT EXISTS action_receipts (
    receipt_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    result_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    error TEXT NOT NULL,
    undoable INTEGER NOT NULL,
    undo_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES action_proposals(action_id)
);

CREATE TABLE IF NOT EXISTS execution_ledger (
    action_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    receipt_id TEXT,
    workspace_json TEXT NOT NULL,
    target_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES action_proposals(action_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_ledger_status
    ON execution_ledger(status);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_message_id INTEGER,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    activation REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_memories_session
    ON memories(session_id, status, updated_at);

CREATE TABLE IF NOT EXISTS experiences (
    experience_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_message_id INTEGER,
    receipt_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS affect_states (
    affect_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    target TEXT NOT NULL,
    mood_label TEXT NOT NULL,
    valence REAL NOT NULL,
    arousal REAL NOT NULL,
    energy REAL,
    confidence REAL NOT NULL,
    source_message_id INTEGER,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_affect_latest
    ON affect_states(session_id, target, created_at);

CREATE TABLE IF NOT EXISTS workspace_grants (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    path TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


SCHEMA_VERSION = 13

MEMORY_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED,
    seg
);
"""

MEMORY_LIFECYCLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_evidence (
    evidence_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    source_role TEXT NOT NULL,
    quote TEXT NOT NULL,
    quote_start INTEGER NOT NULL,
    quote_end INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id),
    FOREIGN KEY(message_id) REFERENCES messages(id),
    UNIQUE(memory_id, message_id, quote)
);

CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory
    ON memory_evidence(memory_id, created_at);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    content TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_session
    ON memory_candidates(session_id, status, updated_at);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_events_memory
    ON memory_events(memory_id, created_at);

CREATE TABLE IF NOT EXISTS recall_log (
    recall_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    fact_ids TEXT NOT NULL,
    scores TEXT NOT NULL,
    used_in_reply INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recall_log_session
    ON recall_log(session_id, created_at);

CREATE TABLE IF NOT EXISTS memory_tombstones (
    tombstone_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    forget_epoch INTEGER NOT NULL,
    through_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_tombstones_session
    ON memory_tombstones(session_id, canonical_key);
"""

MEMORY_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_relations (
    relation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_memory_id TEXT NOT NULL,
    child_memory_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(parent_memory_id) REFERENCES memories(memory_id),
    FOREIGN KEY(child_memory_id) REFERENCES memories(memory_id),
    UNIQUE(parent_memory_id, child_memory_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_memory_relations_parent
    ON memory_relations(session_id, parent_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_relations_child
    ON memory_relations(session_id, child_memory_id);
"""

IMPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS import_jobs (
    job_id TEXT PRIMARY KEY,
    source_fingerprint TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    target_session_id TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 0,
    total_units INTEGER NOT NULL DEFAULT 0,
    imported_messages INTEGER NOT NULL DEFAULT 0,
    imported_memories INTEGER NOT NULL DEFAULT 0,
    skipped_duplicates INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_messages (
    message_hash TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    imported_message_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    through_message_id INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    summary TEXT NOT NULL,
    summary_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_checkpoints_session
    ON context_checkpoints(session_id, through_message_id);
"""

PROACTIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    reminder_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    source_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_reminders_due
    ON reminders(session_id, status, due_at);
"""

PROACTIVE_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS proactive_state (
    session_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    quiet_start INTEGER NOT NULL DEFAULT 22,
    quiet_end INTEGER NOT NULL DEFAULT 8,
    daily_limit INTEGER NOT NULL DEFAULT 2,
    min_interval_hours REAL NOT NULL DEFAULT 3.0,
    max_unanswered INTEGER NOT NULL DEFAULT 2,
    include_memory INTEGER NOT NULL DEFAULT 0,
    daily_count INTEGER NOT NULL DEFAULT 0,
    daily_date TEXT NOT NULL DEFAULT '',
    last_spoken_at TEXT,
    last_event_id TEXT NOT NULL DEFAULT '',
    unanswered_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_message_id INTEGER,
    trigger_reason TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    content_hash TEXT NOT NULL DEFAULT '',
    url_hash TEXT NOT NULL DEFAULT '',
    read_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proactive_events_session
    ON proactive_events(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_proactive_events_source
    ON proactive_events(session_id, source_type, source_id);
"""

WORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    state TEXT NOT NULL,
    title TEXT NOT NULL,
    reason TEXT NOT NULL,
    spoken_summary TEXT NOT NULL,
    goal TEXT NOT NULL,
    executor TEXT NOT NULL,
    project TEXT NOT NULL,
    source_message_id INTEGER,
    info_asked INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    approval_status TEXT NOT NULL DEFAULT 'pending',
    approval_reason TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT NOT NULL DEFAULT '',
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_work_tasks_session
    ON work_tasks(session_id, state, updated_at);

CREATE TABLE IF NOT EXISTS work_receipts (
    receipt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES work_tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_work_receipts_session
    ON work_receipts(session_id, status, updated_at);
"""


SKILL_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_audit (
    audit_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    action TEXT NOT NULL,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_skill_audit_session
    ON skill_audit(session_id, created_at);
"""


SESSION_DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_drafts (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""


APP_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


DOCUMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    persona_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    converted INTEGER NOT NULL DEFAULT 0,
    chunks INTEGER NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_persona
    ON documents(persona_id, created_at);

CREATE TABLE IF NOT EXISTS doc_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    chars INTEGER NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(document_id)
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_document
    ON doc_chunks(document_id, ordinal);
"""

# FTS5 lives apart from DOCUMENT_SCHEMA so a build without the extension only
# loses lexical doc search, exactly like memory_fts does for memories.
DOC_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    chunk_id UNINDEXED,
    seg
);
"""


# The user's MCP servers. Credentials are deliberately absent: an env var or a
# header that carries a token lives in config/mcp_keys.json, so a backup of this
# database can be handed to anyone without leaking a key.
MCP_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    server_id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'stdio',
    command TEXT NOT NULL DEFAULT '',
    args TEXT NOT NULL DEFAULT '[]',
    cwd TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    timeout REAL NOT NULL DEFAULT 15,
    enabled INTEGER NOT NULL DEFAULT 0,
    tools TEXT NOT NULL DEFAULT '[]',
    discovered TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_enabled
    ON mcp_servers(enabled, server_id);
"""


# 桌面执行代理 S1：目录授权与任务级短期租约。
#
# 一行代表"这个目录"，不是一行代表"一项能力"：合并授权卡一次勾走读/写/搜索，
# 撤销也按整条目录撤，拆成多行会让"撤回了哪些权限"无法一句话回答。
# 凭据目录的覆盖位留在行上，授权状态本身可审计，不藏在调用方内存里。
ACCESS_GRANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_grants (
    grant_id TEXT PRIMARY KEY,
    resource_kind TEXT NOT NULL DEFAULT 'directory',
    path TEXT NOT NULL DEFAULT '',
    path_key TEXT NOT NULL DEFAULT '',
    volume_id TEXT NOT NULL DEFAULT '',
    file_id TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'grant',
    sensitivity_override INTEGER NOT NULL DEFAULT 0,
    legacy_workspace INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL DEFAULT '',
    revoked_at TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_access_grants_path
    ON access_grants(resource_kind, path_key)
    WHERE resource_kind = 'directory';

CREATE INDEX IF NOT EXISTS idx_access_grants_status
    ON access_grants(status, resource_kind);

CREATE TABLE IF NOT EXISTS grant_leases (
    lease_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    resource_ids TEXT NOT NULL DEFAULT '[]',
    nonce TEXT NOT NULL,
    confirm_digest TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    max_operations INTEGER NOT NULL DEFAULT 1,
    operations_used INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    released_at TEXT NOT NULL,
    FOREIGN KEY(grant_id) REFERENCES access_grants(grant_id)
);

CREATE INDEX IF NOT EXISTS idx_grant_leases_task
    ON grant_leases(task_id, status);

CREATE INDEX IF NOT EXISTS idx_grant_leases_active
    ON grant_leases(status, expires_at);
"""

# 旧 workspace_grants 只有一行，它的授权面就是"在这目录里写笔记"。提升时按
# P1 的三档勾选补齐读/写/搜索，但不给删除和外发——那两项从来没有被用户授予过。
LEGACY_WORKSPACE_CAPABILITIES = ("fs-read", "fs-write", "fs-search")


def _add_column_if_missing(connection, table, column, definition):
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info({})".format(table)
        ).fetchall()
    }
    if not existing:
        # 半迁移的库（迁移中途崩溃后重开）可能还没有这张表；跳过它让梯子继续，
        # 缺的表由 SCHEMA 或它自己的迁移步骤补上。
        return
    if column not in existing:
        connection.execute(
            "ALTER TABLE {} ADD COLUMN {} {}".format(
                table,
                column,
                definition,
            )
        )


# The server row is stored as the user typed it, so these three helpers are the
# only place that decides how a command, a list or a JSON column is represented.
MAX_MCP_STORED_DESCRIPTION = 400


def _mcp_json(value) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return "[]"
    if not isinstance(value, (list, tuple, dict)):
        return "[]"
    return json.dumps(list(value) if isinstance(value, (list, tuple)) else value, ensure_ascii=False)


def _mcp_command_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(item) for item in command if str(item).strip())
    return str(command or "").strip()


def _mcp_server_from_row(row) -> Dict:
    value = dict(row)
    value["args"] = json.loads(value.get("args") or "[]")
    value["tools"] = json.loads(value.get("tools") or "[]")
    value["discovered"] = json.loads(value.get("discovered") or "[]")
    value["enabled"] = bool(value.get("enabled"))
    return value


def _migrate_v1(connection):
    _add_column_if_missing(
        connection,
        "memories",
        "canonical_key",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "evidence_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "protected",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "merged_from_ids",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "forget_epoch",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "first_seen_at",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        connection,
        "memories",
        "last_seen_at",
        "TEXT NOT NULL DEFAULT ''",
    )
    connection.executescript(MEMORY_LIFECYCLE_SCHEMA)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_key
            ON memories(session_id, canonical_key, status)
        """
    )


def _migrate_v2(connection):
    """召回索引：recall_log.source 列 + FTS5 虚表与全量回填。

    FTS5 不可用（编译选项缺失）时不抛错：只跳过虚表，
    召回自动降级为关键词路径，聊天主流程不受影响。
    """
    _add_column_if_missing(
        connection,
        "recall_log",
        "source",
        "TEXT NOT NULL DEFAULT ''",
    )
    try:
        connection.executescript(MEMORY_FTS_SCHEMA)
    except sqlite3.Error:
        return
    _reindex_memory_fts(connection)


def _migrate_v3(connection):
    """派生关系、经历状态与旧聊天导入账本。"""
    connection.executescript(MEMORY_GRAPH_SCHEMA)
    connection.executescript(IMPORT_SCHEMA)
    _add_column_if_missing(
        connection,
        "experiences",
        "memory_id",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        connection,
        "experiences",
        "status",
        "TEXT NOT NULL DEFAULT 'active'",
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_experiences_memory
            ON experiences(session_id, memory_id)
        """
    )


def _migrate_v4(connection):
    """上下文压缩 checkpoint（每会话只保留最新一条）。"""
    connection.executescript(CONTEXT_SCHEMA)


def _migrate_v5(connection):
    """主动陪伴底座：本地提醒表（到点查询按 pending + due_at 索引）。"""
    connection.executescript(PROACTIVE_SCHEMA)


def _migrate_v6(connection):
    """主动陪伴主体：会话级策略状态与主动发言触发日志。"""
    connection.executescript(PROACTIVE_STATE_SCHEMA)


def _migrate_v7(connection):
    """工作任务卡：可审阅计划状态与转发回执。"""
    connection.executescript(WORK_SCHEMA)


def _migrate_v8(connection):
    """技能调用审计：记录调用、参考读取与越权拒绝。"""
    connection.executescript(SKILL_AUDIT_SCHEMA)


def _migrate_v9(connection):
    """会话管理：自定义标题、置顶、未读标记与每会话草稿。"""
    _add_column_if_missing(
        connection,
        "sessions",
        "title",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        connection,
        "sessions",
        "pinned_at",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        connection,
        "sessions",
        "unread",
        "INTEGER NOT NULL DEFAULT 0",
    )
    connection.executescript(SESSION_DRAFT_SCHEMA)


def _migrate_v10(connection):
    """应用级设置：模型推理与记忆召回偏好，单行 JSON blob。"""
    connection.executescript(APP_SETTINGS_SCHEMA)


def _migrate_v11(connection):
    """文档库：documents/doc_chunks 与 recall_log 的召回范围列。"""
    _add_column_if_missing(
        connection,
        "recall_log",
        "scope",
        "TEXT NOT NULL DEFAULT 'memory'",
    )
    connection.executescript(DOCUMENT_SCHEMA)


def _migrate_v12(connection):
    """MCP 治理：mcp_servers 表与 action_proposals 的执行器分派列。"""
    _add_column_if_missing(
        connection,
        "action_proposals",
        "executor",
        "TEXT NOT NULL DEFAULT 'note'",
    )
    connection.executescript(MCP_SCHEMA)


def _migrate_v13(connection):
    """桌面执行代理 S1：目录授权表、任务级短期租约表与旧工作区提升。"""
    connection.executescript(ACCESS_GRANT_SCHEMA)
    _promote_legacy_workspace_grant(connection)


def _access_json_list(value) -> str:
    """Store a capability or resource list as JSON, never as a bare string."""
    if value is None:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return "[]"
    if isinstance(value, (list, tuple, set)):
        value = sorted({str(item) for item in value if str(item).strip()})
    else:
        return "[]"
    return json.dumps(list(value), ensure_ascii=False)


def _directory_identity_or_empty(path) -> Dict:
    """Best-effort identity: a grant to a currently missing directory is still a
    fact the user authorized, it just cannot be used until the directory returns."""
    try:
        return resource_identity(path)
    except (OSError, sqlite3.Error):
        return {"root": "", "device": "", "file_id": ""}


def _access_grant_from_row(row) -> Optional[Dict]:
    if row is None:
        return None
    value = dict(row)
    value["capabilities"] = json.loads(value.get("capabilities") or "[]")
    value["sensitivity_override"] = bool(value.get("sensitivity_override"))
    value["legacy_workspace"] = bool(value.get("legacy_workspace"))
    return value


def _grant_lease_from_row(row) -> Optional[Dict]:
    if row is None:
        return None
    value = dict(row)
    value["resource_ids"] = json.loads(value.get("resource_ids") or "[]")
    value["max_operations"] = int(value.get("max_operations") or 0)
    value["operations_used"] = int(value.get("operations_used") or 0)
    return value


def _promote_legacy_workspace_grant(connection):
    """Copy the pre-v13 single workspace row into access_grants once.

    Called both from the migration and on every launch: an archive exported before
    v13 carries only ``workspace_grants``, and importing it must not silently drop
    the user's authorization.

    The old row is disabled either way. Leaving it enabled would resurrect a
    workspace the user revoked as soon as the app restarts, because nothing writes
    that table any more.
    """
    try:
        legacy = connection.execute(
            "SELECT path, created_at, enabled FROM workspace_grants WHERE id = 1"
        ).fetchone()
    except sqlite3.Error:
        return
    if legacy is None or not int(legacy["enabled"] or 0):
        return
    already = connection.execute(
        "SELECT grant_id FROM access_grants WHERE legacy_workspace = 1 LIMIT 1"
    ).fetchone()
    if already is None:
        path = str(legacy["path"] or "").strip()
        if path:
            identity = _directory_identity_or_empty(path)
            now = utc_now()
            connection.execute(
                """
                INSERT INTO access_grants(
                    grant_id, resource_kind, path, path_key, volume_id, file_id,
                    capabilities, status, source, legacy_workspace,
                    created_at, updated_at
                ) VALUES (?, 'directory', ?, ?, ?, ?, ?, 'active', 'workspace', 1, ?, ?)
                ON CONFLICT(resource_kind, path_key)
                    WHERE resource_kind = 'directory' DO UPDATE SET
                    status = 'active',
                    legacy_workspace = 1,
                    revoked_at = '',
                    updated_at = excluded.updated_at
                """,
                (
                    new_id("grant"),
                    path,
                    path_key(path),
                    identity["device"],
                    identity["file_id"],
                    _access_json_list(LEGACY_WORKSPACE_CAPABILITIES),
                    str(legacy["created_at"] or now),
                    now,
                ),
            )
    connection.execute("UPDATE workspace_grants SET enabled = 0 WHERE id = 1")


def _reindex_doc_fts(connection):
    """从 doc_chunks 全量重建 doc_fts 索引（幂等）。"""
    connection.execute("DELETE FROM doc_fts")
    rows = connection.execute(
        "SELECT chunk_id, text FROM doc_chunks"
    ).fetchall()
    connection.executemany(
        "INSERT INTO doc_fts(chunk_id, seg) VALUES (?, ?)",
        [(row["chunk_id"], fts_segment(row["text"])) for row in rows],
    )


def _reindex_memory_fts(connection):
    """从 memories 全量重建 memory_fts 索引（幂等）。"""
    connection.execute("DELETE FROM memory_fts")
    rows = connection.execute(
        "SELECT memory_id, content FROM memories"
    ).fetchall()
    connection.executemany(
        "INSERT INTO memory_fts(memory_id, seg) VALUES (?, ?)",
        [(row["memory_id"], fts_segment(row["content"])) for row in rows],
    )


class CompanionStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup_path = ""
        self.migration_backup_error = ""
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(str(self.database_path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self):
        self.fts_enabled = False
        self.doc_fts_enabled = False
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version < SCHEMA_VERSION:
                has_data = version > 0
                if not has_data:
                    try:
                        has_data = (
                            int(
                                connection.execute(
                                    "SELECT COUNT(*) FROM messages"
                                ).fetchone()[0]
                            )
                            > 0
                        )
                    except sqlite3.Error:
                        has_data = False
                if has_data:
                    connection.commit()
                    try:
                        from .maintenance import create_backup_from_connection

                        info = create_backup_from_connection(
                            connection,
                            self.database_path.parent,
                            self.database_path,
                            reason="pre-migration",
                            schema_version=version,
                        )
                        self.migration_backup_path = info["path"]
                    except Exception as error:
                        self.migration_backup_error = str(error)
                if version < 1:
                    _migrate_v1(connection)
                    version = 1
                if version < 2:
                    _migrate_v2(connection)
                    version = 2
                if version < 3:
                    _migrate_v3(connection)
                    version = 3
                if version < 4:
                    _migrate_v4(connection)
                    version = 4
                if version < 5:
                    _migrate_v5(connection)
                    version = 5
                if version < 6:
                    _migrate_v6(connection)
                    version = 6
                if version < 7:
                    _migrate_v7(connection)
                    version = 7
                if version < 8:
                    _migrate_v8(connection)
                    version = 8
                if version < 9:
                    _migrate_v9(connection)
                    version = 9
                if version < 10:
                    _migrate_v10(connection)
                    version = 10
                if version < 11:
                    _migrate_v11(connection)
                    version = 11
                if version < 12:
                    _migrate_v12(connection)
                    version = 12
                if version < 13:
                    _migrate_v13(connection)
                    version = 13
            connection.execute(
                "PRAGMA user_version = {}".format(int(version))
            )
            if int(version) >= 13:
                # Self-heal, not just a migration step: a pre-v13 archive imported
                # into a v13 database carries its workspace in the old table only.
                _promote_legacy_workspace_grant(connection)
            self.fts_enabled = self._ensure_memory_fts(connection)
            self.doc_fts_enabled = self._ensure_doc_fts(connection)

    @staticmethod
    def _ensure_memory_fts(connection) -> bool:
        """启动时自愈 FTS 索引：缺表建表、行数不符则重建。

        任何异常都只关闭 FTS 召回（降级关键词），不阻塞启动。
        """
        try:
            connection.executescript(MEMORY_FTS_SCHEMA)
            indexed = connection.execute(
                "SELECT COUNT(*) AS value FROM memory_fts"
            ).fetchone()["value"]
            total = connection.execute(
                "SELECT COUNT(*) AS value FROM memories"
            ).fetchone()["value"]
            if int(indexed) != int(total):
                _reindex_memory_fts(connection)
            connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()
            return True
        except sqlite3.Error:
            return False

    @staticmethod
    def _ensure_doc_fts(connection) -> bool:
        """文档索引自愈：缺表建表、行数不符则全量重建。

        与 ``_ensure_memory_fts`` 同理：任何异常只关闭词法文档检索，
        不阻塞启动，也不影响记忆召回。
        """
        try:
            connection.executescript(DOC_FTS_SCHEMA)
            indexed = connection.execute(
                "SELECT COUNT(*) AS value FROM doc_fts"
            ).fetchone()["value"]
            total = connection.execute(
                "SELECT COUNT(*) AS value FROM doc_chunks"
            ).fetchone()["value"]
            if int(indexed) != int(total):
                _reindex_doc_fts(connection)
            connection.execute("SELECT COUNT(*) FROM doc_fts").fetchone()
            return True
        except sqlite3.Error:
            return False

    def ensure_session(self, session_id: str, mode: AgentMode):
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, mode, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    mode = excluded.mode,
                    updated_at = excluded.updated_at
                """,
                (session_id, AgentMode(mode).value, now, now),
            )

    def set_session_mode(self, session_id: str, mode: AgentMode):
        self.ensure_session(session_id, mode)

    def get_session_mode(self, session_id: str) -> Optional[AgentMode]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT mode FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return AgentMode(row["mode"]) if row else None

    def list_sessions(self):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_session_summaries(self) -> List[Dict]:
        """One row per session with the first user topic and last message."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.mode, s.created_at, s.updated_at,
                       s.title, s.pinned_at, s.unread,
                       (SELECT COUNT(*) FROM messages m
                        WHERE m.session_id = s.id) AS message_count,
                       (SELECT m.content FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'user'
                        ORDER BY m.id ASC LIMIT 1) AS topic,
                       (SELECT m.role FROM messages m
                        WHERE m.session_id = s.id
                        ORDER BY m.id DESC LIMIT 1) AS last_role,
                       (SELECT m.content FROM messages m
                        WHERE m.session_id = s.id
                        ORDER BY m.id DESC LIMIT 1) AS last_content,
                       (SELECT m.created_at FROM messages m
                        WHERE m.session_id = s.id
                        ORDER BY m.id DESC LIMIT 1) AS last_at
                FROM sessions s
                ORDER BY (s.pinned_at <> '') DESC,
                         s.pinned_at DESC,
                         s.updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_session(self, session_id: str, title: str) -> bool:
        title = str(title or "").strip()[:60]
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            return cursor.rowcount > 0

    def set_session_pinned(self, session_id: str, pinned: bool = True) -> bool:
        value = utc_now() if pinned else ""
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET pinned_at = ? WHERE id = ?",
                (value, session_id),
            )
            return cursor.rowcount > 0

    def set_session_unread(self, session_id: str, unread: bool = True) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET unread = ? WHERE id = ?",
                (1 if unread else 0, session_id),
            )
            return cursor.rowcount > 0

    def get_draft(self, session_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT content FROM session_drafts WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return str(row["content"]) if row else ""

    def set_draft(self, session_id: str, content: str) -> bool:
        content = str(content or "")
        with self._connection() as connection:
            if not content.strip():
                connection.execute(
                    "DELETE FROM session_drafts WHERE session_id = ?",
                    (session_id,),
                )
                return True
            cursor = connection.execute(
                """
                INSERT INTO session_drafts(session_id, content, updated_at)
                SELECT ?, ?, ? WHERE EXISTS (
                    SELECT 1 FROM sessions WHERE id = ?
                )
                ON CONFLICT(session_id) DO UPDATE SET
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (session_id, content, utc_now(), session_id),
            )
            return cursor.rowcount > 0

    def schema_version(self) -> int:
        """``PRAGMA user_version`` as it currently stands on disk."""
        with self._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def load_settings(self) -> Optional[Dict[str, Any]]:
        """Stored settings, or None when nothing has been saved yet."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT data FROM app_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["data"]))
        except ValueError:
            return None
        return app_settings.normalize(payload)

    def save_settings(self, values) -> Dict[str, Any]:
        clean = app_settings.normalize(values)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(id, data, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    json.dumps(clean, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
        return clean

    def delete_session(self, session_id: str) -> bool:
        """Remove one session and every row scoped to it, including drafts."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        # Child tables first so foreign keys never block the session delete.
        tables = (
            "memory_evidence",
            "memory_relations",
            "memory_candidates",
            "memory_events",
            "action_receipts",
            "execution_ledger",
            "work_receipts",
            "messages",
            "action_proposals",
            "work_tasks",
            "memories",
            "experiences",
            "affect_states",
            "skill_audit",
            "recall_log",
            "memory_tombstones",
            "context_checkpoints",
            "reminders",
            "proactive_state",
            "proactive_events",
            "session_drafts",
        )
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not exists:
                return False
            memory_ids = [
                row["memory_id"]
                for row in connection.execute(
                    "SELECT memory_id FROM memories WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
            ]
            for table in tables:
                connection.execute(
                    "DELETE FROM {} WHERE session_id = ?".format(table),
                    (session_id,),
                )
            connection.execute(
                "DELETE FROM sessions WHERE id = ?",
                (session_id,),
            )
            if memory_ids:
                try:
                    connection.executemany(
                        "DELETE FROM memory_fts WHERE memory_id = ?",
                        [(memory_id,) for memory_id in memory_ids],
                    )
                except sqlite3.Error:
                    pass
        return True

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        mode: AgentMode,
    ) -> int:
        self.ensure_session(session_id, mode)
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages(session_id, role, content, mode, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, AgentMode(mode).value, now),
            )
            return int(cursor.lastrowid)

    def list_messages(
        self,
        session_id: str,
        limit: int = 30,
    ) -> List[Dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, role, content, mode, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def list_messages_after(
        self,
        session_id: str,
        after_id: int = 0,
        limit: int = 400,
    ) -> List[Dict]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, role, content, mode, created_at
                FROM messages
                WHERE session_id = ? AND id > ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    session_id,
                    int(after_id or 0),
                    max(1, min(int(limit), 1000)),
                ),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def delete_message(
        self,
        message_id: int,
        session_id: Optional[str] = None,
    ) -> bool:
        query = "DELETE FROM messages WHERE id = ?"
        parameters = [int(message_id)]
        if session_id:
            query += " AND session_id = ?"
            parameters.append(session_id)
        with self._connection() as connection:
            cursor = connection.execute(query, tuple(parameters))
            return cursor.rowcount == 1

    def clear_messages(self, session_id: str):
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM context_checkpoints WHERE session_id = ?",
                (session_id,),
            )

    def save_context_checkpoint(
        self,
        session_id: str,
        through_message_id: int,
        message_count: int,
        summary: str,
    ) -> str:
        checkpoint_id = new_id("ctx")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO context_checkpoints(
                    checkpoint_id, session_id, through_message_id,
                    message_count, summary, summary_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    session_id,
                    int(through_message_id),
                    int(message_count),
                    summary,
                    hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                    utc_now(),
                ),
            )
            connection.execute(
                """
                DELETE FROM context_checkpoints
                WHERE session_id = ? AND checkpoint_id <> ?
                  AND through_message_id <= ?
                """,
                (session_id, checkpoint_id, int(through_message_id)),
            )
        return checkpoint_id

    def get_latest_context_checkpoint(self, session_id: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT checkpoint_id, session_id, through_message_id,
                       message_count, summary, summary_hash, created_at
                FROM context_checkpoints
                WHERE session_id = ?
                ORDER BY through_message_id DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_proposal(self, proposal: ActionProposal):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO action_proposals(
                    action_id, session_id, mode, intent, tool_name,
                    raw_arguments, normalized_arguments, expected_state,
                    risk, target_summary, reversible, user_visible_plan,
                    confirmation_digest, idempotency_key, status,
                    created_at, updated_at, executor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.action_id,
                    proposal.session_id,
                    AgentMode(proposal.mode).value,
                    proposal.intent,
                    proposal.tool_name,
                    json.dumps(proposal.raw_arguments, ensure_ascii=False),
                    json.dumps(proposal.normalized_arguments, ensure_ascii=False),
                    json.dumps(proposal.expected_state, ensure_ascii=False),
                    RiskLevel(proposal.risk).value,
                    proposal.target_summary,
                    1 if proposal.reversible else 0,
                    proposal.user_visible_plan,
                    proposal.confirmation_digest,
                    proposal.idempotency_key,
                    ProposalStatus(proposal.status).value,
                    proposal.created_at,
                    proposal.updated_at,
                    str(proposal.executor or EXECUTOR_NOTE),
                ),
            )

    def get_proposal(self, action_id: str) -> Optional[ActionProposal]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM action_proposals WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return self._proposal_from_row(row) if row else None

    def list_pending_proposals(self, session_id: Optional[str] = None):
        with self._connection() as connection:
            if session_id:
                rows = connection.execute(
                    """
                    SELECT * FROM action_proposals
                    WHERE session_id = ? AND status = ?
                    ORDER BY created_at DESC
                    """,
                    (
                        session_id,
                        ProposalStatus.AWAITING_CONFIRMATION.value,
                    ),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM action_proposals
                    WHERE status = ?
                    ORDER BY created_at DESC
                    """,
                    (ProposalStatus.AWAITING_CONFIRMATION.value,),
                ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def list_proposals(
        self,
        session_id: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 50,
    ):
        values = [
            ProposalStatus(status).value for status in (statuses or [])
        ]
        clauses = []
        parameters = []
        if session_id:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if values:
            placeholders = ",".join("?" for _ in values)
            clauses.append("status IN ({})".format(placeholders))
            parameters.extend(values)
        where = " WHERE {}".format(" AND ".join(clauses)) if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM action_proposals{}
                ORDER BY created_at DESC
                LIMIT ?
                """.format(where),
                parameters,
            ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def update_proposal_status(self, action_id: str, status: ProposalStatus):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE action_proposals
                SET status = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (ProposalStatus(status).value, utc_now(), action_id),
            )

    def transition_proposal_status(
        self,
        action_id: str,
        from_status: ProposalStatus,
        to_status: ProposalStatus,
    ) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE action_proposals
                SET status = ?, updated_at = ?
                WHERE action_id = ? AND status = ?
                """,
                (
                    ProposalStatus(to_status).value,
                    utc_now(),
                    action_id,
                    ProposalStatus(from_status).value,
                ),
            )
            return cursor.rowcount == 1

    def save_receipt(self, receipt: ActionResult):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO action_receipts(
                    receipt_id, action_id, session_id, status, summary,
                    result_json, started_at, finished_at, error,
                    undoable, undo_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.action_id,
                    receipt.session_id,
                    receipt.status,
                    redact_text(receipt.summary),
                    json.dumps(receipt.result, ensure_ascii=False),
                    receipt.started_at,
                    receipt.finished_at,
                    redact_text(receipt.error),
                    1 if receipt.undoable else 0,
                    receipt.undo_status,
                    receipt.created_at,
                ),
            )

    def get_receipt(self, receipt_id: str) -> Optional[ActionResult]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM action_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        return self._receipt_from_row(row) if row else None

    def get_receipt_for_action(self, action_id: str) -> Optional[ActionResult]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM action_receipts WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return self._receipt_from_row(row) if row else None

    def list_receipts(self, session_id: str, limit: int = 20):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM action_receipts
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._receipt_from_row(row) for row in rows]

    def update_receipt_undo_status(self, receipt_id: str, status: str):
        with self._connection() as connection:
            connection.execute(
                "UPDATE action_receipts SET undo_status = ? WHERE receipt_id = ?",
                (status, receipt_id),
            )

    def save_receipt_once(self, receipt: ActionResult) -> ActionResult:
        try:
            self.save_receipt(receipt)
            return receipt
        except sqlite3.IntegrityError:
            existing = self.get_receipt_for_action(receipt.action_id)
            if existing is None:
                raise
            return existing

    def claim_execution(self, proposal: ActionProposal):
        workspace = proposal.expected_state.get("workspace") or {}
        target = proposal.expected_state.get("target")
        content = proposal.normalized_arguments.get("content", "")
        content_sha256 = hashlib.sha256(
            str(content).encode("utf-8")
        ).hexdigest()
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO execution_ledger(
                    action_id, idempotency_key, session_id, status, receipt_id,
                    workspace_json, target_path, content_sha256, before_json,
                    after_json, started_at, updated_at
                ) VALUES (?, ?, ?, 'running', NULL, ?, ?, ?, ?, '{}', ?, ?)
                """,
                (
                    proposal.action_id,
                    proposal.idempotency_key,
                    proposal.session_id,
                    json.dumps(workspace, ensure_ascii=False),
                    proposal.target_summary,
                    content_sha256,
                    json.dumps(target, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_ledger WHERE action_id = ?",
                (proposal.action_id,),
            ).fetchone()
            if row is None:
                conflict = connection.execute(
                    "SELECT action_id FROM execution_ledger WHERE idempotency_key = ?",
                    (proposal.idempotency_key,),
                ).fetchone()
                if conflict is not None:
                    raise ValueError("执行幂等键已被其他动作使用。")
        if row is None:
            raise ValueError("执行账本写入失败。")
        return cursor.rowcount == 1, dict(row)

    def mark_execution_state(self, action_id: str, status: str):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE execution_ledger
                SET status = ?, updated_at = ?
                WHERE action_id = ?
                  AND status IN ('running', 'dispatching', 'replaced')
                """,
                (status, utc_now(), action_id),
            )

    def finish_execution(
        self,
        action_id: str,
        status: str,
        receipt_id: str,
        after=None,
    ):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE execution_ledger
                SET status = ?, receipt_id = ?, after_json = ?, updated_at = ?
                WHERE action_id = ?
                  AND status IN ('running', 'dispatching', 'replaced')
                """,
                (
                    status,
                    receipt_id,
                    json.dumps(after, ensure_ascii=False),
                    utc_now(),
                    action_id,
                ),
            )

    def get_execution(self, action_id: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM execution_ledger WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return dict(row) if row else None

    def recover_incomplete_executions(self):
        recovered = []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM execution_ledger
                WHERE status IN ('running', 'dispatching', 'replaced')
                """
            ).fetchall()
        for row in rows:
            action_id = row["action_id"]
            receipt = self.get_receipt_for_action(action_id)
            if receipt is None:
                receipt = self.save_receipt_once(
                    ActionResult(
                        receipt_id=new_id("rcp"),
                        action_id=action_id,
                        session_id=row["session_id"],
                        status="unknown",
                        summary="上次执行未完成，结果不确定。",
                        result={
                            "recovered": True,
                            "target_path": row["target_path"],
                            "workspace": json.loads(row["workspace_json"]),
                            "before": json.loads(row["before_json"]),
                        },
                        started_at=row["started_at"],
                        finished_at=utc_now(),
                        error="process interrupted before a receipt was recorded",
                        undoable=False,
                    )
                )
            status = receipt.status
            proposal_status = {
                "completed": ProposalStatus.COMPLETED,
                "failed": ProposalStatus.FAILED,
                "not_executed": ProposalStatus.NOT_EXECUTED,
            }.get(status, ProposalStatus.UNKNOWN)
            ledger_status = (
                status
                if status in ("completed", "failed", "not_executed")
                else "unknown"
            )
            self.update_proposal_status(action_id, proposal_status)
            self.finish_execution(
                action_id,
                ledger_status,
                receipt.receipt_id,
                after=receipt.result.get("after"),
            )
            recovered.append(receipt)

        with self._connection() as connection:
            legacy = connection.execute(
                """
                SELECT action_id, session_id, updated_at
                FROM action_proposals
                WHERE status = ?
                """,
                (ProposalStatus.RUNNING.value,),
            ).fetchall()
        for row in legacy:
            if self.get_execution(row["action_id"]) is not None:
                continue
            receipt = self.get_receipt_for_action(row["action_id"])
            if receipt is None:
                receipt = self.save_receipt_once(
                    ActionResult(
                        receipt_id=new_id("rcp"),
                        action_id=row["action_id"],
                        session_id=row["session_id"],
                        status="unknown",
                        summary="上次执行未完成，结果不确定。",
                        result={"recovered": True},
                        started_at=row["updated_at"],
                        finished_at=utc_now(),
                        error="process interrupted before a receipt was recorded",
                        undoable=False,
                    )
                )
            self.update_proposal_status(row["action_id"], ProposalStatus.UNKNOWN)
            recovered.append(receipt)
        return recovered

    def create_work_task(self, task: WorkTask):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO work_tasks(
                    task_id, session_id, state, title, reason,
                    spoken_summary, goal, executor, project,
                    source_message_id, info_asked, revision,
                    approval_status, approval_reason, metadata_json,
                    expires_at, approved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.session_id,
                    task.state,
                    task.card.title,
                    task.card.reason,
                    task.card.spoken_summary,
                    task.card.goal,
                    task.card.executor,
                    task.card.project,
                    task.source_message_id,
                    int(task.info_asked),
                    int(task.revision),
                    task.approval_status,
                    task.approval_reason,
                    json.dumps(task.metadata, ensure_ascii=False),
                    task.expires_at,
                    task.approved_at,
                    task.created_at,
                    task.updated_at,
                ),
            )

    def get_work_task(self, task_id: str) -> Optional[WorkTask]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._work_task_from_row(row) if row else None

    def update_work_task(self, task: WorkTask):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE work_tasks SET
                    state = ?, title = ?, reason = ?, spoken_summary = ?,
                    goal = ?, executor = ?, project = ?,
                    source_message_id = ?, info_asked = ?, revision = ?,
                    approval_status = ?, approval_reason = ?,
                    metadata_json = ?, expires_at = ?, approved_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task.state,
                    task.card.title,
                    task.card.reason,
                    task.card.spoken_summary,
                    task.card.goal,
                    task.card.executor,
                    task.card.project,
                    task.source_message_id,
                    int(task.info_asked),
                    int(task.revision),
                    task.approval_status,
                    task.approval_reason,
                    json.dumps(task.metadata, ensure_ascii=False),
                    task.expires_at,
                    task.approved_at,
                    task.updated_at,
                    task.task_id,
                ),
            )

    def transition_work_state(
        self,
        task_id: str,
        from_state: str,
        to_state: str,
        **updates,
    ) -> bool:
        allowed = {
            "approval_status",
            "approval_reason",
            "approved_at",
            "info_asked",
            "revision",
            "expires_at",
            "title",
            "reason",
            "spoken_summary",
            "goal",
            "executor",
            "project",
        }
        columns = {"state": to_state, "updated_at": utc_now()}
        for key, value in updates.items():
            if key == "metadata":
                columns["metadata_json"] = json.dumps(value, ensure_ascii=False)
            elif key in allowed:
                columns[key] = value
            else:
                raise ValueError("不支持的任务状态字段：{}".format(key))
        assignments = ", ".join("{} = ?".format(name) for name in columns)
        values = list(columns.values()) + [task_id, from_state]
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE work_tasks SET {} WHERE task_id = ? AND state = ?".format(
                    assignments
                ),
                values,
            )
            return cursor.rowcount == 1

    def list_work_tasks(
        self,
        session_id: str,
        state: Optional[str] = None,
        limit: int = 20,
    ):
        with self._connection() as connection:
            if state:
                rows = connection.execute(
                    """
                    SELECT * FROM work_tasks
                    WHERE session_id = ? AND state = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (session_id, state, max(1, min(int(limit), 100))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM work_tasks
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (session_id, max(1, min(int(limit), 100))),
                ).fetchall()
        return [self._work_task_from_row(row) for row in rows]

    def latest_work_task(
        self,
        session_id: str,
        active_only: bool = False,
    ) -> Optional[WorkTask]:
        with self._connection() as connection:
            if active_only:
                row = connection.execute(
                    """
                    SELECT * FROM work_tasks
                    WHERE session_id = ? AND state <> ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (session_id, WORK_NORMAL),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM work_tasks
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
        return self._work_task_from_row(row) if row else None

    def create_work_receipt(self, receipt: WorkReceipt):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO work_receipts(
                    receipt_id, task_id, session_id, status, summary,
                    detail, external_id, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.task_id,
                    receipt.session_id,
                    receipt.status,
                    redact_text(receipt.summary),
                    redact_text(receipt.detail),
                    receipt.external_id,
                    int(receipt.attempts),
                    receipt.created_at,
                    receipt.updated_at,
                ),
            )

    def get_work_receipt(self, receipt_id: str) -> Optional[WorkReceipt]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        return self._work_receipt_from_row(row) if row else None

    def get_work_receipt_for_task(self, task_id: str) -> Optional[WorkReceipt]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_receipts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._work_receipt_from_row(row) if row else None

    def update_work_receipt(
        self,
        receipt_id: str,
        status: str,
        summary: Optional[str] = None,
        detail: Optional[str] = None,
        external_id: Optional[str] = None,
        increment_attempt: bool = False,
    ):
        columns = {"status": status, "updated_at": utc_now()}
        if summary is not None:
            columns["summary"] = redact_text(summary)
        if detail is not None:
            columns["detail"] = redact_text(detail)
        if external_id is not None:
            columns["external_id"] = external_id
        assignments = ", ".join("{} = ?".format(name) for name in columns)
        if increment_attempt:
            assignments += ", attempts = attempts + 1"
        values = list(columns.values()) + [receipt_id]
        with self._connection() as connection:
            connection.execute(
                "UPDATE work_receipts SET {} WHERE receipt_id = ?".format(
                    assignments
                ),
                values,
            )

    def list_work_receipts(self, session_id: str, limit: int = 20):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM work_receipts
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._work_receipt_from_row(row) for row in rows]

    def recover_incomplete_work(self):
        """把中断的审阅/执行任务退回讨论态，并把转发中的回执标为 unknown。

        恢复过程绝不重发，也不自动批准。
        """
        recovered = []
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT task_id, state FROM work_tasks
                WHERE state IN (?, ?)
                """,
                (WORK_PLAN_REVIEW, WORK_EXECUTING),
            ).fetchall()
        for row in rows:
            task_id = row["task_id"]
            receipt = self.get_work_receipt_for_task(task_id)
            if (
                row["state"] == WORK_EXECUTING
                and receipt is not None
                and receipt.status not in ("awaiting_confirmation", "unavailable")
            ):
                self.update_work_receipt(
                    receipt.receipt_id,
                    FORWARD_UNKNOWN,
                    summary="上次转发未完成，结果不确定。系统不会自动重发。",
                    detail="process interrupted while forwarding",
                )
            self.transition_work_state(
                task_id,
                row["state"],
                WORK_PLAN_DISCUSSING,
                approval_status=APPROVAL_PENDING,
                approval_reason="崩溃恢复：请重新审阅计划后再确认。",
                approved_at=None,
            )
            recovered.append(task_id)
        return recovered

    def log_skill_call(
        self,
        session_id: str,
        skill_name: str,
        action: str,
        mode: str,
        outcome: str,
        detail: str = "",
    ) -> str:
        audit_id = new_id("ska")
        created_at = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO skill_audit(
                    audit_id, session_id, skill_name, action, mode,
                    outcome, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    session_id,
                    str(skill_name or "")[:80],
                    str(action or "")[:40],
                    str(getattr(mode, "value", mode) or "")[:20],
                    str(outcome or "")[:20],
                    redact_text(str(detail or ""))[:500],
                    created_at,
                ),
            )
        return audit_id

    def list_skill_audits(self, session_id: str, limit: int = 20):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM skill_audit
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(
        self,
        session_id: str,
        kind: str,
        content: str,
        source_message_id: Optional[int],
        confidence: float,
        importance: float,
        status: str = "candidate",
        canonical_key: str = "",
        evidence: Optional[Dict] = None,
        candidate_score: Optional[float] = None,
        candidate_reason: str = "",
        protected: bool = False,
        derived_from: Optional[Iterable[str]] = None,
        relation: str = "derived",
    ) -> str:
        now = utc_now()
        memory_id = new_id("mem")
        derived_from = list(derived_from or [])
        with self._connection() as connection:
            for parent_id in derived_from:
                parent = connection.execute(
                    "SELECT status FROM memories WHERE memory_id = ?",
                    (parent_id,),
                ).fetchone()
                if parent is not None and parent["status"] == "forgotten":
                    raise ValueError("来源事实已遗忘，不能创建派生记忆。")
            validated = self._validate_evidence(connection, evidence)
            quote, quote_start = validated if validated else ("", 0)
            connection.execute(
                """
                INSERT INTO memories(
                    memory_id, session_id, kind, content, source_message_id,
                    confidence, importance, activation, status, created_at,
                    updated_at, canonical_key, evidence_count, first_seen_at,
                    last_seen_at, protected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    session_id,
                    kind,
                    content,
                    source_message_id,
                    max(0.0, min(float(confidence), 1.0)),
                    max(0.0, min(float(importance), 1.0)),
                    0.7,
                    status,
                    now,
                    now,
                    canonical_key,
                    1 if quote else 0,
                    now,
                    now,
                    1 if protected else 0,
                ),
            )
            for parent_id in derived_from:
                if not parent_id or parent_id == memory_id:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO memory_relations(
                        relation_id, session_id, parent_memory_id,
                        child_memory_id, relation, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("rel"),
                        session_id,
                        parent_id,
                        memory_id,
                        relation,
                        now,
                    ),
                )
            if quote:
                self._insert_evidence(
                    connection,
                    memory_id,
                    session_id,
                    evidence,
                    quote,
                    quote_start,
                    now,
                )
            self._index_memory_fts(connection, memory_id, content)
            self._insert_memory_event(
                connection,
                memory_id,
                session_id,
                "created",
                {
                    "kind": kind,
                    "status": status,
                    "canonical_key": canonical_key,
                    "has_evidence": bool(quote),
                },
                now,
            )
            if status == "candidate":
                self._insert_memory_candidate(
                    connection,
                    session_id,
                    memory_id,
                    canonical_key,
                    content,
                    candidate_score if candidate_score is not None else confidence,
                    candidate_reason or "自动抽取",
                    now,
                )
        return memory_id

    @staticmethod
    def _validate_evidence(connection, evidence):
        if not evidence:
            return None
        message_id = evidence.get("message_id")
        quote = str(evidence.get("quote") or "").strip()
        if message_id is None or not quote:
            raise ValueError("记忆证据必须包含消息 ID 和原文片段。")
        row = connection.execute(
            "SELECT role, content FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise ValueError("记忆证据指向的消息不存在。")
        if row["role"] != "user":
            raise ValueError("记忆证据必须来自用户消息。")
        content = row["content"] or ""
        start = content.find(quote)
        if start < 0:
            raise ValueError("记忆证据必须是用户消息原文的子串。")
        return quote, start

    @staticmethod
    def _insert_evidence(
        connection,
        memory_id,
        session_id,
        evidence,
        quote,
        quote_start,
        now,
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO memory_evidence(
                evidence_id, memory_id, session_id, message_id,
                source_role, quote, quote_start, quote_end, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evd"),
                memory_id,
                session_id,
                evidence["message_id"],
                "user",
                quote,
                quote_start,
                quote_start + len(quote),
                now,
            ),
        )

    @staticmethod
    def _insert_memory_event(
        connection,
        memory_id,
        session_id,
        event_type,
        detail,
        now=None,
    ):
        connection.execute(
            """
            INSERT INTO memory_events(
                event_id, memory_id, session_id, event_type,
                detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                memory_id,
                session_id,
                event_type,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now or utc_now(),
            ),
        )

    @staticmethod
    def _insert_memory_candidate(
        connection,
        session_id,
        memory_id,
        canonical_key,
        content,
        score,
        reason,
        now,
        status="pending",
    ):
        connection.execute(
            """
            INSERT INTO memory_candidates(
                candidate_id, session_id, memory_id, canonical_key,
                content, score, status, reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("cand"),
                session_id,
                memory_id,
                canonical_key,
                content,
                max(0.0, min(float(score), 1.0)),
                status,
                reason,
                now,
                now,
            ),
        )

    def list_memories(
        self,
        session_id: str,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 50,
    ) -> List[MemoryRecord]:
        statuses = list(statuses or [])
        with self._connection() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE session_id = ? AND status IN ({})
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """.format(placeholders),
                    (session_id, *statuses, max(1, min(int(limit), 500))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (session_id, max(1, min(int(limit), 500))),
                ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        status: Optional[str] = None,
    ):
        updates = []
        values = []
        if content is not None:
            updates.append("content = ?")
            values.append(content)
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if not updates:
            return
        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(memory_id)
        with self._connection() as connection:
            connection.execute(
                "UPDATE memories SET {} WHERE memory_id = ?".format(
                    ", ".join(updates)
                ),
                tuple(values),
            )
            if content is not None:
                self._reindex_memory_fts_row(
                    connection,
                    memory_id,
                    content,
                )

    def _index_memory_fts(self, connection, memory_id, content):
        """向 FTS 索引追加一行；索引异常时自动降级，不阻塞记忆写入。"""
        if not self.fts_enabled:
            return
        try:
            connection.execute(
                "INSERT INTO memory_fts(memory_id, seg) VALUES (?, ?)",
                (memory_id, fts_segment(content)),
            )
        except sqlite3.Error:
            self.fts_enabled = False

    def _reindex_memory_fts_row(self, connection, memory_id, content):
        """内容变更（如用户纠正）后替换该记忆的索引行。"""
        if not self.fts_enabled:
            return
        try:
            connection.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?",
                (memory_id,),
            )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, seg) VALUES (?, ?)",
                (memory_id, fts_segment(content)),
            )
        except sqlite3.Error:
            self.fts_enabled = False

    def search_memories_fts(
        self,
        session_id: str,
        query: str,
        limit: int = 30,
    ) -> Optional[List[Dict]]:
        """FTS5/BM25 候选检索，仅返回 confirmed 记忆。

        返回值：``None`` 表示 FTS 不可用或查询异常（调用方应降级为
        关键词召回）；``[]`` 表示索引可用但没有候选。BM25 分数只用于
        候选排序，最终排序仍由记忆服务的单一衰减公式完成。
        """
        if not self.fts_enabled:
            return None
        match = fts_match_query(query)
        if not match:
            return []
        with self._connection() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT memory_fts.memory_id AS memory_id,
                        bm25(memory_fts) AS score
                    FROM memory_fts
                    JOIN memories m
                        ON m.memory_id = memory_fts.memory_id
                    WHERE memory_fts MATCH ?
                        AND m.session_id = ?
                        AND m.status = 'confirmed'
                    ORDER BY score
                    LIMIT ?
                    """,
                    (match, session_id, max(1, min(int(limit), 100))),
                ).fetchall()
            except sqlite3.Error:
                self.fts_enabled = False
                return None
        return [
            {"memory_id": row["memory_id"], "bm25": float(row["score"])}
            for row in rows
        ]

    def get_memories_by_ids(self, memory_ids) -> Dict[str, MemoryRecord]:
        ids = [str(item) for item in memory_ids if item]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE memory_id IN ({})".format(
                    placeholders
                ),
                tuple(ids),
            ).fetchall()
        return {
            row["memory_id"]: self._memory_from_row(row) for row in rows
        }

    def rebuild_memory_fts(self) -> bool:
        """全量重建 FTS 索引（管理台/doctor 自愈入口）。"""
        with self._connection() as connection:
            try:
                connection.executescript(MEMORY_FTS_SCHEMA)
                _reindex_memory_fts(connection)
                self.fts_enabled = True
                return True
            except sqlite3.Error:
                self.fts_enabled = False
                return False

    def get_memory(self, memory_id: str) -> Optional[MemoryRecord]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def get_message_role(self, message_id: int) -> Optional[str]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT role FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return row["role"] if row else None

    def get_message(self, message_id: int) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def message_side_effect(self, session_id: str, message_id: int) -> str:
        """Label the first durable record produced by one user turn.

        Regeneration replays the turn, so any reminder, work task, or file
        proposal linked to that user row would be created twice.
        """
        message_id = int(message_id or 0)
        with self._connection() as connection:
            if connection.execute(
                """
                SELECT 1 FROM reminders
                WHERE session_id = ? AND source_message_id = ?
                LIMIT 1
                """,
                (session_id, message_id),
            ).fetchone():
                return "提醒"
            if connection.execute(
                """
                SELECT 1 FROM work_tasks
                WHERE session_id = ? AND source_message_id = ?
                LIMIT 1
                """,
                (session_id, message_id),
            ).fetchone():
                return "工作任务"
            rows = connection.execute(
                """
                SELECT normalized_arguments FROM action_proposals
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchall()
        for row in rows:
            try:
                arguments = json.loads(row["normalized_arguments"] or "{}")
            except (TypeError, ValueError):
                continue
            try:
                linked = int(arguments.get("source_message_id") or 0)
            except (TypeError, ValueError):
                linked = 0
            if linked == message_id:
                return "待确认写入"
        return ""

    def find_memory_by_key(
        self,
        session_id: str,
        canonical_key: str,
        statuses: Optional[Iterable[str]] = None,
    ) -> Optional[MemoryRecord]:
        if not canonical_key:
            return None
        statuses = list(statuses or ["candidate", "confirmed"])
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM memories
                WHERE session_id = ? AND canonical_key = ?
                    AND status IN ({})
                ORDER BY updated_at DESC
                LIMIT 1
                """.format(placeholders),
                (session_id, canonical_key, *statuses),
            ).fetchone()
        return self._memory_from_row(row) if row else None

    def reinforce_memory(self, memory_id: str, evidence: Dict) -> bool:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return False
            validated = self._validate_evidence(connection, evidence)
            if not validated:
                return False
            quote, quote_start = validated
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO memory_evidence(
                    evidence_id, memory_id, session_id, message_id,
                    source_role, quote, quote_start, quote_end, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("evd"),
                    memory_id,
                    row["session_id"],
                    evidence["message_id"],
                    "user",
                    quote,
                    quote_start,
                    quote_start + len(quote),
                    now,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE memories SET
                        evidence_count = evidence_count + 1,
                        last_seen_at = ?,
                        updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (now, now, memory_id),
                )
                self._insert_memory_event(
                    connection,
                    memory_id,
                    row["session_id"],
                    "reinforced",
                    {"message_id": evidence["message_id"]},
                    now,
                )
                return True
        return False

    def promote_memory(self, memory_id: str, score: float, reason: str):
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE memories SET
                    status = 'confirmed',
                    confidence = ?,
                    updated_at = ?
                WHERE memory_id = ?
                """,
                (max(0.0, min(float(score), 1.0)), now, memory_id),
            )
            connection.execute(
                """
                UPDATE memory_candidates SET
                    status = 'accepted',
                    updated_at = ?
                WHERE memory_id = ? AND status = 'pending'
                """,
                (now, memory_id),
            )
            self._insert_memory_event(
                connection,
                memory_id,
                row["session_id"],
                "promoted",
                {"score": score, "reason": reason},
                now,
            )

    def archive_memory(self, memory_id: str, reason: str) -> bool:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id, protected FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return False
            if row["protected"]:
                return False
            connection.execute(
                "UPDATE memories SET status = 'archived', updated_at = ? WHERE memory_id = ?",
                (now, memory_id),
            )
            connection.execute(
                """
                UPDATE memory_candidates SET
                    status = 'superseded',
                    updated_at = ?
                WHERE memory_id = ? AND status = 'pending'
                """,
                (now, memory_id),
            )
            self._insert_memory_event(
                connection,
                memory_id,
                row["session_id"],
                "superseded",
                {"reason": reason},
                now,
            )
        return True

    def set_memory_protected(self, memory_id: str, protected: bool = True):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE memories SET protected = ?, updated_at = ? WHERE memory_id = ?",
                (1 if protected else 0, utc_now(), memory_id),
            )
            self._insert_memory_event(
                connection,
                memory_id,
                row["session_id"],
                "protected" if protected else "unprotected",
                {},
            )
        return True

    def list_child_memories(self, memory_id: str) -> List[MemoryRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT m.* FROM memory_relations r
                JOIN memories m ON m.memory_id = r.child_memory_id
                WHERE r.parent_memory_id = ?
                """,
                (memory_id,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def add_memory_candidate(
        self,
        session_id: str,
        memory_id: str,
        canonical_key: str,
        content: str,
        score: float,
        reason: str,
    ):
        with self._connection() as connection:
            self._insert_memory_candidate(
                connection,
                session_id,
                memory_id,
                canonical_key,
                content,
                score,
                reason,
                utc_now(),
            )

    def add_memory_event(
        self,
        memory_id: str,
        session_id: str,
        event_type: str,
        detail,
    ):
        with self._connection() as connection:
            self._insert_memory_event(
                connection,
                memory_id,
                session_id,
                event_type,
                detail,
            )

    def add_memory_tombstone(
        self,
        session_id: str,
        canonical_key: str,
        content_hash: str,
        forget_epoch: int,
        through_seq: int,
    ):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_tombstones(
                    tombstone_id, session_id, canonical_key, content_hash,
                    forget_epoch, through_seq, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("tmb"),
                    session_id,
                    canonical_key,
                    content_hash,
                    int(forget_epoch),
                    int(through_seq),
                    utc_now(),
                ),
            )

    def content_tombstoned(self, session_id: str, content_hash: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM memory_tombstones
                WHERE session_id = ? AND content_hash = ?
                LIMIT 1
                """,
                (session_id, content_hash),
            ).fetchone()
        return row is not None

    def list_memory_tombstones(self, session_id: str):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_tombstones
                WHERE session_id = ?
                ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def max_message_id(self, session_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS value FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["value"])

    def list_memory_evidence(self, memory_id: str):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_evidence
                WHERE memory_id = ?
                ORDER BY created_at, evidence_id
                """,
                (memory_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_memory_events(self, memory_id: str):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_events
                WHERE memory_id = ?
                ORDER BY created_at, event_id
                """,
                (memory_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_memory_candidates(
        self,
        session_id: str,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 50,
        memory_id: Optional[str] = None,
    ):
        statuses = list(statuses or [])
        with self._connection() as connection:
            if memory_id:
                rows = connection.execute(
                    """
                    SELECT * FROM memory_candidates
                    WHERE session_id = ? AND memory_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (
                        session_id,
                        memory_id,
                        max(1, min(int(limit), 500)),
                    ),
                ).fetchall()
            elif statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    """
                    SELECT * FROM memory_candidates
                    WHERE session_id = ? AND status IN ({})
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """.format(placeholders),
                    (session_id, *statuses, max(1, min(int(limit), 500))),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM memory_candidates
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (session_id, max(1, min(int(limit), 500))),
                ).fetchall()
        return [dict(row) for row in rows]

    def log_recall(
        self,
        session_id: str,
        query_hash: str,
        fact_ids: List[str],
        scores: List[float],
        latency_ms: float,
        source: str = "",
        scope: str = "memory",
    ):
        recall_id = new_id("rcl")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recall_log(
                    recall_id, session_id, query_hash, fact_ids, scores,
                    used_in_reply, latency_ms, source, scope, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    recall_id,
                    session_id,
                    query_hash,
                    json.dumps(fact_ids, ensure_ascii=False),
                    json.dumps(scores),
                    float(latency_ms),
                    source,
                    scope or "memory",
                    utc_now(),
                ),
            )
        return recall_id

    def mark_recall_used(self, recall_id: str, used: bool = True):
        if not recall_id:
            return
        with self._connection() as connection:
            connection.execute(
                "UPDATE recall_log SET used_in_reply = ? WHERE recall_id = ?",
                (1 if used else 0, recall_id),
            )

    def list_recall_logs(self, session_id: str, limit: int = 20):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM recall_log
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recall_logs_for_memory(
        self,
        session_id: str,
        memory_id: str,
        limit: int = 10,
    ):
        matched = []
        for row in self.list_recall_logs(session_id, limit=500):
            try:
                fact_ids = json.loads(row["fact_ids"] or "[]")
            except ValueError:
                continue
            if memory_id not in fact_ids:
                continue
            scores = []
            try:
                scores = json.loads(row["scores"] or "[]")
            except ValueError:
                pass
            index = fact_ids.index(memory_id)
            row["score"] = scores[index] if index < len(scores) else None
            matched.append(row)
            if len(matched) >= max(1, min(int(limit), 100)):
                break
        return matched

    def add_experience(
        self,
        session_id: str,
        title: str,
        summary: str,
        source_message_id: Optional[int],
        receipt_id: Optional[str],
        memory_id: str = "",
    ) -> str:
        from .models import new_id

        experience_id = new_id("exp")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experiences(
                    experience_id, session_id, title, summary,
                    source_message_id, receipt_id, created_at,
                    memory_id, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    experience_id,
                    session_id,
                    title,
                    summary,
                    source_message_id,
                    receipt_id,
                    utc_now(),
                    memory_id,
                ),
            )
        return experience_id

    def list_experiences(self, session_id: str, limit: int = 30):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM experiences
                WHERE session_id = ? AND status != 'forgotten'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            ExperienceRecord(
                experience_id=row["experience_id"],
                session_id=row["session_id"],
                title=row["title"],
                summary=row["summary"],
                source_message_id=row["source_message_id"],
                receipt_id=row["receipt_id"],
                created_at=row["created_at"],
                memory_id=(
                    row["memory_id"] if "memory_id" in set(row.keys()) else ""
                ),
                status=row["status"] if "status" in set(row.keys()) else "active",
            )
            for row in rows
        ]

    def forget_experience_by_memory(self, memory_id: str, session_id: str):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE experiences SET status = 'forgotten'
                WHERE session_id = ? AND memory_id = ?
                """,
                (session_id, memory_id),
            )

    def get_import_job(self, fingerprint: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE source_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return dict(row) if row else None

    def get_import_job_by_id(self, job_id: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_import_job(
        self,
        fingerprint: str,
        source_name: str,
        target_session_id: str,
        total_units: int,
    ) -> str:
        job_id = new_id("job")
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO import_jobs(
                    job_id, source_fingerprint, source_name,
                    target_session_id, cursor, total_units,
                    imported_messages, imported_memories,
                    skipped_duplicates, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, 0, 0, 0, 'running', ?, ?)
                """,
                (
                    job_id,
                    fingerprint,
                    source_name,
                    target_session_id,
                    max(0, int(total_units)),
                    now,
                    now,
                ),
            )
        return job_id

    def update_import_job(
        self,
        job_id: str,
        cursor: int,
        imported_messages: int,
        imported_memories: int,
        skipped_duplicates: int,
        status: str,
    ):
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE import_jobs SET
                    cursor = ?,
                    imported_messages = ?,
                    imported_memories = ?,
                    skipped_duplicates = ?,
                    status = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    max(0, int(cursor)),
                    max(0, int(imported_messages)),
                    max(0, int(imported_memories)),
                    max(0, int(skipped_duplicates)),
                    status,
                    utc_now(),
                    job_id,
                ),
            )

    def import_message_seen(self, message_hash: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM import_messages WHERE message_hash = ?",
                (message_hash,),
            ).fetchone()
        return row is not None

    def record_import_message(
        self,
        message_hash: str,
        job_id: str,
        message_id: int,
        role: str,
    ):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO import_messages(
                    message_hash, job_id, imported_message_id, role, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (message_hash, job_id, int(message_id), role, utc_now()),
            )

    def set_affect(
        self,
        session_id: str,
        target: str,
        mood_label: str,
        valence: float,
        arousal: float,
        energy: Optional[float],
        confidence: float,
        source_message_id: Optional[int],
        source: str,
    ) -> str:
        from .models import new_id

        affect_id = new_id("aff")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO affect_states(
                    affect_id, session_id, target, mood_label,
                    valence, arousal, energy, confidence,
                    source_message_id, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    affect_id,
                    session_id,
                    target,
                    mood_label,
                    max(-1.0, min(float(valence), 1.0)),
                    max(0.0, min(float(arousal), 1.0)),
                    None
                    if energy is None
                    else max(0.0, min(float(energy), 1.0)),
                    max(0.0, min(float(confidence), 1.0)),
                    source_message_id,
                    source,
                    utc_now(),
                ),
            )
        return affect_id

    def latest_affect(self, session_id: str, target: str):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM affect_states
                WHERE session_id = ? AND target = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (session_id, target),
            ).fetchone()
        if not row:
            return None
        return AffectRecord(
            affect_id=row["affect_id"],
            session_id=row["session_id"],
            target=row["target"],
            mood_label=row["mood_label"],
            valence=row["valence"],
            arousal=row["arousal"],
            energy=row["energy"],
            confidence=row["confidence"],
            source_message_id=row["source_message_id"],
            source=row["source"],
            created_at=row["created_at"],
        )

    def list_affect_states(
        self,
        session_id: str,
        target: Optional[str] = None,
        limit: int = 30,
    ):
        parameters = [session_id]
        where = "session_id = ?"
        if target:
            where += " AND target = ?"
            parameters.append(target)
        parameters.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM affect_states
                WHERE {}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """.format(where),
                parameters,
            ).fetchall()
        return [
            AffectRecord(
                affect_id=row["affect_id"],
                session_id=row["session_id"],
                target=row["target"],
                mood_label=row["mood_label"],
                valence=row["valence"],
                arousal=row["arousal"],
                energy=row["energy"],
                confidence=row["confidence"],
                source_message_id=row["source_message_id"],
                source=row["source"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_reminder(
        self,
        session_id: str,
        content: str,
        due_at: str,
        source_message_id: Optional[int] = None,
    ) -> ReminderRecord:
        reminder_id = new_id("rem")
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO reminders(
                    reminder_id, session_id, content, due_at, status,
                    source_message_id, created_at, updated_at, acknowledged_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, NULL)
                """,
                (
                    reminder_id,
                    session_id,
                    content,
                    due_at,
                    source_message_id,
                    now,
                    now,
                ),
            )
        return self.get_reminder(reminder_id)

    def get_reminder(self, reminder_id: str) -> Optional[ReminderRecord]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
        return self._reminder_from_row(row) if row else None

    def due_reminders(
        self,
        session_id: str,
        now: str,
        limit: int = 5,
    ) -> List[ReminderRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE session_id = ? AND status = 'pending' AND due_at <= ?
                ORDER BY due_at ASC, rowid ASC
                LIMIT ?
                """,
                (session_id, now, max(1, min(int(limit), 20))),
            ).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def next_reminder_due(self, session_id: str) -> Optional[ReminderRecord]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM reminders
                WHERE session_id = ? AND status = 'pending'
                ORDER BY due_at ASC, rowid ASC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return self._reminder_from_row(row) if row else None

    def list_reminders(
        self,
        session_id: str,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[ReminderRecord]:
        query = "SELECT * FROM reminders WHERE session_id = ?"
        params = [session_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY due_at ASC, rowid ASC LIMIT ?"
        params.append(max(1, min(int(limit), 50)))
        with self._connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def close_reminder(self, reminder_id: str, status: str):
        if status not in ("done", "dismissed"):
            raise ValueError("未知的提醒状态：{}".format(status))
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = ?, updated_at = ?, acknowledged_at = ?
                WHERE reminder_id = ? AND status = 'pending'
                """,
                (status, now, now, reminder_id),
            )
            updated = cursor.rowcount
        return self.get_reminder(reminder_id) if updated else None

    def get_proactive_state(self, session_id: str) -> ProactiveState:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return ProactiveState(session_id=session_id)
        return self._proactive_state_from_row(row)

    def ensure_proactive_state(self, session_id: str) -> ProactiveState:
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO proactive_state(session_id, updated_at)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (session_id, now),
            )
        return self.get_proactive_state(session_id)

    def update_proactive_state(self, session_id: str, **fields) -> ProactiveState:
        allowed = {
            "enabled",
            "quiet_start",
            "quiet_end",
            "daily_limit",
            "min_interval_hours",
            "max_unanswered",
            "include_memory",
            "daily_count",
            "daily_date",
            "last_spoken_at",
            "last_event_id",
            "unanswered_count",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                "未知的主动状态字段：{}".format("、".join(sorted(unknown)))
            )
        self.ensure_proactive_state(session_id)
        values = {}
        for key, value in fields.items():
            if key in ("enabled", "include_memory"):
                value = 1 if value else 0
            elif key in ("daily_count", "unanswered_count"):
                value = max(0, int(value))
            elif key in ("quiet_start", "quiet_end", "daily_limit", "max_unanswered"):
                value = int(value)
            elif key == "min_interval_hours":
                value = float(value)
            values[key] = value
        values["updated_at"] = utc_now()
        assignments = ", ".join("{} = ?".format(key) for key in values)
        with self._connection() as connection:
            connection.execute(
                "UPDATE proactive_state SET {} WHERE session_id = ?".format(
                    assignments
                ),
                tuple(values.values()) + (session_id,),
            )
        return self.get_proactive_state(session_id)

    def add_proactive_event(
        self,
        session_id: str,
        source_type: str,
        source_id: str,
        trigger_reason: str,
        content: str,
        status: str,
        reason: str = "",
        source_message_id: Optional[int] = None,
        message_id: Optional[int] = None,
        content_hash: str = "",
        url_hash: str = "",
        read_at: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> ProactiveEvent:
        event_id = new_id("pev")
        created_at = created_at or utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO proactive_events(
                    event_id, session_id, source_type, source_id,
                    source_message_id, trigger_reason, content, status,
                    reason, message_id, content_hash, url_hash, read_at,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    source_type,
                    source_id,
                    source_message_id,
                    trigger_reason,
                    content,
                    status,
                    reason,
                    message_id,
                    content_hash,
                    url_hash,
                    read_at,
                    created_at,
                ),
            )
        return self.get_proactive_event(event_id)

    def get_proactive_event(self, event_id: str) -> Optional[ProactiveEvent]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._proactive_event_from_row(row) if row else None

    def list_proactive_events(
        self,
        session_id: str,
        limit: int = 20,
        status: Optional[str] = None,
    ) -> List[ProactiveEvent]:
        query = "SELECT * FROM proactive_events WHERE session_id = ?"
        params = [session_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._connection() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._proactive_event_from_row(row) for row in rows]

    def latest_proactive_event(
        self,
        session_id: str,
        status: Optional[str] = None,
    ) -> Optional[ProactiveEvent]:
        events = self.list_proactive_events(session_id, limit=1, status=status)
        return events[0] if events else None

    def count_proactive_events_since(
        self,
        session_id: str,
        since: str,
        status: str = "delivered",
    ) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS value FROM proactive_events
                WHERE session_id = ? AND status = ? AND created_at >= ?
                """,
                (session_id, status, since),
            ).fetchone()
        return int(row["value"])

    def proactive_source_exists(
        self,
        session_id: str,
        source_type: str,
        source_id: str,
        statuses: Optional[Iterable[str]] = None,
    ) -> bool:
        statuses = list(statuses or ["delivered", "missed"])
        if not statuses:
            return False
        placeholders = ",".join("?" for _ in statuses)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM proactive_events
                WHERE session_id = ? AND source_type = ? AND source_id = ?
                  AND status IN ({})
                LIMIT 1
                """.format(placeholders),
                (session_id, source_type, source_id, *statuses),
            ).fetchone()
        return row is not None

    def last_user_message_id(self, session_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(id), 0) AS value FROM messages
                WHERE session_id = ? AND role = 'user'
                """,
                (session_id,),
            ).fetchone()
        return int(row["value"])

    def count_proactive_delivered_after(
        self,
        session_id: str,
        message_id: int,
    ) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS value FROM proactive_events
                WHERE session_id = ? AND status = 'delivered'
                  AND message_id IS NOT NULL AND message_id > ?
                """,
                (session_id, int(message_id or 0)),
            ).fetchone()
        return int(row["value"])

    def count_unread_proactive_events(self, session_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS value FROM proactive_events
                WHERE session_id = ? AND read_at IS NULL
                """,
                (session_id,),
            ).fetchone()
        return int(row["value"])

    def mark_proactive_read(self, session_id: str, event_id: Optional[str] = None):
        now = utc_now()
        with self._connection() as connection:
            if event_id:
                cursor = connection.execute(
                    """
                    UPDATE proactive_events SET read_at = ?
                    WHERE session_id = ? AND event_id = ? AND read_at IS NULL
                    """,
                    (now, session_id, event_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE proactive_events SET read_at = ?
                    WHERE session_id = ? AND read_at IS NULL
                    """,
                    (now, session_id),
                )
        return int(cursor.rowcount)

    @staticmethod
    def _proactive_state_from_row(row):
        return ProactiveState(
            session_id=row["session_id"],
            enabled=bool(row["enabled"]),
            quiet_start=row["quiet_start"],
            quiet_end=row["quiet_end"],
            daily_limit=row["daily_limit"],
            min_interval_hours=row["min_interval_hours"],
            max_unanswered=row["max_unanswered"],
            include_memory=bool(row["include_memory"]),
            daily_count=row["daily_count"],
            daily_date=row["daily_date"],
            last_spoken_at=row["last_spoken_at"],
            last_event_id=row["last_event_id"],
            unanswered_count=row["unanswered_count"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _proactive_event_from_row(row):
        return ProactiveEvent(
            event_id=row["event_id"],
            session_id=row["session_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            source_message_id=row["source_message_id"],
            trigger_reason=row["trigger_reason"],
            content=row["content"],
            status=row["status"],
            reason=row["reason"],
            message_id=row["message_id"],
            content_hash=row["content_hash"],
            url_hash=row["url_hash"],
            read_at=row["read_at"],
            created_at=row["created_at"],
        )

    def set_workspace(self, path: str, sensitivity_override: bool = False):
        """Compatibility adapter: the workspace is one flagged row in access_grants.

        The path is stored exactly as the caller wrote it, like the pre-v13 table
        did; identity columns are computed separately so a moved directory is
        detectable instead of silently re-resolved.
        """
        return self.upsert_directory_grant(
            path,
            LEGACY_WORKSPACE_CAPABILITIES,
            source="workspace",
            sensitivity_override=bool(sensitivity_override),
            legacy_workspace=True,
        )

    def get_workspace(self) -> Optional[str]:
        grant = self.get_legacy_workspace_grant()
        return grant["path"] if grant else None

    def clear_workspace(self):
        grant = self.get_legacy_workspace_grant()
        if grant is None:
            return
        self.set_access_grant_status(grant["grant_id"], "revoked")

    # -- 授权中心（桌面执行代理 S1）----------------------------------------

    def get_legacy_workspace_grant(self) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM access_grants
                WHERE legacy_workspace = 1 AND status = 'active' AND resource_kind = 'directory'
                ORDER BY updated_at DESC
                """
            ).fetchone()
        return _access_grant_from_row(row) if row else None

    def upsert_directory_grant(
        self,
        path,
        capabilities,
        source="grant",
        sensitivity_override=False,
        legacy_workspace=False,
    ) -> Dict:
        """Insert or replace the one row that stands for this directory.

        Capabilities are replaced, not merged: the merged authorization card shows
        the whole set at once, so "re-grant" means "this is what it is now".
        """
        key = path_key(path)
        identity = _directory_identity_or_empty(path)
        caps = _access_json_list(capabilities)
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT grant_id FROM access_grants
                WHERE resource_kind = 'directory' AND path_key = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                grant_id = new_id("grant")
                connection.execute(
                    """
                    INSERT INTO access_grants(
                        grant_id, resource_kind, path, path_key,
                        volume_id, file_id, capabilities, status, source,
                        sensitivity_override, legacy_workspace,
                        created_at, updated_at
                    ) VALUES (?, 'directory', ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                    """,
                    (
                        grant_id,
                        str(path),
                        key,
                        identity["device"],
                        identity["file_id"],
                        caps,
                        str(source or "grant"),
                        1 if sensitivity_override else 0,
                        1 if legacy_workspace else 0,
                        now,
                        now,
                    ),
                )
            else:
                grant_id = row["grant_id"]
                connection.execute(
                    """
                    UPDATE access_grants
                    SET path = ?, volume_id = ?, file_id = ?, capabilities = ?,
                        status = 'active', source = ?, sensitivity_override = ?,
                        legacy_workspace = ?, revoked_at = '', updated_at = ?
                    WHERE grant_id = ?
                    """,
                    (
                        str(path),
                        identity["device"],
                        identity["file_id"],
                        caps,
                        str(source or "grant"),
                        1 if sensitivity_override else 0,
                        1 if legacy_workspace else 0,
                        now,
                        grant_id,
                    ),
                )
            if legacy_workspace:
                # At most one row carries the workspace flag, or get_workspace()
                # would have two candidates to guess between.
                connection.execute(
                    """
                    UPDATE access_grants SET legacy_workspace = 0
                    WHERE legacy_workspace = 1 AND grant_id != ?
                    """,
                    (grant_id,),
                )
            current = connection.execute(
                "SELECT * FROM access_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        return _access_grant_from_row(current)

    def get_access_grant(self, grant_id) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM access_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        return _access_grant_from_row(row) if row else None

    def find_directory_grant(self, path) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM access_grants
                WHERE resource_kind = 'directory' AND path_key = ?
                """,
                (path_key(path),),
            ).fetchone()
        return _access_grant_from_row(row) if row else None

    def list_access_grants(self, status=None) -> List[Dict]:
        with self._connection() as connection:
            if status:
                rows = connection.execute(
                    """
                    SELECT * FROM access_grants WHERE status = ?
                    ORDER BY updated_at DESC, path_key
                    """,
                    (status,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM access_grants
                    ORDER BY status, updated_at DESC, path_key
                    """
                ).fetchall()
        return [_access_grant_from_row(row) for row in rows]

    def count_access_grants(self, status="active") -> int:
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM access_grants WHERE status = ?",
                    (status,),
                ).fetchone()[0]
            )

    def set_access_grant_status(self, grant_id, status) -> bool:
        """Move a grant between active and revoked.

        Revocation also retires every live lease in the same transaction: the plan
        says a revoked resource invalidates immediately, and a lease that outlives
        its grant by one TTL window is exactly the hole that promise closes.
        """
        if status not in ("active", "revoked"):
            raise ValueError("未知的授权状态：{}".format(status))
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT grant_id FROM access_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE access_grants
                SET status = ?, revoked_at = ?, updated_at = ?
                WHERE grant_id = ?
                """,
                (
                    status,
                    now if status == "revoked" else "",
                    now,
                    grant_id,
                ),
            )
            if status == "revoked":
                connection.execute(
                    """
                    UPDATE grant_leases
                    SET status = 'revoked', released_at = ?
                    WHERE grant_id = ? AND status = 'active'
                    """,
                    (now, grant_id),
                )
        return True

    def bind_grant_identity(self, grant_id, identity: Dict) -> bool:
        """Record the directory identity the first time it becomes readable.

        A grant created while the directory was offline (a drive that was
        unplugged, a promotion from before v13) has no identity to compare against.
        Binding it on first sight keeps such a grant usable; a directory that later
        changes identity still fails the comparison.
        """
        if not identity or not identity.get("root"):
            return False
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE access_grants
                SET volume_id = ?, file_id = ?, updated_at = ?
                WHERE grant_id = ? AND volume_id = '' AND file_id = ''
                """,
                (
                    str(identity["device"]),
                    str(identity["file_id"]),
                    utc_now(),
                    grant_id,
                ),
            )
            return cursor.rowcount > 0

    def touch_access_grant(self, grant_id):
        with self._connection() as connection:
            connection.execute(
                "UPDATE access_grants SET last_used_at = ? WHERE grant_id = ?",
                (utc_now(), grant_id),
            )

    def add_grant_lease(
        self,
        grant_id,
        task_id,
        capability,
        expires_at,
        nonce,
        resource_ids=(),
        max_operations=1,
        confirm_digest="",
    ) -> Dict:
        now = utc_now()
        lease_id = new_id("lease")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO grant_leases(
                    lease_id, grant_id, task_id, capability, resource_ids,
                    nonce, confirm_digest, expires_at, max_operations,
                    operations_used, status, created_at, released_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active', ?, '')
                """,
                (
                    lease_id,
                    grant_id,
                    str(task_id),
                    str(capability),
                    _access_json_list(resource_ids),
                    str(nonce),
                    str(confirm_digest or ""),
                    str(expires_at),
                    int(max_operations),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM grant_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
        return _grant_lease_from_row(row)

    def get_grant_lease(self, lease_id) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM grant_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
        return _grant_lease_from_row(row) if row else None

    def list_grant_leases(self, status=None, task_id=None, grant_id=None) -> List[Dict]:
        clauses = []
        values = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if task_id:
            clauses.append("task_id = ?")
            values.append(str(task_id))
        if grant_id:
            clauses.append("grant_id = ?")
            values.append(str(grant_id))
        query = "SELECT * FROM grant_leases"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._connection() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [_grant_lease_from_row(row) for row in rows]

    def consume_grant_lease(self, lease_id, now=None) -> bool:
        """Take one operation from a lease, atomically.

        Expiry and the operation budget are checked inside the UPDATE, so two
        concurrent callers cannot both read "one left" and both spend it.
        """
        moment = str(now or utc_now())
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE grant_leases
                SET operations_used = operations_used + 1
                WHERE lease_id = ? AND status = 'active'
                  AND expires_at > ? AND operations_used < max_operations
                """,
                (lease_id, moment),
            )
            spent = cursor.rowcount == 1
            if spent:
                row = connection.execute(
                    "SELECT grant_id FROM grant_leases WHERE lease_id = ?",
                    (lease_id,),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "UPDATE access_grants SET last_used_at = ? WHERE grant_id = ?",
                        (moment, row["grant_id"]),
                    )
        return spent

    def finish_grant_leases(
        self,
        status="released",
        task_id=None,
        grant_id=None,
        lease_id=None,
    ) -> int:
        if status not in ("released", "revoked"):
            raise ValueError("未知的租约状态：{}".format(status))
        clauses = ["status = 'active'"]
        filters = []
        if lease_id:
            filters.append(("lease_id", str(lease_id)))
        if task_id:
            filters.append(("task_id", str(task_id)))
        if grant_id:
            filters.append(("grant_id", str(grant_id)))
        if not filters:
            # Without a filter this would close every lease in the table; the
            # caller has to say which task, grant or lease it means.
            raise ValueError("结束租约需要指定 lease_id、task_id 或 grant_id")
        for column, _ in filters:
            clauses.append("{} = ?".format(column))
        values = [status, utc_now()] + [value for _, value in filters]
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE grant_leases
                SET status = ?, released_at = ?
                WHERE {}
                """.format(" AND ".join(clauses)),
                tuple(values),
            )
            return int(cursor.rowcount)


    # -- 文档库（M16 切片 1）------------------------------------------------

    def add_document(self, meta: Dict, chunks: List[Dict]) -> int:
        """写入一条文档与其分块，返回落库的块数。

        词法索引与 ``doc_chunks`` 同事务写入；FTS 语句失败只关掉
        ``doc_fts_enabled``，行数不一致会在下次启动被自愈重建。
        """
        document_id = str(meta["document_id"])
        indexed = 0
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO documents(
                    document_id, persona_id, name, kind, path, bytes,
                    sha256, converted, chunks, chars, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    str(meta.get("persona_id") or ""),
                    str(meta.get("name") or ""),
                    str(meta.get("kind") or ""),
                    str(meta.get("path") or ""),
                    int(meta.get("bytes") or 0),
                    str(meta.get("sha256") or ""),
                    1 if meta.get("converted") else 0,
                    len(chunks),
                    int(meta.get("chars") or 0),
                    str(meta.get("created_at") or utc_now()),
                ),
            )
            pairs = []
            for ordinal, chunk in enumerate(chunks):
                chunk_id = str(chunk.get("chunk_id") or new_id("dck"))
                text = str(chunk["text"])
                connection.execute(
                    """
                    INSERT INTO doc_chunks(
                        chunk_id, document_id, ordinal, heading, text, chars
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        document_id,
                        ordinal,
                        str(chunk.get("heading") or ""),
                        text,
                        len(text),
                    ),
                )
                pairs.append((chunk_id, fts_segment(text)))
            if pairs and self.doc_fts_enabled:
                try:
                    connection.executemany(
                        "INSERT INTO doc_fts(chunk_id, seg) VALUES (?, ?)",
                        pairs,
                    )
                    indexed = len(pairs)
                except sqlite3.Error:
                    self.doc_fts_enabled = False
        return indexed

    def list_documents(self, persona_id=None) -> List[Dict]:
        with self._connection() as connection:
            if persona_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM documents ORDER BY created_at DESC, name
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM documents
                    WHERE persona_id = ? OR persona_id = ''
                    ORDER BY created_at DESC, name
                    """,
                    (str(persona_id),),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?",
                (str(document_id),),
            ).fetchone()
        return dict(row) if row else None

    def document_total_bytes(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(bytes), 0) AS value FROM documents"
            ).fetchone()
        return int(row["value"])

    def delete_document(self, document_id: str) -> int:
        """删除文档及其分块与索引行，返回删除的块数。"""
        text_id = str(document_id)
        with self._connection() as connection:
            ids = [
                row["chunk_id"]
                for row in connection.execute(
                    "SELECT chunk_id FROM doc_chunks WHERE document_id = ?",
                    (text_id,),
                ).fetchall()
            ]
            if ids and self.doc_fts_enabled:
                try:
                    connection.executemany(
                        "DELETE FROM doc_fts WHERE chunk_id = ?",
                        [(value,) for value in ids],
                    )
                except sqlite3.Error:
                    self.doc_fts_enabled = False
            connection.execute(
                "DELETE FROM doc_chunks WHERE document_id = ?", (text_id,)
            )
            cursor = connection.execute(
                "DELETE FROM documents WHERE document_id = ?", (text_id,)
            )
        if int(cursor.rowcount) <= 0:
            return 0
        return len(ids)

    def get_doc_chunks_by_ids(self, chunk_ids) -> Dict[str, Dict]:
        values = [str(item) for item in chunk_ids if str(item)]
        if not values:
            return {}
        marks = ",".join("?" for _ in values)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.chunk_id AS chunk_id, c.document_id AS document_id,
                    c.ordinal AS ordinal, c.heading AS heading,
                    c.text AS text, c.chars AS chars,
                    d.name AS name, d.kind AS kind, d.path AS path
                FROM doc_chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE c.chunk_id IN ({})
                """.format(marks),
                values,
            ).fetchall()
        return {row["chunk_id"]: dict(row) for row in rows}

    def search_documents_fts(
        self,
        query: str,
        limit: int = 30,
        persona_id: Optional[str] = None,
    ) -> Optional[List[Dict]]:
        """FTS5/BM25 文档块候选。

        ``None`` 表示词法索引不可用或查询异常（调用方应退化为"没有文档
        可注入"，绝不阻塞回合）；``[]`` 表示索引可用但没有候选。分数只
        用于候选排序，最终裁剪由文档服务的字符预算完成。
        """
        if not self.doc_fts_enabled:
            return None
        match = fts_match_query(query)
        if not match:
            return []
        with self._connection() as connection:
            try:
                if persona_id is None:
                    rows = connection.execute(
                        """
                        SELECT doc_fts.chunk_id AS chunk_id,
                            bm25(doc_fts) AS score
                        FROM doc_fts
                        WHERE doc_fts MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (match, max(1, min(int(limit), 100))),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT doc_fts.chunk_id AS chunk_id,
                            bm25(doc_fts) AS score
                        FROM doc_fts
                        JOIN doc_chunks c
                            ON c.chunk_id = doc_fts.chunk_id
                        JOIN documents d
                            ON d.document_id = c.document_id
                        WHERE doc_fts MATCH ?
                            AND (d.persona_id = ? OR d.persona_id = '')
                        ORDER BY score
                        LIMIT ?
                        """,
                        (
                            match,
                            str(persona_id),
                            max(1, min(int(limit), 100)),
                        ),
                    ).fetchall()
            except sqlite3.Error:
                self.doc_fts_enabled = False
                return None
        return [
            {"chunk_id": row["chunk_id"], "bm25": float(row["score"])}
            for row in rows
        ]

    def upsert_mcp_server(self, record: Dict) -> None:
        """Insert or replace one server row; ``created_at`` survives an edit.

        Credentials never come through here — env values and headers live in
        config/mcp_keys.json, so a database backup holds addresses only.
        """
        server_id = str(record.get("server_id") or "").strip().lower()
        if not server_id:
            raise ValueError("MCP 服务器缺少标识。")
        now = utc_now()
        created = str(record.get("created_at") or now)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT created_at FROM mcp_servers WHERE server_id = ?",
                (server_id,),
            ).fetchone()
            if existing is not None:
                created = str(existing["created_at"] or created)
            connection.execute(
                """
                INSERT INTO mcp_servers(
                    server_id, label, transport, command, args, cwd, url,
                    timeout, enabled, tools, discovered, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    label = excluded.label,
                    transport = excluded.transport,
                    command = excluded.command,
                    args = excluded.args,
                    cwd = excluded.cwd,
                    url = excluded.url,
                    timeout = excluded.timeout,
                    enabled = excluded.enabled,
                    tools = excluded.tools,
                    discovered = excluded.discovered,
                    updated_at = excluded.updated_at
                """,
                (
                    server_id,
                    str(record.get("label") or ""),
                    str(record.get("transport") or "stdio"),
                    _mcp_command_text(record.get("command")),
                    _mcp_json(record.get("args")),
                    str(record.get("cwd") or ""),
                    str(record.get("url") or ""),
                    float(record.get("timeout") or 15.0),
                    1 if record.get("enabled") else 0,
                    _mcp_json(record.get("tools")),
                    _mcp_json(record.get("discovered")),
                    created,
                    now,
                ),
            )

    def list_mcp_servers(self) -> List[Dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM mcp_servers ORDER BY created_at, server_id"
            ).fetchall()
        return [_mcp_server_from_row(row) for row in rows]

    def get_mcp_server(self, server_id: str) -> Optional[Dict]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_servers WHERE server_id = ?",
                (str(server_id or "").strip().lower(),),
            ).fetchone()
        return _mcp_server_from_row(row) if row else None

    def set_mcp_server_enabled(self, server_id: str, enabled: bool) -> bool:
        """Turn a server into the model's tool list, or take it out.

        Switching off keeps the per-tool selections: the user turns the server
        back on with the tools they had chosen, not with nothing.
        """
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE mcp_servers SET enabled = ?, updated_at = ?"
                " WHERE server_id = ?",
                (
                    1 if enabled else 0,
                    utc_now(),
                    str(server_id or "").strip().lower(),
                ),
            )
        return cursor.rowcount == 1

    def set_mcp_server_tools(self, server_id: str, tools) -> bool:
        """Store the per-tool switches (A9) as one JSON column on the row."""
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE mcp_servers SET tools = ?, updated_at = ?"
                " WHERE server_id = ?",
                (
                    _mcp_json(tools),
                    utc_now(),
                    str(server_id or "").strip().lower(),
                ),
            )
        return cursor.rowcount == 1

    def set_mcp_server_discovered(self, server_id: str, tools) -> bool:
        """Cache what tools/list reported so the panel can draw the switches.

        Only name, description and the server's own ``readOnlyHint`` are kept: an
        inputSchema is the server's untrusted text and can be arbitrarily large.
        ``read_only`` stays three-valued (True / False / None) because 「远端写入」
        may only be claimed by a server that actually said so.
        """
        slim = []
        for item in tools or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "")
                if not name:
                    continue
                description = " ".join(
                    str(item.get("description") or "").split()
                )
                if len(description) > MAX_MCP_STORED_DESCRIPTION:
                    description = description[:MAX_MCP_STORED_DESCRIPTION] + "…"
                hint = item.get("annotations")
                read_only = None
                if isinstance(hint, dict) and isinstance(
                    hint.get("readOnlyHint"), bool
                ):
                    read_only = hint["readOnlyHint"]
                slim.append(
                    {"name": name, "description": description, "read_only": read_only}
                )
            else:
                slim.append(
                    {"name": str(item), "description": "", "read_only": None}
                )
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE mcp_servers SET discovered = ?, updated_at = ?"
                " WHERE server_id = ?",
                (
                    json.dumps(slim, ensure_ascii=False),
                    utc_now(),
                    str(server_id or "").strip().lower(),
                ),
            )
        return cursor.rowcount == 1

    def delete_mcp_server(self, server_id: str) -> bool:
        text_id = str(server_id or "").strip().lower()
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM mcp_servers WHERE server_id = ?",
                (text_id,),
            )
        return cursor.rowcount == 1

    def rebuild_doc_fts(self) -> bool:
        with self._connection() as connection:
            try:
                connection.executescript(DOC_FTS_SCHEMA)
                _reindex_doc_fts(connection)
            except sqlite3.Error:
                self.doc_fts_enabled = False
                return False
        self.doc_fts_enabled = True
        return True

    @staticmethod
    def _proposal_from_row(row):
        return ActionProposal(
            action_id=row["action_id"],
            session_id=row["session_id"],
            mode=AgentMode(row["mode"]),
            intent=row["intent"],
            tool_name=row["tool_name"],
            raw_arguments=json.loads(row["raw_arguments"]),
            normalized_arguments=json.loads(row["normalized_arguments"]),
            expected_state=json.loads(row["expected_state"]),
            risk=RiskLevel(row["risk"]),
            target_summary=row["target_summary"],
            reversible=bool(row["reversible"]),
            user_visible_plan=row["user_visible_plan"],
            confirmation_digest=row["confirmation_digest"],
            idempotency_key=row["idempotency_key"],
            status=ProposalStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            executor=str(row["executor"] or EXECUTOR_NOTE)
            if "executor" in row.keys()
            else EXECUTOR_NOTE,
        )

    @staticmethod
    def _receipt_from_row(row):
        return ActionResult(
            receipt_id=row["receipt_id"],
            action_id=row["action_id"],
            session_id=row["session_id"],
            status=row["status"],
            summary=row["summary"],
            result=json.loads(row["result_json"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            undoable=bool(row["undoable"]),
            undo_status=row["undo_status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _work_task_from_row(row):
        return WorkTask(
            task_id=row["task_id"],
            session_id=row["session_id"],
            state=row["state"],
            card=TaskCard(
                title=row["title"],
                reason=row["reason"],
                spoken_summary=row["spoken_summary"],
                goal=row["goal"],
                executor=row["executor"],
                project=row["project"],
            ),
            source_message_id=row["source_message_id"],
            info_asked=int(row["info_asked"]),
            revision=int(row["revision"]),
            approval_status=row["approval_status"],
            approval_reason=row["approval_reason"],
            metadata=json.loads(row["metadata_json"] or "{}"),
            expires_at=row["expires_at"],
            approved_at=row["approved_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _work_receipt_from_row(row):
        return WorkReceipt(
            receipt_id=row["receipt_id"],
            task_id=row["task_id"],
            session_id=row["session_id"],
            status=row["status"],
            summary=row["summary"],
            detail=row["detail"],
            external_id=row["external_id"],
            attempts=int(row["attempts"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _reminder_from_row(row):
        return ReminderRecord(
            reminder_id=row["reminder_id"],
            session_id=row["session_id"],
            content=row["content"],
            due_at=row["due_at"],
            status=row["status"],
            source_message_id=row["source_message_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            acknowledged_at=row["acknowledged_at"],
        )

    @staticmethod
    def _memory_from_row(row):
        keys = set(row.keys())
        return MemoryRecord(
            memory_id=row["memory_id"],
            session_id=row["session_id"],
            kind=row["kind"],
            content=row["content"],
            source_message_id=row["source_message_id"],
            confidence=row["confidence"],
            importance=row["importance"],
            activation=row["activation"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            canonical_key=(
                row["canonical_key"] if "canonical_key" in keys else ""
            ),
            evidence_count=(
                row["evidence_count"] if "evidence_count" in keys else 0
            ),
            protected=bool(row["protected"]) if "protected" in keys else False,
            forget_epoch=(
                row["forget_epoch"] if "forget_epoch" in keys else 0
            ),
            first_seen_at=(
                row["first_seen_at"] if "first_seen_at" in keys else ""
            ),
            last_seen_at=(
                row["last_seen_at"] if "last_seen_at" in keys else ""
            ),
        )
