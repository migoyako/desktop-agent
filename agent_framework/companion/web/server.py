"""Local HTTP + SSE adapter exposing the application service to the web UI."""

from __future__ import annotations

import base64
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
from urllib.parse import parse_qs, quote, unquote, urlparse

from ...config import AgentConfig, save_config as save_key_values
from .. import settings as app_settings
from ..app.projection import build_context_view
from ..assets import MAX_AVATAR_BYTES, AssetImportError
from ..documents import DocumentError, MAX_DOC_BYTES, MAX_LIBRARY_BYTES
from ..models import EXECUTOR_MCP, AgentMode, ProposalStatus, RiskLevel
from ..policy import (
    PERMISSION_HINTS,
    PERMISSION_LABELS,
    PERMISSION_TIERS,
    mode_label,
    resolve_permission_tier,
)
from ..mcp.console import McpConsoleError
from ..mcp.proposal import REMOTE_WARNING
from ..redaction import redact_text
from ..store import SCHEMA_VERSION
from ..tokens import CHARS_PER_TOKEN
from . import upstream
from ..ui.state import UiStateStore
from ..work import STATE_LABELS

ASSETS = Path(__file__).resolve().parent / "assets"

# The body used to be read straight off Content-Length with no ceiling, so any
# local process could make this loopback server buffer a request of its choice.
# The ceiling has to sit *above* the largest payload a handler refuses on its own
# terms: the avatar handler rejects 16 MiB of base64 (an 8 MiB file inflated ~1.33x,
# plus JSON) with the copy "单个头像不能超过 8 MiB", and closing the socket before
# that message can be delivered would turn a readable refusal into a network error.
# 24 MiB still bounds the read; every handler checks its own field before disk.
MAX_REQUEST_BYTES = 24 * 1024 * 1024

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

# Labels for the stored output_format keys. The keys themselves live in
# settings.FIELDS, so a new choice shows up in the page as its own id until a
# label is added here — OUTPUT_FORMAT_CHOICES falls back to the raw key.
OUTPUT_FORMAT_LABELS = {
    "default": "跟随人设",
    "concise": "简短直接",
    "bullets": "要点列表",
    "detailed": "详细说明",
}

OUTPUT_FORMAT_NOTES = {
    "default": "不额外加要求，按人设与当前模式的语气回答。",
}


