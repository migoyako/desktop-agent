"""Directory authorizations and task-scoped leases (desktop agent S1).

Nothing in this module touches a file. It decides *whether* an action may touch a
file at all: which directories the user authorized, for which capabilities, and
for how long one task may act on them. Executors ask here before they act, and a
revocation is supposed to bite immediately rather than at the next restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from .models import new_id, utc_now
from .path_safety import (
    PathSafetyError,
    check_grantable_directory,
    path_key,
    resource_identity,
)


CAPABILITY_FS_READ = "fs-read"
CAPABILITY_FS_WRITE = "fs-write"
CAPABILITY_FS_SEARCH = "fs-search"
CAPABILITY_FS_DELETE = "fs-delete"
CAPABILITY_FS_EXPORT = "fs-export"

# Directory-scoped capabilities only. Application launching, UI control, launch
# chains and chat sending get their own resource kinds when those slices land;
# inventing them now would create grant states no executor can ever reach.
DIRECTORY_CAPABILITIES = (
    CAPABILITY_FS_READ,
    CAPABILITY_FS_WRITE,
    CAPABILITY_FS_SEARCH,
    CAPABILITY_FS_DELETE,
    CAPABILITY_FS_EXPORT,
)

RESOURCE_DIRECTORY = "directory"

GRANT_ACTIVE = "active"
GRANT_REVOKED = "revoked"
GRANT_STATUSES = (GRANT_ACTIVE, GRANT_REVOKED)

LEASE_ACTIVE = "active"
LEASE_RELEASED = "released"
LEASE_REVOKED = "revoked"
LEASE_EXPIRED = "expired"

MAX_DIRECTORY_GRANTS = 16
DEFAULT_LEASE_TTL_SECONDS = 300
MAX_LEASE_TTL_SECONDS = 900
MAX_LEASE_OPERATIONS = 50
MAX_LEASE_RESOURCES = 32


class AccessControlError(RuntimeError):
    def __init__(self, message, code="access_denied"):
        super().__init__(message)
        self.code = code


def _parse_instant(value) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso_instant(value: datetime) -> str:
    """Second-precision ISO stamp.

    Lease expiry is compared inside a single SQL UPDATE so two callers cannot both
    spend the last operation. String comparison only holds while every stamp written
    here has the same shape, and ``datetime.isoformat()`` drops the fractional part
    whenever microseconds happen to be zero.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AccessGrant:
    grant_id: str
    path: str
    capabilities: Tuple[str, ...] = ()
    resource_kind: str = RESOURCE_DIRECTORY
    volume_id: str = ""
    file_id: str = ""
    status: str = GRANT_ACTIVE
    source: str = "grant"
    sensitivity_override: bool = False
    legacy_workspace: bool = False
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    revoked_at: str = ""

    @property
    def canonical_root(self) -> str:
        return str(Path(self.path).resolve()) if self.path else ""

    def has_capability(self, capability) -> bool:
        return str(capability) in self.capabilities

    def identity_snapshot(self) -> Dict:
        return {
            "root": self.canonical_root,
            "device": str(self.volume_id or ""),
            "file_id": str(self.file_id or ""),
        }


@dataclass(frozen=True)
class GrantLease:
    lease_id: str
    grant_id: str
    task_id: str
    capability: str
    expires_at: str
    nonce: str
    resource_ids: Tuple[str, ...] = ()
    confirm_digest: str = ""
    max_operations: int = 1
    operations_used: int = 0
    status: str = LEASE_ACTIVE
    created_at: str = ""
    released_at: str = ""

    def remaining_operations(self) -> int:
        return max(0, int(self.max_operations) - int(self.operations_used))

    def is_expired(self, now=None) -> bool:
        expiry = _parse_instant(self.expires_at)
        if expiry is None:
            return True
        moment = now or _utc_now()
        return moment >= expiry

    def effective_status(self, now=None) -> str:
        if self.status != LEASE_ACTIVE:
            return self.status
        return LEASE_EXPIRED if self.is_expired(now) else LEASE_ACTIVE


