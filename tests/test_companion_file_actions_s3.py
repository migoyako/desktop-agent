"""S3 — authorised-directory file actions: write, copy, move and recycle-bin delete.

These tests hold the three promises the slice is built on: a proposal writes nothing
until the user confirms, every refusal happens before the first side effect so the
receipt can honestly say "not executed", and delete has exactly one implementation
path — the system recycle bin.
"""

from dataclasses import replace
import inspect
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_framework.companion import file_actions as file_actions_module
from agent_framework.companion.access_control import (
    CAPABILITY_FS_DELETE,
    CAPABILITY_FS_READ,
    CAPABILITY_FS_SEARCH,
    CAPABILITY_FS_WRITE,
    AccessControlService,
)
from agent_framework.companion.file_actions import (
    MAX_FILE_WRITE_BYTES,
    MAX_UNDO_BACKUP_BYTES,
    TEMP_CREATE_ATTEMPTS,
    FileActionError,
    FileActionService,
    NotExecutedError,
    UncertainExecutionError,
    stamp,
    volume_of,
)
from agent_framework.companion.file_index import FileIndexService
from agent_framework.companion.models import (
    EXECUTOR_FILE,
    EXECUTOR_NOTE,
    AgentMode,
    ProposalStatus,
    RiskLevel,
    SearchResult,
)
from agent_framework.companion.recycle import (
    FOF_ALLOWUNDO,
    MAX_SHELL_PATH_CHARS,
    RecycleBinError,
    delete_arguments,
    send_to_recycle_bin,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


ALL_WRITE_CAPS = (
    CAPABILITY_FS_READ,
    CAPABILITY_FS_SEARCH,
    CAPABILITY_FS_WRITE,
    CAPABILITY_FS_DELETE,
)


class StubSearch:
    def search(self, query, limit=5):
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class FakeRecycleBin:
    """替身回收站：文件被搬走而不是删掉，所以"还原"在测试里仍然可验证。"""

    def __init__(self, bin_dir, *, vanish=False, raise_after=False, keep=False):
        self.bin_dir = Path(bin_dir)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.vanish = vanish
        self.raise_after = raise_after
        self.keep = keep
        self.calls = []

    def __call__(self, paths):
        target = Path(str(paths[0]))
        self.calls.append(str(target))
        if self.keep:
            return {"shell_code": 0}
        stored = self.bin_dir / target.name
        shutil.move(str(target), str(stored))
        if self.raise_after:
            raise RecycleBinError("模拟：已经搬走，系统回报失败", "recycle_failed", 123)
        if self.vanish:
            raise RecycleBinError("模拟：未知错误", "recycle_failed", 124)
        return {"shell_code": 0}


class S3Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.access = AccessControlService(self.store)
        self.index = FileIndexService(self.store, self.access)
        self.files = FileActionService(
            self.store, self.data, self.access, self.index
        )
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.bin = self.root / "recycle"

    def tearDown(self):
        self.index.shutdown()
        self.temp.cleanup()

    def grant(self, directory=None, capabilities=ALL_WRITE_CAPS):
        return self.access.grant_directory(
            directory if directory is not None else self.projects,
            list(capabilities),
        )

    def write(self, relative, text="旧内容", directory=None):
        target = (directory if directory is not None else self.projects) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def live(self, relative):
        return "live:" + str(relative).replace("\\", "/")

    def write_proposal(self, grant, relative="reports/new.md", content="正文内容", **kw):
        return self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path=relative,
            content=content,
            **kw,
        )


