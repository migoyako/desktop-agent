"""SQLite persistence for companion state and execution audit records."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Dict, Iterable, List, Optional

from .models import (
    ActionProposal,
    ActionResult,
    AffectRecord,
    AgentMode,
    ExperienceRecord,
    MemoryRecord,
    ProposalStatus,
    RiskLevel,
    new_id,
    utc_now,
)
from .textseg import fts_match_query, fts_segment


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


SCHEMA_VERSION = 3

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


def _add_column_if_missing(connection, table, column, definition):
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info({})".format(table)
        ).fetchall()
    }
    if column not in existing:
        connection.execute(
            "ALTER TABLE {} ADD COLUMN {} {}".format(
                table,
                column,
                definition,
            )
        )


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
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version < SCHEMA_VERSION:
                if version < 1:
                    _migrate_v1(connection)
                    version = 1
                if version < 2:
                    _migrate_v2(connection)
                    version = 2
                if version < 3:
                    _migrate_v3(connection)
                    version = 3
            connection.execute(
                "PRAGMA user_version = {}".format(int(version))
            )
            self.fts_enabled = self._ensure_memory_fts(connection)

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

    def clear_messages(self, session_id: str):
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )

    def create_proposal(self, proposal: ActionProposal):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO action_proposals(
                    action_id, session_id, mode, intent, tool_name,
                    raw_arguments, normalized_arguments, expected_state,
                    risk, target_summary, reversible, user_visible_plan,
                    confirmation_digest, idempotency_key, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    receipt.summary,
                    json.dumps(receipt.result, ensure_ascii=False),
                    receipt.started_at,
                    receipt.finished_at,
                    receipt.error,
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
    ):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recall_log(
                    recall_id, session_id, query_hash, fact_ids, scores,
                    used_in_reply, latency_ms, source, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    new_id("rcl"),
                    session_id,
                    query_hash,
                    json.dumps(fact_ids, ensure_ascii=False),
                    json.dumps(scores),
                    float(latency_ms),
                    source,
                    utc_now(),
                ),
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

    def set_workspace(self, path: str):
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO workspace_grants(id, path, enabled, created_at)
                VALUES (1, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    path = excluded.path,
                    enabled = 1,
                    created_at = excluded.created_at
                """,
                (path, utc_now()),
            )

    def get_workspace(self) -> Optional[str]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT path FROM workspace_grants WHERE id = 1 AND enabled = 1"
            ).fetchone()
        return row["path"] if row else None

    def clear_workspace(self):
        with self._connection() as connection:
            connection.execute(
                "UPDATE workspace_grants SET enabled = 0 WHERE id = 1"
            )

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
