"""M16 切片 3：MCP 客户端（传输、发现、命名空间注册）。

退出判据里最硬的一条是「假服务器跑通闭环 + 每种坏行为都翻成可读中文」，所以这里
同时跑真子进程 stdio 与本机 HTTP 两种传输，而不是只测一个假对象。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.maintenance import create_backup, export_data
from agent_framework.companion.web import WebAdapter

from agent_framework.companion.mcp import (
    McpError,
    McpServerSpec,
    McpService,
    keys,
)
from agent_framework.companion.mcp.servers import (
    reload_service,
    spec_from_record,
)
from agent_framework.companion.mcp.console import (
    McpConsole,
    McpConsoleError,
)
from agent_framework.companion.mcp.bridge import (
    MAX_ARGS_BYTES,
    MAX_ENUM_ITEMS,
    MAX_SCHEMA_KEYS,
    BridgeError,
    build_tools,
    check_arguments,
    clean_schema,
    make_tool,
    register_tools,
    tool_name,
)
from agent_framework.companion.mcp.jsonrpc import (
    MAX_MESSAGE_BYTES,
    MAX_RESULT_CHARS,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
    call_text,
    check,
    decode,
    encode,
    parse_tools,
    request as rpc_request,
)
from agent_framework.companion.mcp.proposal import (
    REMOTE_WARNING,
    build_call_proposal,
    preview_value,
    server_identity,
    server_identity_matches,
)
from agent_framework.companion.mcp.service import (
    STATE_CONNECTED,
    STATE_DOWN,
    STATE_FAILED,
)
from agent_framework.companion.mcp.transport_http import HttpTransport
from agent_framework.companion.mcp.transport_stdio import _STDERR_KEEP, StdioTransport
from agent_framework.companion import settings as app_settings
from agent_framework.companion.redaction import MASK
from agent_framework.companion.app.contracts import ApprovalView
from agent_framework.companion.file_actions import FileActionError
from agent_framework.companion.models import (
    AgentMode,
    EXECUTOR_MCP,
    ProposalStatus,
    RiskLevel,
)
from agent_framework.companion.policy import (
    ASK,
    ALLOW,
    DENY,
    PERMISSION_BALANCED,
    PERMISSION_HINTS,
    PERMISSION_LABELS,
    PERMISSION_RELAXED,
    PERMISSION_STRICT,
    PERMISSION_TIERS,
    decide_tool_policy,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.tools import (
    CompanionTool,
    CompanionToolRegistry,
    ToolResult,
)
from agent_framework.llm import OfflineLLMClient


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_stdio_server.py"

ECHO_TOOL = {
    "name": "echo_text",
    "description": "Repeat the text back.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}

# 声明了 readOnlyHint=false 的工具：面板上那枚「远端写入」只能由服务器自己说出来，
# 不能由我们根据名字猜。
WRITE_TOOL = {
    "name": "write_note",
    "description": "Write a note on the server.",
    "annotations": {"readOnlyHint": False},
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def spec(**kwargs):
    base = {
        "server_id": "demo",
        "label": "示例服务器",
        "transport": "stdio",
        "command": "python",
        "enabled_tools": ("echo_text",),
    }
    base.update(kwargs)
    return McpServerSpec(**base)


def proposal_maker(session_id="main"):
    """What the runtime hands the bridge: describe the call, send nothing."""

    def factory(entry, spec_, arguments):
        return build_call_proposal(entry, spec_, arguments, session_id=session_id)

    return factory


class StubTransport:
    """An in-memory server used by the service/bridge/registry tests."""

    def __init__(self, tools=None, connect_error="", page_tools=None, clock=None):
        self.tools = list(tools if tools is not None else [ECHO_TOOL])
        self.page_tools = page_tools
        self.connect_error = connect_error
        self.connected_flag = False
        self.closed = 0
        self.calls = []
        self.notified = []
        self.dead = False
        self.error = ""
        self.clock = clock

    @property
    def connected(self):
        return self.connected_flag and not self.dead

    def connect(self):
        if self.connect_error:
            raise McpError(self.connect_error, "mcp_launch_failed")
        self.connected_flag = True

    def request(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if not self.connected:
            raise McpError("服务器尚未连接。", "mcp_not_connected")
        if self.error:
            raise McpError(self.error, "mcp_server_exit")
        if method == "initialize":
            return {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub"},
            }
        if method == "tools/list":
            if self.page_tools is not None:
                cursor = (params or {}).get("cursor")
                pages = self.page_tools
                index = int(cursor or 0)
                if index >= len(pages):
                    raise McpError("分页越界。", "stub")
                body = {"tools": pages[index]}
                if index + 1 < len(pages):
                    body["nextCursor"] = str(index + 1)
                return body
            return {"tools": self.tools}
        if method == "tools/call":
            name = (params or {}).get("name")
            if name == "boom":
                raise McpRemoteError("服务器内部错误。", error_code=-32603)
            return {"content": [{"type": "text", "text": "已执行 {}".format(name)}]}
        raise McpRemoteError("没有这个方法。", error_code=-32601)

    def notify(self, method, params=None):
        self.notified.append(method)

    def close(self):
        self.closed += 1
        self.connected_flag = False


class FramingTest(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        data = encode(rpc_request(7, "tools/list", {"cursor": None}))
        self.assertTrue(data.endswith(b"\n"))
        message = decode(data)
        self.assertEqual(message["id"], 7)
        self.assertEqual(message["method"], "tools/list")

    def test_encode_rejects_a_huge_request(self):
        with self.assertRaises(McpProtocolError) as caught:
            encode(rpc_request(1, "tools/call", {"blob": "x" * (MAX_MESSAGE_BYTES + 10)}))
        self.assertIn("请求体", str(caught.exception))

    def test_decode_names_each_bad_frame(self):
        cases = {
            "响应不是合法 JSON": b"{oops",
            "响应不是 UTF-8 文本": b"\xff\xfe not json",
            "不支持批量响应": b"[]",
            "响应不是 JSON 对象": b"3",
            "服务器没有返回内容": None,
        }
        for expected, raw in cases.items():
            with self.subTest(raw=expected):
                with self.assertRaises(McpProtocolError) as caught:
                    decode(raw)
                self.assertIn(expected, caught.exception.reason)

    def test_decode_caps_an_oversized_frame(self):
        huge = b'{"a":"' + b"x" * (MAX_MESSAGE_BYTES + 16) + b'"}'
        self.assertGreater(len(huge), MAX_MESSAGE_BYTES)
        with self.assertRaises(McpProtocolError) as caught:
            decode(huge)
        self.assertIn("超过", caught.exception.reason)

    def test_check_validates_the_envelope(self):
        result = check({"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}, 3, "tools/list")
        self.assertEqual(result, {"tools": []})
        broken = {
            "缺 jsonrpc": {"id": 3, "result": {}},
            "缺 id": {"jsonrpc": "2.0", "result": {}},
            "id 不配对": {"jsonrpc": "2.0", "id": 4, "result": {}},
            "既无 result 也无 error": {"jsonrpc": "2.0", "id": 3},
            "result 类型不对": {"jsonrpc": "2.0", "id": 3, "result": 5},
        }
        for label, message in broken.items():
            with self.subTest(case=label):
                with self.assertRaises(McpProtocolError):
                    check(message, 3, "tools/list")

    def test_remote_error_codes_are_translated(self):
        for code, text in (
            (-32700, "不是合法 JSON"),
            (-32601, "没有这个工具"),
            (-32602, "参数不符合"),
            (-32603, "服务器内部错误"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(McpRemoteError) as caught:
                    check(
                        {"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": "boom"}},
                        1,
                        "tools/call",
                    )
                self.assertIn(text, caught.exception.reason)

    def test_parse_tools_skips_bad_entries_not_the_server(self):
        tools, issues = parse_tools(
            {"tools": [ECHO_TOOL, {"description": "无名"}, "不是对象"]}
        )
        self.assertEqual([item["name"] for item in tools], ["echo_text"])
        self.assertEqual(len(issues), 2)
        self.assertIn("名称", issues[0])

    def test_parse_tools_rejects_a_shapeless_result(self):
        for result in ({}, {"tools": "x"}, ["x"]):
            with self.subTest(result=result):
                with self.assertRaises(McpProtocolError):
                    parse_tools(result)

    def test_call_text_flattens_and_marks_errors(self):
        text, is_error = call_text(
            {
                "content": [
                    {"type": "text", "text": "第一行"},
                    {"type": "image", "data": "…"},
                    {"type": "text"},
                ],
                "isError": True,
            }
        )
        self.assertTrue(is_error)
        self.assertIn("第一行", text)
        self.assertIn("image 类型的返回，已省略其内容", text)
        self.assertIn("文本片段缺少内容", text)

    def test_call_text_truncates_a_flood(self):
        text, _ = call_text({"content": [{"type": "text", "text": "x" * (MAX_RESULT_CHARS + 500)}]})
        self.assertIn("已截断", text)
        self.assertLess(len(text), MAX_RESULT_CHARS + 40)

    def test_call_text_falls_back_to_structured_content(self):
        text, _ = call_text({"structuredContent": {"ok": True}})
        self.assertIn('"ok": true', text)
        empty, _ = call_text({"content": []})
        self.assertEqual(empty, "（服务器返回了空结果）")


class StdioTransportTest(unittest.TestCase):
    def transport(self, mode, timeout=10.0):
        return StdioTransport(
            [sys.executable, "-X", "utf8", str(FIXTURE), mode],
            timeout=timeout,
            label="fixture-{}".format(mode),
        )

    def test_initialize_list_and_call(self):
        transport = self.transport("ok")
        transport.connect()
        try:
            info = transport.request("initialize", {"protocolVersion": "2025-06-18"})
            self.assertEqual(info["protocolVersion"], "2025-06-18")
            transport.notify("notifications/initialized")
            listed = transport.request("tools/list", {})
            names = [item["name"] for item in listed["tools"]]
            self.assertEqual(names, ["echo_text", "no_schema"])
            called = transport.request(
                "tools/call", {"name": "echo_text", "arguments": {"text": "你好"}}
            )
            text, is_error = call_text(called)
            self.assertEqual(text, "你好")
            self.assertFalse(is_error)
        finally:
            transport.close()

    def test_child_exits_after_we_close(self):
        transport = self.transport("ok")
        transport.connect()
        process = transport._process
        transport.close()
        self.assertIsNotNone(process.poll())
        # 关闭是幂等的：第二次不能再崩。
        transport.close()

    def test_a_launch_failure_is_readable(self):
        transport = StdioTransport(["__definitely_not_a_program__.exe"])
        with self.assertRaises(McpError) as caught:
            transport.connect()
        self.assertIn("启动服务器进程失败", caught.exception.reason)

    def test_crash_mid_request(self):
        transport = self.transport("crash")
        transport.connect()
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn(caught.exception.code, {"mcp_server_exit", "mcp_server_closed", "mcp_write_failed"})
        self.assertTrue(caught.exception.reason)

    def test_silent_server_times_out(self):
        transport = self.transport("silent", timeout=1.0)
        transport.connect()
        process = transport._process
        with self.assertRaises(McpTimeout) as caught:
            transport.request("initialize", {})
        self.assertIn("没有回应", caught.exception.reason)
        # 超时后传输自己收尾：子进程不能留在后台睡 30 秒。
        self.assertFalse(transport.connected)
        self.assertIsNotNone(process.poll())
        transport.close()

    def test_garbage_line_is_a_protocol_error(self):
        transport = self.transport("garbage")
        transport.connect()
        with self.assertRaises(McpProtocolError) as caught:
            transport.request("initialize", {})
        self.assertIn("不是合法 JSON", caught.exception.reason)

    def test_oversized_line_is_capped(self):
        transport = self.transport("huge")
        transport.connect()
        with self.assertRaises(McpProtocolError) as caught:
            transport.request("initialize", {})
        self.assertIn("超过", caught.exception.reason)

    def test_mismatched_id_is_reported(self):
        transport = self.transport("mismatch")
        transport.connect()
        with self.assertRaises(McpProtocolError) as caught:
            transport.request("initialize", {})
        self.assertIn("id 与请求不一致", caught.exception.reason)

    def test_server_notifications_are_skipped_not_failed(self):
        transport = self.transport("notification_first")
        transport.connect()
        try:
            info = transport.request("initialize", {})
            self.assertIn("protocolVersion", info)
        finally:
            transport.close()

    def test_stderr_thread_keeps_the_child_unblocked(self):
        transport = self.transport("ok")
        transport.connect()
        try:
            transport.request("initialize", {})
            # 排空线程必须一直在跑，否则子进程会被 stderr 卡死。
            self.assertTrue(
                any(thread.name == "mcp-stdio-err" and thread.is_alive() for thread in threading.enumerate())
            )
        finally:
            transport.close()

    def test_stderr_tail_is_bounded_and_keeps_the_end(self):
        transport = StdioTransport(["noop"], label="stderr")
        noise = ("噪音 " * 400 + "真正的最后一段").encode("utf-8")
        transport._drain_stderr(BytesIO(noise))
        self.assertLessEqual(len(transport.stderr_tail), _STDERR_KEEP)
        self.assertTrue(transport.stderr_tail.endswith("真正的最后一段"))


class _HttpHandler(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *args):
        pass

    def _write(self, body: bytes, content_type="application/json", extra=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        type(self).seen.append(dict(self.headers))
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._write(b"not json", status=400)
            return
        method = message.get("method")
        message_id = message.get("id")
        if type(self).mode == "http500":
            self._write(b'{"error":"boom"}', status=500)
            return
        if type(self).mode == "http401":
            self._write(b"", status=401)
            return
        if type(self).mode == "garbage":
            self._write("<html>这不是 JSON</html>".encode("utf-8"))
            return
        if type(self).mode == "huge":
            self._write(
                json.dumps(
                    {
                        "id": message_id,
                        "jsonrpc": "2.0",
                        "result": {"blob": "x" * (MAX_MESSAGE_BYTES + 1024)},
                    }
                ).encode("utf-8")
            )
            return
        if type(self).mode == "silent":
            import time

            # 比测试里的 1 秒超时慢就够：睡太久会让这条挂着的连接在解释器退出时
            # 留下一个没关的 socket（ResourceWarning）。
            time.sleep(3)
            return
        if type(self).mode == "empty":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            self._write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake-http"},
                        },
                    }
                ).encode("utf-8"),
                extra={"Mcp-Session-Id": "session-42"},
            )
            return
        if message_id is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "tools/list":
            body = {"tools": [ECHO_TOOL]}
            if type(self).mode == "sse":
                self._write(
                    ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": message_id, "result": body}) + "\n\n").encode("utf-8"),
                    content_type="text/event-stream",
                )
                return
            self._write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": body}).encode())
            return
        if method == "tools/call":
            name = (message.get("params") or {}).get("name")
            if name == "boom":
                self._write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "error": {"code": -32603, "message": "kaboom"},
                        }
                    ).encode("utf-8")
                )
                return
            text = ((message.get("params") or {}).get("arguments") or {}).get("text", "")
            self._write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {"content": [{"type": "text", "text": text}]},
                    }
                ).encode("utf-8")
            )
            return
        self._write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "no method"},
                }
            ).encode("utf-8")
        )

    def do_DELETE(self):
        type(self).seen.append({"method": "DELETE", "session": self.headers.get("Mcp-Session-Id")})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class HttpTransportTest(unittest.TestCase):
    def setUp(self):
        _HttpHandler.mode = "ok"
        _HttpHandler.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:{}/mcp".format(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def transport(self, mode, **kwargs):
        _HttpHandler.mode = mode
        kwargs.setdefault("timeout", 10.0)
        return HttpTransport(self.url, **kwargs)

    def test_initialize_list_and_call(self):
        transport = self.transport("ok")
        transport.connect()
        info = transport.request("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(info["protocolVersion"], "2025-06-18")
        self.assertEqual(transport.session_id, "session-42")
        transport.notify("notifications/initialized")
        listed = transport.request("tools/list", {})
        self.assertEqual([item["name"] for item in listed["tools"]], ["echo_text"])
        called = transport.request(
            "tools/call", {"name": "echo_text", "arguments": {"text": "远端你好"}}
        )
        text, is_error = call_text(called)
        self.assertEqual(text, "远端你好")
        self.assertFalse(is_error)
        # 会话号必须回带到后续每一个请求。
        self.assertEqual(_HttpHandler.seen[-1]["Mcp-Session-Id"], "session-42")
        transport.close()

    def test_sse_body_is_parsed(self):
        transport = self.transport("sse")
        transport.connect()
        transport.request("initialize", {})
        listed = transport.request("tools/list", {})
        self.assertEqual([item["name"] for item in listed["tools"]], ["echo_text"])

    def test_remote_error_still_reads_as_one_call(self):
        transport = self.transport("ok")
        transport.connect()
        transport.request("initialize", {})
        with self.assertRaises(McpRemoteError) as caught:
            transport.request("tools/call", {"name": "boom", "arguments": {}})
        self.assertIn("服务器内部错误", caught.exception.reason)
        self.assertEqual(caught.exception.error_code, -32603)

    def test_http_status_codes_are_readable(self):
        for mode, needle in (("http500", "HTTP 500"), ("http401", "HTTP 401")):
            with self.subTest(mode=mode):
                transport = self.transport(mode)
                with self.assertRaises(McpError) as caught:
                    transport.request("initialize", {})
                self.assertIn(needle, caught.exception.reason)

    def test_html_body_is_a_protocol_error(self):
        transport = self.transport("garbage")
        with self.assertRaises(McpProtocolError) as caught:
            transport.request("initialize", {})
        self.assertIn("不是合法 JSON", caught.exception.reason)

    def test_oversized_body_is_capped(self):
        transport = self.transport("huge")
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn("超过", caught.exception.reason)

    def test_empty_body_is_readable(self):
        transport = self.transport("empty")
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn("空响应", caught.exception.reason)

    def test_hanging_server_times_out(self):
        transport = self.transport("silent", timeout=1.0)
        with self.assertRaises(McpTimeout):
            transport.request("initialize", {})

    def test_unreachable_server(self):
        # .invalid 永远解析不了（RFC 2606），比挑一个空闲端口稳定。
        transport = HttpTransport("http://companion.invalid/mcp", timeout=2.0)
        with self.assertRaises(McpError) as caught:
            transport.request("initialize", {})
        self.assertIn("无法连接服务器", caught.exception.reason)

    def test_bad_url_and_headers_are_refused_before_the_network(self):
        for bad in ("", "ftp://x/y", "http://"):
            with self.subTest(url=bad):
                with self.assertRaises(McpError) as caught:
                    HttpTransport(bad)
                self.assertIn("地址", caught.exception.reason)
        with self.assertRaises(McpError) as caught:
            HttpTransport(self.url, headers={"X-Key": "a\r\nb"})
        self.assertIn("请求头", caught.exception.reason)
        self.assertNotIn("a\r\nb", caught.exception.reason)

    def test_delete_ends_the_session_quietly(self):
        transport = self.transport("ok")
        transport.connect()
        transport.request("initialize", {})
        transport.close()
        self.assertEqual(_HttpHandler.seen[-1]["method"], "DELETE")
        # 关掉之后不能再发请求，也不能重复 close 报错。
        transport.close()
        with self.assertRaises(McpError):
            transport.request("tools/list", {})


class ServiceTest(unittest.TestCase):
    def service(self, stub=None, enabled=("echo_text",)):
        stub = stub or StubTransport(tools=[ECHO_TOOL])
        service = McpService(
            specs=[spec(enabled_tools=enabled)],
            transport_factory=lambda spec_: stub,
        )
        return service, stub

    def test_connect_caches_tools_and_snapshot_is_offline(self):
        service, stub = self.service()
        service.connect("demo")
        self.assertEqual(stub.calls[0][0], "initialize")
        self.assertEqual(stub.notified, ["notifications/initialized"])
        snapshot = service.snapshot()
        self.assertEqual([item[0].server_id for item in snapshot], ["demo"])
        before = len(stub.calls)
        for _ in range(5):
            service.snapshot()
        self.assertEqual(len(stub.calls), before)

    def test_status_reports_connected(self):
        service, _ = self.service()
        service.connect("demo")
        status = service.status("demo")
        self.assertEqual(status["state"], STATE_CONNECTED)
        self.assertEqual(status["tools"][0]["name"], "echo_text")

    def test_enabled_tools_default_to_none(self):
        service, _ = self.service(enabled=())
        service.connect("demo")
        self.assertEqual(build_tools(service), [])

    def test_call_tool_returns_text(self):
        service, _ = self.service()
        service.connect("demo")
        result = service.call_tool("demo", "echo_text", {"text": "hi"})
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "已执行 echo_text")

    def test_call_tool_remote_error(self):
        service, _ = self.service()
        service.connect("demo")
        result = service.call_tool("demo", "boom", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_remote_error")
        self.assertIn("服务器内部错误", result.text)
        # 服务器只是拒绝这一次调用，连接还在。
        self.assertEqual(len(service.snapshot()), 1)

    def test_call_tool_is_error_flag(self):
        service, stub = self.service()
        stub.tools = []  # not used; call path only
        service.connect("demo")
        original = stub.request

        def failing(method, params=None, timeout=None):
            if method == "tools/call":
                return {"content": [{"type": "text", "text": "参数不对"}], "isError": True}
            return original(method, params, timeout)

        stub.request = failing
        result = service.call_tool("demo", "echo_text", {"text": "x"})
        self.assertFalse(result.ok)
        self.assertTrue(result.remote_error)
        self.assertEqual(result.code, "mcp_tool_error")
        self.assertEqual(result.text, "参数不对")

    def test_connect_failure_is_recorded_not_lost(self):
        stub = StubTransport(connect_error="启动服务器进程失败：拒绝访问。")
        service = McpService(specs=[spec()], transport_factory=lambda spec_: stub)
        with self.assertRaises(McpError) as caught:
            service.connect("demo")
        self.assertIn("启动服务器进程失败", caught.exception.reason)
        self.assertEqual(service.status("demo")["state"], STATE_FAILED)
        self.assertEqual(service.snapshot(), [])
        self.assertEqual(stub.closed, 1)

    def test_a_dead_server_disappears_from_the_snapshot(self):
        service, stub = self.service()
        service.connect("demo")
        stub.dead = True
        self.assertEqual(service.snapshot(), [])
        self.assertEqual(service.status("demo")["state"], STATE_DOWN)
        result = service.call_tool("demo", "echo_text", {"text": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_not_connected")
        self.assertIn("未连接", result.text)

    def test_transport_breaking_during_a_call_drops_the_server(self):
        service, stub = self.service()
        service.connect("demo")

        def broken(method, params=None, timeout=None):
            if method == "tools/call":
                raise McpTimeout("服务器在 1 秒内没有回应 tools/call。")
            return {"content": []}

        stub.request = broken
        result = service.call_tool("demo", "echo_text", {"text": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_timeout")
        self.assertEqual(service.snapshot(), [])

    def test_a_non_mcp_fault_becomes_a_failure_not_an_exception(self):
        service, stub = self.service()
        service.connect("demo")

        def explode(method, params=None, timeout=None):
            raise ValueError("传输自己坏了")

        stub.request = explode
        result = service.call_tool("demo", "echo_text", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_call_failed")
        self.assertIn("服务器调用异常", result.text)

    def test_close_all_disconnects_everything(self):
        service, stub = self.service()
        service.connect("demo")
        service.close_all()
        self.assertEqual(service.snapshot(), [])
        self.assertEqual(stub.closed, 1)

    def test_pagination_and_bad_entries_become_issues(self):
        stub = StubTransport(page_tools=[[ECHO_TOOL], [{"description": "没有名字"}]])
        service = McpService(specs=[spec()], transport_factory=lambda spec_: stub)
        service.connect("demo")
        status = service.status("demo")
        self.assertEqual([tool["name"] for tool in status["tools"]], ["echo_text"])
        self.assertIn("名称", " ".join(status["issues"]))

    def test_pagination_that_never_ends_is_stopped(self):
        class Endless(StubTransport):
            def request(self, method, params=None, timeout=None):
                if method == "tools/list":
                    return {"tools": [ECHO_TOOL], "nextCursor": "again"}
                return super().request(method, params, timeout)

        stub = Endless()
        service = McpService(specs=[spec()], transport_factory=lambda spec_: stub)
        service.connect("demo")
        issues = " ".join(service.status("demo")["issues"])
        self.assertIn("分页超过", issues)

    def test_duplicate_tool_names_keep_the_first(self):
        stub = StubTransport(tools=[ECHO_TOOL, dict(ECHO_TOOL, description="第二个")])
        service = McpService(specs=[spec()], transport_factory=lambda spec_: stub)
        service.connect("demo")
        status = service.status("demo")
        self.assertIn("重名工具", " ".join(status["issues"]))
        self.assertEqual(len(build_tools(service)), 1)

    def test_unknown_server(self):
        service = McpService()
        with self.assertRaises(McpError):
            service.connect("nope")
        self.assertEqual(service.status("nope")["state"], "down")

    def test_refresh_needs_a_live_connection(self):
        service, stub = self.service()
        with self.assertRaises(McpError):
            service.refresh("demo")
        service.connect("demo")
        stub.tools = [ECHO_TOOL, {"name": "late_tool"}]
        tools = service.refresh("demo")
        self.assertEqual([tool["name"] for tool in tools], ["echo_text", "late_tool"])


class SchemaConvergenceTest(unittest.TestCase):
    def test_non_object_schema_becomes_an_empty_object(self):
        for bad in (None, "string", 5, [], ["a"]):
            with self.subTest(bad=bad):
                self.assertEqual(clean_schema(bad), {"type": "object", "properties": {}, "required": []})

    def test_foreign_keywords_are_dropped(self):
        schema = clean_schema(
            {
                "type": "object",
                "$ref": "http://evil.example/schema",
                "$defs": {"secret": {}},
                "x-internal": "别给模型看",
                "properties": {"text": {"type": "string", "x-secret": "y"}},
            }
        )
        text = json.dumps(schema, ensure_ascii=False)
        for needle in ("$ref", "$defs", "x-internal", "x-secret", "evil.example"):
            self.assertNotIn(needle, text)

    def test_enum_lists_are_bounded(self):
        schema = clean_schema({"properties": {"kind": {"enum": ["v%d" % i for i in range(500)]}}})
        self.assertEqual(len(schema["properties"]["kind"]["enum"]), MAX_ENUM_ITEMS)

    def test_property_count_is_bounded(self):
        schema = clean_schema({"properties": {"p%d" % i: {"type": "string"} for i in range(200)}})
        self.assertLessEqual(len(schema["properties"]), MAX_SCHEMA_KEYS)

    def test_depth_is_bounded(self):
        node = {"type": "string"}
        for _ in range(8):
            node = {"type": "object", "properties": {"deep": node}}
        schema = clean_schema(node)
        self.assertIn("（层级过深，已省略）", json.dumps(schema, ensure_ascii=False))

    def test_long_text_is_clipped(self):
        schema = clean_schema({"properties": {"q": {"type": "string", "description": "说" * 5000}}})
        self.assertLess(len(json.dumps(schema, ensure_ascii=False)), 4096 + 200)

    def test_required_only_keeps_declared_keys(self):
        schema = clean_schema({"properties": {"q": {"type": "string"}}, "required": ["q", "ghost"]})
        self.assertEqual(schema["required"], ["q"])

    def test_arguments_are_checked_against_the_whitelist(self):
        schema = clean_schema(ECHO_TOOL["inputSchema"])
        self.assertEqual(
            __import__("agent_framework.companion.mcp.bridge", fromlist=["x"]).check_arguments(
                {"text": "hi"}, schema
            ),
            {"text": "hi"},
        )
        for bad, needle in (
            ({"text": "hi", "sudo": True}, "不在服务器声明的清单里"),
            ({}, "缺少必填参数"),
            ("不是对象", "参数必须是一个 JSON 对象"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(BridgeError) as caught:
                    check_arguments(bad, schema)
                self.assertIn(needle, caught.exception.reason)

    def test_nested_argument_limits(self):
        schema = {"type": "object", "properties": {"blob": {"type": "object"}}, "required": []}
        with self.assertRaises(BridgeError) as caught:
            check_arguments({"blob": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}, schema)
        self.assertIn("嵌套超过", caught.exception.reason)
        with self.assertRaises(BridgeError) as caught:
            check_arguments({"blob": "x" * (MAX_ARGS_BYTES + 10)}, schema)
        self.assertIn("超过", caught.exception.reason)
        with self.assertRaises(BridgeError) as caught:
            check_arguments({"blob": {"k%d" % i: i for i in range(200)}}, schema)
        self.assertIn("个键", caught.exception.reason)
        # 路径点名要能被模型看懂，不该出现「参数 参数.」。
        with self.assertRaises(BridgeError) as caught:
            check_arguments({"blob": {"a": {"b": {"c": {"d": {"e": {"f": {"g": "x" * 20000}}}}}}}}, schema)
        self.assertNotIn("参数 参数", caught.exception.reason)


class NamespaceTest(unittest.TestCase):
    def test_names_are_namespaced_and_safe(self):
        self.assertEqual(tool_name("demo", "echo_text"), "mcp__demo__echo_text")
        # 中文工具名不能直接当函数名用：改写后要带摘要，否则两个中文同名会撞成一个。
        rewritten = tool_name("demo", "删除 文件!")
        self.assertTrue(rewritten.startswith("mcp__demo__"))
        self.assertNotIn(" ", rewritten)
        self.assertEqual(len(rewritten), len("mcp__demo__tool_" + "x" * 6))
        self.assertNotEqual(rewritten, tool_name("demo", "删除 目录!"))

    def test_long_names_stay_within_the_openai_limit(self):
        name = tool_name("s" * 40, "t" * 40)
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.startswith("mcp__"))
        self.assertEqual(name, tool_name("s" * 40, "t" * 40))
        self.assertNotEqual(name, tool_name("s" * 40, "t" * 41))

    def test_two_servers_never_share_a_tool_name(self):
        self.assertNotEqual(tool_name("a", "x"), tool_name("b", "x"))


class BridgeRegistrationTest(unittest.TestCase):
    def service(self, tools=None, enabled=("echo_text",), server_id="demo"):
        stub = StubTransport(tools=tools if tools is not None else [ECHO_TOOL])
        service = McpService(
            specs=[spec(server_id=server_id, enabled_tools=enabled)],
            transport_factory=lambda spec_: stub,
        )
        service.connect(server_id)
        return service, stub

    def test_registered_tool_carries_external_risk(self):
        service, _ = self.service()
        registry = CompanionToolRegistry()
        names, issues = register_tools(registry, service, proposal_maker())
        self.assertEqual(names, ["mcp__demo__echo_text"])
        self.assertEqual(issues, [])
        tool = registry._tools["mcp__demo__echo_text"]
        self.assertIs(tool.risk, RiskLevel.EXTERNAL)
        self.assertIn("外部工具（来自 示例服务器：echo_text）", tool.description)
        self.assertEqual(tool.parameters["properties"]["text"]["type"], "string")

    def test_tools_the_user_did_not_enable_stay_out(self):
        service, _ = self.service(tools=[ECHO_TOOL, {"name": "rm_rf"}], enabled=("echo_text",))
        registry = CompanionToolRegistry()
        names, _ = register_tools(registry, service, proposal_maker())
        self.assertEqual(names, ["mcp__demo__echo_text"])

    def test_a_name_colliding_with_a_core_tool_is_skipped_not_fatal(self):
        service, _ = self.service()
        registry = CompanionToolRegistry()
        registry.register(
            CompanionTool(
                name=tool_name("demo", "echo_text"),
                description="core",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.SAFE,
                handler=lambda arguments: ToolResult.success("core"),
            )
        )
        names, issues = register_tools(registry, service, proposal_maker())
        self.assertEqual(names, [])
        self.assertIn("跳过远程工具", issues[0])

    def test_the_handler_stops_at_a_proposal_and_never_touches_the_wire(self):
        # 注册表在 ASK 档也会真的跑 handler，所以远程工具的 handler 必须只出提案。
        service, stub = self.service()
        registry = CompanionToolRegistry()
        seen = []

        def factory(entry, spec_, arguments):
            seen.append((entry["name"], spec_.server_id, arguments))
            return build_call_proposal(entry, spec_, arguments, session_id="main")

        names, _ = register_tools(registry, service, factory)
        self.assertEqual(names, ["mcp__demo__echo_text"])
        before = len(stub.calls)
        result = registry.execute(
            "mcp__demo__echo_text", {"text": "hi"}, AgentMode.EXECUTE
        )
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.proposal)
        self.assertEqual(len(stub.calls), before)
        self.assertEqual(seen, [("mcp__demo__echo_text", "demo", {"text": "hi"})])

    def test_bad_arguments_are_rejected_before_a_proposal_exists(self):
        seen = []

        def factory(entry, spec_, arguments):
            seen.append((spec_.server_id, entry["remote_name"], arguments))
            return build_call_proposal(entry, spec_, arguments, session_id="main")

        service, _ = self.service()
        registry = CompanionToolRegistry()
        register_tools(registry, service, factory)
        result = registry.execute(
            "mcp__demo__echo_text", {"sudo": "rm"}, AgentMode.EXECUTE
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_bad_arguments")
        self.assertEqual(seen, [])
        # 参数名先过一遍白名单，再原样交给提案：确认时执行的就是这份收敛过的参数。
        ok = registry._tools["mcp__demo__echo_text"].handler({"text": "喂"})
        self.assertTrue(ok.ok)
        self.assertEqual(seen, [("demo", "echo_text", {"text": "喂"})])
        self.assertEqual(ok.proposal.normalized_arguments, {"text": "喂"})

    def test_a_server_removed_after_registration_yields_no_proposal(self):
        service, _ = self.service()
        registry = CompanionToolRegistry()
        register_tools(registry, service, proposal_maker())
        service.remove_spec("demo")
        result = registry.execute(
            "mcp__demo__echo_text", {"text": "hi"}, AgentMode.EXECUTE
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_unknown_server")

    def test_a_tool_switched_off_after_registration_yields_no_proposal(self):
        service, _ = self.service()
        registry = CompanionToolRegistry()
        register_tools(registry, service, proposal_maker())
        service.add_spec(spec(enabled_tools=()))
        result = registry.execute(
            "mcp__demo__echo_text", {"text": "hi"}, AgentMode.EXECUTE
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_tool_disabled")

    def test_a_success_without_a_proposal_is_not_a_pass(self):
        # 没有提案的成功结果会被注册表判成 confirmation_missing；桥接层宁可在
        # 自己这一层就报错，也不让一次远程调用挂着「成功」进去。
        service, _ = self.service()
        tool = make_tool(build_tools(service)[0], service, lambda *a: None)
        result = tool.handler({"text": "hi"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_proposal_missing")

    def test_a_factory_that_blows_up_is_reported_not_raised(self):
        def factory(entry, spec_, arguments):
            raise RuntimeError("提案存储炸了")

        service, _ = self.service()
        tool = make_tool(build_tools(service)[0], service, factory)
        result = tool.handler({"text": "hi"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "mcp_proposal_failed")
        self.assertIn("提案存储炸了", result.content)


class ExternalTierTest(unittest.TestCase):
    """远程工具是唯一不能靠「信任」放开的副作用：它落在别人的机器上。"""

    def test_matrix_ships_ask_for_work_and_execute(self):
        self.assertEqual(decide_tool_policy(AgentMode.CHAT, RiskLevel.EXTERNAL), DENY)
        self.assertEqual(decide_tool_policy(AgentMode.QUIET, RiskLevel.EXTERNAL), DENY)
        self.assertEqual(decide_tool_policy(AgentMode.WORK, RiskLevel.EXTERNAL), ASK)
        self.assertEqual(decide_tool_policy(AgentMode.EXECUTE, RiskLevel.EXTERNAL), ASK)

    def test_strict_denies_external_everywhere(self):
        for mode in AgentMode:
            with self.subTest(mode=mode):
                self.assertEqual(
                    decide_tool_policy(mode, RiskLevel.EXTERNAL, PERMISSION_STRICT), DENY
                )

    def test_relaxed_never_upgrades_external(self):
        for mode in AgentMode:
            with self.subTest(mode=mode):
                self.assertNotEqual(
                    decide_tool_policy(mode, RiskLevel.EXTERNAL, PERMISSION_RELAXED), ALLOW
                )
        self.assertEqual(
            decide_tool_policy(AgentMode.WORK, RiskLevel.EXTERNAL, PERMISSION_RELAXED), ASK
        )
        self.assertEqual(
            decide_tool_policy(AgentMode.EXECUTE, RiskLevel.EXTERNAL, PERMISSION_RELAXED), ASK
        )

    def test_no_tier_opens_anything_the_matrix_closed(self):
        for mode in AgentMode:
            for risk in RiskLevel:
                if decide_tool_policy(mode, risk) != DENY:
                    continue
                for tier in PERMISSION_TIERS:
                    with self.subTest(mode=mode, risk=risk, tier=tier):
                        self.assertEqual(decide_tool_policy(mode, risk, tier), DENY)

    def test_permission_hints_name_remote_tools(self):
        # 三档说明以前只点名 shell 与界面控制；加了 external 后文案必须跟上，
        # 否则设置页会承诺一个不存在的行为。
        for tier in PERMISSION_TIERS:
            with self.subTest(tier=tier):
                self.assertIn("远程工具", PERMISSION_HINTS[tier])


class ExternalRegistryTest(unittest.TestCase):
    def build(self, tier):
        self.ran = []

        def run(arguments):
            self.ran.append(arguments)
            return ToolResult.success("已执行")

        registry = CompanionToolRegistry(policy_tier=tier)
        registry.register(
            CompanionTool(
                name="mcp__demo__echo_text",
                description="external",
                parameters={"type": "object", "properties": {"text": {"type": "string"}}},
                risk=RiskLevel.EXTERNAL,
                handler=run,
            )
        )
        return registry

    def test_denied_external_tool_does_not_run_the_handler(self):
        for tier in ("strict",):
            registry = self.build(tier)
            result = registry.execute("mcp__demo__echo_text", {"text": "x"}, AgentMode.WORK)
            self.assertEqual(result.code, "mode_denied")
            self.assertEqual(self.ran, [])
            self.assertEqual(registry.available(AgentMode.WORK), [])

    def test_ask_still_needs_a_proposal_from_a_local_tool(self):
        registry = self.build("balanced")
        result = registry.execute("mcp__demo__echo_text", {"text": "x"}, AgentMode.WORK)
        self.assertEqual(self.ran, [{"text": "x"}])
        self.assertEqual(result.code, "confirmation_missing")

    def test_chat_never_sees_the_tool(self):
        for tier in ("strict", "balanced", "relaxed"):
            registry = self.build(tier)
            self.assertEqual([tool.name for tool in registry.available(AgentMode.CHAT)], [])


class TurnRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, service):
        runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=True,
        )
        runtime.mcp = service
        return runtime

    def test_snapshot_tools_appear_per_mode(self):
        stub = StubTransport(tools=[ECHO_TOOL])
        service = McpService(
            specs=[spec(enabled_tools=("echo_text",))], transport_factory=lambda spec_: stub
        )
        service.connect("demo")
        runtime = self.runtime(service)

        execute_names = [
            tool.name for tool in runtime._build_tool_registry(AgentMode.EXECUTE, None).available(AgentMode.EXECUTE)
        ]
        self.assertIn("mcp__demo__echo_text", execute_names)
        chat_names = [
            tool.name for tool in runtime._build_tool_registry(AgentMode.CHAT, None).available(AgentMode.CHAT)
        ]
        self.assertNotIn("mcp__demo__echo_text", chat_names)
        # 模型看到的工具清单同样按模式过滤。
        chat_payload = runtime._build_tool_registry(AgentMode.CHAT, None).as_openai_tools(AgentMode.CHAT)
        self.assertFalse(any(item["function"]["name"].startswith("mcp__") for item in chat_payload))

    def test_the_table_switch_is_what_the_model_sees(self):
        # 切片 4 退出判据：未启用的工具不进模型可见清单——而且开关的来源是表里的行，
        # 不是 spec 构造参数，否则界面改了、模型看到的还是旧的。
        previous = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.data / "keys.json")
        self.addCleanup(
            lambda: os.environ.__setitem__(keys.KEYS_PATH_ENV, previous)
            if previous is not None
            else os.environ.pop(keys.KEYS_PATH_ENV, None)
        )
        self.store.upsert_mcp_server(
            {
                "server_id": "demo",
                "label": "示例服务器",
                "transport": "stdio",
                "command": "python server.py",
                "args": [],
                "enabled": True,
                "tools": ["echo_text"],
            }
        )
        stub = StubTransport(tools=[ECHO_TOOL, WRITE_TOOL])
        service = McpService(transport_factory=lambda spec_: stub)
        reload_service(service, self.store.list_mcp_servers())
        service.connect("demo")
        runtime = self.runtime(service)

        registry = runtime._build_tool_registry(AgentMode.EXECUTE, None)
        names = [tool.name for tool in registry.available(AgentMode.EXECUTE)]
        self.assertIn("mcp__demo__echo_text", names)
        self.assertNotIn("mcp__demo__write_note", names)
        payload = [
            item["function"]["name"]
            for item in registry.as_openai_tools(AgentMode.EXECUTE)
            if str(item["function"]["name"]).startswith("mcp__")
        ]
        self.assertEqual(payload, ["mcp__demo__echo_text"])

        # 关上一个开关，不用重连：下一轮的清单就少了它。
        self.store.set_mcp_server_tools("demo", ["write_note"])
        runtime.reload_mcp_servers()
        registry = runtime._build_tool_registry(AgentMode.EXECUTE, None)
        payload = [
            item["function"]["name"]
            for item in registry.as_openai_tools(AgentMode.EXECUTE)
            if str(item["function"]["name"]).startswith("mcp__")
        ]
        self.assertEqual(payload, ["mcp__demo__write_note"])
        service.close_all()

    def test_a_disconnected_server_is_not_offered(self):
        stub = StubTransport(tools=[ECHO_TOOL])
        service = McpService(
            specs=[spec(enabled_tools=("echo_text",))], transport_factory=lambda spec_: stub
        )
        service.connect("demo")
        runtime = self.runtime(service)
        stub.dead = True
        names = [tool.name for tool in runtime._build_tool_registry(AgentMode.EXECUTE, None).available(AgentMode.EXECUTE)]
        self.assertNotIn("mcp__demo__echo_text", names)
        self.assertEqual(runtime.last_mcp_issues, [])

    def test_a_broken_service_cannot_kill_the_turn(self):
        runtime = self.runtime(None)
        boom = McpService()

        def explode():
            raise RuntimeError("服务器注册炸了")

        boom.snapshot = explode
        runtime.mcp = boom
        registry = runtime._build_tool_registry(AgentMode.WORK, None)
        self.assertIn("search_web", registry.names())


class RemoteConfirmTest(unittest.TestCase):
    """切片 4 A8：确认→执行→回执→撤销被拒，跑真 store、真账本、真 stub。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.stub = StubTransport(tools=[ECHO_TOOL])
        self.service = McpService(
            specs=[spec(enabled_tools=("echo_text",))],
            transport_factory=lambda spec_: self.stub,
        )
        self.service.connect("demo")
        self.runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=True,
        )
        self.runtime.mcp = self.service
        self.runtime.set_mode(AgentMode.WORK)

    def tearDown(self):
        self.service.close_all()
        self.temp.cleanup()

    def queue(self, arguments=None, mode=AgentMode.WORK):
        entry = build_tools(self.service)[0]
        proposal = build_call_proposal(
            entry,
            self.service.spec("demo"),
            arguments if arguments is not None else {"text": "喂"},
            session_id="main",
            mode=mode,
        )
        self.store.create_proposal(proposal)
        return proposal

    def wire(self):
        return [call for call in self.stub.calls if call[0] == "tools/call"]

    def test_confirm_sends_one_call_and_leaves_an_undo_free_receipt(self):
        proposal = self.queue()
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        receipt = outcome["receipt"]
        self.assertEqual(
            self.wire(),
            [("tools/call", {"name": "echo_text", "arguments": {"text": "喂"}})],
        )
        self.assertEqual(receipt.status, "completed")
        self.assertFalse(receipt.undoable)
        self.assertEqual(receipt.undo_status, "unavailable")
        self.assertEqual(receipt.result["executor"], EXECUTOR_MCP)
        self.assertIsNone(receipt.result["workspace"])
        self.assertIn("已执行 echo_text", receipt.result["text"])
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.COMPLETED,
        )
        self.assertIn(REMOTE_WARNING, outcome["answer"])
        with self.assertRaises(FileActionError) as refused:
            self.runtime.undo_receipt(receipt.receipt_id)
        self.assertIn("不提供撤销", str(refused.exception))
        self.assertEqual(len(self.wire()), 1)

    def test_a_second_confirm_does_not_send_the_call_again(self):
        proposal = self.queue()
        first = self.runtime.confirm_proposal(proposal.action_id)
        again = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(
            again["receipt"].receipt_id, first["receipt"].receipt_id
        )
        self.assertEqual(len(self.wire()), 1)

    def test_editing_the_server_invalidates_the_approval(self):
        proposal = self.queue()
        self.service.add_spec(spec(command="python other_server.py"))
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(outcome["receipt"].status, "not_executed")
        self.assertEqual(
            outcome["receipt"].result["code"], "mcp_server_changed"
        )
        self.assertIn("原确认失效", outcome["answer"])
        self.assertEqual(self.wire(), [])

    def test_switching_the_tool_off_invalidates_the_approval(self):
        proposal = self.queue()
        self.service.add_spec(spec(enabled_tools=()))
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(
            outcome["receipt"].result["code"], "mcp_tool_disabled"
        )
        self.assertEqual(self.wire(), [])

    def test_removing_the_server_invalidates_the_approval(self):
        proposal = self.queue()
        self.service.remove_spec("demo")
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(
            outcome["receipt"].result["code"], "mcp_server_missing"
        )
        self.assertEqual(self.wire(), [])

    def test_a_runtime_that_lost_the_service_refuses_to_call(self):
        proposal = self.queue()
        self.runtime.mcp = None
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(
            outcome["receipt"].result["code"], "mcp_server_missing"
        )
        self.assertEqual(self.wire(), [])

    def test_the_conservative_tier_refuses_without_calling(self):
        proposal = self.queue()
        self.runtime.tool_permission = PERMISSION_STRICT
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(outcome["receipt"].result["code"], "mode_denied")
        self.assertEqual(self.wire(), [])

    def test_chat_mode_refuses_without_calling(self):
        proposal = self.queue(mode=AgentMode.CHAT)
        outcome = self.runtime.confirm_proposal(
            proposal.action_id, current_mode=AgentMode.CHAT
        )
        self.assertEqual(outcome["receipt"].result["code"], "mode_denied")
        self.assertEqual(self.wire(), [])

    def test_a_timed_out_call_is_reported_as_unknown(self):
        # 不确定 ≠ 失败：超时那次可能已经在远端落地，所以账本只允许人再看一次，
        # 不允许第二条确认把它补发出去。
        proposal = self.queue()
        original = self.stub.request

        def hang(method, params=None, timeout=None):
            if method == "tools/call":
                self.stub.calls.append((method, params))
                raise McpTimeout("服务器在 15 秒内没有回。")
            return original(method, params, timeout)

        self.stub.request = hang
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        receipt = outcome["receipt"]
        self.assertEqual(receipt.status, "unknown")
        self.assertEqual(receipt.undo_status, "unavailable")
        again = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(again["receipt"].receipt_id, receipt.receipt_id)
        self.assertEqual(len(self.wire()), 1)

    def test_a_remote_failure_never_echoes_the_credential(self):
        proposal = self.queue()
        self.stub.error = "鉴权失败：Bearer sk-secretvalue123456789"
        outcome = self.runtime.confirm_proposal(proposal.action_id)
        receipt = outcome["receipt"]
        self.assertEqual(receipt.status, "failed")
        everywhere = json.dumps(
            {
                "answer": outcome["answer"],
                "error": receipt.error,
                "summary": receipt.summary,
                "result": receipt.result,
            },
            ensure_ascii=False,
        )
        self.assertNotIn("sk-secretvalue123456789", everywhere)
        self.assertIn("[REDACTED]", receipt.error)
        stored = self.store.get_receipt(receipt.receipt_id)
        self.assertNotIn("sk-secretvalue123456789", json.dumps(stored.result, ensure_ascii=False))

    def test_preview_of_a_remote_proposal_shows_the_endpoint_and_arguments(self):
        proposal = self.queue({"text": "喂"})
        view = self.runtime.preview_proposal(proposal.action_id)
        self.assertEqual(view["preview"]["change"], "remote_call")
        self.assertEqual(view["preview"]["server"]["id"], "demo")
        self.assertEqual(view["preview"]["arguments"], {"text": "喂"})
        self.assertEqual(view["workspace"], "")
        self.assertFalse(view["expired"])

    def test_the_view_contract_carries_the_server_and_arguments(self):
        proposal = self.queue({"text": "喂"})
        view = ApprovalView.from_proposal(
            proposal,
            preview_value(proposal),
            expired=False,
            workspace="",
        )
        payload = view.as_dict()
        self.assertEqual(payload["preview"]["server"]["transport"], "stdio")
        self.assertEqual(payload["preview"]["arguments"], {"text": "喂"})
        self.assertFalse(payload["reversible"])
        self.assertEqual(payload["risk"], "external")


