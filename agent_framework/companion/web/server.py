"""Local HTTP + SSE adapter exposing the application service to the web UI."""

from __future__ import annotations

import json
import mimetypes
import queue
import re
import secrets
import sys
import threading
from dataclasses import fields, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from ..models import AgentMode
from ..policy import mode_label
from ..ui.state import UiStateStore
from ..work import STATE_LABELS

ASSETS = Path(__file__).resolve().parent / "assets"

PAGES = {
    "/": "index.html",
    "/settings": "settings.html",
    "/console": "console.html",
    "/overlay": "overlay.html",
}

MOOD_TEXT = {
    "neutral": "平静",
    "calm": "平静",
    "positive": "开心",
    "warm": "温和",
    "low": "低落",
    "tired": "疲惫",
    "concerned": "担心",
    "happy": "开心",
}

MOOD_KEYS = {
    "平静": "neutral",
    "开心": "positive",
    "低落": "low",
    "疲惫": "tired",
}


def plain(value):
    """Convert runtime dataclasses into JSON-safe values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: plain(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(item) for item in value]
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int)):
        return value.value
    return value


def clip(text, limit=120):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def stamp(value):
    text = str(value or "").strip()
    return "" if not text else text.replace("T", " ")[:16]


def mood_text(label):
    key = str(label or "")
    return MOOD_TEXT.get(key, key or "平静")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
        ):
            return
        super().handle_error(request, client_address)


class WebAdapter:
    """Owns the HTTP server, the event fan-out, and JSON payload shaping."""

    def __init__(self, service, data_dir=None, host="127.0.0.1", port=0, token=None):
        self.service = service
        self.runtime = service.runtime
        root = Path(data_dir or getattr(self.runtime, "data_dir", Path.cwd()))
        self.ui_state = UiStateStore(root)
        self.token = token or secrets.token_urlsafe(24)
        self._server = _Server((host, int(port)), _Handler)
        self._server.adapter = self
        self._thread = None
        self._subs = []
        self._sub_lock = threading.Lock()
        self._closed = False
        self._stopped = False
        self._activate_handler = None

    @property
    def address(self):
        host, port = self._server.server_address[:2]
        return "{}:{}".format(host, port)

    @property
    def port(self):
        return int(self._server.server_address[1])

    @property
    def url(self):
        return "http://{}/?t={}".format(self.address, self.token)

    def set_activate_handler(self, handler):
        self._activate_handler = handler

    def activate(self):
        """Show the running window; False means browser-only or no handler."""
        handler = self._activate_handler
        if handler is None:
            return False
        try:
            handler()
        except Exception:
            return False
        return True

    def start(self):
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="companion-web",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._closed = True
        self.broadcast({"type": "shutdown"})
        self._server.shutdown()
        self._server.server_close()

    # -- event fan-out -------------------------------------------------

    def subscribe(self):
        channel = queue.Queue()
        with self._sub_lock:
            self._subs.append(channel)
        return channel

    def unsubscribe(self, channel):
        with self._sub_lock:
            if channel in self._subs:
                self._subs.remove(channel)

    def broadcast(self, message):
        data = json.dumps(message, ensure_ascii=False)
        with self._sub_lock:
            targets = list(self._subs)
        for channel in targets:
            channel.put(data)

    def _affect_view(self):
        snapshot = plain(self._safe(lambda: self.runtime.affect_snapshot(), {}))
        for target in ("user", "agent"):
            item = snapshot.get(target)
            if isinstance(item, dict):
                item["mood_text"] = mood_text(item.get("mood_label"))
        return snapshot

    def update_ui(self, body):
        state = self.ui_state
        if "theme" in body:
            state.set_theme(body.get("theme"))
        if "font_scale" in body:
            state.set_font_scale(body.get("font_scale"))
        if "reduce_motion" in body:
            state.set_reduce_motion(body.get("reduce_motion"))
        if "inspector_visible" in body:
            state.set_inspector_visible(body.get("inspector_visible"))
        if "inspector_width" in body:
            state.set_inspector_width(body.get("inspector_width"))
        if "overlay_visible" in body:
            state.set_overlay_visible(body.get("overlay_visible"))
        result = {
            "theme": state.theme(),
            "font_scale": state.font_scale(),
            "reduce_motion": state.reduce_motion(),
            "inspector_visible": state.inspector_visible(),
            "inspector_width": state.inspector_width(),
            "overlay_visible": state.overlay_visible(),
        }
        self.broadcast({"type": "ui", "ui": result})
        return result

    # -- payloads ------------------------------------------------------

    def bootstrap(self):
        service = self.service
        projection = service.ui_projection()
        persona = self._safe(lambda: self.runtime.persona_info(), {})
        ui = {
            "theme": self.ui_state.theme(),
            "font_scale": self.ui_state.font_scale(),
            "reduce_motion": self.ui_state.reduce_motion(),
            "inspector_visible": self.ui_state.inspector_visible(),
            "inspector_width": self.ui_state.inspector_width(),
            "overlay_visible": self.ui_state.overlay_visible(),
        }
        return {
            "persona": {
                "id": persona.get("id") or "",
                "name": persona.get("name") or "数字分身",
                "version": persona.get("version") or "",
                "loaded": bool(persona.get("loaded")),
                "error": persona.get("error") or "",
            },
            "persona_preferences": persona.get("preferences") or {},
            "mode": service.current_mode().value,
            "mode_label": mode_label(service.current_mode()),
            "workspace": self.runtime.workspace() or "",
            "session_id": service.current_session_id,
            "sessions": self.sessions(),
            "messages": [
                {
                    "id": item.message_id,
                    "role": item.role,
                    "text": item.text,
                    "created_at": item.created_at,
                    "mode": item.mode,
                }
                for item in projection.messages
            ],
            "run": plain(projection.run) if projection.run else None,
            "context": plain(projection.context) if projection.context else None,
            "approvals": [item.as_dict() for item in projection.approvals],
            "tasks": [
                self._task_row(task)
                for task in self._safe(lambda: self.runtime.work_tasks(20), []) or []
            ],
            "inspector": [
                {"title": section.title, "lines": list(section.lines)}
                for section in projection.inspector
            ],
            "status": self._safe(lambda: self.runtime.desktop_status(), {}),
            "affect": self._affect_view(),
            "proactive": self._safe(lambda: self.runtime.proactive_status(), {}),
            "skills": self._safe(lambda: self.runtime.skills_status(), {}),
            "ui": ui,
            "draft": self._safe(lambda: service.draft(), ""),
            "settings_summary": self.settings_summary(),
        }

    def sessions(self):
        rows = self._safe(lambda: self.service.session_summaries(), [])
        result = []
        for row in rows or []:
            title = str(row.get("title") or row.get("topic") or "").strip()
            result.append(
                {
                    "id": str(row.get("id") or ""),
                    "title": clip(title or "新会话", 40),
                    "mode": str(row.get("mode") or "chat"),
                    "mode_label": mode_label(AgentMode(row.get("mode") or "chat")),
                    "updated_at": str(row.get("updated_at") or ""),
                    "last_at": str(row.get("last_at") or row.get("updated_at") or ""),
                    "pinned": bool(row.get("pinned_at")),
                    "unread": bool(row.get("unread")),
                    "message_count": int(row.get("message_count") or 0),
                    "last_role": str(row.get("last_role") or ""),
                    "last_content": clip(row.get("last_content") or "", 44),
                }
            )
        return result

    def settings_summary(self):
        summary = {}
        persona = self._safe(lambda: self.runtime.persona_info(), {})
        summary["persona"] = {
            "name": persona.get("name") or "数字分身",
            "loaded": bool(persona.get("loaded")),
            "version": persona.get("version") or "",
        }
        memories = self._safe(lambda: self.runtime.memories(), [])
        summary["memory_count"] = len(memories or [])
        affect = self._safe(lambda: self.runtime.affect_snapshot(), {})
        summary["mood"] = (
            (affect.get("user") or {}).get("mood_label") or "平静"
        )
        summary["mood_text"] = mood_text(summary["mood"])
        work = self._safe(lambda: self.runtime.work_status(), {})
        summary["work_state"] = work.get("state_label") or ""
        proactive = self._safe(lambda: self.runtime.proactive_status(), {})
        summary["proactive_enabled"] = bool(proactive.get("enabled"))
        summary["proactive_daily"] = "{} / {}".format(
            proactive.get("daily_count", 0),
            proactive.get("daily_limit", 0),
        )
        return summary

    def overlay(self):
        status = self._safe(lambda: self.runtime.desktop_status(), {})
        affect = self._safe(lambda: self.runtime.affect_snapshot(), {})
        agent = affect.get("agent") or {}
        return {
            "status": {
                "label": status.get("label") or "待机",
                "detail": clip(status.get("detail") or "", 120),
                "state": status.get("state") or "idle",
            },
            "mood": agent.get("mood_label") or "calm",
            "agent": agent,
            "persona": (self._safe(lambda: self.runtime.persona_info(), {})).get(
                "name"
            )
            or "数字分身",
            "theme": self.ui_state.theme(),
            "font_scale": self.ui_state.font_scale(),
            "pending": len(self._safe(lambda: self.runtime.pending_proposals(), []) or []),
        }

    def console_page(self, page):
        if page == "memory":
            return {"rows": [plain(item) for item in self._memories()]}
        if page == "recall":
            logs = self._safe(lambda: self.runtime.recall_logs(80), [])
            return {"rows": [plain(item) for item in logs or []]}
        if page == "tasks":
            tasks = self._safe(lambda: self.runtime.work_tasks(limit=100), [])
            return {"rows": [self._task_row(item) for item in tasks or []]}
        if page == "affect":
            return {
                "snapshot": self._affect_view(),
                "activity": plain(self._safe(lambda: self.runtime.activity_state(), {})),
            }
        if page == "proactive":
            return {
                "status": plain(self._safe(lambda: self.runtime.proactive_status(), {})),
                "events": [plain(item) for item in self._safe(lambda: self.runtime.proactive_events(100), []) or []],
            }
        if page == "audit":
            receipts = self._safe(lambda: self.runtime.latest_receipts(100), []) or []
            return {"rows": [plain(item) for item in receipts]}
        if page == "settings":
            return {
                "workspace": self.runtime.workspace() or "",
                "mode": self.service.current_mode().value,
                "persona": plain(self._safe(lambda: self.runtime.persona_info(), {})),
                "proactive": plain(self._safe(lambda: self.runtime.proactive_status(), {})),
                "skills": plain(self._safe(lambda: self.runtime.skills_status(), {})),
                "sessions": len(self._safe(lambda: self.service.session_summaries(), []) or []),
                "memories": len(self._memories()),
                "ui": {
                    "theme": self.ui_state.theme(),
                    "font_scale": self.ui_state.font_scale(),
                },
            }
        if page == "skills":
            return {
                "skills": plain(self._safe(lambda: self.runtime.skills_status(), {})),
                "receipts": [plain(item) for item in self._safe(lambda: self.runtime.latest_receipts(50), []) or []],
            }
        if page == "failures":
            return self.failures()
        return {}

    def failures(self):
        receipts = self._safe(lambda: self.runtime.latest_receipts(100), []) or []
        failed = [
            plain(item)
            for item in receipts
            if str(getattr(item, "status", "")) not in ("completed", "pending")
        ]
        events = self._safe(lambda: self.runtime.proactive_events(100), []) or []
        missed = [plain(item) for item in events if str(item.status) == "missed"]
        skills = self._safe(lambda: self.runtime.skills_status(), {}) or {}
        issues = plain(skills.get("issues") or [])
        return {"receipts": failed, "proactive": missed, "skills": issues}

    def _memories(self):
        rows = self._safe(lambda: self.runtime.memories(), [])
        return rows or []

    def _task_row(self, task):
        card = getattr(task, "card", None)
        receipt = self._safe(lambda: self.runtime.work_receipt(task.task_id), None)
        return {
            "task_id": task.task_id,
            "state": task.state,
            "state_label": STATE_LABELS.get(task.state, task.state),
            "title": getattr(card, "title", ""),
            "reason": getattr(card, "reason", ""),
            "summary": getattr(card, "spoken_summary", ""),
            "goal": getattr(card, "goal", ""),
            "executor": getattr(card, "executor", ""),
            "project": getattr(card, "project", ""),
            "approval_status": task.approval_status,
            "approval_reason": task.approval_reason,
            "expires_at": task.expires_at,
            "revision": task.revision,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "receipt": (
                {
                    "status": receipt.status,
                    "summary": receipt.summary,
                    "attempts": receipt.attempts,
                }
                if receipt is not None
                else None
            ),
        }

    # -- commands ------------------------------------------------------

    def run_turn(self, text, mode=None):
        run = self.service.begin_turn(text, mode)

        def worker():
            try:
                for event in self.service.stream_turn(run):
                    self.broadcast({"type": "turn", "event": event.as_dict()})
            except Exception as error:  # surfaced in the chat as a failed run
                self.broadcast(
                    {
                        "type": "turn",
                        "event": {
                            "kind": "error",
                            "run_id": run.run_id,
                            "session_id": run.session_id,
                            "message": str(error),
                            "stage": "failed",
                        },
                    }
                )
            finally:
                self.broadcast({"type": "refresh"})

        threading.Thread(target=worker, name="companion-turn", daemon=True).start()
        return run

    def _safe(self, function, default):
        try:
            return function()
        except Exception:
            return default


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CompanionWeb/1.0"

    # quiet logging; the shell keeps its own log
    def log_message(self, format, *args):
        return

    @property
    def adapter(self) -> WebAdapter:
        return self.server.adapter

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            if not self._authorized(parsed):
                return self._json(403, {"error": "访问令牌无效。"})
            return self._api_get(path, parse_qs(parsed.query))
        if path in PAGES:
            return self._asset(PAGES[path])
        if path.startswith("/assets/"):
            return self._asset(path[len("/assets/") :], base=ASSETS)
        return self._json(404, {"error": "未找到。"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/"):
            return self._json(404, {"error": "未找到。"})
        if not self._authorized(parsed):
            return self._json(403, {"error": "访问令牌无效。"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "请求内容不是合法 JSON。"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "请求内容必须是对象。"})
        return self._api_post(path, body)

    # -- routing -------------------------------------------------------

    def _api_get(self, path, query):
        adapter = self.adapter
        if path == "/api/bootstrap":
            return self._json(200, adapter.bootstrap())
        if path == "/api/events":
            return self._stream()
        if path == "/api/overlay":
            return self._json(200, adapter.overlay())
        if path == "/api/console/memory":
            memory_id = (query.get("id") or [""])[0]
            if memory_id:
                details = adapter._safe(
                    lambda: adapter.runtime.memory_details(memory_id), {}
                )
                return self._json(200, plain(details) or {})
            return self._json(200, {"rows": [plain(item) for item in adapter._memories()]})
        match = re.match(r"^/api/console/([a-z]+)$", path)
        if match:
            return self._json(200, adapter.console_page(match.group(1)))
        return self._json(404, {"error": "未找到。"})

    def _api_post(self, path, body):
        adapter = self.adapter
        service = adapter.service
        try:
            if path == "/api/activate":
                if adapter.activate():
                    return self._json(200, {"shown": True})
                return self._json(200, {"shown": False, "url": adapter.url})
            if path == "/api/turn":
                run = adapter.run_turn(body.get("text"), body.get("mode"))
                return self._json(202, {"run_id": run.run_id})
            if path == "/api/runs/cancel":
                return self._json(200, {"cancelled": service.request_cancel(body.get("run_id"))})
            if path == "/api/mode":
                service.set_mode(body.get("mode") or "chat")
                adapter.broadcast({"type": "refresh"})
                return self._json(200, {"mode": service.current_mode().value})
            if path == "/api/workspace":
                if body.get("clear"):
                    service.clear_workspace()
                    root = ""
                else:
                    root = service.select_workspace(str(body.get("path") or ""))
                adapter.broadcast({"type": "refresh"})
                return self._json(200, {"workspace": root or ""})
            if path == "/api/draft":
                service.save_draft(service.current_session_id, str(body.get("content") or ""))
                return self._json(200, {"saved": True})
            if path == "/api/ui":
                return self._json(200, adapter.update_ui(body))
            if path == "/api/persona":
                content = service.runtime.set_persona_preference(
                    str(body.get("field") or ""), body.get("content")
                )
                adapter.broadcast({"type": "refresh"})
                return self._json(200, {"content": content})
            if path == "/api/approvals/confirm":
                result = service.confirm_action(body.get("action_id"), body.get("mode"))
                adapter.broadcast({"type": "refresh"})
                return self._json(200, plain(result) or {})
            if path == "/api/approvals/cancel":
                result = service.cancel_action(body.get("action_id"))
                adapter.broadcast({"type": "refresh"})
                return self._json(200, plain(result) or {})
            if path == "/api/receipts/undo":
                result = service.undo_receipt(body.get("receipt_id"))
                adapter.broadcast({"type": "refresh"})
                return self._json(200, plain(result) or {})
            if path.startswith("/api/sessions/"):
                return self._session_action(path.rsplit("/", 1)[-1], body)
            if path == "/api/proactive":
                return self._proactive(body)
            if path == "/api/console/memory":
                return self._memory_action(body)
            if path == "/api/console/tasks":
                return self._task_action(body)
            if path == "/api/console/affect":
                mood = str(body.get("mood") or "")
                adapter.runtime.correct_affect(MOOD_KEYS.get(mood, mood or "neutral"))
                adapter.broadcast({"type": "refresh"})
                return self._json(200, adapter._affect_view())
        except Exception as error:
            return self._json(409, {"error": str(error)})
        return self._json(404, {"error": "未找到。"})

    def _session_action(self, action, body):
        service = self.adapter.service
        session_id = str(body.get("session_id") or "")
        changed = False
        if action == "switch":
            changed = service.switch_session(session_id)
        elif action == "create":
            session_id = service.create_session(body.get("mode"))
            changed = service.switch_session(session_id)
        elif action == "rename":
            changed = service.rename_session(session_id, str(body.get("title") or ""))
        elif action == "pin":
            changed = service.set_session_pinned(session_id, bool(body.get("pinned", True)))
        elif action == "delete":
            changed = service.delete_session(session_id)
        else:
            return self._json(404, {"error": "未找到。"})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(200, {"changed": changed, "session_id": self.adapter.service.current_session_id})

    def _proactive(self, body):
        runtime = self.adapter.runtime
        action = str(body.get("action") or "")
        if action == "tick":
            result = runtime.proactive_tick()
        elif action == "read":
            result = {"marked": runtime.mark_proactive_read(body.get("event_id"))}
        elif action == "settings":
            values = {
                key: body[key]
                for key in (
                    "enabled",
                    "quiet_start",
                    "quiet_end",
                    "daily_limit",
                    "min_interval_hours",
                    "max_unanswered",
                    "include_memory",
                )
                if key in body
            }
            result = runtime.update_proactive_settings(**values)
        else:
            return self._json(404, {"error": "未找到。"})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(200, plain(result) or {})

    def _memory_action(self, body):
        runtime = self.adapter.runtime
        action = str(body.get("action") or "")
        memory_id = str(body.get("memory_id") or "")
        if action == "correct":
            runtime.correct_memory(memory_id, str(body.get("replacement") or ""))
        elif action == "forget":
            runtime.forget_memory(memory_id)
        elif action == "protect":
            runtime.set_memory_protected(memory_id, bool(body.get("protected", True)))
        elif action == "purge":
            from ..maintenance import purge_memory

            purge_memory(runtime.store, memory_id)
        elif action == "details":
            return self._json(200, plain(runtime.memory_details(memory_id)) or {})
        else:
            return self._json(404, {"error": "未找到。"})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(200, {"ok": True, "rows": [plain(item) for item in self.adapter._memories()]})

    def _task_action(self, body):
        runtime = self.adapter.runtime
        action = str(body.get("action") or "")
        task_id = str(body.get("task_id") or "")
        if action == "approve":
            result = runtime.approve_work_task(task_id)
        elif action == "revise":
            result = runtime.revise_work_task(task_id, str(body.get("instruction") or ""))
        elif action == "reject":
            result = runtime.reject_work_task(task_id, str(body.get("reason") or ""))
        else:
            return self._json(404, {"error": "未找到。"})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(200, plain(result) or {})

    # -- plumbing ------------------------------------------------------

    def _authorized(self, parsed):
        supplied = (parse_qs(parsed.query).get("t") or [""])[0]
        if not supplied:
            supplied = self.headers.get("X-Companion-Token") or ""
        return secrets.compare_digest(supplied, self.adapter.token)

    def _asset(self, name, base=ASSETS):
        target = (base / name).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            return self._json(403, {"error": "路径无效。"})
        if not target.is_file():
            return self._json(404, {"error": "未找到。"})
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _write_chunk(self, payload):
        self.wfile.write(b"%X\r\n" % len(payload) + payload + b"\r\n")
        self.wfile.flush()

    def _stream(self):
        channel = self.adapter.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            self._write_chunk(b"retry: 1000\n\n")
            while not self.adapter._closed:
                try:
                    data = channel.get(timeout=15)
                except queue.Empty:
                    data = None
                chunk = (
                    b": ping\n\n"
                    if data is None
                    else ("data: {}\n\n".format(data)).encode("utf-8")
                )
                self._write_chunk(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass
            self.adapter.unsubscribe(channel)