class WriteProposalTest(S3Fixture):
    def test_writes_only_after_execute(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)

        self.assertEqual(proposal.executor, EXECUTOR_FILE)
        self.assertEqual(proposal.risk, RiskLevel.FS_WRITE)
        self.assertFalse((self.projects / "reports" / "new.md").exists())

        receipt = self.files.execute(proposal)

        target = self.projects / "reports" / "new.md"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), "正文内容")
        self.assertEqual(receipt.status, "completed")
        self.assertTrue(receipt.undoable)
        self.assertEqual(receipt.result["operation"], "write")

    def test_proposal_freezes_the_grant_snapshot(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)
        snapshot = proposal.expected_state["grants"]["target"]

        self.assertEqual(snapshot["grant_id"], grant.grant_id)
        self.assertEqual(snapshot["capability"], CAPABILITY_FS_WRITE)
        self.assertEqual(
            os.path.normcase(snapshot["root"]),
            os.path.normcase(grant.canonical_root),
        )

    def test_overwrite_backs_up_and_undo_restores_previous_content(self):
        self.write("keep.md", "第一版")
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="keep.md", content="第二版")

        self.assertEqual(
            proposal.expected_state["target"], stamp(self.projects / "keep.md")
        )
        self.assertIn("覆盖", proposal.user_visible_plan)
        receipt = self.files.execute(proposal)
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"), "第二版"
        )
        self.assertTrue(receipt.result["backup_path"])

        message = self.files.undo(receipt)

        self.assertIn("已撤销写入", message)
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"), "第一版"
        )

    def test_undo_removes_a_newly_created_file(self):
        grant = self.grant()
        receipt = self.files.execute(self.write_proposal(grant))
        target = self.projects / "reports" / "new.md"
        self.assertTrue(target.is_file())

        message = self.files.undo(receipt)

        self.assertIn("已删除这次新建的文件", message)
        self.assertFalse(target.exists())

    def test_creates_missing_parent_directories(self):
        grant = self.grant()
        self.files.execute(
            self.write_proposal(grant, relative="a/b/c/deep.md", content="深一层")
        )

        self.assertEqual(
            (self.projects / "a" / "b" / "c" / "deep.md").read_text(
                encoding="utf-8"
            ),
            "深一层",
        )

    def test_refuses_paths_outside_the_granted_directory(self):
        grant = self.grant()
        for relative in ("../outside.md", str(self.root / "absolute.md")):
            with self.assertRaises(FileActionError):
                self.write_proposal(grant, relative=relative)
        self.assertEqual(list(self.projects.rglob("*.md")), [])

    def test_requires_fs_write_on_the_grant(self):
        grant = self.grant(capabilities=(CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH))
        with self.assertRaises(NotExecutedError) as caught:
            self.write_proposal(grant)
        self.assertIn(CAPABILITY_FS_WRITE, str(caught.exception))
        self.assertFalse((self.projects / "reports").exists())

    def test_refuses_the_legacy_workspace_grant(self):
        """旧版工作区的自动提升只代表"笔记还能写"，不是用户为通用写入挑的目录。"""
        self.store.set_workspace(str(self.projects))
        legacy = self.access.find_grant(self.projects)
        self.assertTrue(legacy.legacy_workspace)
        self.assertTrue(legacy.has_capability(CAPABILITY_FS_WRITE))

        with self.assertRaises(NotExecutedError) as caught:
            self.write_proposal(legacy)
        self.assertIn("旧版工作区", str(caught.exception))
        self.assertFalse((self.projects / "reports").exists())

    def test_refuses_the_agent_data_directory(self):
        grant = self.grant(directory=self.data)

        with self.assertRaises(NotExecutedError) as caught:
            self.write_proposal(grant, relative="companion-note.md")
        self.assertIn("数据目录", str(caught.exception))
        self.assertFalse((self.data / "companion-note.md").exists())

    def test_refuses_a_live_reference_into_the_agent_data_directory(self):
        """数据目录就算被索引挑了出来，也不能当通用写入的落点。"""
        inside = self.data / "scratch.md"
        inside.write_text("代理自己的文件", encoding="utf-8")
        grant = self.access.grant_directory(self.data, list(ALL_WRITE_CAPS))
        with patch.object(self.access, "find_grant", return_value=grant), patch.object(
            self.index, "indexable_grants", return_value=[grant]
        ):
            with self.assertRaises(NotExecutedError) as caught:
                self.files.create_move_proposal(
                    session_id="main",
                    file_ref=self.live("scratch.md"),
                    target_grant_id=grant.grant_id,
                    target_rel_path="moved.md",
                    mode=AgentMode.EXECUTE,
                    source_message_id=None,
                    intent="移动文件",
                )
        self.assertIn("数据目录", str(caught.exception))
        self.assertTrue(inside.is_file())

    def test_refuses_empty_and_oversized_content(self):
        grant = self.grant()
        with self.assertRaises(NotExecutedError):
            self.write_proposal(grant, content="")
        with self.assertRaises(NotExecutedError):
            self.write_proposal(
                grant, content="x" * (MAX_FILE_WRITE_BYTES + 1)
            )

    def test_conflict_after_proposal_stops_the_write(self):
        self.write("keep.md", "提案时的内容")
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="keep.md", content="新内容")
        (self.projects / "keep.md").write_text("别人改过了", encoding="utf-8")

        with self.assertRaises(NotExecutedError) as caught:
            self.files.execute(proposal)
        self.assertIn("确认后发生变化", str(caught.exception))
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"), "别人改过了"
        )

    def test_revoked_grant_after_proposal_stops_the_write(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)
        self.access.revoke_grant(grant.grant_id)

        with self.assertRaises(NotExecutedError):
            self.files.execute(proposal)
        self.assertFalse((self.projects / "reports" / "new.md").exists())

    def test_capability_downgrade_after_proposal_stops_the_write(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)
        self.access.grant_directory(self.projects, [CAPABILITY_FS_READ])

        with self.assertRaises(NotExecutedError) as caught:
            self.files.execute(proposal)
        self.assertIn(CAPABILITY_FS_WRITE, str(caught.exception))
        self.assertFalse((self.projects / "reports" / "new.md").exists())

    def test_targeted_directory_identity_change_stops_the_write(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)
        proposal.expected_state["grants"]["target"]["identity"]["file_id"] = (
            "目录被人换成另一个了"
        )

        with self.assertRaises(NotExecutedError) as caught:
            self.files.execute(proposal)
        self.assertIn("目录身份", str(caught.exception))

    def test_tampered_arguments_refuse_execution(self):
        grant = self.grant()
        proposal = self.write_proposal(grant)
        proposal.normalized_arguments["content"] = "确认之后被人改掉的内容"

        with self.assertRaises(NotExecutedError) as caught:
            self.files.execute(proposal)
        self.assertIn("不一致", str(caught.exception))
        self.assertFalse((self.projects / "reports" / "new.md").exists())

    def test_expired_proposal_is_not_executed(self):
        grant = self.grant()
        proposal = self.write_proposal(grant, ttl_hours=-1)

        with self.assertRaises(NotExecutedError):
            self.files.execute(proposal)
        self.assertFalse((self.projects / "reports").exists())

    def test_preview_never_writes_and_shows_the_diff(self):
        self.write("keep.md", "旧的一行\n要删的一行")
        grant = self.grant()
        proposal = self.write_proposal(
            grant, relative="keep.md", content="旧的一行\n新加的一行"
        )

        value = self.files.preview(proposal)

        self.assertEqual(value["change"], "update")
        self.assertEqual(value["added"], 1)
        self.assertEqual(value["removed"], 1)
        self.assertEqual(value["error"], "")
        self.assertIn(
            ("del", "要删的一行"),
            [tuple(row) for row in value["diff"]],
        )
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"),
            "旧的一行\n要删的一行",
        )

    def test_refuses_when_the_parent_is_a_file(self):
        self.write("blocker.md", "占位")
        grant = self.grant()

        with self.assertRaises(FileActionError):
            self.write_proposal(grant, relative="blocker.md/child.md")


