"""Local data maintenance: backups, export, integrity and hard deletion."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
import zipfile

from .models import utc_now


BACKUP_DIR = "backups"
EXPORT_DIR = "exports"
TOOL_RESULT_DIR = "tool_results"
LOCK_FILE = ".companion.lock"
BACKUP_PREFIX = "companion-"
EXPORT_PREFIX = "companion-export-"
EXPORT_FORMAT = "companion-export"
EXPORT_FORMAT_VERSION = 1

EXPORT_TABLES = (
    "sessions",
    "messages",
    "action_proposals",
    "action_receipts",
    "execution_ledger",
    "memories",
    "memory_evidence",
    "memory_candidates",
    "memory_events",
    "memory_relations",
    "memory_tombstones",
    "recall_log",
    "experiences",
    "affect_states",
    "workspace_grants",
    "import_jobs",
    "import_messages",
    "context_checkpoints",
    "reminders",
    "proactive_state",
    "proactive_events",
    "work_tasks",
    "work_receipts",
    "skill_audit",
)

EXPECTED_INDEXES = (
    "idx_messages_session",
    "idx_proposals_pending",
    "idx_execution_ledger_status",
    "idx_memories_session",
    "idx_affect_latest",
    "idx_memory_evidence_memory",
    "idx_memory_candidates_session",
    "idx_memory_events_memory",
    "idx_memory_relations_parent",
    "idx_memory_relations_child",
    "idx_context_checkpoints_session",
    "idx_reminders_due",
    "idx_proactive_events_session",
    "idx_proactive_events_source",
    "idx_work_tasks_session",
    "idx_work_receipts_session",
    "idx_skill_audit_session",
)

# Child rows before parents so foreign keys stay valid during a session purge.
PURGE_SESSION_STEPS = (
    ("memory_relations", "session_id"),
    ("memory_evidence", "session_id"),
    ("memory_candidates", "session_id"),
    ("memory_events", "session_id"),
    ("recall_log", "session_id"),
    ("experiences", "session_id"),
    ("memory_tombstones", "session_id"),
    ("memories", "session_id"),
    ("execution_ledger", "session_id"),
    ("action_receipts", "session_id"),
    ("action_proposals", "session_id"),
    ("work_receipts", "session_id"),
    ("work_tasks", "session_id"),
    ("reminders", "session_id"),
    ("proactive_state", "session_id"),
    ("proactive_events", "session_id"),
    ("skill_audit", "session_id"),
    ("context_checkpoints", "session_id"),
    ("affect_states", "session_id"),
    ("import_jobs", "target_session_id"),
    ("messages", "session_id"),
    ("sessions", "id"),
)

PURGE_ALL_TABLES = (
    "memory_relations",
    "memory_evidence",
    "memory_candidates",
    "memory_events",
    "recall_log",
    "experiences",
    "memory_tombstones",
    "execution_ledger",
    "action_receipts",
    "action_proposals",
    "work_receipts",
    "work_tasks",
    "reminders",
    "proactive_state",
    "proactive_events",
    "skill_audit",
    "context_checkpoints",
    "affect_states",
    "import_messages",
    "import_jobs",
    "memories",
    "messages",
    "sessions",
    "workspace_grants",
)


class MaintenanceError(RuntimeError):
    def __init__(self, message, code="maintenance_error"):
        super().__init__(message)
        self.code = code


def _latest_schema_version():
    from .store import SCHEMA_VERSION

    return SCHEMA_VERSION


def _uri(path):
    return "{}?mode=ro".format(Path(path).resolve().as_uri())


def _readonly_connection(path):
    connection = sqlite3.connect(_uri(path), uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }


def _count(connection, table):
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM {}".format(table)
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None


def _safe_slug(value, fallback="manual"):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-")
    return (cleaned[:24] or fallback).lower()


def _unique_path(directory, stem, suffix):
    candidate = Path(directory) / "{}{}".format(stem, suffix)
    counter = 2
    while candidate.exists():
        candidate = Path(directory) / "{}-{}{}".format(stem, counter, suffix)
        counter += 1
    return candidate


def _write_json_atomic(path, payload):
    path = Path(path)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".tmp-",
            suffix=".json",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _ensure_directory(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise MaintenanceError("数据子目录不能是符号链接。", "unsafe_path")
    return directory


def integrity_report(database_path, expected_version=None):
    path = Path(database_path)
    expected = int(
        expected_version
        if expected_version is not None
        else _latest_schema_version()
    )
    wal = Path(str(path) + "-wal")
    report = {
        "path": str(path),
        "exists": path.exists(),
        "integrity": "missing",
        "foreign_key_violations": 0,
        "user_version": 0,
        "expected_version": expected,
        "version_state": "missing",
        "journal_mode": "",
        "fts_present": False,
        "fts_rows": None,
        "memory_rows": None,
        "missing_indexes": [],
        "counts": {},
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "ok": False,
    }
    if not path.exists():
        return report

    connection = _readonly_connection(path)
    try:
        report["integrity"] = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        report["foreign_key_violations"] = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        report["user_version"] = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        report["journal_mode"] = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        )
        names = _table_names(connection)
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        report["missing_indexes"] = [
            name for name in EXPECTED_INDEXES if name not in indexes
        ]
        report["counts"] = {
            name: _count(connection, name)
            for name in (
                "sessions",
                "messages",
                "memories",
                "action_proposals",
                "action_receipts",
                "work_tasks",
                "work_receipts",
                "recall_log",
                "skill_audit",
            )
            if name in names
        }
        report["fts_present"] = "memory_fts" in names
        report["fts_rows"] = _count(connection, "memory_fts")
        report["memory_rows"] = _count(connection, "memories")
    finally:
        connection.close()

    version = report["user_version"]
    if version == expected:
        report["version_state"] = "current"
    elif version > expected:
        report["version_state"] = "newer"
    elif version > 0:
        report["version_state"] = "outdated"
    report["ok"] = bool(
        report["integrity"] == "ok"
        and report["foreign_key_violations"] == 0
        and report["version_state"] == "current"
        and not report["missing_indexes"]
        and (
            not report["fts_present"]
            or report["fts_rows"] == report["memory_rows"]
        )
    )
    return report


def create_backup_from_connection(
    connection,
    data_dir,
    database_path,
    reason="manual",
    schema_version=None,
):
    backups = _ensure_directory(Path(data_dir) / BACKUP_DIR)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = "{}{}-{}".format(BACKUP_PREFIX, stamp, _safe_slug(reason))
    final_path = _unique_path(backups, stem, ".db")
    manifest_path = final_path.with_name(final_path.stem + ".json")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(backups),
            prefix=".tmp-",
            suffix=".db",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        destination = sqlite3.connect(str(temp_path))
        try:
            connection.backup(destination)
            destination.commit()
        finally:
            destination.close()
        os.replace(temp_path, final_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    version = (
        int(schema_version)
        if schema_version is not None
        else int(connection.execute("PRAGMA user_version").fetchone()[0])
    )
    manifest = {
        "backup": final_path.name,
        "database": Path(database_path).name,
        "created_at": utc_now(),
        "reason": _safe_slug(reason),
        "schema_version": version,
        "size_bytes": final_path.stat().st_size,
        "sha256": _sha256(final_path),
        "messages": _count(connection, "messages"),
        "memories": _count(connection, "memories"),
        "sessions": _count(connection, "sessions"),
    }
    _write_json_atomic(manifest_path, manifest)
    return {
        "path": str(final_path),
        "manifest_path": str(manifest_path),
        "name": final_path.name,
        **manifest,
    }


def create_backup(data_dir, database_path=None, reason="manual"):
    data_dir = Path(data_dir)
    database_path = Path(database_path or data_dir / "companion.db")
    if not database_path.exists():
        raise MaintenanceError("数据库尚未创建。", "no_database")
    connection = _readonly_connection(database_path)
    try:
        return create_backup_from_connection(
            connection,
            data_dir,
            database_path,
            reason=reason,
        )
    finally:
        connection.close()


def list_backups(data_dir):
    directory = Path(data_dir) / BACKUP_DIR
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(
        directory.glob("{}*.db".format(BACKUP_PREFIX)),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        entry = {
            "path": str(path),
            "name": path.name,
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds"),
            "size_bytes": path.stat().st_size,
            "schema_version": None,
            "messages": None,
            "readable": False,
            "manifest": None,
        }
        manifest_path = path.with_name(path.stem + ".json")
        if manifest_path.is_file():
            try:
                entry["manifest"] = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                entry["created_at"] = entry["manifest"].get(
                    "created_at",
                    entry["created_at"],
                )
                entry["schema_version"] = entry["manifest"].get(
                    "schema_version"
                )
                entry["messages"] = entry["manifest"].get("messages")
            except (OSError, ValueError):
                entry["manifest"] = None
        try:
            connection = _readonly_connection(path)
            try:
                entry["schema_version"] = int(
                    connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                )
                entry["messages"] = _count(connection, "messages")
                entry["readable"] = True
            finally:
                connection.close()
        except sqlite3.Error:
            entry["readable"] = False
        entries.append(entry)
    return entries


def verify_backup(backup_path):
    path = Path(backup_path)
    expected = _latest_schema_version()
    result = {
        "path": str(path),
        "exists": path.exists(),
        "valid": False,
        "reason": "",
        "schema_version": None,
        "expected_version": expected,
        "integrity": "missing",
        "counts": {},
        "sha256": "",
        "size_bytes": 0,
        "manifest": None,
    }
    if not path.is_file():
        result["reason"] = "备份文件不存在。"
        return result
    result["size_bytes"] = path.stat().st_size
    if result["size_bytes"] <= 0:
        result["reason"] = "备份文件为空。"
        return result
    result["sha256"] = _sha256(path)
    manifest_path = path.with_name(path.stem + ".json")
    if manifest_path.is_file():
        try:
            result["manifest"] = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            result["manifest"] = None

    try:
        connection = _readonly_connection(path)
        try:
            result["integrity"] = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            violations = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            result["schema_version"] = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            names = _table_names(connection)
            result["counts"] = {
                name: _count(connection, name)
                for name in ("sessions", "messages", "memories")
                if name in names
            }
        finally:
            connection.close()
    except sqlite3.Error as error:
        result["reason"] = "无法读取备份：{}".format(error)
        return result

    required = {"sessions", "messages", "memories"}
    if not required <= set(result["counts"]):
        result["reason"] = "备份缺少核心表。"
        return result
    if result["schema_version"] <= 0:
        result["reason"] = "备份缺少 schema 版本。"
        return result
    if result["schema_version"] > expected:
        result["reason"] = "备份来自更新的版本（v{} > v{}）。".format(
            result["schema_version"],
            expected,
        )
        return result
    if result["integrity"] != "ok":
        result["reason"] = "备份完整性检查失败：{}".format(
            result["integrity"]
        )
        return result
    if violations:
        result["reason"] = "备份存在外键错误。"
        return result
    result["valid"] = True
    return result


def restore_backup(data_dir, backup_path, database_path=None, safety=True):
    data_dir = Path(data_dir)
    backup_path = Path(backup_path)
    database_path = Path(database_path or data_dir / "companion.db")
    verification = verify_backup(backup_path)
    if not verification["valid"]:
        raise MaintenanceError(
            verification["reason"] or "备份无效。",
            "invalid_backup",
        )
    if backup_path.resolve() == database_path.resolve():
        raise MaintenanceError("不能把数据库恢复到自身。", "invalid_target")

    safety_info = None
    if safety and database_path.exists():
        safety_info = create_backup(
            data_dir,
            database_path,
            reason="pre-restore",
        )

    source = _readonly_connection(backup_path)
    destination = sqlite3.connect(str(database_path), timeout=15)
    try:
        source.backup(destination)
        destination.commit()
        try:
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
    finally:
        destination.close()
        source.close()
    return {
        "restored_from": str(backup_path),
        "database": str(database_path),
        "schema_version": verification["schema_version"],
        "safety_backup": safety_info["path"] if safety_info else "",
        "counts": verification["counts"],
    }


def export_data(
    data_dir,
    database_path=None,
    output_path=None,
    include_tool_results=True,
):
    data_dir = Path(data_dir)
    database_path = Path(database_path or data_dir / "companion.db")
    if not database_path.exists():
        raise MaintenanceError("数据库尚未创建。", "no_database")
    report = integrity_report(database_path)
    connection = _readonly_connection(database_path)
    try:
        names = _table_names(connection)
        tables = {}
        for name in EXPORT_TABLES:
            if name not in names:
                continue
            tables[name] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM {}".format(name)
                ).fetchall()
            ]
    finally:
        connection.close()

    counts = {name: len(rows) for name, rows in tables.items()}
    payload = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utc_now(),
        "database": {
            "name": database_path.name,
            "schema_version": report["user_version"],
            "integrity": report["integrity"],
        },
        "counts": counts,
        "tables": tables,
    }
    if output_path is None:
        exports = _ensure_directory(data_dir / EXPORT_DIR)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = _unique_path(
            exports,
            "{}{}".format(EXPORT_PREFIX, stamp),
            ".zip",
        )
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    tool_count = 0
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(output_path.parent),
            prefix=".tmp-",
            suffix=".zip",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        with zipfile.ZipFile(
            str(temp_path),
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "export.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            if include_tool_results:
                source_dir = data_dir / TOOL_RESULT_DIR
                if source_dir.is_dir():
                    for path in sorted(
                        source_dir.glob("tr_*.json")
                    ):
                        if path.is_symlink() or not path.is_file():
                            continue
                        archive.write(
                            path,
                            "{}/{}".format(TOOL_RESULT_DIR, path.name),
                        )
                        tool_count += 1
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": EXPORT_FORMAT,
                        "format_version": EXPORT_FORMAT_VERSION,
                        "exported_at": payload["exported_at"],
                        "schema_version": report["user_version"],
                        "counts": counts,
                        "tool_results": tool_count,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        os.replace(temp_path, output_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return {
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "counts": counts,
        "tool_results": tool_count,
        "schema_version": report["user_version"],
    }


def _memory_tree(connection, memory_id):
    session_row = connection.execute(
        "SELECT session_id FROM memories WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    if session_row is None:
        return None, set()
    session_id = session_row[0]
    seen = {memory_id}
    frontier = [memory_id]
    while frontier:
        placeholders = ",".join("?" for _ in frontier)
        rows = connection.execute(
            """
            SELECT child_memory_id FROM memory_relations
            WHERE session_id = ? AND parent_memory_id IN ({})
            """.format(placeholders),
            (session_id, *frontier),
        ).fetchall()
        frontier = [
            row[0] for row in rows if row[0] not in seen
        ]
        seen.update(frontier)
    return session_id, seen


def _delete_ids(connection, table, column, ids):
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cursor = connection.execute(
        "DELETE FROM {} WHERE {} IN ({})".format(
            table,
            column,
            placeholders,
        ),
        tuple(ids),
    )
    return max(0, int(cursor.rowcount))


def purge_memory(store, memory_id):
    counts = {}
    with store._connection() as connection:
        session_id, ids = _memory_tree(connection, memory_id)
        if session_id is None:
            return {
                "deleted": 0,
                "memory_ids": [],
                "counts": counts,
                "recall_updated": 0,
                "recall_deleted": 0,
            }
        id_list = sorted(ids)
        counts["memory_relations"] = (
            _delete_ids(connection, "memory_relations", "parent_memory_id", id_list)
            + _delete_ids(connection, "memory_relations", "child_memory_id", id_list)
        )
        counts["memory_evidence"] = _delete_ids(
            connection, "memory_evidence", "memory_id", id_list
        )
        counts["memory_candidates"] = _delete_ids(
            connection, "memory_candidates", "memory_id", id_list
        )
        counts["memory_events"] = _delete_ids(
            connection, "memory_events", "memory_id", id_list
        )
        counts["experiences"] = _delete_ids(
            connection, "experiences", "memory_id", id_list
        )
        try:
            connection.execute(
                "DELETE FROM memory_fts WHERE memory_id IN ({})".format(
                    ",".join("?" for _ in id_list)
                ),
                tuple(id_list),
            )
        except sqlite3.Error:
            pass

        recall_updated = 0
        recall_deleted = 0
        rows = connection.execute(
            """
            SELECT recall_id, fact_ids, scores FROM recall_log
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        for row in rows:
            try:
                fact_ids = json.loads(row["fact_ids"] or "[]")
                scores = json.loads(row["scores"] or "[]")
            except ValueError:
                continue
            kept = [
                (fact_id, score)
                for index, fact_id in enumerate(fact_ids)
                for score in [scores[index] if index < len(scores) else None]
                if fact_id not in ids
            ]
            if len(kept) == len(fact_ids):
                continue
            if not kept:
                connection.execute(
                    "DELETE FROM recall_log WHERE recall_id = ?",
                    (row["recall_id"],),
                )
                recall_deleted += 1
            else:
                connection.execute(
                    """
                    UPDATE recall_log SET fact_ids = ?, scores = ?
                    WHERE recall_id = ?
                    """,
                    (
                        json.dumps([item[0] for item in kept], ensure_ascii=False),
                        json.dumps([item[1] for item in kept]),
                        row["recall_id"],
                    ),
                )
                recall_updated += 1
        counts["memories"] = _delete_ids(
            connection, "memories", "memory_id", id_list
        )
    return {
        "deleted": len(id_list),
        "memory_ids": id_list,
        "counts": counts,
        "recall_updated": recall_updated,
        "recall_deleted": recall_deleted,
    }


