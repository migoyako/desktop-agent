"""Application use cases and run lifecycle shared by every UI adapter."""

from __future__ import annotations

import threading
from typing import Dict, Iterator, Optional

from ..models import AgentMode, ProposalStatus, new_id, utc_now
from .contracts import (
    ApplicationEvent,
    ApprovalUnavailableError,
    ApprovalView,
    EventKind,
    InvalidTurnError,
    MessageActions,
    MessageUnavailableError,
    RegenerateMessageCommand,
    RunAlreadyActiveError,
    RunContext,
    RunNotFoundError,
    RunStage,
    RunStatus,
    RunStep,
    RunView,
    SessionBusyError,
    StartTurnCommand,
    TranscriptMessage,
    TurnPersistenceError,
    UiProjection,
    WorkspaceSnapshot,
    new_run_id,
)
from .ports import CompanionRuntimePort
from .projection import (
    build_context_view,
    build_inspector_sections,
    build_sources,
    clip,
    stage_for_event,
)


class CompanionApplicationService:
    """Owns adapter-visible use cases while the runtime remains the domain engine."""

    def __init__(self, runtime: CompanionRuntimePort, run_id_factory=None):
        self.runtime = runtime
        self._run_id_factory = run_id_factory or new_run_id
        self._lock = threading.RLock()
        self._runs: Dict[str, RunContext] = {}
        self._session_runs: Dict[str, str] = {}

    @property
    def current_session_id(self) -> str:
        return self.runtime.session_id

    def current_mode(self) -> AgentMode:
        return self.runtime.current_mode()

    def workspace(self):
        return self.runtime.workspace()

    def history(self, limit=60):
        return self.runtime.history(limit=limit)

    def session_summaries(self):
        return self.runtime.session_summaries()

    def pending_proposals(self):
        return self.runtime.pending_proposals()

    def latest_receipts(self, limit=20):
        return self.runtime.latest_receipts(limit=limit)

    def ui_projection(self, message_limit=60) -> UiProjection:
        """Everything the chat adapter may render, without runtime objects."""
        session_id = self.runtime.session_id
        rows = self.runtime.history(limit=message_limit)
        messages = tuple(
            TranscriptMessage(
                message_id=int(row.get("id") or 0),
                role=str(row.get("role") or ""),
                text=str(row.get("content") or ""),
                created_at=str(row.get("created_at") or ""),
                mode=str(row.get("mode") or ""),
            )
            for row in rows
            if row.get("role") in ("user", "assistant")
        )
        try:
            pending = self.runtime.pending_proposals()
        except Exception:
            pending = None
        approvals = tuple(
            self._approval_view_for(item) for item in (pending or ())
        )
        run_view = self.run_view(session_id)
        return UiProjection(
            session_id=session_id,
            messages=messages,
            run=run_view,
            approvals=approvals,
            inspector=build_inspector_sections(
                self.runtime,
                pending if pending is not None else None,
            ),
            context=build_context_view(self.runtime),
            sources=build_sources(self.runtime),
            message_actions=self.message_actions(),
        )

    def run_view(self, session_id=None) -> Optional[RunView]:
        """The active run alone: adapters poll this per event instead of rebuilding the projection."""
        run = self.active_run(self.runtime.session_id if session_id is None else session_id)
        if run is None:
            return None
        return RunView(
            run_id=run.run_id,
            session_id=run.session_id,
            mode=run.mode.value,
            status=run.status.value,
            stage=run.stage,
            sequence=run.sequence,
            started_at=run.started_at,
            detail=run.detail,
            tool=run.tool,
            steps=tuple(run.steps),
        )

    def begin_turn(self, command, mode=None) -> RunContext:
        if not isinstance(command, StartTurnCommand):
            command = StartTurnCommand(str(command or ""), mode)
        text = command.text.strip()
        if not text:
            raise InvalidTurnError("消息不能为空。")
        selected_mode = AgentMode(command.mode or mode or self.runtime.current_mode())
        with self._lock:
            session_id = self.runtime.session_id
            active_id = self._session_runs.get(session_id)
            if active_id:
                active = self._runs.get(active_id)
                if active is not None and not active.finished:
                    raise RunAlreadyActiveError(
                        "当前会话已有回复正在生成，请先停止或等待完成。"
                    )
                self._session_runs.pop(session_id, None)
            self.runtime.set_mode(selected_mode)
            workspace = None
            try:
                workspace = WorkspaceSnapshot.capture(self.runtime.workspace())
            except OSError:
                workspace = None
            run = RunContext(
                run_id=self._run_id_factory(),
                session_id=session_id,
                mode=selected_mode,
                text=text,
                previous_message_id=self._latest_message_id(session_id),
                workspace=workspace,
            )
            self._runs[run.run_id] = run
            self._session_runs[session_id] = run.run_id
            return run

    def regenerate(self, command) -> RunContext:
        """Replace the latest assistant reply without resending the user row."""
        if not isinstance(command, RegenerateMessageCommand):
            try:
                message_id = int(command or 0)
            except (TypeError, ValueError):
                message_id = 0
            command = RegenerateMessageCommand(message_id)
        with self._lock:
            self._assert_idle("重新生成")
            assistant, user, reason = self._regeneration_rows(command.message_id)
            if assistant is None:
                raise MessageUnavailableError(reason)
            session_id = self.runtime.session_id
            selected_mode = AgentMode(
                user.get("mode")
                or assistant.get("mode")
                or self.runtime.current_mode()
            )
            workspace = None
            try:
                workspace = WorkspaceSnapshot.capture(self.runtime.workspace())
            except OSError:
                workspace = None
            run = RunContext(
                run_id=self._run_id_factory(),
                session_id=session_id,
                mode=selected_mode,
                text=str(user.get("content") or ""),
                previous_message_id=int(assistant["id"]),
                workspace=workspace,
                regenerate_of=int(assistant["id"]),
                source_message_id=int(user["id"]),
            )
            self._runs[run.run_id] = run
            self._session_runs[session_id] = run.run_id
            return run

    def remember_message(self, message_id) -> Dict:
        with self._lock:
            self._assert_idle("保存记忆")
            return self.runtime.remember_message(message_id)

    def message_actions(self) -> MessageActions:
        """Adapters render actions only for the rows this contract names."""
        try:
            rows = self.runtime.store.list_messages(
                self.runtime.session_id,
                limit=4,
            )
        except Exception:
            rows = []
        rows = [
            row
            for row in rows or []
            if row.get("role") in ("user", "assistant")
        ]
        if not rows:
            return MessageActions()
        regenerate_id = 0
        last = rows[-1]
        if last.get("role") == "assistant":
            assistant, _, _ = self._regeneration_rows(int(last.get("id") or 0))
            if assistant is not None:
                regenerate_id = int(assistant["id"])
        source_id = 0
        # 跳转来源 offered whenever this turn has any provenance at all: a web
        # hit or a chunk from the local library.
        sources = build_sources(self.runtime)
        if sources:
            for row in reversed(rows):
                if row.get("role") == "assistant":
                    source_id = int(row.get("id") or 0)
                    break
        return MessageActions(
            regenerate_id=regenerate_id,
            source_id=source_id,
        )

    def stream_turn(self, run: RunContext) -> Iterator[ApplicationEvent]:
        with self._lock:
            current = self._runs.get(run.run_id)
            if current is not run:
                raise RunNotFoundError("找不到这次回复运行。")
            run.status = RunStatus.RUNNING
        sequence = 0
        terminal = False
        saw_error = False
        replaced = None
        deleted = False
        try:
            stage = stage_for_event(EventKind.STARTED.value)
            self._advance_run(run, sequence, stage, "", "")
            yield ApplicationEvent(
                kind=EventKind.STARTED.value,
                run_id=run.run_id,
                session_id=run.session_id,
                sequence=sequence,
                payload={
                    "mode": run.mode.value,
                    "workspace": (
                        run.workspace.as_dict() if run.workspace else None
                    ),
                    "started_at": run.started_at,
                },
                stage=stage,
            )
            if run.regenerate_of:
                replaced = self.runtime.store.get_message(run.regenerate_of)
                if (
                    replaced is None
                    or replaced.get("session_id") != run.session_id
                    or not self.runtime.store.delete_message(
                        run.regenerate_of,
                        run.session_id,
                    )
                ):
                    raise MessageUnavailableError("找不到要替换的回复。")
                deleted = True
                stream = self.runtime.stream_regenerate(
                    run.source_message_id,
                    run.mode,
                    generation_id=run.run_id,
                    cancel_event=run.cancel_event,
                )
            else:
                stream = self.runtime.stream_message(
                    run.text,
                    run.mode,
                    generation_id=run.run_id,
                    cancel_event=run.cancel_event,
                )
            for raw in stream:
                sequence += 1
                if not isinstance(raw, dict):
                    raise TurnPersistenceError("运行时返回了无法识别的事件。")
                kind = str(raw.get("kind") or "").strip()
                if not kind:
                    raise TurnPersistenceError("运行时事件缺少类型。")
                payload = {
                    key: value
                    for key, value in raw.items()
                    if key not in ("kind", "generation_id")
                }
                if kind == EventKind.ERROR.value:
                    saw_error = True
                if kind == EventKind.DONE.value:
                    result = dict(payload.get("result") or {})
                    message_id = self._persisted_message_id(run, result)
                    if message_id is None:
                        raise TurnPersistenceError(
                            "回复尚未写入会话，应用层拒绝发送完成事件。"
                        )
                    result["message_id"] = message_id
                    result["persisted"] = True
                    payload["result"] = result
                    status = (
                        RunStatus.CANCELLED
                        if result.get("cancelled")
                        else RunStatus.COMPLETED
                    )
                    run.complete(status, result)
                    terminal = True
                stage = stage_for_event(kind, payload, saw_error)
                detail, tool = self._stage_detail(kind, payload)
                self._advance_run(run, sequence, stage, detail, tool)
                yield ApplicationEvent(
                    kind=kind,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    sequence=sequence,
                    payload=payload,
                    stage=stage,
                )
            if not terminal:
                raise TurnPersistenceError("回复流在完成事件前结束。")
        except Exception as error:
            if deleted and not self._has_assistant_after(run):
                self._restore_message(run, replaced)
            if not run.finished:
                run.complete(RunStatus.FAILED, error=str(error))
                run.stage = RunStage.FAILED.value
                run.detail = str(error)
                self._record_step(run)
            raise
        finally:
            if not run.finished:
                status = (
                    RunStatus.CANCELLED
                    if run.cancel_event.is_set()
                    else RunStatus.FAILED
                )
                run.complete(status, error="run closed before a terminal event")
                run.stage = (
                    RunStage.CANCELLED.value
                    if status == RunStatus.CANCELLED
                    else RunStage.FAILED.value
                )
                run.detail = run.error
                self._record_step(run)
            self._release_run(run)

    def run_sync(self, text, mode=None):
        run = self.begin_turn(text, mode)
        result = None
        for event in self.stream_turn(run):
            if event.kind == EventKind.DONE.value:
                result = event.payload.get("result")
        if result is None:
            raise TurnPersistenceError("回复没有产生完成结果。")
        return result

    def request_cancel(self, run_id=None) -> bool:
        with self._lock:
            run = self._find_run(run_id)
            if run is None or run.finished:
                return False
            return run.request_cancel()

    def stop_run(self, run_id=None, timeout=None) -> RunContext:
        with self._lock:
            run = self._find_run(run_id)
            if run is None:
                raise RunNotFoundError("找不到正在生成的回复。")
            run.request_cancel()
        run.wait(timeout)
        return run

    def wait_for_run(self, run_id, timeout=None) -> RunContext:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            if run is None:
                raise RunNotFoundError("找不到这次回复运行。")
        run.wait(timeout)
        return run

    def active_run(self, session_id=None) -> Optional[RunContext]:
        with self._lock:
            if session_id is None:
                for run in self._runs.values():
                    if not run.finished:
                        return run
                return None
            run_id = self._session_runs.get(str(session_id))
            run = self._runs.get(run_id) if run_id else None
            return run if run is not None and not run.finished else None

    def has_active_run(self, session_id=None) -> bool:
        return self.active_run(session_id) is not None

    def abandon_run(self, run_id, reason="run abandoned by adapter"):
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            if run is None:
                return False
            if not run.finished:
                run.request_cancel()
                run.complete(RunStatus.FAILED, error=str(reason))
            self._release_run(run)
            return True

    def set_mode(self, mode):
        with self._lock:
            self._assert_idle("切换模式")
            self.runtime.set_mode(AgentMode(mode))

    def select_workspace(self, path):
        with self._lock:
            self._assert_idle("切换工作目录")
            return self.runtime.select_workspace(path)

    def clear_workspace(self):
        with self._lock:
            self._assert_idle("取消工作目录")
            return self.runtime.clear_workspace()

    def switch_session(self, session_id) -> bool:
        with self._lock:
            self._assert_idle("切换会话")
            return self.runtime.switch_session(session_id)

    def create_session(self, mode=None) -> str:
        with self._lock:
            self._assert_idle("新建会话")
            session_id = new_id("chat")
            selected_mode = AgentMode(mode or self.runtime.current_mode())
            self.runtime.store.ensure_session(session_id, selected_mode)
            return session_id

    def rename_session(self, session_id, title) -> bool:
        with self._lock:
            self._assert_idle("重命名会话")
            return self.runtime.rename_session(session_id, title)

    def set_session_pinned(self, session_id, pinned=True) -> bool:
        with self._lock:
            self._assert_idle("置顶会话")
            return self.runtime.set_session_pinned(session_id, pinned)

    def set_session_unread(self, session_id, unread=True) -> bool:
        with self._lock:
            self._assert_idle("标记会话")
            return self.runtime.set_session_unread(session_id, unread)

    def delete_session(self, session_id) -> bool:
        with self._lock:
            self._assert_idle("删除会话")
            return self.runtime.delete_session(session_id)

    def draft(self, session_id=None) -> str:
        return self.runtime.draft(session_id)

    def save_draft(self, session_id, content) -> bool:
        return self.runtime.save_draft(session_id, content)

    def approval_view(self, action_id) -> ApprovalView:
        proposal = self.runtime.store.get_proposal(str(action_id or ""))
        if proposal is None or proposal.session_id != self.runtime.session_id:
            raise ApprovalUnavailableError("找不到这条待确认动作。")
        return self._approval_view_for(proposal)

    def _approval_view_for(self, proposal) -> ApprovalView:
        preview = getattr(self.runtime, "preview_proposal", None)
        if not callable(preview):
            return ApprovalView.from_proposal(proposal)
        try:
            data = preview(proposal.action_id)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return ApprovalView.from_proposal(proposal)
        return ApprovalView.from_proposal(
            proposal,
            preview=data.get("preview"),
            expired=data.get("expired"),
            workspace=data.get("workspace"),
        )

    def confirm_action(self, action_id, mode=None):
        with self._lock:
            self._assert_idle("确认动作")
            view = self.approval_view(action_id)
            if view.status != ProposalStatus.AWAITING_CONFIRMATION.value:
                raise ApprovalUnavailableError("这条动作已经处理，不能重复确认。")
            if view.expired:
                raise ApprovalUnavailableError("提案已过期，请重新生成。")
            return self.runtime.confirm_proposal(action_id, mode)

    def cancel_action(self, action_id):
        with self._lock:
            self._assert_idle("取消动作")
            view = self.approval_view(action_id)
            if view.status != ProposalStatus.AWAITING_CONFIRMATION.value:
                raise ApprovalUnavailableError("这条动作已经处理。")
            return self.runtime.cancel_proposal(action_id)

    def undo_receipt(self, receipt_id):
        with self._lock:
            self._assert_idle("撤销写入")
            return self.runtime.undo_receipt(receipt_id)

    def _advance_run(self, run, sequence, stage, detail, tool):
        run.sequence = int(sequence)
        if stage:
            run.stage = str(stage)
        run.detail = str(detail or "")
        run.tool = str(tool or "")
        self._record_step(run)

    def _record_step(self, run):
        """Collapse repeated stage/detail pairs so streaming deltas add no rows."""
        step = RunStep(
            sequence=run.sequence,
            stage=run.stage,
            at=utc_now(),
            detail=run.detail,
            tool=run.tool,
        )
        last = run.steps[-1] if run.steps else None
        if last is not None and (last.stage, last.detail, last.tool) == (
            step.stage,
            step.detail,
            step.tool,
        ):
            return
        run.steps.append(step)

    def _stage_detail(self, kind, payload):
        if kind == EventKind.TOOL.value:
            name = str(payload.get("tool_name") or "")
            if payload.get("state") == "running":
                return name, name
            summary = clip(payload.get("summary") or "", 120)
            return (
                "{}：{}".format(name, summary) if summary else name,
                name,
            )
        if kind == EventKind.ERROR.value:
            return str(payload.get("message") or ""), ""
        return "", ""

    def _regeneration_rows(self, message_id):
        """The only regenerable pair: trailing assistant plus its user message."""
        session_id = self.runtime.session_id
        try:
            rows = self.runtime.store.list_messages(session_id, limit=2)
        except Exception:
            return None, None, "暂时读不到这条会话的消息。"
        if len(rows) < 2:
            return None, None, "当前会话还没有可重新生成的回复。"
        assistant, user = rows[-1], rows[-2]
        if (
            int(assistant.get("id") or 0) != int(message_id or 0)
            or assistant.get("role") != "assistant"
        ):
            return None, None, "只能重新生成最新一条回复。"
        if user.get("role") != "user" or not str(user.get("content") or "").strip():
            return None, None, "这条回复没有对应的用户消息。"
        effect = self._message_side_effect(session_id, int(user["id"]))
        if effect:
            return (
                None,
                None,
                "这条消息已经产生{}，重新生成可能重复执行，暂不支持。".format(
                    effect
                ),
            )
        return assistant, user, ""

    def _message_side_effect(self, session_id, message_id) -> str:
        finder = getattr(self.runtime.store, "message_side_effect", None)
        if finder is None:
            return ""
        try:
            return str(finder(session_id, int(message_id)) or "")
        except Exception:
            return ""

    def _has_assistant_after(self, run: RunContext) -> bool:
        try:
            rows = self.runtime.store.list_messages_after(
                run.session_id,
                run.previous_message_id,
                limit=20,
            )
        except Exception:
            return False
        return any(row.get("role") == "assistant" for row in rows or ())

    def _restore_message(self, run: RunContext, replaced):
        if not replaced:
            return
        try:
            self.runtime.store.add_message(
                run.session_id,
                "assistant",
                str(replaced.get("content") or ""),
                AgentMode(replaced.get("mode") or run.mode),
            )
        except Exception:
            pass

    def _persisted_message_id(
        self,
        run: RunContext,
        result: Dict,
    ) -> Optional[int]:
        answer = str(result.get("answer") or "")
        rows = self.runtime.store.list_messages(run.session_id, limit=20)
        for row in rows:
            if (
                int(row.get("id") or 0) > run.previous_message_id
                and row.get("role") == "assistant"
                and str(row.get("content") or "") == answer
            ):
                return int(row["id"])
        return None

    def _latest_message_id(self, session_id: str) -> int:
        rows = self.runtime.store.list_messages(session_id, limit=1)
        return int(rows[-1]["id"]) if rows else 0

    def _assert_idle(self, action):
        active = self.active_run()
        if active is not None:
            raise SessionBusyError(
                "正在生成回复，完成前不能{}。".format(action)
            )

    def _find_run(self, run_id=None) -> Optional[RunContext]:
        if run_id is None:
            return self.active_run()
        run = self._runs.get(str(run_id))
        return run

    def _release_run(self, run: RunContext):
        with self._lock:
            if self._runs.get(run.run_id) is run:
                self._runs.pop(run.run_id, None)
            if self._session_runs.get(run.session_id) == run.run_id:
                self._session_runs.pop(run.session_id, None)