class TransferTest(S3Fixture):
    def test_copy_keeps_the_source_and_undo_removes_the_copy(self):
        source = self.write("src.md", "复制我")
        grant = self.grant()
        proposal = self.files.create_copy_proposal(
            session_id="main",
            file_ref=self.live("src.md"),
            target_grant_id=grant.grant_id,
            target_rel_path="copies/src.md",
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            intent="复制文件",
        )
        receipt = self.files.execute(proposal)

        self.assertEqual(
            (self.projects / "copies" / "src.md").read_text(encoding="utf-8"),
            "复制我",
        )
        self.assertTrue(source.is_file())
        self.assertEqual(receipt.result["operation"], "copy")

        message = self.files.undo(receipt)

        self.assertIn("已撤销复制", message)
        self.assertFalse((self.projects / "copies" / "src.md").exists())
        self.assertTrue(source.is_file())

    def test_copy_needs_only_fs_read_on_the_source(self):
        self.write("src.md", "只读源")
        read_only = self.grant(capabilities=(CAPABILITY_FS_READ,))
        writable = self.grant(self.archive)

        proposal = self.files.create_copy_proposal(
            session_id="main",
            file_ref=self.live("src.md"),
            target_grant_id=writable.grant_id,
            target_rel_path="src-copy.md",
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            intent="复制文件",
        )
        receipt = self.files.execute(proposal)

        self.assertTrue((self.archive / "src-copy.md").is_file())
        self.assertEqual(receipt.status, "completed")

    def test_move_renames_and_undo_moves_back(self):
        self.write("old-name.md", "改名我")
        grant = self.grant()
        proposal = self.files.create_move_proposal(
            session_id="main",
            file_ref=self.live("old-name.md"),
            target_grant_id=grant.grant_id,
            target_rel_path="new-name.md",
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            intent="重命名文件",
        )
        self.assertEqual(proposal.executor, EXECUTOR_FILE)
        receipt = self.files.execute(proposal)

        self.assertFalse((self.projects / "old-name.md").exists())
        self.assertEqual(
            (self.projects / "new-name.md").read_text(encoding="utf-8"), "改名我"
        )

        message = self.files.undo(receipt)

        self.assertIn("已撤销移动", message)
        self.assertTrue((self.projects / "old-name.md").is_file())
        self.assertFalse((self.projects / "new-name.md").exists())

    def test_move_between_two_grants_on_the_same_volume(self):
        self.write("go.md", "跨目录")
        source_grant = self.grant(capabilities=ALL_WRITE_CAPS)
        target_grant = self.grant(self.archive)

        proposal = self.files.create_move_proposal(
            session_id="main",
            file_ref=self.live("go.md"),
            target_grant_id=target_grant.grant_id,
            target_rel_path="moved/go.md",
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            intent="移动文件",
        )
        self.assertIn("两次授权都要成立", proposal.user_visible_plan)
        receipt = self.files.execute(proposal)

        self.assertFalse((self.projects / "go.md").exists())
        self.assertEqual(
            (self.archive / "moved" / "go.md").read_text(encoding="utf-8"), "跨目录"
        )
        self.assertEqual(
            os.path.normcase(receipt.result["source_root"]),
            os.path.normcase(str(source_grant.canonical_root)),
        )

    def test_move_refuses_an_existing_target(self):
        self.write("go.md", "新")
        self.write("here.md", "已经占了位置")
        grant = self.grant()

        with self.assertRaises(NotExecutedError) as caught:
            self.files.create_move_proposal(
                session_id="main",
                file_ref=self.live("go.md"),
                target_grant_id=grant.grant_id,
                target_rel_path="here.md",
                mode=AgentMode.EXECUTE,
                source_message_id=None,
                intent="移动文件",
            )
        self.assertIn("已有同名文件", str(caught.exception))
        self.assertTrue((self.projects / "go.md").is_file())

    def test_move_needs_fs_write_on_the_source_grant(self):
        """移动会把文件从源目录搬走，所以源目录的授权里必须有写。"""
        self.write("go.md", "要搬走")
        read_only = self.grant(capabilities=(CAPABILITY_FS_READ,))
        writable = self.grant(self.archive)

        with self.assertRaises(NotExecutedError) as caught:
            self.files.create_move_proposal(
                session_id="main",
                file_ref=self.live("go.md"),
                target_grant_id=writable.grant_id,
                target_rel_path="go.md",
                mode=AgentMode.EXECUTE,
                source_message_id=None,
                intent="移动文件",
            )
        self.assertIn(CAPABILITY_FS_WRITE, str(caught.exception))
        self.assertTrue((self.projects / "go.md").is_file())

    def test_move_refuses_a_cross_volume_target_at_proposal(self):
        self.write("go.md", "跨卷")
        self.grant(
            capabilities=(
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_WRITE,
            )
        )
        grant = self.grant(self.archive)
        archive_key = os.path.normcase(str(self.archive))
        real_volume_of = volume_of

        def fake_volume_of(path):
            value = real_volume_of(path)
            return value + 777 if os.path.normcase(str(path)).startswith(
                archive_key
            ) else value

        with patch(
            "agent_framework.companion.file_actions.volume_of", fake_volume_of
        ):
            with self.assertRaises(NotExecutedError) as caught:
                self.files.create_move_proposal(
                    session_id="main",
                    file_ref=self.live("go.md"),
                    target_grant_id=grant.grant_id,
                    target_rel_path="go.md",
                    mode=AgentMode.EXECUTE,
                    source_message_id=None,
                    intent="移动文件",
                )
        self.assertIn("同一个磁盘卷", str(caught.exception))
        self.assertTrue((self.projects / "go.md").is_file())

    def test_move_rechecks_the_volume_before_replacing(self):
        """确认后、动手前卷号变了——提案成立也不许把文件搬走。"""
        self.write("go.md", "卷内改名")
        grant = self.grant()
        proposal = self.files.create_move_proposal(
            session_id="main",
            file_ref=self.live("go.md"),
            target_grant_id=grant.grant_id,
            target_rel_path="renamed.md",
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            intent="移动文件",
        )
        real_volume_of = volume_of

        def fake_volume_of(path):
            return real_volume_of(path) + 1

        with patch(
            "agent_framework.companion.file_actions.volume_of", fake_volume_of
        ):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)
        self.assertTrue((self.projects / "go.md").is_file())
        self.assertFalse((self.projects / "renamed.md").exists())

    def test_transfer_from_a_legacy_grant_is_refused(self):
        self.store.set_workspace(str(self.projects))
        legacy = self.access.find_grant(self.projects)
        self.write("go.md", "旧工作区")

        with self.assertRaises(NotExecutedError):
            self.files.create_move_proposal(
                session_id="main",
                file_ref=self.live("go.md"),
                target_grant_id=legacy.grant_id,
                target_rel_path="moved.md",
                mode=AgentMode.EXECUTE,
                source_message_id=None,
                intent="移动文件",
            )


