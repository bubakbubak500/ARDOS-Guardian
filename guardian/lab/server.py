"""Loopback-only authenticated API shared by the dashboard and automation clients."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse, parse_qs

from .model import template, validate
from .runner import Lab


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port=0):
        root.mkdir(parents=True, exist_ok=True)
        self._lock_file = (root / "server.lock").open("a+b")
        self._lock_file.seek(0)
        if __import__("sys").platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self._lock_file.close()
                raise RuntimeError("A LAB service already owns this state directory")
        else:
            import fcntl
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lab = Lab(root)
        self.token = secrets.token_urlsafe(32)
        try:
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            self._lock_file.close()
            raise

    def server_close(self):
        super().server_close()
        self._lock_file.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def stop(self):
        self.lab.stop()
        if self.lab.thread:
            self.lab.thread.join(timeout=60)
        self.shutdown()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, code=200, content_type="application/json; charset=utf-8", filename=None):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.reply({"error": "Invalid Host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {self.server.url, f"http://localhost:{self.server.server_port}"}:
            self.reply({"error": "Cross-origin requests are disabled"}, 403)
            return False
        supplied = self.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + self.server.token):
            self.reply({"error": "Bearer token required"}, 401)
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/":
            self.reply(Path(__file__).with_name("dashboard.html").read_bytes(),
                       content_type="text/html; charset=utf-8")
            return
        if not self.authorized():
            return
        try:
            if path.path == "/api/status":
                self.reply(self.server.lab.snapshot())
            elif path.path == "/api/events":
                after = int(parse_qs(path.query).get("after", ["0"])[0])
                self.reply(self.server.lab.event_slice(after))
            elif path.path == "/api/template":
                self.reply(template())
            elif path.path == "/api/devices":
                from dataclasses import asdict
                from guardian.modem.audio import scan_audio_devices
                self.reply(asdict(scan_audio_devices()))
            elif path.path == "/api/runs":
                items = []
                for summary in sorted((self.server.lab.root / "runs").glob("*/summary.json"), reverse=True):
                    value = json.loads(summary.read_text(encoding="utf-8"))
                    items.append({k: value.get(k) for k in ("run_id", "state", "parity", "trial", "error")})
                self.reply(items)
            elif path.path.startswith("/api/export/"):
                run_id = path.path.rsplit("/", 1)[1]
                self.reply(self.server.lab.export(run_id), content_type="application/zip",
                           filename=f"guardian-lab-{run_id}.zip")
            else:
                self.reply({"error": "Unknown endpoint"}, 404)
        except (ValueError, OSError) as exc:
            self.reply({"error": str(exc)}, 400)

    def do_POST(self):
        if not self.authorized():
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                raise ValueError("JSON request must be 1 byte..2 MiB")
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict):
                raise ValueError("Expected JSON object")
            path = urlparse(self.path).path
            if path == "/api/validate":
                self.reply({"plan": validate(value), "hardware_started": False})
            elif path == "/api/run":
                self.reply({"run_id": self.server.lab.start(value["plan"], armed=value.get("arm") is True)}, 202)
            elif path == "/api/stop":
                self.server.lab.stop()
                self.reply({"state": "stopping"})
            elif path == "/api/shutdown":
                self.reply({"state": "shutting_down"})
                threading.Thread(target=self.server.stop, daemon=True).start()
            else:
                self.reply({"error": "Unknown endpoint"}, 404)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.reply({"error": str(exc)}, 400)
