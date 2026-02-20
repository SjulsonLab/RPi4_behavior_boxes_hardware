import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def make_handler(registry, static_dir: str):
    class MockWebHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send_json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, status=HTTPStatus.OK):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, relative_path: str):
            if relative_path in ("", "/"):
                relative_path = "index.html"
            if relative_path.startswith("/"):
                relative_path = relative_path[1:]

            file_path = os.path.normpath(os.path.join(static_dir, relative_path))
            if not file_path.startswith(os.path.abspath(static_dir)):
                self._send_text("Invalid path", status=HTTPStatus.BAD_REQUEST)
                return

            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                self._send_text("Not found", status=HTTPStatus.NOT_FOUND)
                return

            ext = os.path.splitext(file_path)[1]
            content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
            with open(file_path, "rb") as f:
                body = f.read()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return {}
            raw_body = self.rfile.read(content_length)
            try:
                return json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON body")

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                self._send_json(registry.get_state())
                return

            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                limit = 200
                try:
                    if "limit" in query:
                        limit = int(query["limit"][0])
                except Exception:
                    limit = 200
                self._send_json(registry.get_events(limit=limit))
                return

            if parsed.path in ("/", "/index.html", "/app.js", "/style.css"):
                self._send_static(parsed.path)
                return

            self._send_text("Not found", status=HTTPStatus.NOT_FOUND)

        def do_POST(self):
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/input/"):
                self._send_text("Not found", status=HTTPStatus.NOT_FOUND)
                return

            remainder = parsed.path[len("/api/input/"):]
            parts = [p for p in remainder.split("/") if p]
            if len(parts) != 2:
                self._send_text("Bad request", status=HTTPStatus.BAD_REQUEST)
                return

            label = unquote(parts[0])
            action = parts[1]

            try:
                if action == "press":
                    registry.press_input(label=label, source="ui")
                elif action == "release":
                    registry.release_input(label=label, source="ui")
                elif action == "pulse":
                    body = self._read_json()
                    duration_ms = int(body.get("duration_ms", 100))
                    registry.pulse_input(label=label, duration_ms=duration_ms, source="pulse")
                else:
                    self._send_text("Unknown action", status=HTTPStatus.BAD_REQUEST)
                    return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"ok": True})

    return MockWebHandler
