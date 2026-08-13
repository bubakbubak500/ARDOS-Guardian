"""Minimal paired HTTP server used by the offline phone companion."""

from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .controller import CompanionError


SESSION_COOKIE = "guardian_session"
MAX_REQUEST = 32_768
_MESSAGE_PATH = re.compile(r"^/api/messages/(\d+)/(read)$")


class CompanionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, controller) -> None:
        super().__init__(address, handler)
        self.controller = controller


class CompanionRequestHandler(BaseHTTPRequestHandler):
    server_version = "GuardianCompanion/2"

    @property
    def controller(self):
        return self.server.controller

    def log_message(self, _format: str, *_args) -> None:
        # Routine polling must not fill Guardian's log or stderr.
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        if parsed.path == "/api/info":
            self._json(HTTPStatus.OK, self.controller.public_info())
            return
        if parsed.path == "/api/state":
            if not self._authenticated():
                return
            query = parse_qs(parsed.query)
            try:
                after = int(query.get("after", ["-1"])[0])
            except ValueError:
                after = -1
            self._json(HTTPStatus.OK, self.controller.wait_for_state(after))
            return
        if parsed.path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        try:
            payload = self._read_json()
        except CompanionError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if parsed.path == "/api/pair":
            try:
                session = self.controller.pair(
                    str(payload.get("token", "")),
                    name=str(payload.get("name", "Phone")),
                    address=self.client_address[0],
                )
            except CompanionError as exc:
                self._json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
                return
            self._json(
                HTTPStatus.OK,
                {"ok": True},
                cookie=(
                    f"{SESSION_COOKIE}={session}; Path=/; HttpOnly; "
                    "SameSite=Strict; Max-Age=43200"
                ),
            )
            return
        if not self._authenticated():
            return
        match = _MESSAGE_PATH.fullmatch(parsed.path)
        action = None
        if parsed.path == "/api/messages":
            action = "compose"
        elif match:
            action = "mark_read"
            payload["id"] = int(match.group(1))
        elif parsed.path == "/api/notes":
            action = "save_note"
        elif parsed.path == "/api/notes/delete":
            action = "delete_note"
        elif parsed.path == "/api/checkin":
            action = "start_checkin"
        elif parsed.path == "/api/checkin/clear":
            action = "clear_checkin"
        elif parsed.path == "/api/ping":
            action = "ping_station"
        if action is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
            return
        try:
            result = self.controller.submit(action, payload)
        except CompanionError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
            return
        self._json(HTTPStatus.OK, result)

    def _authenticated(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        token = morsel.value if morsel is not None else ""
        if token and self.controller.authenticate(
            token, address=self.client_address[0]
        ):
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": "Pair this phone again."})
        return False

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise CompanionError("Invalid request length.") from None
        if length < 0 or length > MAX_REQUEST:
            raise CompanionError("Request is too large.")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CompanionError("Request is not valid JSON.") from None
        if not isinstance(value, dict):
            raise CompanionError("Request must be a JSON object.")
        return value

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        if ".." in name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        resource = files("guardian.companion.web").joinpath(name)
        try:
            data = resource.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = (
            "application/manifest+json"
            if name.endswith(".webmanifest")
            else mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if name == "index.html" else "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: HTTPStatus, value, *, cookie: str = "") -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionError:
            # Phones routinely suspend or roam while a long poll is pending.
            pass
