#!/usr/bin/env python3
"""
Power agent: runs on each computer you want to control (Mac or Windows).

It listens on your home network and does shutdown / restart / sleep when
the hub (on the NUC) asks with the right token. Only uses the Python
standard library, so there's nothing to install besides Python.

    python3 agent.py            # uses agent_config.json next to this file
"""
import hmac
import json
import os
import platform
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("AGENT_CONFIG", os.path.join(HERE, "agent_config.json"))

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

TOKEN = CONFIG["token"]
PORT = int(CONFIG.get("port", 8765))
DRY_RUN = bool(CONFIG.get("dry_run", False))
SYSTEM = platform.system()  # "Windows", "Darwin" (Mac), "Linux"

if TOKEN in ("", "change-me"):
    sys.exit("Set a real 'token' in agent_config.json first.")

COMMANDS = {
    "Windows": {
        "shutdown": ["shutdown", "/s", "/t", "5"],
        "restart": ["shutdown", "/r", "/t", "5"],
        # Sleeps (or hibernates if hibernation is on).
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
    },
    "Darwin": {
        "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
        "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
        "sleep": ["pmset", "sleepnow"],
    },
    "Linux": {
        "shutdown": ["systemctl", "poweroff"],
        "restart": ["systemctl", "reboot"],
        "sleep": ["systemctl", "suspend"],
    },
}


def run_action(action):
    cmd = COMMANDS.get(SYSTEM, {}).get(action)
    if cmd is None:
        raise ValueError(f"Unsupported action '{action}' on {SYSTEM}")
    if DRY_RUN:
        print(f"[dry run] would run: {' '.join(cmd)}", flush=True)
        return
    # Small delay so the HTTP reply gets back to the hub before we go down.
    threading.Timer(1.0, lambda: subprocess.Popen(cmd)).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "PowerAgent/1.0"

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        given = self.headers.get("X-Token", "")
        return hmac.compare_digest(given.encode(), TOKEN.encode())

    def do_GET(self):
        if self.path == "/ping":
            if not self._authorized():
                return self._reply(401, {"error": "bad token"})
            return self._reply(200, {"ok": True, "os": SYSTEM, "host": platform.node()})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/power":
            return self._reply(404, {"error": "not found"})
        if not self._authorized():
            return self._reply(401, {"error": "bad token"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            action = json.loads(self.rfile.read(length) or b"{}").get("action")
            run_action(action)
        except ValueError as e:
            return self._reply(400, {"error": str(e)})
        print(f"{self.client_address[0]} -> {action}", flush=True)
        self._reply(200, {"ok": True, "action": action})

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; actions are printed above


if __name__ == "__main__":
    print(f"Power agent on {SYSTEM}, port {PORT}{' (DRY RUN)' if DRY_RUN else ''}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