class DeleteTest(S3Fixture):
    def proposal_for(self, grant, relative="gone.md", **kwargs):
        self.write(relative, "要进回收站的内容")
        return self.files.create_delete_proposal(
            session_id="main",
            file_ref=self.live(relative),
            mode=AgentMode.EXECUTE,
            source_message_id=None,
            **kwargs,
        )

    def test_delete_moves_to_the_recycle_bin_and_undo_restores(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)

        self.assertEqual(proposal.risk, RiskLevel.FS_DELETE)
        self.assertIn("系统回收站", proposal.user_visible_plan)
        self.assertNotIn("永久删除这个文件", proposal.user_visible_plan)
        bin = FakeRecycleBin(self.bin)
        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            receipt = self.files.execute(proposal)

        self.assertEqual(receipt.status, "completed")
        self.assertTrue(receipt.undoable)
        self.assertTrue(receipt.result["recycled"])
        self.assertFalse((self.projects / "gone.md").exists())
        self.assertTrue((self.bin / "gone.md").is_file())

        message = self.files.undo(receipt)

        self.assertIn("已从备份还原", message)
        self.assertEqual(
            (self.projects / "gone.md").read_text(encoding="utf-8"),
            "要进回收站的内容",
        )

    def test_delete_requires_an_explicit_fs_delete_grant(self):
        self.write("gone.md")
        grant = self.grant(capabilities=(CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH))

        with self.assertRaises(NotExecutedError) as caught:
            self.files.create_delete_proposal(
                session_id="main",
                file_ref=self.live("gone.md"),
                mode=AgentMode.EXECUTE,
                source_message_id=None,
            )
        self.assertIn(CAPABILITY_FS_DELETE, str(caught.exception))
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_the_selected_workspace_never_authorises_a_delete(self):
        """选工作区只提升出笔记读写，删除必须由用户单独授予 fs-delete。"""
        self.store.set_workspace(str(self.projects))
        legacy = self.access.find_grant(self.projects)
        self.write("gone.md")

        with self.assertRaises(NotExecutedError) as caught:
            self.files.create_delete_proposal(
                session_id="main",
                file_ref=self.live("gone.md"),
                mode=AgentMode.EXECUTE,
                source_message_id=None,
            )
        self.assertIn(CAPABILITY_FS_DELETE, str(caught.exception))
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_backup_cap_reports_honestly_and_disables_undo(self):
        grant = self.grant()
        with patch(
            "agent_framework.companion.file_actions.MAX_UNDO_BACKUP_BYTES", 4
        ):
            proposal = self.proposal_for(grant)
            self.assertFalse(proposal.reversible)
            self.assertIn("没有可自动还原的副本", proposal.expected_state["undo_reason"])
            bin = FakeRecycleBin(self.bin)
            with patch(
                "agent_framework.companion.file_actions.send_to_recycle_bin", bin
            ):
                receipt = self.files.execute(proposal)

        self.assertFalse(receipt.undoable)
        self.assertEqual(receipt.result["backup_path"], "")
        self.assertFalse((self.data / "backups" / proposal.action_id).exists())
        with self.assertRaises(NotExecutedError) as caught:
            self.files.undo(receipt)
        self.assertIn("没有可自动还原的副本", str(caught.exception))

    def test_refuses_when_the_file_changed_after_the_proposal(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)
        (self.projects / "gone.md").write_text("换了内容", encoding="utf-8")
        bin = FakeRecycleBin(self.bin)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)
        self.assertEqual(bin.calls, [])
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_refuses_when_the_grant_was_revoked_after_the_proposal(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)
        self.access.revoke_grant(grant.grant_id)
        bin = FakeRecycleBin(self.bin)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)
        self.assertEqual(bin.calls, [])
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_recycle_error_with_the_file_still_present_is_not_executed(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)

        def explode(paths):
            raise RecycleBinError("系统拒绝了这次删除。", "recycle_failed", 5)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", explode
        ):
            with self.assertRaises(NotExecutedError) as caught:
                self.files.execute(proposal)
        self.assertIn("删除没有执行", str(caught.exception))
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_recycle_error_after_the_file_left_is_uncertain(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)
        bin = FakeRecycleBin(self.bin, raise_after=True)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            with self.assertRaises(UncertainExecutionError):
                self.files.execute(proposal)
        self.assertFalse((self.projects / "gone.md").exists())

    def test_recycle_success_but_the_file_remains_is_uncertain(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)
        bin = FakeRecycleBin(self.bin, keep=True)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            with self.assertRaises(UncertainExecutionError):
                self.files.execute(proposal)
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_backup_lands_before_the_execution_ledger_advances(self):
        """备份先写、账本后推进——否则一次备份失败就能留下半路的 dispatching。"""
        grant = self.grant()
        proposal = self.proposal_for(grant)
        states = []
        real_mark = self.store.mark_execution_state

        def spy(action_id, status):
            states.append(status)
            return real_mark(action_id, status)

        def check_then_fail(paths):
            self.assertTrue(
                (self.data / "backups" / proposal.action_id / "before.bin").is_file()
            )
            raise RecycleBinError("系统拒绝了这次删除。", "recycle_failed", 5)

        with patch.object(self.store, "mark_execution_state", spy), patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin",
            check_then_fail,
        ):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)

        self.assertEqual(states, ["dispatching"])
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_preview_of_a_pending_delete_does_not_touch_the_file(self):
        grant = self.grant()
        proposal = self.proposal_for(grant)

        value = self.files.preview(proposal)

        self.assertEqual(value["change"], "delete")
        self.assertEqual(value["error"], "")
        self.assertTrue((self.projects / "gone.md").is_file())


