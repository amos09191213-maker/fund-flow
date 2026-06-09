# -*- coding: utf-8 -*-
"""
server.py — 資金流向 Dashboard 伺服器(台股 / 美股 / BTC)

啟動：
    python server.py                 # 本機 http://127.0.0.1:8787
    python server.py --port 9000 --no-browser

雲端部署(如 Render)：自動讀取環境變數
    PORT          監聽埠(雲端平台會自動帶入) → 偵測到時自動綁 0.0.0.0、不開瀏覽器
    APP_USER      登入帳號(預設 admin)
    APP_PASSWORD  登入密碼；有設定才會啟用密碼保護(本機未設則不擋，方便開發)

路由：
    GET /            首頁(index.html)
    GET /healthz     健康檢查(不需密碼)
    GET /api/tw|us|btc   各市場 JSON
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import analyze
import usstock
import crypto

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "index.html")

# 密碼保護(HTTP Basic)：APP_PASSWORD 有設定才啟用
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# 各市場記憶體快取：避免每次開頁都重算
_cache_lock = threading.Lock()
_tw_cache: dict = {}            # key: days -> (ts, rep)
_simple_cache: dict = {}        # key: 'us'/'btc' -> (ts, rep)
TW_TTL = 600    # 台股盤後資料穩定，快取 10 分鐘
US_TTL = 300    # 美股延遲報價，5 分鐘
BTC_TTL = 45    # BTC 變動快，45 秒


def get_tw(days: int, refresh: bool) -> dict:
    key = f"days={days}"
    now = time.time()
    with _cache_lock:
        if not refresh and key in _tw_cache:
            ts, rep = _tw_cache[key]
            if now - ts < TW_TTL:
                return rep
        rep = analyze.build_report(days=days, refresh=refresh)
        if rep.get("ok"):
            _tw_cache[key] = (now, rep)
        return rep


def get_simple(name: str, builder, ttl: int, refresh: bool) -> dict:
    now = time.time()
    with _cache_lock:
        if not refresh and name in _simple_cache:
            ts, rep = _simple_cache[name]
            if now - ts < ttl:
                return rep
    rep = builder()  # 不在鎖內呼叫網路，避免互相阻塞
    if rep.get("ok"):
        with _cache_lock:
            _simple_cache[name] = (time.time(), rep)
    return rep


class Handler(BaseHTTPRequestHandler):
    # 安靜一點，只在出錯時印
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _send_json(self, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        if not APP_PASSWORD:
            return True  # 未設密碼 → 不啟用保護(本機開發)
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(hdr[6:]).decode("utf-8", "ignore").partition(":")
                if hmac.compare_digest(user, APP_USER) and hmac.compare_digest(pw, APP_PASSWORD):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _require_auth(self):
        body = "需要登入".encode("utf-8")
        self.send_response(401)
        # 注意：HTTP 標頭只能用 ASCII，realm 不可放中文(否則編碼例外導致 500/502)
        self.send_header("WWW-Authenticate", 'Basic realm="Fund Flow Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/healthz":  # 健康檢查不需密碼
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return

        if not self._authorized():
            self._require_auth()
            return

        if path == "/" or path == "/index.html":
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"index.html not found", "text/plain; charset=utf-8")
            return

        if path in ("/api/tw", "/api/data"):  # /api/data 為相容舊路徑
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["6"])[0])
            except ValueError:
                days = 6
            days = max(2, min(days, 15))
            refresh = qs.get("refresh", ["0"])[0] in ("1", "true", "yes")
            try:
                rep = get_tw(days, refresh)
            except Exception as e:  # noqa: BLE001 — 不讓伺服器掛掉
                rep = {"ok": False, "error": f"台股分析發生例外：{e}"}
            self._send_json(rep)
            return

        if path == "/api/us":
            refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] in ("1", "true", "yes")
            try:
                rep = get_simple("us", usstock.build_us_report, US_TTL, refresh)
            except Exception as e:  # noqa: BLE001
                rep = {"ok": False, "error": f"美股分析發生例外：{e}"}
            self._send_json(rep)
            return

        if path == "/api/btc":
            refresh = parse_qs(parsed.query).get("refresh", ["0"])[0] in ("1", "true", "yes")
            try:
                rep = get_simple("btc", crypto.build_btc_report, BTC_TTL, refresh)
            except Exception as e:  # noqa: BLE001
                rep = {"ok": False, "error": f"BTC 分析發生例外：{e}"}
            self._send_json(rep)
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        self._send(404, b"Not Found", "text/plain; charset=utf-8")


def main():
    # 雲端平台(如 Render)會帶入 PORT 環境變數；偵測到就綁 0.0.0.0 且不開瀏覽器
    in_cloud = "PORT" in os.environ
    default_port = int(os.environ.get("PORT", "8787"))
    default_host = "0.0.0.0" if in_cloud else "127.0.0.1"

    ap = argparse.ArgumentParser(description="資金流向 Dashboard(台股/美股/BTC)")
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--host", default=default_host)
    ap.add_argument("--no-browser", action="store_true", help="啟動時不自動開瀏覽器")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{shown}:{args.port}/"

    print("=" * 56)
    print("  資金流向 Dashboard 已啟動(台股 / 美股 / BTC)")
    print(f"  本機開啟： {url}")
    if args.host == "0.0.0.0":
        print("  區網/雲端：以伺服器對外位址 + 上方埠號開啟")
    print(f"  密碼保護： {'已啟用(APP_PASSWORD)' if APP_PASSWORD else '未啟用(未設 APP_PASSWORD)'}")
    print("  按 Ctrl+C 結束")
    print("=" * 56)

    if not args.no_browser and not in_cloud:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已停止。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