class ServerReloadTest(unittest.TestCase):
    """切片 4 A9/Q2：表里的行变成 spec，密钥只从 config/mcp_keys.json 进来。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.keys_file = self.root / "mcp_keys.json"
        self.previous_env = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.keys_file)
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        if self.previous_env is None:
            os.environ.pop(keys.KEYS_PATH_ENV, None)
        else:
            os.environ[keys.KEYS_PATH_ENV] = self.previous_env
        self.temp.cleanup()

    def row(self, server_id="demo", **kwargs):
        base = {
            "server_id": server_id,
            "label": "示例服务器",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "enabled": True,
            "tools": ["echo_text"],
        }
        base.update(kwargs)
        self.store.upsert_mcp_server(base)
        return base

    def load(self, service=None):
        service = service or McpService()
        loaded, issues = reload_service(service, self.store.list_mcp_servers())
        return service, loaded, issues

    def test_rows_become_specs_with_their_tool_switches(self):
        self.row()
        service, loaded, issues = self.load()
        self.assertEqual(loaded, ["demo"])
        self.assertEqual(issues, [])
        live = service.spec("demo")
        self.assertEqual(live.command, "python")
        self.assertEqual(list(live.args), ["server.py"])
        self.assertEqual(live.enabled_tools, ("echo_text",))
        self.assertEqual(live.display, "示例服务器")

    def test_stdio_credentials_come_from_the_keys_file(self):
        keys.save_server(
            "demo",
            env={"API_TOKEN": "sk-from-keys-file"},
            path=str(self.keys_file),
        )
        self.row()
        service, _, _ = self.load()
        self.assertEqual(
            service.spec("demo").env, {"API_TOKEN": "sk-from-keys-file"}
        )
        self.assertEqual(service.spec("demo").headers, {})
        rows = self.store.list_mcp_servers()
        self.assertNotIn("sk-from-keys-file", json.dumps(rows, ensure_ascii=False))
        self.assertNotIn("API_TOKEN", json.dumps(rows, ensure_ascii=False))

    def test_an_http_token_never_leaks_into_a_child_environment(self):
        keys.save_server(
            "api",
            env={"API_TOKEN": "sk-stdio-only"},
            headers={"Authorization": "Bearer sk-http-only"},
            path=str(self.keys_file),
        )
        self.row(
            "api",
            transport="http",
            url="https://example.invalid/mcp",
            command="",
        )
        service, _, _ = self.load()
        live = service.spec("api")
        self.assertEqual(live.headers, {"Authorization": "Bearer sk-http-only"})
        self.assertEqual(live.env, {})

    def test_editing_the_address_drops_the_socket_it_was_opened_on(self):
        stub = StubTransport()
        service = McpService(transport_factory=lambda spec_: stub)
        self.row()
        reload_service(service, self.store.list_mcp_servers())
        service.connect("demo")
        self.assertTrue(stub.connected)
        self.row(command="python other_server.py")
        reload_service(service, self.store.list_mcp_servers())
        self.assertEqual(stub.closed, 1)
        self.assertEqual(service.status("demo")["state"], STATE_DOWN)
        self.assertEqual(service.spec("demo").command, "python other_server.py")

    def test_switching_a_server_off_disconnects_but_keeps_the_config(self):
        stub = StubTransport()
        service = McpService(transport_factory=lambda spec_: stub)
        self.row()
        reload_service(service, self.store.list_mcp_servers())
        service.connect("demo")
        self.row(enabled=False)
        self.load(service)
        self.assertEqual(service.status("demo")["state"], STATE_DOWN)
        self.assertEqual(service.snapshot(), [])
        self.assertIsNotNone(service.spec("demo"))

    def test_a_removed_row_takes_its_spec_with_it(self):
        stub = StubTransport()
        service = McpService(transport_factory=lambda spec_: stub)
        self.row()
        reload_service(service, self.store.list_mcp_servers())
        service.connect("demo")
        self.store.delete_mcp_server("demo")
        self.load(service)
        self.assertIsNone(service.spec("demo"))
        self.assertEqual(service.specs, ())

    def test_a_transport_we_did_not_build_is_a_readable_issue(self):
        # S04b 上画了 SSE，实现里只有 stdio 与 streamable-http：不能让整轮加载崩掉。
        self.row(transport="sse")
        service, loaded, issues = self.load()
        self.assertEqual(loaded, [])
        self.assertIn("不支持的传输类型", issues[0])
        self.assertIsNone(service.spec("demo"))

    def test_an_unreadable_keys_file_is_said_out_loud(self):
        self.keys_file.write_text("{不是 JSON", encoding="utf-8")
        self.row()
        service, loaded, issues = self.load()
        self.assertEqual(loaded, [])
        self.assertIn("凭据文件", issues[0])

    def test_the_runtime_loads_the_table_when_it_starts(self):
        self.row()
        runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=True,
        )
        try:
            self.assertEqual(runtime.mcp_load_issues, [])
            self.assertIsNotNone(runtime.mcp.spec("demo"))
            self.row(command="python b.py")
            runtime.reload_mcp_servers()
            self.assertEqual(runtime.mcp.spec("demo").command, "python b.py")
        finally:
            runtime.mcp.close_all()

    def test_a_spec_built_without_the_service_is_still_the_row(self):
        self.row(timeout=3.0, cwd=str(self.root), tools=["echo_text", "read_file"])
        built = spec_from_record(self.store.list_mcp_servers()[0])
        self.assertEqual(built.timeout, 3.0)
        self.assertEqual(built.cwd, str(self.root))
        self.assertEqual(built.enabled_tools, ("echo_text", "read_file"))


class ConsoleTest(unittest.TestCase):
    """切片 4 A9/Q2：面板的读写面。这里也是凭据的界面侧硬判据——出去的只有掩码。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.keys_file = self.root / "mcp_keys.json"
        self.previous_env = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.keys_file)
        self.store = CompanionStore(self.data / "companion.db")
        self.stub = StubTransport(tools=[ECHO_TOOL, WRITE_TOOL])
        # 计数而不是断言状态：任何一次编辑都不该造出一个传输来，这是「按钮之外不
        # 出网」最直接的证据。
        self.built = []

        def factory(spec_):
            self.built.append(spec_.server_id)
            return self.stub

        self.service = McpService(transport_factory=factory)
        self.console = McpConsole(self.store, lambda: self.service)

    def tearDown(self):
        self.service.close_all()
        if self.previous_env is None:
            os.environ.pop(keys.KEYS_PATH_ENV, None)
        else:
            os.environ[keys.KEYS_PATH_ENV] = self.previous_env
        self.temp.cleanup()

    def form(self, **kwargs):
        base = {
            "server_id": "demo",
            "label": "示例服务器",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "cwd": "",
            "url": "",
            "timeout": 15,
            "enabled": True,
        }
        base.update(kwargs)
        return base

    def test_the_panel_describes_the_row_it_just_stored(self):
        row = self.console.save(self.form())
        self.assertEqual(row["server_id"], "demo")
        self.assertEqual(row["transport_label"], "stdio")
        self.assertEqual(row["state"], STATE_DOWN)
        self.assertEqual(row["tools"], [])
        panel = self.console.panel()
        self.assertEqual(
            sorted(panel),
            ["connected_ui", "issues", "keys", "limits", "rows", "summary", "transports"],
        )
        self.assertEqual(panel["summary"]["servers"], 1)
        self.assertEqual(panel["summary"]["connected"], 0)
        self.assertEqual(panel["summary"]["tools"], 0)
        self.assertEqual(panel["summary"]["enabled_tools"], 0)
        self.assertTrue(panel["connected_ui"])
        self.assertEqual(panel["limits"]["args"], 16)

    def test_sse_is_a_stated_dead_end_not_a_missing_option(self):
        # 画板 S04b 上确实画了三种传输；实现只做了两种，第三种要说清为什么点不动。
        choices = {item["value"]: item for item in self.console.panel()["transports"]}
        self.assertEqual(sorted(choices), ["http", "sse", "stdio"])
        self.assertFalse(choices["sse"]["available"])
        self.assertIn("streamable-http", choices["sse"]["reason"])
        self.assertTrue(choices["stdio"]["available"])

    def test_a_new_server_starts_with_every_tool_switched_off(self):
        self.console.save(self.form())
        self.console.connect("demo")
        self.store.set_mcp_server_tools("demo", ["echo_text"])
        self.console.save(self.form(server_id="other"))
        other = self.console.row(self.store.get_mcp_server("other"))
        self.assertEqual(other["tools"], [])
        self.assertEqual(other["enabled_tools"], 0)

    def test_editing_a_row_keeps_the_switches_and_the_cached_tools(self):
        self.console.save(self.form())
        connected = self.console.connect("demo")
        self.assertTrue(connected["ok"])
        self.store.set_mcp_server_tools("demo", ["echo_text"])
        edited = self.console.save(self.form(label="改过名字的服务器", timeout=8))
        self.assertEqual(edited["label"], "改过名字的服务器")
        self.assertEqual(edited["timeout"], 8.0)
        self.assertEqual(
            [item["name"] for item in edited["tools"]], ["echo_text", "write_note"]
        )
        self.assertEqual(
            [item["name"] for item in edited["tools"] if item["enabled"]], ["echo_text"]
        )

    def test_the_write_tag_only_comes_from_the_server_itself(self):
        self.console.save(self.form())
        self.console.connect("demo")
        tools = {
            item["name"]: item
            for item in self.console.row(self.store.get_mcp_server("demo"))["tools"]
        }
        self.assertEqual(tools["echo_text"]["tag"], "外部调用")
        self.assertEqual(tools["write_note"]["tag"], "远端写入")
        self.assertEqual(tools["echo_text"]["risk"], "external")
        self.assertEqual(tools["echo_text"]["tool_name"], "mcp__demo__echo_text")

    def test_the_transport_of_an_existing_row_cannot_be_swapped(self):
        self.console.save(self.form())
        with self.assertRaises(McpConsoleError) as caught:
            self.console.save(self.form(transport="http", url="https://x.invalid/mcp"))
        self.assertEqual(caught.exception.code, "mcp_transport_locked")
        self.assertIn("不能改", caught.exception.reason)

    def test_typing_an_unknown_tool_refuses_rather_than_inventing_one(self):
        # 未启用的工具不进模型可见清单，反向也必须成立：清单外的名字不能被打开。
        self.console.save(self.form())
        self.console.connect("demo")
        with self.assertRaises(McpConsoleError) as caught:
            self.console.set_tools("demo", ["echo_text", "delete_everything"], True)
        self.assertEqual(caught.exception.code, "mcp_unknown_tool")
        self.assertIn("delete_everything", caught.exception.reason)
        record = self.store.get_mcp_server("demo")
        self.assertEqual(record["tools"], [])

    def test_a_switch_moves_only_the_tool_it_names(self):
        self.console.save(self.form())
        self.console.connect("demo")
        row = self.console.set_tools("demo", ["echo_text"], True)
        self.assertEqual(
            [item["name"] for item in row["tools"] if item["enabled"]], ["echo_text"]
        )
        row = self.console.set_tools("demo", ["write_note"], True)
        self.assertEqual(row["enabled_tools"], 2)
        row = self.console.set_tools("demo", ["echo_text"], False)
        self.assertEqual(
            [item["name"] for item in row["tools"] if item["enabled"]], ["write_note"]
        )
        row = self.console.set_all_tools("demo", False)
        self.assertEqual(row["enabled_tools"], 0)
        row = self.console.set_all_tools("demo", True)
        self.assertEqual(row["enabled_tools"], 2)

    def test_no_edit_builds_a_transport(self):
        self.console.save(self.form())
        self.console.set_enabled("demo", False)
        self.console.set_enabled("demo", True)
        self.console.set_tools("demo", [], None)
        self.console.reload()
        self.assertEqual(self.built, [])

    def test_a_disabled_server_is_refused_before_the_wire(self):
        self.console.save(self.form(enabled=False))
        result = self.console.connect("demo")
        self.assertFalse(result["ok"])
        self.assertIn("停用", result["error"])
        self.assertEqual(self.built, [])

    def test_the_panel_returns_a_readable_failure_instead_of_a_stack(self):
        self.console.save(self.form())
        self.stub.connect_error = "子进程退出了：token sk-launch-secret"
        result = self.console.connect("demo")
        self.assertFalse(result["ok"])
        self.assertNotIn("sk-launch-secret", result["error"])
        self.assertIn(MASK, result["error"])

    def test_credentials_reach_the_page_only_as_names_and_masks(self):
        self.console.save(self.form())
        secret = "sk-console-secret-value"
        view = self.console.set_credentials("demo", env={"API_TOKEN": secret})
        self.assertTrue(view["present"])
        masked = view["sections"][keys.SECTION_ENV][0]
        self.assertEqual(masked["name"], "API_TOKEN")
        self.assertEqual(masked["masked"], app_settings.mask_secret(secret))
        for payload in (
            json.dumps(self.console.panel(), ensure_ascii=False),
            json.dumps(self.console.row(self.store.get_mcp_server("demo")), ensure_ascii=False),
            json.dumps(self.store.list_mcp_servers(), ensure_ascii=False),
        ):
            self.assertNotIn(secret, payload)
        self.assertIn("API_TOKEN", json.dumps(self.console.panel(), ensure_ascii=False))

    def test_a_section_only_fits_the_transport_it_belongs_to(self):
        self.console.save(
            self.form(transport="http", command="", url="https://x.invalid/mcp")
        )
        with self.assertRaises(McpConsoleError) as caught:
            self.console.set_credentials("demo", env={"API_TOKEN": "sk-a"})
        self.assertEqual(caught.exception.code, "mcp_credentials_section")
        self.console.save(self.form(server_id="local"))
        with self.assertRaises(McpConsoleError) as second:
            self.console.set_credentials("local", headers={"Authorization": "Bearer sk-b"})
        self.assertEqual(second.exception.code, "mcp_credentials_section")

    def test_revalidating_a_form_writes_nothing_and_opens_no_socket(self):
        # 「重新校验」只回答字段能不能用：不写表、不建传输、不出网。
        result = self.console.validate(self.form())
        self.assertTrue(result["ok"])
        self.assertEqual(result["server_id"], "demo")
        self.assertFalse(result["exists"])
        self.assertEqual(result["discovered"], 0)
        self.assertFalse(result["credentials"])
        self.assertIsNone(self.store.get_mcp_server("demo"))
        self.assertEqual(self.built, [])

        self.console.save(self.form())
        self.console.connect("demo")
        calls = len(self.stub.calls)
        built = len(self.built)
        again = self.console.validate(self.form())
        self.assertTrue(again["exists"])
        self.assertEqual(again["discovered"], 2)
        self.assertEqual(again["enabled_tools"], 0)
        self.assertEqual(len(self.stub.calls), calls, "校验不该再发一帧")
        self.assertEqual(len(self.built), built, "校验不该造出传输")

        for bad in ("", "Demo Server"):
            with self.subTest(bad=bad):
                with self.assertRaises(McpConsoleError) as caught:
                    self.console.validate(self.form(server_id=bad))
                self.assertIn(caught.exception.code, ("mcp_bad_id", "mcp_invalid_id"))
        with self.assertRaises(McpConsoleError) as locked:
            self.console.validate(
                self.form(transport="http", command="", url="https://x.invalid/mcp")
            )
        self.assertEqual(locked.exception.code, "mcp_transport_locked")
        self.assertIsNone(self.store.get_mcp_server("x"))

    def test_replacing_one_credential_keeps_its_siblings(self):
        # set_credentials 整段替换，服务不了「只换这一个」：合并必须发生在服务端。
        self.console.save(self.form())
        self.console.set_credentials("demo", env={"API_TOKEN": "sk-one", "OTHER": "sk-two"})
        view = self.console.set_credential("demo", "API_TOKEN", "sk-three")
        self.assertEqual(
            [(item["name"], item["masked"]) for item in view["sections"][keys.SECTION_ENV]],
            [
                ("API_TOKEN", app_settings.mask_secret("sk-three")),
                ("OTHER", app_settings.mask_secret("sk-two")),
            ],
        )
        stored = keys.server_credentials("demo")
        self.assertEqual(stored["env"]["API_TOKEN"], "sk-three")
        self.assertEqual(stored["env"]["OTHER"], "sk-two")
        # 掩码之外的原文既不在面板 JSON 里，也不在活体 spec 的旧值上。
        self.assertNotIn("sk-three", json.dumps(self.console.panel(), ensure_ascii=False))
        self.assertEqual(self.service.spec("demo").env["OTHER"], "sk-two")

        view = self.console.set_credential("demo", "OTHER", remove=True)
        self.assertEqual([item["name"] for item in view["sections"][keys.SECTION_ENV]], ["API_TOKEN"])
        with self.assertRaises(McpConsoleError) as missing:
            self.console.set_credential("demo", "NOPE", remove=True)
        self.assertEqual(missing.exception.code, "mcp_unknown_credential")
        with self.assertRaises(McpConsoleError) as blank:
            self.console.set_credential("demo", "   ", "sk-x")
        self.assertEqual(blank.exception.code, "mcp_bad_credential")

    def test_a_single_credential_lands_in_the_section_of_its_transport(self):
        self.console.save(
            self.form(transport="http", command="", url="https://x.invalid/mcp")
        )
        view = self.console.set_credential("demo", "Authorization", "Bearer sk-h")
        self.assertEqual(
            [item["name"] for item in view["sections"][keys.SECTION_HEADERS]],
            ["Authorization"],
        )
        self.assertEqual(view["sections"][keys.SECTION_ENV], [])
        self.assertEqual(keys.server_credentials("demo")["headers"]["Authorization"], "Bearer sk-h")
        self.assertEqual(self.service.spec("demo").headers["Authorization"], "Bearer sk-h")

    def test_removing_a_server_takes_the_switches_and_the_secret(self):
        self.console.save(self.form())
        self.console.connect("demo")
        self.store.set_mcp_server_tools("demo", ["echo_text"])
        keys.save_server("demo", env={"API_TOKEN": "sk-gone"}, path=str(self.keys_file))
        result = self.console.remove("demo")
        self.assertTrue(result["removed"])
        self.assertEqual(result["switches"], 1)
        self.assertTrue(result["credentials_cleared"])
        self.assertIsNone(self.store.get_mcp_server("demo"))
        self.assertIsNone(self.service.spec("demo"))
        self.assertFalse(keys.describe_server("demo", path=str(self.keys_file))["present"])

    def test_an_id_that_cannot_be_a_server_is_said_in_a_sentence(self):
        for bad in ("", "Demo Server", "a" * 40):
            with self.subTest(bad=bad):
                with self.assertRaises(McpConsoleError) as caught:
                    self.console.save(self.form(server_id=bad))
                self.assertIn(caught.exception.code, ("mcp_bad_id", "mcp_invalid_id"))

    def test_a_timeout_has_to_stay_in_the_range_the_page_offers(self):
        for bad in (0, 4000, "abc"):
            with self.subTest(bad=bad):
                with self.assertRaises(McpConsoleError) as caught:
                    self.console.save(self.form(timeout=bad))
                self.assertEqual(caught.exception.code, "mcp_bad_timeout")

    def test_leaving_the_timeout_empty_takes_the_default(self):
        for blank in (None, "", "  "):
            with self.subTest(blank=blank):
                row = self.console.save(self.form(timeout=blank))
                self.assertEqual(row["timeout"], 15.0)
                self.store.delete_mcp_server("demo")

    def test_an_unknown_server_id_is_a_refusal_not_a_none(self):
        with self.assertRaises(McpConsoleError) as caught:
            self.console.set_enabled("ghost", True)
        self.assertEqual(caught.exception.code, "mcp_unknown_server")

    def test_reloading_everything_refreshes_only_what_is_live(self):
        self.console.save(self.form())
        self.console.connect("demo")
        self.console.save(self.form(server_id="quiet", enabled=False))
        payload = self.console.reload_all()
        self.assertEqual(payload["issues"], [])
        self.assertEqual(
            [item["server_id"] for item in payload["refreshed"]], ["demo"]
        )
        self.assertEqual(payload["summary"]["servers"], 2)


