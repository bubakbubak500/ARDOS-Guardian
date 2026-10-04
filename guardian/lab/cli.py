"""Source and frozen LAB entry points; the API never requires GUI automation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
import urllib.request
import webbrowser


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guardian production LAB")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "template", "identity", "self-test", "validate", "status", "run", "stop", "shutdown", "export"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--connection", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--arm", action="store_true")
    args = parser.parse_args(argv)
    from guardian.config import config_dir
    root = args.root or config_dir() / "lab"
    connection_path = args.connection or root / "connection.json"
    if args.command == "serve":
        from .server import Server
        from .runner import write_json
        server = Server(root, args.port)
        write_json(connection_path, {"url": server.url, "token": server.token,
                                     "pid": __import__("os").getpid()})
        if not args.no_browser:
            webbrowser.open(server.url + "/#" + server.token)
        if sys.stdout:
            print(f"Guardian LAB: {server.url} (connection: {connection_path})", flush=True)
        def stop(*_):
            threading.Thread(target=server.stop, daemon=True).start()
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.lab.stop()
            server.server_close()
            try:
                if json.loads(connection_path.read_text())["token"] == server.token:
                    connection_path.unlink()
            except (OSError, ValueError, KeyError):
                pass
        return
    from .model import template, validate
    if args.command == "template":
        result = template()
    elif args.command == "self-test":
        from .selftest import run
        result = run()
    elif args.command == "identity":
        from .identity import identity
        result = identity()
    elif args.command == "validate":
        if not args.plan:
            parser.error("validate requires --plan")
        result = validate(json.loads(args.plan.read_text(encoding="utf-8-sig")))
    else:
        connection = json.loads(connection_path.read_text(encoding="utf-8"))
        path, data = f"/api/{args.command}", None
        if args.command == "run":
            if not args.plan or not args.arm:
                parser.error("run requires --plan and --arm")
            data = {"plan": json.loads(args.plan.read_text(encoding="utf-8-sig")), "arm": True}
        elif args.command in {"stop", "shutdown"}:
            data = {}
        elif args.command == "export":
            if not args.run_id or not args.output:
                parser.error("export requires --run-id and --output")
            path = "/api/export/" + args.run_id
        request = urllib.request.Request(connection["url"] + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        if args.command == "export":
            args.output.write_bytes(body)
            return
        result = json.loads(body)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    elif sys.stdout:
        print(text)


def launch_dashboard():
    """Called by the normal GUI; reuse only a responsive current-version LAB."""
    import subprocess
    from guardian import __version__
    from guardian.config import config_dir
    root = config_dir() / "lab"
    connection_path = root / "connection.json"
    try:
        connection = json.loads(connection_path.read_text(encoding="utf-8"))
        request = urllib.request.Request(connection["url"] + "/api/status",
            headers={"Authorization": "Bearer " + connection["token"]})
        with urllib.request.urlopen(request, timeout=0.5) as response:
            state = json.load(response)
        if state["version"] != __version__:
            raise RuntimeError("Close the LAB from the previous Guardian version first")
        webbrowser.open(connection["url"] + "/#" + connection["token"])
        return
    except (OSError, ValueError, KeyError):
        pass
    command = ([sys.executable, "--lab"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "guardian", "--lab"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / "server.log").open("a", encoding="utf-8") as log:
        subprocess.Popen(command + ["serve", "--root", str(root)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
