"""Fake MCP server spoken over stdio, used by tests/test_companion_mcp.py.

Run as ``python mcp_stdio_server.py <mode>``. It answers initialize/tools/list/
tools/call and each mode breaks the protocol in exactly one way, so a test can
name the failure it is checking instead of trusting a real server's mood.
"""

import json
import sys
import time

TOOLS = [
    {
        "name": "echo_text",
        "description": "Repeat the text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "no_schema",
        "description": "A tool that forgot to declare a schema.",
    },
]


def reply(payload):
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        # The client gave up on us; that is its decision, not a crash to print.
        sys.exit(0)


def answer(message):
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        reply(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "0"},
                },
            }
        )
        return True
    if method == "notifications/initialized":
        return True
    if method == "tools/list":
        reply(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"tools": TOOLS},
            }
        )
        return True
    if method == "tools/call":
        name = (message.get("params") or {}).get("name")
        if name == "echo_text":
            text = ((message.get("params") or {}).get("arguments") or {}).get("text", "")
            reply(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            )
        elif name == "boom":
            reply(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32603, "message": "kaboom"},
                }
            )
        else:
            reply(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": [{"type": "text", "text": "调用了 {}".format(name)}],
                        "isError": True,
                    },
                }
            )
        return True
    if message_id is not None:
        reply(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        )
        return True
    return True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    sys.stderr.write("fake server starting\n")
    sys.stderr.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        if mode == "crash":
            return
        if mode == "silent":
            time.sleep(30)
            return
        if mode == "garbage":
            sys.stdout.write("这不是 JSON\n")
            sys.stdout.flush()
            continue
        if mode == "huge":
            sys.stdout.write(json.dumps({"id": 1, "jsonrpc": "2.0", "result": {"blob": "x" * 6_000_000}}) + "\n")
            sys.stdout.flush()
            continue
        if mode == "mismatch":
            reply({"jsonrpc": "2.0", "id": 9999, "result": {"tools": []}})
            continue
        if mode == "notification_first":
            reply({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info"}})
            reply({"jsonrpc": "2.0", "id": json.loads(line).get("id"), "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {}}})
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not answer(message):
            return


if __name__ == "__main__":
    main()