class RecycleBinContractTest(unittest.TestCase):
    def test_arguments_are_a_double_null_wide_list(self):
        prepared = delete_arguments(["C:\\data\\a.txt", "C:\\data\\b.txt"])

        self.assertEqual(
            prepared["block"], "C:\\data\\a.txt\0C:\\data\\b.txt\0\0"
        )
        self.assertTrue(prepared["flags"] & FOF_ALLOWUNDO)

    def test_rejects_paths_the_shell_cannot_explain(self):
        cases = {
            "relative.md": "recycle_bad_path",
            "C:\\data\\dir\\": "recycle_bad_path",
            "\\\\server\\share\\a.txt": "recycle_bad_path",
            "C:\\" + "d" * MAX_SHELL_PATH_CHARS + "\\a.txt": "recycle_path_too_long",
        }
        for path, code in cases.items():
            with self.assertRaises(RecycleBinError) as caught:
                delete_arguments([path])
            self.assertEqual(caught.exception.code, code)

    def test_empty_list_is_refused(self):
        with self.assertRaises(RecycleBinError):
            delete_arguments([])

    def test_non_windows_system_refuses_instead_of_deleting(self):
        target = Path(tempfile.mkdtemp()) / "keep.txt"
        target.write_text("不能删", encoding="utf-8")
        try:
            with patch("agent_framework.companion.recycle.is_available", lambda: False):
                with self.assertRaises(RecycleBinError) as caught:
                    send_to_recycle_bin([str(target)])
            self.assertEqual(caught.exception.code, "recycle_unsupported")
            self.assertTrue(target.is_file())
        finally:
            shutil.rmtree(str(target.parent), ignore_errors=True)


