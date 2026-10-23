"""SQLite persistence for companion state and execution audit records."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
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
    utc_now,
)


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
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)

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

    def add_memory(
        self,
        session_id: str,
        kind: str,
        content: str,
        source_message_id: Optional[int],
        confidence: float,
        importance: float,
        status: str = "candidate",
    ) -> str:
        from .models import new_id

        now = utc_now()
        memory_id = new_id("mem")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memories(
                    memory_id, session_id, kind, content, source_message_id,
                    confidence, importance, activation, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
        return memory_id

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

    def add_experience(
        self,
        session_id: str,
        title: str,
        summary: str,
        source_message_id: Optional[int],
        receipt_id: Optional[str],
    ) -> str:
        from .models import new_id

        experience_id = new_id("exp")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experiences(
                    experience_id, session_id, title, summary,
                    source_message_id, receipt_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    session_id,
                    title,
                    summary,
                    source_message_id,
                    receipt_id,
                    utc_now(),
                ),
            )
        return experience_id

    def list_experiences(self, session_id: str, limit: int = 30):
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM experiences
                WHERE session_id = ?
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
            )
            for row in rows
        ]

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
        )
