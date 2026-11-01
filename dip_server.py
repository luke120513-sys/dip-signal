#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""輕量本機 HTTP：手機查抄底面板 + POST 觸發更新。

綁 0.0.0.0:8765，同 Wi‑Fi 手機可開 http://<LAN-IP>:8765/
外網需自行 cloudflared / ngrok（本機未預裝）。
"""
from __future__ import annotations
import json, os, socket, subprocess, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(os.path.expanduser("~/DipSignal"))
PY = ROOT / ".venv" / "bin" / "python"
SCRIPT = ROOT / "dip_signal.py"
OUT_HTML = Path(os.path.expanduser("~/Desktop/抄底訊號.html"))
PORT = int(os.environ.get("DIP_PORT", "8765"))
HOST = os.environ.get("DIP_HOST", "0.0.0.0")

_lock = threading.Lock()
_job = {
    "id": None,
    "status": "idle",  # idle|running|ok|error
    "mode": None,
    "started": None,
    "finished": None,
    "error": None,
    "log_tail": "",
}


def lan_ips():
    ips = []
    try:
        # primary route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def run_update(mode: str, job_id: str):
    mode = "intraday" if mode == "intraday" else "daily"
    cmd = [str(PY), str(SCRIPT), "--mode", mode, "--out", str(OUT_HTML), "--data-dir", str(ROOT)]
    if mode == "intraday":
        cmd.append("--force")  # 手動按更新：允許非盤中也刷新
    log_path = ROOT / ("run-manual-intraday.log" if mode == "intraday" else "run-manual-daily.log")
    with _lock:
        _job.update({
            "id": job_id, "status": "running", "mode": mode,
            "started": time.time(), "finished": None, "error": None, "log_tail": "",
        })
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            p = subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT,
                cwd=str(ROOT), env={**os.environ, "LANG": "zh_TW.UTF-8", "TZ": "Asia/Taipei"},
                timeout=900,
            )
        text = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        with _lock:
            _job["log_tail"] = text
            _job["finished"] = time.time()
            if p.returncode == 0:
                _job["status"] = "ok"
            else:
                _job["status"] = "error"
                _job["error"] = f"exit {p.returncode}\n{text[-800:]}"
    except subprocess.TimeoutExpired:
        with _lock:
            _job["status"] = "error"
            _job["error"] = "逾時（>15 分）"
            _job["finished"] = time.time()
    except Exception as e:
        with _lock:
            _job["status"] = "error"
            _job["error"] = str(e)
            _job["finished"] = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "DipSignal/1.0"

    def log_message(self, fmt, *args):
        # quieter
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype="text/html; charset=utf-8"):
        if not path.exists():
            self.send_error(404, f"missing {path.name}")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self._cors()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        qs = parse_qs(u.query)

        if path in ("/", "/mobile", "/mobile.html"):
            mobile = ROOT / "mobile.html"
            if not mobile.exists():
                # try rebuild once
                subprocess.run(
                    [str(PY), str(SCRIPT), "--mode", "rebuild", "--out", str(OUT_HTML), "--data-dir", str(ROOT)],
                    cwd=str(ROOT), timeout=120,
                )
            return self._file(mobile)

        if path == "/manifest.webmanifest":
            return self._file(ROOT / "manifest.webmanifest", "application/manifest+json; charset=utf-8")

        if path == "/sw.js":
            sw = ROOT / "sw.js"
            if sw.exists():
                return self._file(sw, "application/javascript; charset=utf-8")
            self.send_error(404)
            return

        if path.startswith("/static/"):
            # only allow files under ROOT/static
            rel = path[len("/static/"):]
            if not rel or ".." in rel or "/" in rel or "\\" in rel:
                self.send_error(404)
                return
            fpath = ROOT / "static" / rel
            if not fpath.exists() or not fpath.is_file():
                self.send_error(404)
                return
            ctype = "application/octet-stream"
            if rel.endswith(".png"):
                ctype = "image/png"
            elif rel.endswith(".webmanifest") or rel.endswith(".json"):
                ctype = "application/manifest+json; charset=utf-8"
            elif rel.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif rel.endswith(".svg"):
                ctype = "image/svg+xml; charset=utf-8"
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/widget.html":
            return self._file(ROOT / "widget.html")
        if path in ("/desktop.html", "/index-desktop.html"):
            return self._file(OUT_HTML)

        if path == "/api/info":
            pub = ""
            try:
                pub = (ROOT / "public-url.txt").read_text(encoding="utf-8").strip()
            except Exception:
                pub = ""
            return self._json(200, {
                "port": PORT,
                "lan_ips": lan_ips(),
                "lan_urls": [f"http://{ip}:{PORT}/" for ip in lan_ips()],
                "public_url": pub or None,
                "tunnel": "cloudflared-quick",
                "note": "同 Wi‑Fi 用 lan_urls；外網用 public_url（quick tunnel，重啟可能變網址）",
            })

        if path == "/api/job":
            jid = (qs.get("id") or [None])[0]
            with _lock:
                snap = dict(_job)
            if jid and snap.get("id") and jid != snap["id"] and snap["status"] != "running":
                # stale id after new job — still return current
                pass
            elapsed = None
            if snap.get("started"):
                end = snap.get("finished") or time.time()
                elapsed = int(end - snap["started"])
            return self._json(200, {
                "job_id": snap.get("id"),
                "status": snap.get("status"),
                "mode": snap.get("mode"),
                "elapsed_sec": elapsed,
                "error": snap.get("error"),
            })

        if path == "/api/status":
            with _lock:
                snap = dict(_job)
            meta = {}
            mp = ROOT / "meta.json"
            if mp.exists():
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return self._json(200, {"job": snap, "meta": meta, "mobile_exists": (ROOT / "mobile.html").exists()})

        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        qs = parse_qs(u.query)
        if path != "/api/update":
            self.send_error(404)
            return
        mode = (qs.get("mode") or ["daily"])[0]
        if mode not in ("daily", "intraday"):
            return self._json(400, {"error": "mode 必須是 daily 或 daily 或 intraday"})

        with _lock:
            if _job.get("status") == "running":
                return self._json(409, {"error": "已有更新在進行", "job_id": _job.get("id")})
            job_id = uuid.uuid4().hex[:12]

        t = threading.Thread(target=run_update, args=(mode, job_id), daemon=True)
        t.start()
        return self._json(202, {"job_id": job_id, "mode": mode, "status": "running"})


def main():
    if not PY.exists():
        raise SystemExit(f"找不到 venv python: {PY}")
    # ensure mobile.html exists
    if not (ROOT / "mobile.html").exists():
        subprocess.run(
            [str(PY), str(SCRIPT), "--mode", "rebuild", "--out", str(OUT_HTML), "--data-dir", str(ROOT)],
            cwd=str(ROOT),
        )
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    ips = lan_ips()
    print(f"[dip_server] http://127.0.0.1:{PORT}/")
    for ip in ips:
        print(f"[dip_server] http://{ip}:{PORT}/  ← 手機同 Wi‑Fi 用這個")
    print("[dip_server] 外網：cloudflared tunnel --url http://127.0.0.1:8765")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