def grant_from_row(row) -> Optional[AccessGrant]:
    if row is None:
        return None
    return AccessGrant(
        grant_id=str(row["grant_id"]),
        path=str(row["path"] or ""),
        capabilities=tuple(row["capabilities"] or ()),
        resource_kind=str(row["resource_kind"] or RESOURCE_DIRECTORY),
        volume_id=str(row["volume_id"] or ""),
        file_id=str(row["file_id"] or ""),
        status=str(row["status"] or GRANT_REVOKED),
        source=str(row["source"] or "grant"),
        sensitivity_override=bool(row["sensitivity_override"]),
        legacy_workspace=bool(row["legacy_workspace"]),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        last_used_at=str(row["last_used_at"] or ""),
        revoked_at=str(row["revoked_at"] or ""),
    )


def lease_from_row(row) -> Optional[GrantLease]:
    if row is None:
        return None
    return GrantLease(
        lease_id=str(row["lease_id"]),
        grant_id=str(row["grant_id"]),
        task_id=str(row["task_id"] or ""),
        capability=str(row["capability"] or ""),
        expires_at=str(row["expires_at"] or ""),
        nonce=str(row["nonce"] or ""),
        resource_ids=tuple(row["resource_ids"] or ()),
        confirm_digest=str(row["confirm_digest"] or ""),
        max_operations=int(row["max_operations"] or 0),
        operations_used=int(row["operations_used"] or 0),
        status=str(row["status"] or LEASE_ACTIVE),
        created_at=str(row["created_at"] or ""),
        released_at=str(row["released_at"] or ""),
    )


def normalize_capabilities(values) -> Tuple[str, ...]:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            values = [values]
    if isinstance(values, (list, tuple, set)):
        items = [str(value).strip() for value in values]
    else:
        items = [str(values).strip()]
    cleaned = []
    for item in items:
        if not item:
            continue
        if item not in DIRECTORY_CAPABILITIES:
            raise AccessControlError(
                "不认识的能力：{}。目录授权只接受 {}。".format(
                    item, "、".join(DIRECTORY_CAPABILITIES)
                ),
                "unknown_capability",
            )
        if item not in cleaned:
            cleaned.append(item)
    if not cleaned:
        raise AccessControlError("至少要勾选一项能力。", "no_capability")
    return tuple(sorted(cleaned))


