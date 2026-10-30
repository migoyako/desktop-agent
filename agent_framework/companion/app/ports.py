"""Runtime port implemented by the current in-process companion runtime."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Protocol

from ..models import AgentMode


class CompanionRuntimePort(Protocol):
    session_id: str
    store: Any

    def stream_message(
        self,
        text: str,
        mode: AgentMode = AgentMode.CHAT,
        generation_id: str = "",
        cancel_event=None,
    ) -> Iterator[dict]:
        ...

    def set_mode(self, mode: AgentMode):
        ...

    def current_mode(self) -> AgentMode:
        ...

    def select_workspace(self, path):
        ...

    def clear_workspace(self):
        ...

    def switch_session(self, session_id: str) -> bool:
        ...

    def workspace(self) -> Optional[str]:
        ...

    def history(self, limit=60) -> List[Dict]:
        ...

    def session_summaries(self) -> List[Dict]:
        ...

    def pending_proposals(self) -> List:
        ...

    def preview_proposal(self, action_id) -> Optional[Dict]:
        ...

    def latest_receipts(self, limit=20) -> List:
        ...

    def confirm_proposal(self, action_id, current_mode=None) -> Dict:
        ...

    def cancel_proposal(self, action_id) -> str:
        ...

    def undo_receipt(self, receipt_id) -> str:
        ...