class RuntimeDispatchTest(S3Fixture):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=True,
        )
        self.runtime.select_workspace(str(self.workspace))
        self.runtime.apply_settings({"tool_permission": "balanced"})

    def tearDown(self):
        self.runtime.file_index.shutdown()
        super().tearDown()

    def confirm(self, proposal):
        self.store.create_proposal(proposal)
        return self.runtime.confirm_proposal(proposal.action_id, AgentMode.EXECUTE)

    def test_file_proposal_runs_through_the_file_executor(self):
        grant = self.grant()
        proposal = self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path="note.md",
            content="执行器分派",
            mode=AgentMode.EXECUTE,
        )

        result = self.confirm(proposal)

        self.assertEqual(result["receipt"].status, "completed")
        self.assertEqual(
            (self.projects / "note.md").read_text(encoding="utf-8"), "执行器分派"
        )
        self.assertIn("回执", result["answer"])

    def test_file_executor_ignores_the_global_workspace(self):
        """通用文件动作带着自己的授权目录，切工作区不该让它失效。"""
        grant = self.grant()
        proposal = self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path="note.md",
            content="与工作区无关",
            mode=AgentMode.EXECUTE,
        )
        self.runtime.select_workspace(str(self.archive))

        result = self.confirm(proposal)

        self.assertEqual(result["receipt"].status, "completed")
        self.assertTrue((self.projects / "note.md").is_file())

    def test_note_proposal_still_checks_the_workspace(self):
        proposal = self.runtime.notes.create_proposal(
            "main", "还是要在笔记里", "内容", mode=AgentMode.WORK
        )
        self.store.create_proposal(proposal)
        self.runtime.select_workspace(str(self.archive))

        result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.WORK)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "workspace_changed")

    def test_unknown_executor_executes_nothing(self):
        grant = self.grant()
        proposal = replace(
            self.files.create_write_proposal(
                session_id="main",
                grant_id=grant.grant_id,
                relative_path="note.md",
                content="不该落盘",
                mode=AgentMode.EXECUTE,
            ),
            executor="ghost",
        )

        result = self.confirm(proposal)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "executor_unknown")
        self.assertFalse((self.projects / "note.md").exists())

    def test_not_executed_receipt_says_undo_is_unavailable(self):
        grant = self.grant()
        proposal = self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path="reports/note.md",
            content="确认后被人抢先写了",
            mode=AgentMode.EXECUTE,
        )
        self.store.create_proposal(proposal)
        (self.projects / "reports" / "note.md").parent.mkdir(
            parents=True, exist_ok=True
        )
        (self.projects / "reports" / "note.md").write_text(
            "别人先写好了", encoding="utf-8"
        )

        result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.EXECUTE)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(
            result["receipt"].result["code"], "not_executed_before_side_effect"
        )
        self.assertFalse(result["receipt"].undoable)
        self.assertEqual(result["receipt"].undo_status, "unavailable")
        self.assertEqual(
            (self.projects / "reports" / "note.md").read_text(encoding="utf-8"),
            "别人先写好了",
        )
        self.assertEqual(
            self.store.get_execution(proposal.action_id)["status"], "not_executed"
        )

    def test_undo_and_preview_route_through_the_executor_table(self):
        grant = self.grant()
        proposal = self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path="note.md",
            content="撤销要走文件执行器",
            mode=AgentMode.EXECUTE,
        )
        result = self.confirm(proposal)

        preview = self.runtime.preview_proposal(proposal.action_id)
        self.assertEqual(preview["preview"]["operation"], "write")

        message = self.runtime.undo_receipt(result["receipt"].receipt_id)

        self.assertIn("已删除这次新建的文件", message)
        self.assertFalse((self.projects / "note.md").exists())

    def test_write_and_move_tools_need_an_explicit_fs_write_grant(self):
        without = self.runtime.tool_names(AgentMode.WORK)
        self.assertNotIn("write_file", without)
        self.assertNotIn("move_file", without)

        self.grant()
        with_tools = self.runtime.tool_names(AgentMode.WORK)
        self.assertIn("write_file", with_tools)
        self.assertIn("move_file", with_tools)

        for mode in (AgentMode.CHAT, AgentMode.QUIET):
            names = self.runtime.tool_names(mode)
            self.assertNotIn("write_file", names)
            self.assertNotIn("move_file", names)

    def test_write_file_tool_only_creates_a_proposal(self):
        grant = self.grant()
        call = self.runtime._tool_write_file(
            {
                "grant_id": grant.grant_id,
                "relative_path": "from-tool.md",
                "content": "工具生成的提案",
            },
            AgentMode.WORK,
            None,
        )

        self.assertTrue(call.ok)
        self.assertEqual(call.proposal.executor, EXECUTOR_FILE)
        self.assertEqual(call.proposal.risk, RiskLevel.FS_WRITE)
        self.assertFalse((self.projects / "from-tool.md").exists())

    def test_single_writable_directory_fills_a_missing_grant_id(self):
        grant = self.grant()
        call = self.runtime._tool_write_file(
            {
                "relative_path": "one-grant.md",
                "content": "only one choice",
            },
            AgentMode.WORK,
            None,
        )
        self.assertTrue(call.ok, call.content)
        self.assertEqual(
            call.proposal.normalized_arguments["grant_id"],
            grant.grant_id,
        )

    def test_existing_filename_selects_the_unique_grant_by_basename(self):
        first = self.grant()
        second = self.grant(
            self.archive,
            capabilities=(CAPABILITY_FS_READ, CAPABILITY_FS_WRITE),
        )
        self.write("wanted.txt", "old")
        call = self.runtime._tool_write_file(
            {"relative_path": "wanted.txt", "content": "new"},
            AgentMode.WORK,
            None,
        )
        self.assertTrue(call.ok, call.content)
        self.assertEqual(
            call.proposal.normalized_arguments["grant_id"], first.grant_id
        )

    def test_move_file_tool_reports_a_refusal_as_a_tool_failure(self):
        call = self.runtime._tool_move_file(
            {
                "file_ref": self.live("missing.md"),
                "target_grant_id": "grant_missing",
                "target_relative_path": "x.md",
            },
            AgentMode.WORK,
            None,
        )

        self.assertFalse(call.ok)
        self.assertEqual(call.code, "file_action_error")

    def test_delete_tool_registration_requires_fs_delete(self):
        """Only an explicit fs-delete grant exposes the recycle-bin tool."""
        self.store.clear_workspace()
        for grant in self.store.list_access_grants(
            status="active", resource_kind="directory"
        ):
            self.runtime.access.revoke_grant(grant.grant_id)
        self.grant(
            capabilities=(
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_WRITE,
            )
        )
        self.assertNotIn("delete_file", self.runtime.tool_names(AgentMode.WORK))
        self.grant(
            capabilities=(
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_DELETE,
            )
        )
        self.assertIn("delete_file", self.runtime.tool_names(AgentMode.WORK))

    def test_chat_mode_denies_a_file_delete_proposal(self):
        self.write("gone.md")
        grant = self.grant()
        proposal = self.files.create_delete_proposal(
            session_id="main",
            file_ref=self.live("gone.md"),
            mode=AgentMode.WORK,
            source_message_id=None,
        )
        self.store.create_proposal(proposal)
        bin = FakeRecycleBin(self.bin)

        with patch(
            "agent_framework.companion.file_actions.send_to_recycle_bin", bin
        ):
            result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.CHAT)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "mode_denied")
        self.assertEqual(bin.calls, [])
        self.assertTrue((self.projects / "gone.md").is_file())

    def test_unknown_execution_status_is_recorded_as_unknown(self):
        grant = self.grant()
        proposal = self.files.create_write_proposal(
            session_id="main",
            grant_id=grant.grant_id,
            relative_path="note.md",
            content="结果不确定",
            mode=AgentMode.EXECUTE,
        )
        self.store.create_proposal(proposal)

        with patch.object(
            self.runtime.files,
            "execute",
            side_effect=UncertainExecutionError("模拟替换后崩溃"),
        ):
            result = self.runtime.confirm_proposal(
                proposal.action_id, AgentMode.EXECUTE
            )

        self.assertEqual(result["receipt"].status, "unknown")
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.UNKNOWN,
        )