def _purge_tool_results(data_dir, session_id=None):
    directory = Path(data_dir) / TOOL_RESULT_DIR
    if not directory.is_dir():
        return {"deleted": 0, "skipped": 0}
    deleted = 0
    skipped = 0
    for path in list(directory.glob("tr_*.json")):
        if path.is_symlink() or not path.is_file():
            skipped += 1
            continue
        if session_id is not None:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skipped += 1
                continue
            if payload.get("session_id") != session_id:
                continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            skipped += 1
    return {"deleted": deleted, "skipped": skipped}


def purge_session(store, session_id, data_dir=None, include_tool_results=True):
    data_dir = Path(data_dir or store.database_path.parent)
    counts = {}
    with store._connection() as connection:
        rows = connection.execute(
            "SELECT job_id FROM import_jobs WHERE target_session_id = ?",
            (session_id,),
        ).fetchall()
        job_ids = [row[0] for row in rows]
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            cursor = connection.execute(
                "DELETE FROM import_messages WHERE job_id IN ({})".format(
                    placeholders
                ),
                tuple(job_ids),
            )
            counts["import_messages"] = max(0, int(cursor.rowcount))
        try:
            cursor = connection.execute(
                """
                DELETE FROM memory_fts WHERE memory_id IN (
                    SELECT memory_id FROM memories WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            counts["memory_fts"] = max(0, int(cursor.rowcount))
        except sqlite3.Error:
            counts["memory_fts"] = 0
        for table, column in PURGE_SESSION_STEPS:
            cursor = connection.execute(
                "DELETE FROM {} WHERE {} = ?".format(table, column),
                (session_id,),
            )
            counts[table] = max(0, int(cursor.rowcount))
    tool_results = (
        _purge_tool_results(data_dir, session_id)
        if include_tool_results
        else {"deleted": 0, "skipped": 0}
    )
    return {
        "session_id": session_id,
        "counts": counts,
        "tool_results": tool_results,
    }


def purge_all(
    store,
    data_dir=None,
    include_backups=False,
    include_exports=False,
):
    data_dir = Path(data_dir or store.database_path.parent)
    counts = {}
    with store._connection() as connection:
        fts_count = _count(connection, "memory_fts")
        try:
            connection.execute("DELETE FROM memory_fts")
        except sqlite3.Error:
            fts_count = 0
        counts["memory_fts"] = fts_count or 0
        for table in PURGE_ALL_TABLES:
            cursor = connection.execute(
                "DELETE FROM {}".format(table)
            )
            counts[table] = max(0, int(cursor.rowcount))
        try:
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'messages'"
            )
        except sqlite3.Error:
            pass
    tool_results = _purge_tool_results(data_dir)
    removed_files = 0
    if include_backups:
        backups = data_dir / BACKUP_DIR
        if backups.is_dir():
            for path in list(backups.iterdir()):
                if path.is_symlink() or path.is_file():
                    try:
                        path.unlink()
                        removed_files += 1
                    except OSError:
                        pass
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                    removed_files += 1
    if include_exports:
        exports = data_dir / EXPORT_DIR
        if exports.is_dir():
            for path in list(exports.iterdir()):
                if path.is_symlink() or path.is_file():
                    try:
                        path.unlink()
                        removed_files += 1
                    except OSError:
                        pass
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                    removed_files += 1
    return {
        "counts": counts,
        "tool_results": tool_results,
        "removed_files": removed_files,
    }


class InstanceLock:
    """Advisory single-instance lock for one data directory."""

    _shared = {}

    def __init__(self, data_dir, filename=LOCK_FILE):
        self.path = Path(data_dir) / filename
        self.owner_path = self.path.with_name(self.path.name + ".owner")
        self._key = str(self.path.resolve())
        self._acquired = False

    def acquire(self):
        if self._acquired:
            return True
        shared = InstanceLock._shared.get(self._key)
        if shared is not None:
            handle, count = shared
            InstanceLock._shared[self._key] = (handle, count + 1)
            self._acquired = True
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        try:
            handle = self.path.open("a+b")
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.seek(0)
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": utc_now(),
                    "app": "companion",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            with self.owner_path.open("wb") as owner:
                owner.write(payload)
                owner.flush()
                try:
                    os.fsync(owner.fileno())
                except OSError:
                    pass
        except (OSError, ImportError):
            if handle is not None:
                handle.close()
            return False
        InstanceLock._shared[self._key] = (handle, 1)
        self._acquired = True
        return True

    def release(self):
        if not self._acquired:
            return
        self._acquired = False
        shared = InstanceLock._shared.get(self._key)
        if shared is None:
            return
        handle, count = shared
        if count > 1:
            InstanceLock._shared[self._key] = (handle, count - 1)
            return
        InstanceLock._shared.pop(self._key, None)
        try:
            with self.owner_path.open("wb"):
                pass
        except OSError:
            pass
        try:
            handle.seek(0)
            handle.truncate()
            handle.flush()
        except OSError:
            pass
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        handle.close()

    def peek(self):
        try:
            text = self.owner_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).strip("\x00 \r\n\t")
        except OSError:
            return None
        if not text:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        payload["lock_file"] = str(self.path)
        return payload

    def __enter__(self):
        if not self.acquire():
            raise MaintenanceError(
                "另一个实例正在使用这个数据目录。",
                "instance_locked",
            )
        return self

    def __exit__(self, *_exc):
        self.release()
        return False


def lock_info(data_dir):
    return InstanceLock(data_dir).peek()
