#!/usr/bin/env python3
"""
Power hub: runs on the always-on NUC.

- Serves the phone web app (static/).
- Wakes computers with Wake-on-LAN magic packets.
- Forwards shutdown / restart / sleep to the agent on each computer.
- Reports which computers are on.

Standard library only.

    python3 server.py           # uses config.json next to this file
"""
import hmac
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
CONFIG_PATH = os.environ.get("HUB_CONFIG", os.path.join(HERE, "config.json"))

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

PASSWORD = CONFIG["password"]
PORT = int(CONFIG.get("port", 8080))
COMPUTERS = {c["id"]: c for c in CONFIG["computers"]}
AGENT_ACTIONS = {"shutdown", "restart", "sleep"}

if PASSWORD in ("", "change-me"):
    sys.exit("Set a real 'password' in config.json first.")


def send_magic_packet(mac, broadcast="255.255.255.255"):
    hex_mac = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hex_mac) != 12:
        raise ValueError(f"Bad MAC address: {mac}")
    packet = b"\xff" * 6 + bytes.fromhex(hex_mac) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(packet, (broadcast, port))


def agent_request(c, method, path, body=None, timeout=3.0):
    url = f"http://{c['ip']}:{c.get('agent_port', 8765)}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Token", c["agent_token"])
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def is_online(c):
    try:
        agent_request(c, "GET", "/ping", timeout=1.5)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


POOL = ThreadPoolExecutor(max_workers=8)


class Handler(BaseHTTPRequestHandler):
    server_version = "PowerHub/1.0"

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        given = self.headers.get("X-Password", "")
        ok = hmac.compare_digest(given.encode(), PASSWORD.encode())
        if not ok:
            time.sleep(1)  # slow down password guessing
        return ok

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._json(404, {"error": "not found"})
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/computers":
            if not self._authorized():
                return self._json(401, {"error": "wrong password"})
            items = list(COMPUTERS.values())
            online = list(POOL.map(is_online, items))
            return self._json(200, [
                {"id": c["id"], "name": c["name"], "online": on}
                for c, on in zip(items, online)
            ])
        return self._static(path)

    def do_POST(self):
        m = re.fullmatch(r"/api/computers/([\w-]+)/(\w+)", self.path)
        if not m:
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._json(401, {"error": "wrong password"})
        cid, action = m.groups()
        c = COMPUTERS.get(cid)
        if c is None:
            return self._json(404, {"error": "unknown computer"})
        try:
            if action == "wake":
                send_magic_packet(c["mac"], c.get("broadcast", "255.255.255.255"))
            elif action in AGENT_ACTIONS:
                agent_request(c, "POST", "/power", {"action": action})
            else:
                return self._json(400, {"error": "unknown action"})
        except urllib.error.HTTPError as e:
            return self._json(502, {"error": f"agent said {e.code}"})
        except (urllib.error.URLError, OSError) as e:
            print(f"{c['name']}: {action} failed: {e}", flush=True)
            return self._json(502, {"error": f"Couldn't reach {c['name']}. Is it on and is the agent running?"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        print(f"{c['name']}: {action}", flush=True)
        self._json(200, {"ok": True})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"Power hub on http://0.0.0.0:{PORT}  ({len(COMPUTERS)} computers)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