class AccessControlService:
    """Grants, leases and revocation for one companion data directory."""

    def __init__(self, store):
        self.store = store

    # -- 授权 ---------------------------------------------------------------

    def grant_directory(
        self,
        path,
        capabilities,
        source="grant",
        allow_sensitive=False,
    ) -> AccessGrant:
        """Authorize one directory; re-granting the same path replaces its set.

        ``allow_sensitive`` is the only way past the credential-directory gate and it
        is written onto the row, so "this grant was let through on purpose" stays
        auditable instead of living in the caller's memory.
        """
        caps = normalize_capabilities(capabilities)
        guard = check_grantable_directory(path, allow_sensitive=allow_sensitive)
        existing = self.store.find_directory_grant(guard.root)
        if existing is None and self.store.count_access_grants(GRANT_ACTIVE) >= MAX_DIRECTORY_GRANTS:
            raise AccessControlError(
                "已授权的目录已达 {} 个上限，请先撤销不再需要的目录。".format(
                    MAX_DIRECTORY_GRANTS
                ),
                "grant_limit",
            )
        row = self.store.upsert_directory_grant(
            str(guard.root),
            caps,
            source=source,
            sensitivity_override=bool(allow_sensitive),
        )
        return grant_from_row(row)

    def revoke_grant(self, grant_id) -> AccessGrant:
        grant = self.get_grant(grant_id)
        if grant is None:
            raise AccessControlError("找不到这条授权。", "grant_missing")
        if grant.status != GRANT_ACTIVE:
            return grant
        self.store.set_access_grant_status(grant_id, GRANT_REVOKED)
        current = self.get_grant(grant_id)
        if current is None or current.status != GRANT_REVOKED:
            raise AccessControlError("撤销没有写入成功，请稍后重试。", "revoke_failed")
        return current

    def get_grant(self, grant_id) -> Optional[AccessGrant]:
        return grant_from_row(self.store.get_access_grant(grant_id))

    def find_grant(self, path) -> Optional[AccessGrant]:
        if not str(path or "").strip():
            return None
        return grant_from_row(self.store.find_directory_grant(path))

    def list_grants(self, status=GRANT_ACTIVE) -> List[AccessGrant]:
        return [grant_from_row(row) for row in self.store.list_access_grants(status=status)]

    def grant_for_capability(self, path, capability) -> Tuple[Optional[AccessGrant], str]:
        """Return the usable grant for ``path`` or the reason there is none."""
        caps = (capability,) if isinstance(capability, str) else tuple(capability)
        normalize_capabilities(caps)
        grant = self.find_grant(path)
        if grant is None:
            return None, "这个目录还没有授权。"
        if grant.status != GRANT_ACTIVE:
            return None, "这条授权已撤销，请重新授权。"
        missing = [item for item in caps if not grant.has_capability(item)]
        if missing:
            return None, "这条授权不包含：{}。".format("、".join(missing))
        usable, reason = self.check_identity(grant)
        if not usable:
            return None, reason
        return grant, ""

    def check_identity(self, grant: AccessGrant) -> Tuple[bool, str]:
        """Verify the directory is still the one the user authorized.

        A row created while the directory was unreachable carries no identity; the
        first successful check binds it rather than refusing forever, otherwise a
        drive that was offline at grant time could never be used again.
        """
        if not grant.path:
            return False, "授权记录没有保存路径。"
        try:
            current = resource_identity(grant.path)
        except OSError:
            return False, "授权目录不存在或已无法访问，请重新授权。"
        if not str(grant.volume_id or "") and not str(grant.file_id or ""):
            self.store.bind_grant_identity(grant.grant_id, current)
            return True, ""
        if (
            str(current["device"]) != str(grant.volume_id)
            or str(current["file_id"]) != str(grant.file_id)
        ):
            return False, "目录身份已变化（卷或文件标识不同），需要重新授权。"
        return True, ""

    # -- 租约 ---------------------------------------------------------------

    def issue_lease(
        self,
        grant_id,
        task_id,
        capability,
        resource_ids: Sequence[str] = (),
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        max_operations: int = 1,
        confirm_digest: str = "",
    ) -> GrantLease:
        """Open a short lease bound to one task, one capability and one grant."""
        if not str(task_id or "").strip():
            raise AccessControlError("租约必须绑定到一个任务。", "no_task")
        caps = normalize_capabilities([capability])
        capability = caps[0]
        ttl = int(ttl_seconds)
        if ttl <= 0 or ttl > MAX_LEASE_TTL_SECONDS:
            raise AccessControlError(
                "授权租约的有效期必须在 1 到 {} 秒之间。".format(MAX_LEASE_TTL_SECONDS),
                "bad_ttl",
            )
        operations = int(max_operations)
        if operations <= 0 or operations > MAX_LEASE_OPERATIONS:
            raise AccessControlError(
                "授权租约的操作次数必须在 1 到 {} 之间。".format(MAX_LEASE_OPERATIONS),
                "bad_operations",
            )
        resources = [str(item).strip() for item in (resource_ids or ()) if str(item).strip()]
        if len(resources) > MAX_LEASE_RESOURCES:
            raise AccessControlError(
                "一条租约最多绑定 {} 个资源。".format(MAX_LEASE_RESOURCES),
                "too_many_resources",
            )
        grant = self.get_grant(grant_id)
        if grant is None:
            raise AccessControlError("找不到这条授权。", "grant_missing")
        if grant.status != GRANT_ACTIVE:
            raise AccessControlError("这条授权已撤销，不能发放租约。", "grant_revoked")
        if not grant.has_capability(capability):
            raise AccessControlError(
                "这条授权不包含「{}」。".format(capability),
                "capability_not_granted",
            )
        usable, reason = self.check_identity(grant)
        if not usable:
            raise AccessControlError(reason, "grant_identity_changed")
        expires_at = _iso_instant(_utc_now() + timedelta(seconds=ttl))
        row = self.store.add_grant_lease(
            grant_id=grant.grant_id,
            task_id=str(task_id),
            capability=capability,
            expires_at=expires_at,
            nonce=secrets.token_hex(8),
            resource_ids=resources,
            max_operations=operations,
            confirm_digest=str(confirm_digest or ""),
        )
        return lease_from_row(row)

    def get_lease(self, lease_id) -> Optional[GrantLease]:
        return lease_from_row(self.store.get_grant_lease(lease_id))

    def list_leases(self, task_id=None, grant_id=None) -> List[GrantLease]:
        return [
            lease_from_row(row)
            for row in self.store.list_grant_leases(task_id=task_id, grant_id=grant_id)
        ]

    def active_leases(self, now=None) -> List[GrantLease]:
        moment = now or _utc_now()
        result = []
        for row in self.store.list_grant_leases(status=LEASE_ACTIVE):
            lease = self.get_lease(row["lease_id"])
            if lease is not None and not lease.is_expired(moment):
                result.append(lease)
        return result

    def use_lease(self, lease_id, task_id=None, confirm_digest=None) -> GrantLease:
        """Spend one operation of a lease, or refuse with the reason.

        Every precondition is re-read here rather than trusted from issue time: the
        grant may have been revoked, the directory may have moved, or the user may
        have changed the capability set while this task was running.
        """
        lease = self.get_lease(lease_id)
        if lease is None:
            raise AccessControlError("找不到这条授权租约。", "lease_missing")
        if task_id is not None and str(task_id) != lease.task_id:
            raise AccessControlError(
                "这条租约属于另一个任务，不能复用。", "lease_task_mismatch"
            )
        if confirm_digest is not None and str(confirm_digest) != lease.confirm_digest:
            raise AccessControlError(
                "执行内容与确认时不一致，租约已失效。", "lease_digest_mismatch"
            )
        if lease.status != LEASE_ACTIVE:
            raise AccessControlError(
                "这条授权租约已经结束（{}）。".format(lease.status), "lease_closed"
            )
        if lease.is_expired():
            raise AccessControlError("授权租约已过期。", "lease_expired")
        if lease.remaining_operations() <= 0:
            raise AccessControlError("授权租约的操作次数已用尽。", "lease_exhausted")
        grant = self.get_grant(lease.grant_id)
        if grant is None or grant.status != GRANT_ACTIVE:
            raise AccessControlError(
                "目录授权已撤销，租约同时失效。", "grant_revoked"
            )
        if not grant.has_capability(lease.capability):
            raise AccessControlError(
                "这条授权已不再包含「{}」。".format(lease.capability),
                "capability_not_granted",
            )
        usable, reason = self.check_identity(grant)
        if not usable:
            raise AccessControlError(reason, "grant_identity_changed")
        if not self.store.consume_grant_lease(lease.lease_id, now=_iso_instant(_utc_now())):
            # The UPDATE itself checks status, expiry and the budget; losing the race
            # is reported as exhaustion instead of a second, contradictory answer.
            refreshed = self.get_lease(lease.lease_id) or lease
            if refreshed.status != LEASE_ACTIVE:
                raise AccessControlError(
                    "这条授权租约已经结束（{}）。".format(refreshed.status), "lease_closed"
                )
            if refreshed.is_expired():
                raise AccessControlError("授权租约已过期。", "lease_expired")
            raise AccessControlError(
                "授权租约的操作次数已用尽。", "lease_exhausted"
            )
        current = self.get_lease(lease.lease_id)
        if current is None:
            raise AccessControlError("租约状态读不回来，本次操作不记为成功。", "lease_lost")
        return current

    def release_lease(self, lease_id) -> int:
        return self.store.finish_grant_leases(LEASE_RELEASED, lease_id=lease_id)

    def release_task(self, task_id) -> int:
        """Close every live lease of a finished task."""
        return self.store.finish_grant_leases(LEASE_RELEASED, task_id=task_id)


