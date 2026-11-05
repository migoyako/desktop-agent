"""stdio MCP transport: the server is a child process we talk to over pipes.

The child is whatever the user added to the config, so nothing here assumes it
behaves: a line longer than the cap is cut and reported, a silent child is given
a deadline and then killed, and stderr is drained continuously so a chatty server
cannot fill the OS buffer and deadlock itself before it answers.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from .jsonrpc import (
    MAX_MESSAGE_BYTES,
    McpError,
    McpProtocolError,
    McpTimeout,
    IdSequence,
    decode,
    encode,
    notification,
    request,
    result_of,
)

# readline() returns at most this many bytes, so an endless "line" from a broken
# server cannot make us allocate without bound.
_READ_HINT = MAX_MESSAGE_BYTES + 1
_STDERR_KEEP = 4000
_CLOSE_WAIT = 2.0
_JOIN_WAIT = 2.0
_EOF = object()


class StdioTransport:
    """Newline-delimited JSON-RPC over a subprocess's stdin/stdout."""

    def __init__(
        self,
        command,
        args=None,
        env=None,
        cwd=None,
        timeout=15.0,
        label="",
    ):
        self.argv = _argv(command, args)
        self.env = env
        self.cwd = cwd
        self.timeout = float(timeout)
        self.label = label or " ".join(self.argv[:2]) or "stdio"
        self._process = None
        self._inbox = queue.Queue()
        self._stderr_tail = ""
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._ids = IdSequence()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def connected(self) -> bool:
        return self._process is not None and not self._closed

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail

    def describe(self) -> str:
        return "stdio {}".format(self.label)

    def connect(self) -> None:
        with self._lock:
            if self._closed:
                raise McpError("传输已关闭，请重新连接服务器。")
            if self._process is not None:
                return
            if not self.argv:
                raise McpError("服务器命令为空，无法启动。")
            try:
                self._process = subprocess.Popen(
                    self.argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.cwd or None,
                    env=_env(self.env),
                    bufsize=0,
                )
            except OSError as error:
                self._process = None
                raise McpError(
                    "启动服务器进程失败：{}".format(error),
                    "mcp_launch_failed",
                    detail=str(error),
                )
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(self._process.stdout,),
                name="mcp-stdio-out",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr,),
                name="mcp-stdio-err",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

    def request(self, method: str, params=None, timeout=None) -> Any:
        message_id = self._ids.next()
        payload = encode(request(message_id, method, params))
        self._write(payload)
        limit = self.timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._fail()
                raise McpTimeout(
                    "服务器在 {} 秒内没有回应 {}。".format(_seconds(limit), method),
                    detail=self._diagnostics(),
                )
            try:
                item = self._inbox.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self._process is not None and self._process.poll() is not None:
                    raise McpError(
                        "服务器进程已退出（退出码 {}）。".format(self._process.poll()),
                        "mcp_server_exit",
                        detail=self._diagnostics(),
                    )
                continue
            if item is _EOF:
                self._fail()
                raise McpError(
                    "服务器关闭了输出，没有给出结果。",
                    "mcp_server_closed",
                    detail=self._diagnostics(),
                )
            try:
                message = decode(item)
            except McpError:
                self._fail()
                raise
            if "id" not in message:
                # Server-initiated notification (logs, progress). Not ours to answer.
                continue
            if str(message.get("id")) != str(message_id):
                self._fail()
                raise McpProtocolError(
                    "响应的 id 与请求不一致（期望 {}，得到 {}）。".format(
                        message_id, message.get("id")
                    ),
                    detail=self._diagnostics(),
                )
            return result_of(message, method)

    def notify(self, method: str, params=None) -> None:
        self._write(encode(notification(method, params)))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            threads = [
                thread
                for thread in (self._stdout_thread, self._stderr_thread)
                if thread is not None
            ]
            self._process = None
        if process is None:
            return
        _close_stream(process.stdin)
        # Terminate ladder: let it notice stdin closed, then SIGTERM, then kill.
        try:
            process.wait(timeout=_CLOSE_WAIT)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=_CLOSE_WAIT)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=_CLOSE_WAIT)
                except subprocess.TimeoutExpired:
                    pass
        # Never close a pipe a reader thread is blocked on: closing a file object
        # from another thread while it is being read crashes the interpreter. The
        # child is gone now, so those threads get their EOF and leave.
        for thread in threads:
            thread.join(timeout=_JOIN_WAIT)
        if not any(thread.is_alive() for thread in threads):
            _close_stream(process.stdout)
            _close_stream(process.stderr)

    # internals

    def _fail(self) -> None:
        """A protocol break means the stream is no longer trustworthy."""
        self.close()

    def _write(self, payload: bytes) -> None:
        process = self._process
        if process is None or self._closed:
            raise McpError("服务器尚未连接。", "mcp_not_connected")
        stdin = process.stdin
        if stdin is None:
            raise McpError("服务器没有可写的输入。", "mcp_no_stdin")
        try:
            stdin.write(payload)
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            code = None if process.poll() is None else process.poll()
            raise McpError(
                "写入服务器输入失败{}。".format(
                    "（进程已退出，退出码 {}）".format(code) if code is not None else ""
                ),
                "mcp_write_failed",
                detail="{} {}".format(error, self._diagnostics()).strip(),
            )

    def _diagnostics(self) -> str:
        tail = self._stderr_tail.strip()
        if self._process is not None:
            return "退出码：{}；stderr：{}".format(self._process.poll(), tail)
        return "stderr：{}".format(tail) if tail else ""

    def _read_stdout(self, stream) -> None:
        if stream is None:
            self._inbox.put(_EOF)
            return
        while True:
            try:
                line = stream.readline(_READ_HINT)
            except (OSError, ValueError):
                self._inbox.put(_EOF)
                return
            if not line:
                self._inbox.put(_EOF)
                return
            if not line.endswith(b"\n"):
                if len(line) < _READ_HINT:
                    # EOF without a trailing newline: still a whole message.
                    self._inbox.put(line)
                    self._inbox.put(_EOF)
                    return
                self._inbox.put(_oversize(line, stream))
            else:
                self._inbox.put(line)

    def _drain_stderr(self, stream) -> None:
        if stream is None:
            return
        buffer = bytearray()
        while True:
            try:
                chunk = stream.read(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            buffer.extend(chunk)
            if len(buffer) > _STDERR_KEEP:
                del buffer[: len(buffer) - _STDERR_KEEP]
            self._stderr_tail = buffer.decode("utf-8", "replace")


def _oversize(line: bytes, stream) -> bytes:
    """Return an over-cap payload (so the caller can name the size) and drop the rest."""
    payload = line
    while True:
        try:
            rest = stream.readline(_READ_HINT)
        except (OSError, ValueError):
            return payload
        if not rest or rest.endswith(b"\n"):
            return payload
        if len(rest) >= _READ_HINT:
            continue
        return payload


def _argv(command, args) -> List[str]:
    if isinstance(command, str):
        argv = [command]
    elif isinstance(command, (list, tuple)):
        argv = [str(item) for item in command if str(item).strip()]
    else:
        argv = []
    argv = [item for item in argv if item.strip()]
    if isinstance(args, (list, tuple)):
        argv.extend(str(item) for item in args)
    elif isinstance(args, str) and args.strip():
        argv.append(args)
    return argv


def _env(extra) -> Optional[Dict[str, str]]:
    if not extra:
        return None
    base = dict(os.environ)
    for key, value in extra.items():
        base[str(key)] = str(value)
    return base


def _close_stream(stream) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


def _seconds(value) -> str:
    try:
        return "{:g}".format(round(float(value), 2))
    except (TypeError, ValueError):
        return str(value)
