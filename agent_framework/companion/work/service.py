"""Task cards, plan review and guarded forwarding for work requests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Callable, Dict, Optional

from ..models import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    FORWARD_ACCEPTED,
    FORWARD_AWAITING_CONFIRMATION,
    FORWARD_COMPLETED,
    FORWARD_FORWARDING,
    FORWARD_UNAVAILABLE,
    FORWARD_UNKNOWN,
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
from .router import (
    DEFAULT_ROUTER_TIMEOUT,
    Intent,
    IntentDecision,
    route_intent,
    should_auto_plan,
)


class WorkError(RuntimeError):
    pass


TASK_PREFIX_RE = re.compile(
    r"^\s*(?:请|帮我|帮忙|麻烦)?\s*(?:创建)?\s*"
    r"(?:任务卡|计划|任务)[:：]?\s*"
)
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]")
MAX_TITLE_CHARS = 36
MAX_GOAL_CHARS = 800
MAX_DETAIL_CHARS = 600

STATE_LABELS = {
    WORK_NORMAL: "空闲",
    WORK_PLAN_DISCUSSING: "讨论中",
    WORK_PLAN_REVIEW: "等待审批",
    WORK_EXECUTING: "执行中",
}
FORWARD_LABELS = {
    FORWARD_AWAITING_CONFIRMATION: "等待确认",
    FORWARD_FORWARDING: "转发中",
    FORWARD_ACCEPTED: "已受理",
    FORWARD_COMPLETED: "已完成",
    FORWARD_UNKNOWN: "结果不确定",
    FORWARD_UNAVAILABLE: "不可用",
}
FORWARD_TERMINAL = {
    FORWARD_COMPLETED,
    FORWARD_UNKNOWN,
    FORWARD_UNAVAILABLE,
}


def _parse_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean_goal(text: str) -> str:
    return TASK_PREFIX_RE.sub("", str(text or "").strip()).strip()[:MAX_GOAL_CHARS]


def _build_title(goal: str) -> str:
    first = SENTENCE_SPLIT_RE.split(goal, 1)[0].strip() or "工作任务"
    if len(first) > MAX_TITLE_CHARS:
        first = first[: MAX_TITLE_CHARS - 1] + "…"
    return first


def build_task_card(
    text: str,
    decision: IntentDecision,
    workspace: Optional[str],
    executor_name: str,
) -> TaskCard:
    goal = _clean_goal(text)
    title = _build_title(goal)
    project = Path(workspace).name if workspace else "未指定"
    reason = decision.reason or "用户提出了工作请求"
    return TaskCard(
        title=title,
        reason=reason,
        spoken_summary="我准备处理「{}」，先给你计划，确认后再执行。".format(title),
        goal=goal,
        executor=executor_name,
        project=project,
    )


def describe_task(task: WorkTask) -> str:
    return (
        "任务卡\n"
        "标题：{}\n"
        "目标：{}\n"
        "理由：{}\n"
        "执行器：{}\n"
        "项目：{}\n"
        "口语播报：{}\n"
        "状态：{}".format(
            task.card.title,
            task.card.goal,
            task.card.reason,
            task.card.executor,
            task.card.project,
            task.card.spoken_summary,
            STATE_LABELS.get(task.state, task.state),
        )
    )


class WorkService:
    """Persist plan state before waiting, and never forward without approval."""

    def __init__(
        self,
        store,
        session_id: str = "main",
        executor: Optional[Callable] = None,
        executor_name: str = "",
        classifier: Optional[Callable[[str], str]] = None,
        router_timeout: float = DEFAULT_ROUTER_TIMEOUT,
        approval_ttl_minutes: float = 30.0,
        clock: Callable[[], str] = utc_now,
    ):
        self.store = store
        self.session_id = session_id
        self.executor = executor
        self.executor_name = executor_name or (
            "转发执行器" if executor is not None else "未配置转发器"
        )
        self.classifier = classifier
        self.router_timeout = router_timeout
        self.approval_ttl_minutes = max(1.0, float(approval_ttl_minutes))
        self.clock = clock
        self.recovered = self.store.recover_incomplete_work()

    # ---- routing and task creation -------------------------------------

    def route(self, text, classifier=None) -> IntentDecision:
        return route_intent(
            text,
            classifier if classifier is not None else self.classifier,
            self.router_timeout,
        )

    def should_plan(self, text) -> bool:
        return should_auto_plan(text)

    def submit(
        self,
        text: str,
        source_message_id: Optional[int] = None,
        force: bool = False,
        classifier=None,
    ) -> Dict:
        text = str(text or "").strip()
        if not text:
            raise WorkError("任务内容不能为空。")

        pending = self.store.latest_work_task(
            self.session_id,
            active_only=True,
        )
        if (
            pending is not None
            and pending.state == WORK_PLAN_DISCUSSING
            and pending.info_asked >= 1
        ):
            return self._accept_missing_info(pending, text)

        decision = self.route(text, classifier=classifier)
        if force and decision.intent != Intent.WORK:
            decision = IntentDecision(
                Intent.WORK,
                "用户显式创建任务卡",
                "manual",
                requires_plan=True,
            )

        if decision.intent == Intent.COMPANION:
            return {
                "decision": decision,
                "task": None,
                "receipt": None,
                "answer": "这更像陪伴话题，我们直接聊就好。",
            }
        if decision.intent == Intent.CLARIFY:
            if decision.prompt_risk:
                return {
                    "decision": decision,
                    "task": None,
                    "receipt": None,
                    "answer": decision.reason,
                }
            return self._start_clarification(text, decision, source_message_id)
        return self._start_work(text, decision, source_message_id)

    def _start_clarification(self, text, decision, source_message_id):
        task = WorkTask(
            task_id=new_id("task"),
            session_id=self.session_id,
            state=WORK_PLAN_DISCUSSING,
            card=build_task_card(text, decision, self.store.get_workspace(), self.executor_name),
            source_message_id=source_message_id,
            info_asked=1,
            approval_status=APPROVAL_PENDING,
            expires_at=self._expires_at(),
        )
        self.store.create_work_task(task)
        return {
            "decision": decision,
            "task": task,
            "receipt": None,
            "answer": (
                "要做这件事，我还需要确认一次：你希望完成的具体目标"
                "和对象是什么？只说一次就够了，我会据此整理任务卡。"
            ),
        }

    def _start_work(self, text, decision, source_message_id):
        card = build_task_card(
            text,
            decision,
            self.store.get_workspace(),
            self.executor_name,
        )
        task = WorkTask(
            task_id=new_id("task"),
            session_id=self.session_id,
            state=WORK_PLAN_REVIEW,
            card=card,
            source_message_id=source_message_id,
            approval_status=APPROVAL_PENDING,
            expires_at=self._expires_at(),
        )
        self.store.create_work_task(task)
        receipt = self._ensure_receipt(task)
        return self._review_result(task, receipt, decision)

    def _accept_missing_info(self, task, text):
        original = task.card.goal.strip()
        vague = re.fullmatch(
            r"(?:请|帮我|帮忙|麻烦)?(?:处理一下|搞定|弄一下|安排一下|看一下)"
            r"[。！？!? ]*",
            original,
        )
        if original and not vague:
            goal = "{}\n补充信息：{}".format(original, text).strip()[:MAX_GOAL_CHARS]
            combined = original + " " + text
        else:
            goal = text[:MAX_GOAL_CHARS]
            combined = text
        decision = IntentDecision(
            Intent.WORK,
            "已收到补充信息，进入计划审阅",
            "clarify-answer",
            requires_plan=True,
        )
        card = build_task_card(
            combined,
            decision,
            self.store.get_workspace(),
            self.executor_name,
        )
        updated = self.store.transition_work_state(
            task.task_id,
            WORK_PLAN_DISCUSSING,
            WORK_PLAN_REVIEW,
            title=card.title,
            reason=card.reason,
            spoken_summary=card.spoken_summary,
            goal=goal,
            executor=card.executor,
            project=card.project,
            revision=task.revision + 1,
            approval_status=APPROVAL_PENDING,
            approval_reason="",
            expires_at=self._expires_at(),
        )
        if not updated:
            raise WorkError("任务已被其他窗口处理，请刷新后再试。")
        task = self.store.get_work_task(task.task_id)
        receipt = self._ensure_receipt(task)
        return self._review_result(task, receipt, decision)

    # ---- review actions -------------------------------------------------

    def approve(self, task_id: str, executor=None) -> Dict:
        task = self._load(task_id)
        if task.state != WORK_PLAN_REVIEW:
            raise WorkError("只有等待审批的计划可以批准。")
        if task.approval_status == APPROVAL_APPROVED:
            raise WorkError("这个计划已经批准过。")
        if self._expired(task):
            self._expire(task, "确认已超时，计划退回讨论；没有执行任何操作。")
            raise WorkError("计划确认已超时，没有执行任何操作。")

        transitioned = self.store.transition_work_state(
            task.task_id,
            WORK_PLAN_REVIEW,
            WORK_EXECUTING,
            approval_status=APPROVAL_APPROVED,
            approval_reason="用户已批准",
            approved_at=self.clock(),
        )
        if not transitioned:
            raise WorkError("计划已被其他窗口处理，请刷新后再试。")
        task = self._load(task.task_id)
        receipt = self._ensure_receipt(task, status=FORWARD_AWAITING_CONFIRMATION)
        self.store.update_work_receipt(
            receipt.receipt_id,
            FORWARD_FORWARDING,
            summary="正在转发任务。",
            increment_attempt=True,
        )
        receipt = self.store.get_work_receipt(receipt.receipt_id)

        active_executor = executor if executor is not None else self.executor
        if active_executor is None:
            outcome = {
                "status": FORWARD_UNAVAILABLE,
                "summary": "未配置转发执行器，任务没有发送。",
                "detail": "executor_not_configured",
            }
        else:
            try:
                value = active_executor(task.card, receipt)
                outcome = self._normalize_outcome(value)
            except Exception as error:
                outcome = {
                    "status": FORWARD_UNKNOWN,
                    "summary": "转发过程中断，结果不确定，系统不会自动重发。",
                    "detail": str(error),
                }

        self.store.update_work_receipt(
            receipt.receipt_id,
            outcome["status"],
            summary=outcome["summary"],
            detail=outcome["detail"][:MAX_DETAIL_CHARS],
            external_id=outcome.get("external_id", ""),
        )
        if outcome["status"] != FORWARD_ACCEPTED:
            self.store.transition_work_state(
                task.task_id,
                WORK_EXECUTING,
                WORK_NORMAL,
            )
            final_task = self._load(task.task_id)
        else:
            final_task = self._load(task.task_id)
        receipt = self.store.get_work_receipt(receipt.receipt_id)
        return {
            "task": final_task,
            "receipt": receipt,
            "answer": "{}\n状态：{}。".format(
                outcome["summary"],
                FORWARD_LABELS.get(receipt.status, receipt.status),
            ),
        }

    def mark_forward_result(
        self,
        task_id: str,
        status: str,
        summary: str = "",
        detail: str = "",
        external_id: str = "",
    ) -> WorkReceipt:
        task = self._load(task_id)
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        if receipt is None:
            raise WorkError("找不到这个任务的转发回执。")
        if receipt.status == FORWARD_UNKNOWN:
            raise WorkError("结果不确定的回执不能改写，也不会自动重发。")
        if status not in (FORWARD_ACCEPTED, FORWARD_COMPLETED, FORWARD_UNKNOWN, FORWARD_UNAVAILABLE):
            raise WorkError("不支持的转发回执状态。")
        if receipt.status in FORWARD_TERMINAL and status != receipt.status:
            raise WorkError("回执已结束，不能重复改写。")
        self.store.update_work_receipt(
            receipt.receipt_id,
            status,
            summary=summary or receipt.summary,
            detail=detail or receipt.detail,
            external_id=external_id or receipt.external_id,
        )
        if status in (FORWARD_COMPLETED, FORWARD_UNKNOWN, FORWARD_UNAVAILABLE):
            self.store.transition_work_state(
                task.task_id,
                WORK_EXECUTING,
                WORK_NORMAL,
            )
        return self.store.get_work_receipt(receipt.receipt_id)

    def revise(self, task_id: str, instruction: str) -> Dict:
        task = self._load(task_id)
        if task.state not in (WORK_PLAN_DISCUSSING, WORK_PLAN_REVIEW):
            raise WorkError("当前状态不能修改计划。")
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        if receipt is not None and receipt.status == FORWARD_UNKNOWN:
            raise WorkError("上次转发结果不确定，不能修改后重发；请新建任务。")
        instruction = str(instruction or "").strip()
        if not instruction:
            raise WorkError("请说明要修改什么。")
        combined = "{}\n修改要求：{}".format(task.card.goal, instruction)
        decision = IntentDecision(
            Intent.WORK,
            "用户要求修改计划",
            "revision",
            requires_plan=True,
        )
        card = build_task_card(
            combined,
            decision,
            self.store.get_workspace(),
            self.executor_name,
        )
        updated = self.store.transition_work_state(
            task.task_id,
            task.state,
            WORK_PLAN_REVIEW,
            title=card.title,
            reason=card.reason,
            spoken_summary=card.spoken_summary,
            goal=card.goal,
            executor=card.executor,
            project=card.project,
            revision=task.revision + 1,
            approval_status=APPROVAL_PENDING,
            approval_reason="用户修改了计划，需要重新确认。",
            expires_at=self._expires_at(),
        )
        if not updated:
            raise WorkError("计划已被其他窗口处理，请刷新后再试。")
        task = self._load(task.task_id)
        receipt = self._ensure_receipt(task)
        return self._review_result(task, receipt, decision)

    def reject(self, task_id: str, reason: str = "") -> Dict:
        task = self._load(task_id)
        if task.state not in (WORK_PLAN_DISCUSSING, WORK_PLAN_REVIEW):
            raise WorkError("当前状态不能拒绝。")
        reason = str(reason or "").strip()[:MAX_DETAIL_CHARS]
        updated = self.store.transition_work_state(
            task.task_id,
            task.state,
            WORK_NORMAL,
            approval_status=APPROVAL_REJECTED,
            approval_reason=reason or "用户拒绝了计划",
            approved_at=None,
        )
        if not updated:
            raise WorkError("计划已被其他窗口处理，请刷新后再试。")
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        if receipt is not None and receipt.status == FORWARD_AWAITING_CONFIRMATION:
            self.store.update_work_receipt(
                receipt.receipt_id,
                FORWARD_UNAVAILABLE,
                summary="用户拒绝了计划，任务没有转发。",
                detail=reason or "rejected_by_user",
            )
        task = self._load(task.task_id)
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        return {
            "task": task,
            "receipt": receipt,
            "answer": "已拒绝这个计划，没有执行或转发任何内容。",
        }

    def expire_if_due(self, task_id: str) -> bool:
        task = self._load(task_id)
        if task.state != WORK_PLAN_REVIEW or task.approval_status != APPROVAL_PENDING:
            return False
        if not self._expired(task):
            return False
        self._expire(task, "确认已超时，计划退回讨论；不会自动批准。")
        return True

    # ---- reads ----------------------------------------------------------

    def task(self, task_id: str) -> Optional[WorkTask]:
        task = self.store.get_work_task(task_id)
        if task is None or task.session_id != self.session_id:
            return None
        return task

    def active_task(self) -> Optional[WorkTask]:
        return self.store.latest_work_task(self.session_id, active_only=True)

    def tasks(self, limit: int = 20):
        return self.store.list_work_tasks(self.session_id, limit=limit)

    def receipt_for(self, task_id: str) -> Optional[WorkReceipt]:
        return self.store.get_work_receipt_for_task(task_id)

    def status(self) -> Dict:
        task = self.active_task()
        receipt = self.receipt_for(task.task_id) if task else None
        return {
            "task": task,
            "receipt": receipt,
            "state": task.state if task else WORK_NORMAL,
            "state_label": STATE_LABELS.get(task.state, task.state) if task else "空闲",
            "forward_status": receipt.status if receipt else "",
            "forward_label": FORWARD_LABELS.get(receipt.status, receipt.status) if receipt else "",
        }

    def recover_pending(self):
        return self.store.recover_incomplete_work()

    # ---- helpers --------------------------------------------------------

    def _load(self, task_id: str) -> WorkTask:
        task = self.task(task_id)
        if task is None:
            raise WorkError("找不到这条任务。")
        return task

    def _expires_at(self) -> str:
        now = _parse_time(self.clock()) or datetime.now(timezone.utc)
        return (now + timedelta(minutes=self.approval_ttl_minutes)).isoformat()

    def _expired(self, task: WorkTask) -> bool:
        expires = _parse_time(task.expires_at)
        now = _parse_time(self.clock())
        return bool(expires and now and now >= expires)

    def _expire(self, task: WorkTask, reason: str):
        self.store.transition_work_state(
            task.task_id,
            WORK_PLAN_REVIEW,
            WORK_PLAN_DISCUSSING,
            approval_status=APPROVAL_EXPIRED,
            approval_reason=reason,
            approved_at=None,
        )
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        if receipt is not None and receipt.status == FORWARD_AWAITING_CONFIRMATION:
            self.store.update_work_receipt(
                receipt.receipt_id,
                FORWARD_UNAVAILABLE,
                summary=reason,
                detail="approval_expired",
            )

    def _ensure_receipt(
        self,
        task: WorkTask,
        status: str = FORWARD_AWAITING_CONFIRMATION,
    ) -> WorkReceipt:
        receipt = self.store.get_work_receipt_for_task(task.task_id)
        if receipt is not None:
            return receipt
        receipt = WorkReceipt(
            receipt_id=new_id("wrc"),
            task_id=task.task_id,
            session_id=task.session_id,
            status=status,
            summary="等待用户确认计划，确认前不会转发。",
        )
        self.store.create_work_receipt(receipt)
        return receipt

    def _review_result(self, task, receipt, decision) -> Dict:
        return {
            "decision": decision,
            "task": task,
            "receipt": receipt,
            "answer": (
                "{}\n\n确认前不会执行或转发。"
                "可以批准、提出修改，或拒绝这个计划。".format(describe_task(task))
            ),
        }

    @staticmethod
    def _normalize_outcome(value) -> Dict:
        if isinstance(value, WorkReceipt):
            payload = {
                "status": value.status,
                "summary": value.summary,
                "detail": value.detail,
                "external_id": value.external_id,
            }
        elif isinstance(value, dict):
            payload = value
        else:
            return {
                "status": FORWARD_UNKNOWN,
                "summary": "执行器返回了无法识别的结果，按结果不确定处理。",
                "detail": repr(value)[:MAX_DETAIL_CHARS],
            }
        status = str(payload.get("status") or "").strip()
        if status not in (
            FORWARD_ACCEPTED,
            FORWARD_COMPLETED,
            FORWARD_UNKNOWN,
            FORWARD_UNAVAILABLE,
        ):
            return {
                "status": FORWARD_UNKNOWN,
                "summary": "执行器返回了未知状态，按结果不确定处理。",
                "detail": "unexpected_status={}".format(status),
            }
        return {
            "status": status,
            "summary": str(payload.get("summary") or FORWARD_LABELS[status]),
            "detail": str(payload.get("detail") or ""),
            "external_id": str(payload.get("external_id") or ""),
        }