def read_state(connection, now=None) -> Dict:
    """Read-only view of grants and leases for diagnostics.

    Deliberately free of writes: a self-check that lazily rebinds an identity could
    turn "this directory moved" into "it is fine now" just by looking.
    """
    moment = now or _utc_now()
    grants: List[Dict] = []
    try:
        rows = connection.execute(
            """
            SELECT grant_id, path, path_key, capabilities, status, source,
                   sensitivity_override, legacy_workspace, volume_id, file_id,
                   created_at, updated_at, last_used_at, revoked_at
            FROM access_grants
            ORDER BY status, updated_at DESC
            """
        ).fetchall()
    except sqlite3.Error:
        return {"available": False, "reason": "access_grants 表不存在（数据库尚未迁移到 v13）"}
    for row in rows:
        value = dict(row)
        try:
            capabilities = json.loads(value.get("capabilities") or "[]")
        except ValueError:
            capabilities = []
        entry = {
            "grant_id": value["grant_id"],
            "path": value["path"],
            "capabilities": sorted(capabilities),
            "status": value["status"],
            "source": value["source"],
            "sensitivity_override": bool(value["sensitivity_override"]),
            "legacy_workspace": bool(value["legacy_workspace"]),
            "identity_bound": bool(value["volume_id"] or value["file_id"]),
            "drifted": False,
            "last_used_at": value["last_used_at"],
            "revoked_at": value["revoked_at"],
        }
        if value["status"] == GRANT_ACTIVE and value["path"]:
            try:
                current = resource_identity(value["path"])
            except OSError:
                current = None
            if current is None:
                entry["drifted"] = True
                entry["reason"] = "missing"
            elif value["volume_id"] or value["file_id"]:
                if (
                    str(current["device"]) != str(value["volume_id"])
                    or str(current["file_id"]) != str(value["file_id"])
                ):
                    entry["drifted"] = True
                    entry["reason"] = "identity_changed"
        grants.append(entry)
    leases: List[Dict] = []
    try:
        lease_rows = connection.execute(
            """
            SELECT lease_id, grant_id, task_id, capability, expires_at,
                   max_operations, operations_used, status, created_at, released_at
            FROM grant_leases
            ORDER BY created_at DESC
            """
        ).fetchall()
    except sqlite3.Error:
        lease_rows = []
    for row in lease_rows:
        value = dict(row)
        state = value["status"]
        if state == LEASE_ACTIVE:
            expiry = _parse_instant(value.get("expires_at"))
            if expiry is None or moment >= expiry:
                state = LEASE_EXPIRED
        leases.append(
            {
                "lease_id": value["lease_id"],
                "grant_id": value["grant_id"],
                "task_id": value["task_id"],
                "capability": value["capability"],
                "state": state,
                "used": int(value["operations_used"] or 0),
                "of": int(value["max_operations"] or 0),
                "expires_at": value["expires_at"],
            }
        )
    active = [item for item in grants if item["status"] == GRANT_ACTIVE]
    return {
        "available": True,
        "reason": "",
        "grants": grants,
        "active_count": len(active),
        "drifted_count": len([item for item in active if item["drifted"]]),
        "override_count": len([item for item in active if item["sensitivity_override"]]),
        "workspace": next(
            (
                item["path"]
                for item in active
                if item["legacy_workspace"]
            ),
            "",
        ),
        "leases": leases,
        "live_lease_count": len(
            [item for item in leases if item["state"] == LEASE_ACTIVE]
        ),
        "expired_lease_count": len(
            [item for item in leases if item["state"] == LEASE_EXPIRED]
        ),
    }


def state_report(database_path) -> Dict:
    """Open the database read-only and describe the authorization state."""
    uri = "{}?mode=ro".format(Path(database_path).resolve().as_uri())
    connection = sqlite3.connect(uri, uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        return read_state(connection)
    finally:
        connection.close()