class WebMcpApiTest(unittest.TestCase):
    """/api/mcp：面板到进程的那一段。凭据在这里只允许以掩码形状出现（Q2）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.keys_file = self.root / "mcp_keys.json"
        self.previous_env = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.keys_file)
        self.store = CompanionStore(self.root / "companion.db")
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        self.service = create_application_service(self.runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.base = "http://" + self.adapter.address

    def tearDown(self):
        self.adapter.stop()
        if self.previous_env is None:
            os.environ.pop(keys.KEYS_PATH_ENV, None)
        else:
            os.environ[keys.KEYS_PATH_ENV] = self.previous_env
        self.runtime.mcp.close_all()
        self.temp.cleanup()

    def request(self, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token if token is None else token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def panel(self):
        return self.request("/api/mcp")[1]

    def test_the_panel_endpoint_is_token_gated_and_offline_honest(self):
        self.assertEqual(self.request("/api/mcp", token="wrong")[0], 403)
        status, payload = self.request("/api/mcp")
        self.assertEqual(status, 200)
        self.assertEqual(payload["rows"], [])
        self.assertTrue(payload["connected_ui"])
        self.assertTrue(payload["offline"])
        self.assertEqual(payload["summary"]["servers"], 0)
        choices = {item["value"]: item for item in payload["transports"]}
        self.assertEqual(sorted(choices), ["http", "sse", "stdio"])
        self.assertFalse(choices["sse"]["available"])

    def test_a_saved_server_comes_back_through_the_wire(self):
        status, payload = self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "label": "示例服务器",
                "transport": "stdio",
                "command": "python server.py",
                "args": ["--verbose"],
                "timeout": 12,
                "enabled": True,
            },
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["server_id"], "demo")
        self.assertEqual(payload["result"]["args"], ["--verbose"])
        rows = self.panel()["rows"]
        self.assertEqual([row["server_id"] for row in rows], ["demo"])
        self.assertTrue(rows[0]["enabled"])
        status, payload = self.request(
            "/api/mcp", {"action": "enable", "server_id": "demo", "enabled": False}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["result"]["enabled"])
        self.assertFalse(self.panel()["rows"][0]["enabled"])

    def test_a_rejected_edit_is_a_400_with_the_sentence_the_page_shows(self):
        status, payload = self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python server.py",
                "timeout": 99999,
                "enabled": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "mcp_bad_timeout")
        self.assertIn("超时", payload["error"])
        self.assertEqual(self.panel()["rows"], [])
        self.assertEqual(
            self.request("/api/mcp", {"action": "nope"})[0], 404
        )

    def test_a_switch_can_only_name_a_tool_the_server_reported(self):
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python server.py",
                "enabled": True,
            },
        )
        self.store.set_mcp_server_discovered(
            "demo", [{"name": "echo_text", "description": "Repeat the text back."}]
        )
        status, payload = self.request(
            "/api/mcp",
            {"action": "tools", "server_id": "demo", "names": ["drop_database"], "enable": True},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "mcp_unknown_tool")
        self.assertEqual(self.store.get_mcp_server("demo")["tools"], [])

    def test_a_credential_stays_out_of_the_answer_and_the_database(self):
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python server.py",
                "enabled": True,
            },
        )
        secret = "sk-web-panel-secret"
        status, payload = self.request(
            "/api/mcp",
            {"action": "credentials", "server_id": "demo", "env": {"API_TOKEN": secret}},
        )
        self.assertEqual(status, 200)
        entry = payload["result"]["sections"][keys.SECTION_ENV][0]
        self.assertEqual(entry["name"], "API_TOKEN")
        self.assertEqual(entry["masked"], app_settings.mask_secret(secret))
        _, panel_body = self.request("/api/mcp")
        for body in (json.dumps(payload), json.dumps(panel_body)):
            self.assertNotIn(secret, body)
        self.assertIn("API_TOKEN", json.dumps(panel_body))
        # 数据库里也查不到：凭据只活在 config/mcp_keys.json，备份因此带不走它。
        self.assertNotIn(secret.encode("utf-8"), (self.root / "companion.db").read_bytes())
        self.assertIn(secret, self.keys_file.read_text(encoding="utf-8"))

    def test_removing_a_server_leaves_no_row_and_no_secret(self):
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python server.py",
                "enabled": True,
            },
        )
        self.request(
            "/api/mcp",
            {"action": "credentials", "server_id": "demo", "env": {"API_TOKEN": "sk-gone"}},
        )
        status, payload = self.request("/api/mcp", {"action": "remove", "server_id": "demo"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["removed"])
        self.assertTrue(payload["result"]["credentials_cleared"])
        self.assertEqual(self.panel()["rows"], [])
        self.assertNotIn("sk-gone", self.keys_file.read_text(encoding="utf-8"))

    def test_a_disabled_server_is_refused_over_http_too(self):
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python server.py",
                "enabled": False,
            },
        )
        status, payload = self.request("/api/mcp", {"action": "connect", "server_id": "demo"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["result"]["ok"])
        self.assertIn("停用", payload["result"]["error"])


    def test_revalidating_over_http_writes_no_row_and_opens_no_socket(self):
        record = {
            "server_id": "demo",
            "label": "示例服务器",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "timeout": 12,
        }
        status, payload = self.request("/api/mcp", {"action": "validate", "record": record})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["result"]["ok"])
        self.assertFalse(payload["result"]["exists"])
        self.assertEqual(payload["result"]["discovered"], 0)
        self.assertEqual(self.panel()["rows"], [])
        self.assertEqual(self.runtime.mcp.snapshot(), [])

        status, payload = self.request(
            "/api/mcp", {"action": "validate", "record": dict(record, server_id="Demo Server")}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_a_single_credential_edit_over_http_returns_masks_only(self):
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python",
                "args": ["server.py"],
                "enabled": True,
            },
        )
        self.request(
            "/api/mcp",
            {
                "action": "credentials",
                "server_id": "demo",
                "env": {"API_TOKEN": "sk-wire-one", "OTHER": "sk-wire-two"},
            },
        )
        status, payload = self.request(
            "/api/mcp",
            {"action": "credential", "server_id": "demo", "name": "API_TOKEN", "value": "sk-wire-three"},
        )
        self.assertEqual(status, 200, payload)
        names = [
            item["name"] for item in payload["result"]["sections"][keys.SECTION_ENV]
        ]
        self.assertEqual(names, ["API_TOKEN", "OTHER"])
        self.assertEqual(
            payload["result"]["sections"][keys.SECTION_ENV][0]["masked"],
            app_settings.mask_secret("sk-wire-three"),
        )
        # 请求体里的原文不许出现在任何一条回答里（Q2）。
        for answer in (payload, self.request("/api/mcp")[1]):
            self.assertNotIn("sk-wire-three", json.dumps(answer, ensure_ascii=False))
        stored = keys.server_credentials("demo")
        self.assertEqual(stored["env"]["API_TOKEN"], "sk-wire-three")
        self.assertEqual(stored["env"]["OTHER"], "sk-wire-two")

        status, payload = self.request(
            "/api/mcp",
            {"action": "credential", "server_id": "demo", "name": "NOPE", "remove": True},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "mcp_unknown_credential")

    def test_a_settled_remote_call_comes_back_as_a_card_that_refuses_undo(self):
        stub = StubTransport(tools=[ECHO_TOOL])
        self.runtime.mcp = McpService(transport_factory=lambda spec: stub)
        self.runtime.set_mode(AgentMode.WORK)
        self.request(
            "/api/mcp",
            {
                "action": "save",
                "server_id": "demo",
                "transport": "stdio",
                "command": "python",
                "args": ["server.py"],
                "enabled": True,
            },
        )
        self.request("/api/mcp", {"action": "connect", "server_id": "demo"})
        self.request(
            "/api/mcp",
            {"action": "tools", "server_id": "demo", "names": ["echo_text"], "enable": True},
        )

        entry = [
            item for item in build_tools(self.runtime.mcp) if item["remote_name"] == "echo_text"
        ][0]
        proposal = build_call_proposal(
            entry,
            self.runtime.mcp.spec("demo"),
            {"text": "喂"},
            session_id="main",
            mode=AgentMode.WORK,
        )
        self.store.create_proposal(proposal)

        boot = self.request("/api/bootstrap")[1]
        card = [item for item in boot["approvals"] if item["action_id"] == proposal.action_id][0]
        self.assertEqual(card["risk"], RiskLevel.EXTERNAL.value)
        self.assertEqual(card["warning"], REMOTE_WARNING)
        self.assertEqual(card["preview"]["server"]["id"], "demo")
        self.assertEqual(card["preview"]["arguments"], {"text": "喂"})
        self.assertEqual(boot["permission_label"], PERMISSION_LABELS[PERMISSION_BALANCED])
        # S04f 的第三种状态走的是另一条只读通道：待确认清单不掺入已处理完的动作。
        self.assertEqual([item["action_id"] for item in boot["remote_cards"]], [])

        status, payload = self.request(
            "/api/approvals/confirm", {"action_id": proposal.action_id}
        )
        self.assertEqual(status, 200, payload)
        boot = self.request("/api/bootstrap")[1]
        self.assertEqual(
            [item["action_id"] for item in boot["approvals"] if item["action_id"] == proposal.action_id],
            [],
        )
        remote = boot["remote_cards"]
        self.assertEqual(len(remote), 1)
        self.assertTrue(remote[0]["settled"])
        self.assertEqual(remote[0]["receipt"]["status"], "completed")
        self.assertFalse(remote[0]["receipt"]["undoable"])
        self.assertIn("tools/call", [call[0] for call in stub.calls])

        status, payload = self.request(
            "/api/receipts/undo", {"receipt_id": remote[0]["receipt"]["receipt_id"]}
        )
        self.assertEqual(status, 409)
        self.assertIn("不提供撤销", payload["error"])
        self.assertEqual(
            len([call for call in stub.calls if call[0] == "tools/call"]),
            1,
            "点「撤销」不该把调用再发一次",
        )


class CredentialPlaneTest(unittest.TestCase):
    """Q2 四条硬判据里的两条：备份与导出的名录里没有 `mcp_keys.json`，doctor 只报存在与权限。"""

    SECRET = "sk-credential-plane-canary"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.keys_file = self.root / "mcp_keys.json"
        self.previous_env = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.keys_file)
        self.store = CompanionStore(self.data / "companion.db")
        self.store.upsert_mcp_server(
            {
                "server_id": "demo",
                "label": "示例服务器",
                "transport": "stdio",
                "command": "python server.py",
                "args": ["--verbose"],
                "enabled": True,
                "tools": ["echo_text"],
                "discovered": [{"name": "echo_text", "description": "Repeat."}],
            }
        )
        keys.save_server(
            "demo",
            env={"API_TOKEN": self.SECRET},
            headers={"Authorization": "Bearer " + self.SECRET},
            path=str(self.keys_file),
        )

    def tearDown(self):
        if self.previous_env is None:
            os.environ.pop(keys.KEYS_PATH_ENV, None)
        else:
            os.environ[keys.KEYS_PATH_ENV] = self.previous_env
        self.temp.cleanup()

    def test_an_export_zip_never_names_the_keys_file(self):
        target = self.root / "out.zip"
        export_data(self.data, output_path=target)
        with zipfile.ZipFile(str(target)) as archive:
            names = archive.namelist()
            self.assertIn("export.json", names)
            # 名录里可以有会话与工具结果，就是不能有凭据文件或其改名。
            self.assertFalse([name for name in names if "keys" in name])
            self.assertFalse([name for name in names if "mcp" in name and name.endswith(".json")])
            body = archive.read("export.json").decode("utf-8")
        self.assertNotIn(self.SECRET, body)
        self.assertNotIn("API_TOKEN", body)
        # 导出里留着的是地址与开关——它们本来就该能被恢复；被排除的只有凭据。
        self.assertIn("demo", body)

    def test_a_database_backup_carries_addresses_but_not_secrets(self):
        backup = create_backup(self.data, reason="manual")
        self.assertTrue(Path(backup["path"]).exists())
        self.assertNotIn(
            self.SECRET.encode("utf-8"), Path(backup["path"]).read_bytes()
        )
        self.assertNotIn(self.SECRET, Path(backup["manifest_path"]).read_text(encoding="utf-8"))
        connection = sqlite3.connect(backup["path"])
        try:
            row = connection.execute(
                "SELECT command, tools FROM mcp_servers WHERE server_id = 'demo'"
            ).fetchone()
        finally:
            # with sqlite3 只提交不关句柄：Windows 上留着它就删不掉临时目录。
            connection.close()
        self.assertEqual(row[0], "python server.py")
        self.assertIn("echo_text", row[1])

    def test_the_files_handed_to_a_person_are_names_and_masks_only(self):
        described = json.dumps(
            keys.describe_server("demo", path=str(self.keys_file)), ensure_ascii=False
        )
        self.assertNotIn(self.SECRET, described)
        self.assertIn("API_TOKEN", described)
        self.assertIn(app_settings.mask_secret(self.SECRET), described)
        # doctor 拿得到本机路径，页面不行：面板的 keys 段是去掉 path 的 status()。
        console = McpConsole(self.store, lambda: McpService())
        panel = json.dumps(console.panel(), ensure_ascii=False)
        self.assertNotIn(self.SECRET, panel)
        self.assertNotIn(str(self.keys_file), panel)
        self.assertNotIn("path", json.dumps(console.panel()["keys"]))
        console.service.close_all()

    def test_doctor_reports_existence_and_permission_and_no_value(self):
        repo = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment[keys.KEYS_PATH_ENV] = str(self.keys_file)
        environment["PYTHONPATH"] = str(repo)
        environment["PYTHONIOENCODING"] = "utf-8"
        finished = subprocess.run(
            [sys.executable, str(repo / "doctor.py")],
            cwd=str(repo),
            env=environment,
            capture_output=True,
            timeout=180,
        )
        output = finished.stdout.decode("utf-8", "replace")
        self.assertIn("MCP servers", output)
        self.assertNotIn(self.SECRET, output)
        self.assertNotIn("API_TOKEN", output)
        self.assertIn("keys=present", output)


class CredentialPlaneGuardTest(unittest.TestCase):
    """源码层面的守卫：这几处将来不能悄悄改成读凭据。"""

    def test_no_export_or_backup_path_reads_the_keys_file(self):
        repo = Path(__file__).resolve().parents[1] / "agent_framework" / "companion"
        for name in ("maintenance.py", "importer.py", "eval_recall.py"):
            source = (repo / name).read_text(encoding="utf-8")
            self.assertNotIn("mcp_keys", source)
            self.assertNotIn("from .mcp", source)

    def test_the_store_never_touches_the_credential_module(self):
        # store.py 的注释里允许出现文件名（那是给后来人的说明），代码里不允许：
        # 一旦 import 进来，数据库写入就有了拿到明文的路径，Q2 就废了。
        source = (
            Path(__file__).resolve().parents[1]
            / "agent_framework"
            / "companion"
            / "store.py"
        ).read_text(encoding="utf-8")
        for needle in ("from .mcp", "import mcp", "transport_payload(", "mask_secret("):
            self.assertNotIn(needle, source)


class SkillBorrowTest(unittest.TestCase):

    def test_external_effect_uses_the_external_risk(self):
        from agent_framework.companion.skills.library import EFFECT_CEILING, EFFECT_RISK, RISK_ORDER

        self.assertIs(EFFECT_RISK["external"], RiskLevel.EXTERNAL)
        self.assertIs(EFFECT_CEILING["external"], RiskLevel.EXTERNAL)
        # 借用检查靠 RISK_ORDER 比大小：漏一档就是 ValueError，扫技能时直接崩。
        self.assertEqual(sorted(map(str, RISK_ORDER)), sorted(map(str, RiskLevel)))

    def test_a_remote_tool_borrows_inside_the_ceiling(self):
        from agent_framework.companion.skills.library import SkillLibrary

        risks = {"mcp__demo__echo_text": RiskLevel.EXTERNAL}
        self.assertIs(
            SkillLibrary._effect_risk(
                type("M", (), {"effect_kind": "external", "tools": ("mcp__demo__echo_text",)})(),
                risks,
            ),
            RiskLevel.EXTERNAL,
        )


if __name__ == "__main__":
    unittest.main()