def output_format_choices():
    """Value, label and the literal line the model is told for each choice."""
    spec = app_settings.BY_KEY["output_format"]
    return [
        {
            "value": key,
            "label": OUTPUT_FORMAT_LABELS.get(key, key),
            "hint": OUTPUT_FORMAT_NOTES.get(key) or "提示词里会加一句：「{}」".format(
                app_settings.OUTPUT_FORMATS.get(key, "")
            ),
        }
        for key in spec.choices
    ]


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
        # The overlay page reports whether WebGL actually came up. Kept in
        # memory only: a restart may find a working GPU again.
        self._overlay_runtime = {"ok": True, "reason": ""}

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
        if "background_enabled" in body:
            state.set_background_enabled(body.get("background_enabled"))
        if "inspector_visible" in body:
            state.set_inspector_visible(body.get("inspector_visible"))
        if "inspector_width" in body:
            state.set_inspector_width(body.get("inspector_width"))
        if "overlay_visible" in body:
            state.set_overlay_visible(body.get("overlay_visible"))
        if "overlay_collapsed" in body:
            state.set_overlay_collapsed(body.get("overlay_collapsed"))
        result = {
            "theme": state.theme(),
            "font_scale": state.font_scale(),
            "reduce_motion": state.reduce_motion(),
            "background_enabled": state.background_enabled(),
            "inspector_visible": state.inspector_visible(),
            "inspector_width": state.inspector_width(),
            "overlay_visible": state.overlay_visible(),
            "overlay_collapsed": state.overlay_collapsed(),
        }
        self.broadcast({"type": "ui", "ui": result})
        return result

    # -- application settings ------------------------------------------

    def settings_payload(self):
        """Stored settings plus the masked key and what the runtime is doing now."""
        runtime = self.runtime
        values = self._safe(lambda: runtime.store.load_settings(), None)
        if values is None:
            values = app_settings.defaults()
        config = self._safe(lambda: AgentConfig.from_env(), None)
        key = str(getattr(config, "api_key", "") or "")
        effective_offline = bool(getattr(runtime, "offline", values["offline"]))
        return {
            "settings": values,
            "api_key": {
                "present": bool(key),
                "masked": app_settings.mask_secret(key),
                "source": "config/keys.json",
            },
            "runtime": {
                "model": str(getattr(runtime, "settings", {}).get("model") or values["model"]),
                "offline": effective_offline,
                # --offline on the command line outranks the stored value, and
                # the page has to say so instead of showing a stale toggle.
                "forced_offline": effective_offline and not bool(values["offline"]),
                "remote_available": bool(
                    self._safe(lambda: runtime.llm_client is not None and not runtime.offline, False)
                ),
                "context_chars": int(getattr(runtime, "max_context_chars", 0) or 0),
                # The panel's "not yet sent" preview has to convert max_tokens the
                # same way the budget does, so the rate travels with the numbers.
                "chars_per_token": CHARS_PER_TOKEN,
                "max_tool_rounds": int(getattr(runtime, "max_tool_rounds", 0) or 0),
            },
            "groups": app_settings.groups(),
            "model_service": self._model_service_view(values, config),
            "permissions": self.permission_view(),
            "output_formats": output_format_choices(),
            "diagnostics": self.diagnostics(),
            # The budget bar in the model and RAG panels reads this single
            # derivation; recomputing bands here would let the settings window
            # disagree with the composer gauge in the main window.
            "context": plain(self._safe(lambda: build_context_view(runtime), None)),
        }

    def documents_payload(self):
        """文档库现状：条目、占用与词法索引是否可用（M16 切片 1，界面仍未接）。"""
        runtime = self.runtime
        rows = self._safe(lambda: runtime.list_documents(), [])
        summary = self._safe(
            lambda: runtime.document_summary(),
            {"count": 0, "bytes": 0, "limit_bytes": MAX_LIBRARY_BYTES, "fts_enabled": False},
        )
        return {
            "rows": [self._document_row(row) for row in rows],
            "summary": summary,
            "max_file_bytes": MAX_DOC_BYTES,
            "max_library_bytes": MAX_LIBRARY_BYTES,
            # 切片 2 已把检索接进上下文层与来源通道，但面板去灰显仍按 R5 等到切片 4：
            # 后端可用与界面已接线是两件事，这里说的仍是后者。
            "connected": False,
        }

    def mcp_payload(self):
        """远程工具服务器面板（A9）：地址、开关与掩码后的凭据（Q2）。

        凭据的值从不离开 :mod:`companion.mcp.keys` —— 这里只回名称与
        ``mask_secret()`` 形状，页面、审计与数据库备份拿到的都是同一份脱敏结果。
        """
        console = getattr(self.runtime, "mcp_console", None)
        if console is None:
            return {
                "rows": [],
                "summary": {
                    "servers": 0,
                    "connected": 0,
                    "tools": 0,
                    "enabled_tools": 0,
                },
                "transports": [],
                "issues": ["这个版本没有远程工具面板。"],
                "keys": {},
                "limits": {},
                "connected_ui": False,
                "error": "",
            }
        try:
            panel = console.panel()
        except Exception as error:
            # 面板读不出来时也要给页面一个能画的空表，而不是一片空白加一个 500。
            return {
                "rows": [],
                "summary": {
                    "servers": 0,
                    "connected": 0,
                    "tools": 0,
                    "enabled_tools": 0,
                },
                "transports": [],
                "issues": [],
                "keys": {},
                "limits": {},
                "connected_ui": False,
                "error": redact_text("服务器列表读不出来：{}".format(error)),
            }
        # --offline 时不会去连任何服务器，面板要说清这是离线策略而不是连接故障。
        panel["offline"] = bool(getattr(self.runtime, "offline", False))
        return panel

    def remote_cards(self, limit: int = 3):
        """S04f 的另两态：本会话最近处理完的远程调用（已执行回执 / 被档位拒绝）。

        待确认的那张来自 approvals；确认之后提案就离开待确认清单，聊天流里要留下
        回执只能另开一条只读通道。这里不给任何写路径：外部调用的「撤销」永远只是
        解释为什么撤不回（K3）。
        """

        def read():
            rows = self.runtime.store.list_proposals(
                self.runtime.session_id,
                statuses=(
                    ProposalStatus.COMPLETED.value,
                    ProposalStatus.NOT_EXECUTED.value,
                    ProposalStatus.FAILED.value,
                    ProposalStatus.UNKNOWN.value,
                ),
                limit=20,
            )
            cards = []
            for proposal in rows or []:
                if str(proposal.executor or "") != EXECUTOR_MCP:
                    continue
                card = plain(self.service.approval_view(proposal.action_id).as_dict())
                receipt = self.runtime.store.get_receipt_for_action(proposal.action_id)
                card["settled"] = True
                card["receipt"] = (
                    None
                    if receipt is None
                    else {
                        "receipt_id": receipt.receipt_id,
                        "status": str(receipt.status),
                        "summary": redact_text(str(receipt.summary or "")),
                        # 被档位拦下的那张卡要说清原因，原因只有回执自己知道。
                        "error": redact_text(str(receipt.error or "")),
                        "undoable": bool(receipt.undoable),
                        "at": str(receipt.finished_at or receipt.created_at or ""),
                    }
                )
                cards.append(card)
                if len(cards) >= max(1, int(limit)):
                    break
            return cards

        return self._safe(read, []) or []

    @staticmethod
    def _document_row(row):
        return {
            "document_id": str(row.get("document_id") or ""),
            "name": str(row.get("name") or ""),
            "kind": str(row.get("kind") or ""),
            "bytes": int(row.get("bytes") or 0),
            "chunks": int(row.get("chunks") or 0),
            "chars": int(row.get("chars") or 0),
            "converted": bool(row.get("converted")),
            "created_at": str(row.get("created_at") or ""),
        }

    def _model_service_view(self, values, config):
        """What the 模型服务接入 dialog needs: endpoint, masked key, window table."""
        model = str(values.get("model") or "")
        return {
            "base_url": str(getattr(config, "base_url", "") or ""),
            "catalog": dict(app_settings.MODEL_WINDOWS),
            "stored": dict(values.get("model_contexts") or {}),
            "current": {
                "model": model,
                "catalog_window": app_settings.catalog_window(model),
                "context_tokens": int(values.get("context_tokens") or 0),
            },
        }

    def _endpoint_matches(self, candidate, configured):
        try:
            return upstream.models_url(candidate) == upstream.models_url(configured)
        except ValueError:
            return False

    def permission_view(self):
        """The three user-facing permission tiers and where the runtime stands."""
        current = resolve_permission_tier(
            getattr(self.runtime, "tool_permission", None)
        )
        return {
            "current": current,
            "tiers": [
                {
                    "value": tier,
                    "label": PERMISSION_LABELS[tier],
                    "hint": PERMISSION_HINTS[tier],
                }
                for tier in PERMISSION_TIERS
            ],
        }

    def diagnostics(self):
        """What the 高级 panel can show as fact instead of a log it does not have."""
        from ..maintenance import list_backups, lock_info

        data_dir = Path(getattr(self.runtime, "data_dir", ".")).resolve()
        owner = self._safe(lambda: lock_info(data_dir), None) or {}
        backups = self._safe(lambda: list_backups(data_dir), []) or []
        version = self._safe(lambda: self.runtime.store.schema_version(), 0) or 0
        return {
            "data_dir": str(data_dir),
            "database": str(data_dir / "companion.db"),
            "schema_version": int(version),
            "expected_schema_version": SCHEMA_VERSION,
            "locked_by": owner.get("pid"),
            "lock_file": str(owner.get("lock_file") or ""),
            "backups": len(backups),
            "latest_backup": str(backups[0].get("name") or "") if backups else "",
        }

    def fetch_models(self, body):
        """Ask the upstream for its model list; the key stays in this process."""
        config = self._safe(lambda: AgentConfig.from_env(), None) or AgentConfig()
        stored_url = str(getattr(config, "base_url", "") or "")
        base_url = str(body.get("base_url") or "").strip() or stored_url
        supplied = str(body.get("api_key") or "").strip()
        if supplied:
            api_key = supplied
        elif self._endpoint_matches(base_url, stored_url):
            # Only the endpoint that the key belongs to may borrow it.
            api_key = str(getattr(config, "api_key", "") or "")
        else:
            raise ValueError("这个地址没有对应的密钥，请把 API Key 一起填上。")
        if not api_key:
            raise ValueError("还没有填写 API Key。")
        stored = self._safe(lambda: self.runtime.store.load_settings(), None) or {}
        windows = dict(stored.get("model_contexts") or {})
        models = []
        for item in upstream.fetch_models(base_url, api_key):
            name = item["id"]
            if name in windows:
                window, source = int(windows[name]), "stored"
            else:
                window = app_settings.catalog_window(name)
                source = "catalog" if window else "manual"
            models.append({"id": name, "context_tokens": window, "source": source})
        return {"models": models, "base_url": base_url}

    def save_model_service(self, body):
        """Write the credential file, then the settings row, then the runtime."""
        active = self._safe(lambda: self.service.active_run(), None)
        if active is not None:
            raise ValueError("这一轮还在进行，先停止再改设置。")
        base_url = str(body.get("base_url") or "").strip()
        model = str(body.get("model") or "").strip()
        api_key = str(body.get("api_key") or "").strip()
        values = {"OPENAI_BASE_URL": base_url}
        if model:
            # No row selected is not "clear the model": the settings row keeps
            # its own value and the file must not contradict it on restart.
            values["MODEL_NAME"] = model
        if api_key:
            # An empty key means "leave what is on disk alone", not "erase it":
            # the dialog only ever shows the masked form.
            values["OPENAI_API_KEY"] = api_key
        save_key_values(values)
        store = self.runtime.store
        current = self._safe(lambda: store.load_settings(), None) or app_settings.defaults()
        patch = {"base_url": base_url}
        if model:
            patch["model"] = model
        if "context_tokens" in body:
            patch["context_tokens"] = body["context_tokens"]
        if isinstance(body.get("model_contexts"), dict):
            merged_map = dict(current.get("model_contexts") or {})
            # The dialog submits only the rows the user touched, so windows
            # typed in an earlier session survive a later save.
            merged_map.update(body["model_contexts"])
            patch["model_contexts"] = merged_map
        merged, errors = app_settings.validate_update(current, patch)
        saved = store.save_settings(merged)
        self.runtime.reload_config()
        self._safe(lambda: self.runtime.apply_settings(saved), None)
        payload = self.settings_payload()
        payload["errors"] = errors
        self.broadcast({"type": "settings", "settings": payload["settings"]})
        return payload

    def update_settings(self, body):
        active = self._safe(lambda: self.service.active_run(), None)
        if active is not None:
            raise ValueError("这一轮还在进行，先停止再改设置。")
        patch = body.get("settings")
        if not isinstance(patch, dict):
            patch = {
                key: value
                for key, value in body.items()
                if key not in ("action", "settings")
            }
        store = self.runtime.store
        current = self._safe(lambda: store.load_settings(), None) or app_settings.defaults()
        merged, errors = app_settings.validate_update(current, patch)
        saved = store.save_settings(merged)
        self._safe(lambda: self.runtime.apply_settings(saved), None)
        payload = self.settings_payload()
        payload["errors"] = errors
        self.broadcast({"type": "settings", "settings": payload["settings"]})
        return payload

    # -- payloads ------------------------------------------------------

    def bootstrap(self):
        service = self.service
        projection = service.ui_projection()
        persona = self._safe(lambda: self.runtime.persona_info(), {})
        ui = {
            "theme": self.ui_state.theme(),
            "font_scale": self.ui_state.font_scale(),
            "reduce_motion": self.ui_state.reduce_motion(),
            "background_enabled": self.ui_state.background_enabled(),
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
            "assets": self.assets(),
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
            "message_actions": plain(projection.message_actions),
            "approvals": self._approval_rows(projection.approvals),
            "remote_cards": self.remote_cards(),
            # S04f 的「档位：保守」说的是当前档位：被档位拦下的那条提案没有出网，
            # 芯片要告诉用户现在这一档是什么，而不是复刻拒绝时的历史值。
            "permission_label": PERMISSION_LABELS[
                resolve_permission_tier(getattr(self.runtime, "tool_permission", None))
            ],
            "tasks": [
                self._task_row(task)
                for task in self._safe(lambda: self.runtime.work_tasks(20), []) or []
            ],
            "inspector": [
                {"title": section.title, "lines": list(section.lines)}
                for section in projection.inspector
            ],
            # 来源通道只有一个形状：本机文档命中在前、网络命中在后。
            "sources": plain(projection.sources),
            "status": self._safe(lambda: self.runtime.desktop_status(), {}),
            "affect": self._affect_view(),
            "proactive": self._safe(lambda: self.runtime.proactive_status(), {}),
            # The overlay bubble is one delivery path; the main window keeps
            # the same events readable when the pet is unavailable.
            "proactive_events": [
                {
                    "event_id": item.event_id,
                    "text": clip(item.content, 200),
                    "at": item.created_at,
                    "read": item.read_at is not None,
                    "status": item.status,
                }
                for item in self._safe(
                    lambda: self.runtime.proactive_events(12), []
                ) or []
            ],
            "skills": self._safe(lambda: self.runtime.skills_status(), {}),
            "ui": ui,
            "draft": self._safe(lambda: service.draft(), ""),
            "settings_summary": self.settings_summary(),
        }

    def _approval_rows(self, items):
        """待确认卡片的外形只有一个来源：提案自己带的那句外部调用警语（K3）。"""
        rows = []
        for item in items:
            row = item.as_dict()
            if str(row.get("risk") or "") == RiskLevel.EXTERNAL.value:
                row["warning"] = REMOTE_WARNING
            rows.append(plain(row))
        return rows

    def assets(self):
        payload = self._safe(lambda: self.runtime.character_assets(), {}) or {}
        result = {"avatar": payload.get("avatar"), "background": payload.get("background")}
        for slot in ("avatar", "background"):
            item = result[slot]
            if isinstance(item, dict):
                result[slot] = dict(item, url="/api/asset/{}".format(slot))
        live2d = payload.get("live2d")
        valid = isinstance(live2d, dict) and bool(live2d.get("valid"))
        runtime = self.overlay_runtime()
        reason = ""
        if not isinstance(live2d, dict):
            live2d = {
                "size": 0,
                "valid": False,
                "expressions": [],
                "motions": [],
                "motion_groups": {},
            }
            reason = "缺少 live2d/model3.json（也支持 live2d/*.model3.json）"
        elif not valid:
            reason = str(live2d.get("reason") or "Live2D 模型无效")
        elif not runtime["ok"]:
            reason = runtime["reason"] or "Live2D 运行环境不可用"
        live2d = dict(live2d, available=valid and runtime["ok"], reason=reason)
        if valid:
            relative = str(live2d.get("model_path") or "model3.json")
            url = "/api/live2d/{}/{}".format(
                quote(self.token, safe=""),
                quote(relative, safe="/"),
            )
            live2d["model_url"] = "{}?v={}".format(url, int(live2d.get("stamp") or 0))
        result["live2d"] = live2d

        mesh = payload.get("mesh")
        mesh_valid = isinstance(mesh, dict) and bool(mesh.get("valid"))
        mesh_reason = ""
        if not isinstance(mesh, dict):
            mesh = {"size": 0, "valid": False, "expressions": []}
            mesh_reason = "缺少 avatar.mavatar"
        elif not mesh_valid:
            mesh_reason = str(mesh.get("reason") or ".mavatar 无效")
        elif not runtime["ok"]:
            mesh_reason = runtime["reason"] or "mesh 运行环境不可用"
        mesh = dict(mesh, available=mesh_valid and runtime["ok"], reason=mesh_reason)
        if mesh_valid:
            mesh["url"] = "/api/asset/mesh?v={}".format(int(mesh.get("stamp") or 0))
        result["mesh"] = mesh

        # One renderer per pages load: Live2D when its model file exists, else
        # .mavatar. A broken Live2D model never silently becomes a mesh pet.
        if isinstance(payload.get("live2d"), dict):
            result["renderer"] = "live2d"
            result["pet"] = dict(live2d, renderer="live2d")
        elif isinstance(payload.get("mesh"), dict):
            result["renderer"] = "mesh"
            result["pet"] = dict(mesh, renderer="mesh")
        else:
            result["renderer"] = ""
            result["pet"] = {
                "available": False,
                "reason": "缺少 live2d 模型或 avatar.mavatar",
                "renderer": "",
            }
        return result

    def overlay_runtime(self):
        return dict(self._overlay_runtime)

    def report_overlay_runtime(self, ok, reason=""):
        ok = bool(ok)
        reason = "" if ok else clip(reason or "桌面形象无法启动。", 160)
        current = self._overlay_runtime
        self._overlay_runtime = {"ok": ok, "reason": reason}
        if (ok, reason) != (current["ok"], current["reason"]):
            self.broadcast({"type": "ui"})
        return self.overlay_runtime()

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
        assets = self.assets()
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
            "reduce_motion": self.ui_state.reduce_motion(),
            "collapsed": self.ui_state.overlay_collapsed(),
            "renderer": assets.get("renderer"),
            "pet": assets.get("pet"),
            "pending": len(self._safe(lambda: self.runtime.pending_proposals(), []) or []),
            "proactive": self._overlay_proactive(),
            "motion_events": self._safe(
                lambda: self.runtime.pending_motion_events(3), []
            )
            or [],
        }

    def _overlay_proactive(self):
        """Unread messages the overlay may speak; newest first, the page reverses."""
        status = self._safe(lambda: self.runtime.proactive_status(), {}) or {}
        enabled = bool(status.get("enabled"))
        events = (
            self._safe(lambda: self.runtime.unread_proactive_events(3), []) or []
            if enabled
            else []
        )
        return {
            "enabled": enabled,
            "unread": [
                {
                    "id": event.event_id,
                    "text": clip(event.content, 160),
                    "at": event.created_at,
                }
                for event in events
            ],
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
        return self._start_run(self.service.begin_turn(text, mode))

    def regenerate(self, message_id):
        return self._start_run(self.service.regenerate(message_id))

    def _start_run(self, run):
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
        # Model files are fetched with relative URLs by the Live2D loader, so
        # the token has to travel in the path; the route checks it itself.
        if path.startswith("/api/live2d/"):
            return self._live2d_file(path)
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
        declared = str(self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(declared)
        except ValueError:
            return self._json(400, {"error": "Content-Length 不合法。"})
        if length < 0 or length > MAX_REQUEST_BYTES:
            return self._refuse_body("请求体超过上限，已拒绝读取。")
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
        if path == "/api/settings":
            return self._json(200, adapter.settings_payload())
        if path == "/api/docs":
            return self._json(200, adapter.documents_payload())
        if path == "/api/mcp":
            return self._json(200, plain(adapter.mcp_payload()) or {})
        if path == "/api/console/memory":
            memory_id = (query.get("id") or [""])[0]
            if memory_id:
                details = adapter._safe(
                    lambda: adapter.runtime.memory_details(memory_id), {}
                )
                return self._json(200, plain(details) or {})
            return self._json(200, {"rows": [plain(item) for item in adapter._memories()]})
        match = re.match(r"^/api/asset/([a-z]+)$", path)
        if match:
            return self._character_asset(match.group(1))
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
            if path == "/api/messages/regenerate":
                run = adapter.regenerate(body.get("message_id"))
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
            if path == "/api/settings":
                return self._json(200, adapter.update_settings(body))
            if path == "/api/models/fetch":
                return self._json(200, adapter.fetch_models(body))
            if path == "/api/model_service":
                return self._json(200, adapter.save_model_service(body))
            if path == "/api/overlay/runtime":
                ok = body.get("ok")
                if not isinstance(ok, bool):
                    return self._json(400, {"error": "ok 必须是布尔值。"})
                return self._json(
                    200, adapter.report_overlay_runtime(ok, str(body.get("reason") or ""))
                )
            if path == "/api/overlay/motion":
                removed = adapter._safe(
                    lambda: adapter.runtime.mark_motion_read(body.get("event_id")), 0
                )
                return self._json(200, {"removed": removed})
            if path == "/api/persona":
                content = service.runtime.set_persona_preference(
                    str(body.get("field") or ""), body.get("content")
                )
                adapter.broadcast({"type": "refresh"})
                return self._json(200, {"content": content})
            if path == "/api/asset/avatar":
                return self._avatar_import(body)
            if path == "/api/docs/import":
                return self._document_import(body)
            if path == "/api/docs/delete":
                return self._document_delete(body)
            if path == "/api/mcp":
                return self._mcp_action(body)
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

    def _avatar_import(self, body):
        """Copy an uploaded picture into this persona's avatar slot."""
        raw = str(body.get("data") or "").strip()
        if raw.lower().startswith("data:") and "," in raw[:128]:
            raw = raw.split(",", 1)[1]
        if not raw:
            return self._json(400, {"error": "没有收到头像内容。"})
        if len(raw) > MAX_AVATAR_BYTES * 2:
            return self._json(400, {"error": "头像超过 8 MiB，请先压缩。"})
        try:
            payload = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            return self._json(400, {"error": "头像内容不是有效的图片数据。"})
        try:
            result = self.adapter.runtime.store_avatar(
                str(body.get("filename") or ""), payload
            )
        except AssetImportError as error:
            return self._json(400, {"error": str(error)})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(200, plain(result) or {})

    def _document_import(self, body):
        """Hand one uploaded document to the library (M16 切片 1).

        The page sends base64 for the same reason the avatar does: the browser
        never learns a path, and the server re-checks type, size and encoding
        before anything touches disk.
        """
        raw = str(body.get("data") or "").strip()
        if raw.lower().startswith("data:") and "," in raw[:128]:
            raw = raw.split(",", 1)[1]
        if not raw:
            return self._json(400, {"error": "没有收到文档内容。"})
        if len(raw) > MAX_DOC_BYTES * 2:
            return self._json(400, {"error": "单个文档不能超过 2 MiB，请先拆分。"})
        try:
            payload = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            return self._json(400, {"error": "文档内容不是有效的文件数据。"})
        try:
            record = self.adapter.runtime.import_document(
                str(body.get("filename") or ""), payload
            )
        except DocumentError as error:
            return self._json(400, {"error": str(error)})
        self.adapter.broadcast({"type": "refresh"})
        return self._json(
            200,
            {
                "document": self.adapter._document_row(record),
                "summary": self.adapter._safe(
                    self.adapter.runtime.document_summary,
                    {"count": 0, "bytes": 0},
                ),
            },
        )

    def _document_delete(self, body):
        document_id = str(body.get("document_id") or "").strip()
        if not document_id:
            return self._json(400, {"error": "缺少文档 ID。"})
        result = self.adapter.runtime.delete_document(document_id)
        self.adapter.broadcast({"type": "refresh"})
        return self._json(
            200,
            {
                "removed": bool(result.get("removed")),
                "chunks": int(result.get("chunks") or 0),
                "files": int(result.get("files") or 0),
                "summary": self.adapter._safe(
                    self.adapter.runtime.document_summary,
                    {"count": 0, "bytes": 0},
                ),
            },
        )

    def _mcp_action(self, body):
        """服务器与逐工具开关的写端点（A9、Q2）。

        Every answer is the panel re-read afterwards, so the page never has to
        guess what an edit did; the credential names and masks are all it can get.
        """
        console = getattr(self.adapter.runtime, "mcp_console", None)
        if console is None:
            return self._json(409, {"error": "这个版本没有远程工具面板。"})
        action = str(body.get("action") or "")
        server_id = str(body.get("server_id") or "")
        try:
            if action == "panel":
                result = console.panel()
            elif action == "save":
                record = body.get("record")
                result = console.save(record if isinstance(record, dict) else body)
            elif action == "validate":
                record = body.get("record")
                result = console.validate(record if isinstance(record, dict) else body)
            elif action == "enable":
                result = console.set_enabled(server_id, bool(body.get("enabled")))
            elif action == "tools":
                enable = body.get("enable")
                result = console.set_tools(
                    server_id,
                    body.get("names") or body.get("tools") or [],
                    None if enable is None else bool(enable),
                )
            elif action == "all_tools":
                result = console.set_all_tools(server_id, bool(body.get("enabled")))
            elif action == "connect":
                result = console.connect(server_id)
            elif action == "disconnect":
                result = console.disconnect(server_id)
            elif action == "refresh":
                result = console.refresh(server_id)
            elif action == "credentials":
                result = console.set_credentials(
                    server_id, body.get("env"), body.get("headers")
                )
            elif action == "clear_credentials":
                result = console.clear_credentials(server_id)
            elif action == "credential":
                result = console.set_credential(
                    server_id,
                    body.get("name"),
                    body.get("value"),
                    bool(body.get("remove")),
                )
            elif action == "reload_all":
                result = console.reload_all()
            elif action == "remove":
                result = console.remove(server_id)
            else:
                return self._json(404, {"error": "未找到。"})
        except McpConsoleError as error:
            # 一句能直接显示的话：面板把它画成行内提示，不是一段堆栈。
            return self._json(400, {"error": error.reason, "code": error.code})
        except Exception as error:
            return self._json(
                400, {"error": redact_text("远程工具操作没有完成：{}".format(error))}
            )
        if action != "panel":
            self.adapter.broadcast({"type": "refresh"})
        return self._json(
            200,
            plain({"ok": True, "action": action, "result": result})
            or {"ok": True, "action": action},
        )

    def _memory_action(self, body):
        runtime = self.adapter.runtime
        action = str(body.get("action") or "")
        memory_id = str(body.get("memory_id") or "")
        if action == "remember":
            result = self.adapter.service.remember_message(body.get("message_id"))
            self.adapter.broadcast({"type": "refresh"})
            return self._json(
                200,
                {
                    "ok": True,
                    "memory_id": result.get("memory_id", ""),
                    "created": bool(result.get("created")),
                    "rows": [
                        plain(item) for item in self.adapter._memories()
                    ],
                },
            )
        if action == "correct":
            runtime.correct_memory(memory_id, str(body.get("replacement") or ""))
        elif action == "forget":
            runtime.forget_memory(memory_id)
        elif action == "protect":
            runtime.set_memory_protected(memory_id, bool(body.get("protected", True)))
        elif action == "purge":
            from ..maintenance import purge_memory

            purge_memory(runtime.store, memory_id)
        elif action == "purge_all":
            from ..maintenance import purge_memories

            result = purge_memories(
                runtime.store,
                session_id=str(body["session_id"]) if body.get("session_id") else None,
            )
            self.adapter.broadcast({"type": "refresh"})
            return self._json(
                200,
                dict(
                    result,
                    rows=[plain(item) for item in self.adapter._memories()],
                ),
            )
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

    def _character_asset(self, slot):
        content_type = None
        if slot == "avatar":
            asset = self.adapter.runtime.avatar_asset()
            missing = "没有头像素材。"
        elif slot == "background":
            asset = self.adapter.runtime.background_asset()
            missing = "没有背景素材。"
        elif slot == "mesh":
            asset = self.adapter.runtime.mesh_asset()
            missing = "没有 .mavatar 桌宠素材。"
            if asset is not None and not asset.valid:
                return self._json(404, {"error": asset.reason or ".mavatar 无效。"})
            content_type = "application/zip"
        else:
            return self._json(404, {"error": "未找到。"})
        if asset is None:
            return self._json(404, {"error": missing})
        try:
            data = asset.path.read_bytes()
        except OSError:
            return self._json(404, {"error": "素材不可读。"})
        if content_type is None:
            content_type = {
                "png": "image/png",
                "webp": "image/webp",
                "jpg": "image/jpeg",
            }[asset.kind]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _live2d_file(self, path):
        match = re.match(r"^/api/live2d/([^/]+)/(.+)$", path)
        if not match:
            return self._json(404, {"error": "未找到。"})
        supplied, relative = match.groups()
        if not secrets.compare_digest(supplied, self.adapter.token):
            return self._json(403, {"error": "访问令牌无效。"})
        result = self.adapter.runtime.live2d_file(relative)
        if result is None:
            return self._json(404, {"error": "未找到。"})
        target, media = result
        try:
            data = target.read_bytes()
        except OSError:
            return self._json(404, {"error": "素材不可读。"})
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

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

    def _refuse_body(self, message):
        """413 without reading the body; the connection has to close because
        the unread bytes would otherwise be parsed as the next request."""
        self.close_connection = True
        return self._json(413, {"error": message})

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
