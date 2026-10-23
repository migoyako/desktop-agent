"""Run the current Python Agent as a local OpenAI-compatible service."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys

from agent_framework.service import AgentService, MODEL_ID
from agent_framework.service import extract_latest_user_message


SERVICE = None


class AgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "CurrentAgent/1.0"

    def _send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok", "model": MODEL_ID})
            return
        if self.path == "/v1/models":
            self._send_json(200, SERVICE.models_payload())
            return
        self._send_json(404, {"error": {"message": "Not found"}})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "Not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = body.get("messages", [])
            prompt = extract_latest_user_message(messages)
            if not prompt:
                self._send_json(
                    400,
                    {"error": {"message": "No user message found"}},
                )
                return

            session_id = SERVICE.get_session_id(body, self.headers)
            answer = SERVICE.chat(session_id, prompt)
            model = body.get("model") or MODEL_ID
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in SERVICE.stream_chunks(model, answer):
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                self.close_connection = True
                return

            self._send_json(200, SERVICE.completion_payload(model, session_id, answer))
        except Exception as error:
            self._send_json(
                500,
                {"error": {"message": str(error), "type": "agent_error"}},
            )

    def log_message(self, format_string, *args):
        try:
            message = format_string % args if args else format_string
        except (TypeError, ValueError):
            message = "{} {}".format(format_string, args)
        sys.stderr.write("[agent-service] " + message + "\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Current Python Agent HTTP bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    global SERVICE
    SERVICE = AgentService(offline=args.offline)
    server = ThreadingHTTPServer((args.host, args.port), AgentRequestHandler)
    print(
        "Current Python Agent listening on http://{}:{}/v1".format(
            args.host,
            args.port,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