@unittest.skipUnless(
    os.name == "nt" and os.environ.get("COMPANION_TEST_RECYCLE_BIN") == "1",
    "只在显式开启时真的往系统回收站里放一个文件（COMPANION_TEST_RECYCLE_BIN=1）",
)
class RealRecycleBinTest(S3Fixture):
    def test_a_real_file_leaves_the_authorised_directory(self):
        grant = self.grant()
        target = self.write("probe.txt", "一次性探针文件")
        proposal = self.files.create_delete_proposal(
            session_id="main",
            file_ref=self.live("probe.txt"),
            mode=AgentMode.EXECUTE,
            source_message_id=None,
        )

        receipt = self.files.execute(proposal)

        self.assertEqual(receipt.status, "completed")
        self.assertFalse(target.exists())
        self.assertEqual(receipt.result["shell_code"], 0)
        self.assertTrue(receipt.undoable)
        self.files.undo(receipt)
        self.assertTrue(target.is_file())


class TempCreateDenialTest(S3Fixture):
    """拒绝创建文件的目录必须让回合立即失败，而不是陷进 mkstemp 的无界重试。"""

    def guard_open(self, directory, error_factory, succeed_after=None):
        real_open = os.open
        calls = []
        prefix = os.path.normcase(str(directory)) + os.sep

        def guarded(path, flags, *args, **kwargs):
            if flags & os.O_EXCL and os.path.normcase(str(path)).startswith(prefix):
                calls.append(str(path))
                if succeed_after is None or len(calls) <= succeed_after:
                    raise error_factory()
            return real_open(path, flags, *args, **kwargs)

        return guarded, calls

    def test_denied_create_fails_fast_without_side_effect(self):
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="new.md")
        guarded, calls = self.guard_open(
            self.projects, lambda: PermissionError(13, "Permission denied")
        )

        started = time.monotonic()
        with patch("agent_framework.companion.file_actions.os.open", guarded):
            with self.assertRaises(NotExecutedError) as caught:
                self.files.execute(proposal)

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(calls), 1)
        self.assertIn("拒绝创建", str(caught.exception))
        self.assertFalse((self.projects / "new.md").exists())

    def test_denied_create_after_backup_keeps_target_bytes(self):
        self.write("keep.md", "第一版")
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="keep.md", content="第二版")
        guarded, calls = self.guard_open(
            self.projects, lambda: PermissionError(13, "Permission denied")
        )

        with patch("agent_framework.companion.file_actions.os.open", guarded):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"), "第一版"
        )

    def test_name_collisions_retry_only_up_to_the_bound(self):
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="new.md")
        guarded, calls = self.guard_open(
            self.projects, lambda: FileExistsError(17, "File exists")
        )

        with patch("agent_framework.companion.file_actions.os.open", guarded):
            with self.assertRaises(NotExecutedError):
                self.files.execute(proposal)

        self.assertEqual(len(calls), TEMP_CREATE_ATTEMPTS)
        self.assertFalse((self.projects / "new.md").exists())

    def test_a_free_name_after_collisions_still_writes(self):
        grant = self.grant()
        proposal = self.write_proposal(grant, relative="new.md")
        guarded, calls = self.guard_open(
            self.projects,
            lambda: FileExistsError(17, "File exists"),
            succeed_after=2,
        )

        with patch("agent_framework.companion.file_actions.os.open", guarded):
            receipt = self.files.execute(proposal)

        self.assertEqual(len(calls), 3)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(
            (self.projects / "new.md").read_text(encoding="utf-8"), "正文内容"
        )

    def test_undo_fails_fast_when_the_directory_denies_create(self):
        self.write("keep.md", "第一版")
        grant = self.grant()
        receipt = self.files.execute(
            self.write_proposal(grant, relative="keep.md", content="第二版")
        )
        guarded, calls = self.guard_open(
            self.projects, lambda: PermissionError(13, "Permission denied")
        )

        with patch("agent_framework.companion.file_actions.os.open", guarded):
            with self.assertRaises(NotExecutedError):
                self.files.undo(receipt)

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            (self.projects / "keep.md").read_text(encoding="utf-8"), "第二版"
        )

    def test_module_no_longer_calls_mkstemp(self):
        source = inspect.getsource(file_actions_module)
        self.assertNotIn("tempfile.mkstemp(", source)


if __name__ == "__main__":
    unittest.main()
