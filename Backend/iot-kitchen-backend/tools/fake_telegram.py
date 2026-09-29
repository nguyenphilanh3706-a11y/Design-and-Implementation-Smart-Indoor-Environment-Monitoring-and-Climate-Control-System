#!/usr/bin/env python3
"""
TELEGRAM GIẢ - in tin nhắn ra màn hình thay vì gửi lên Internet.

Dùng khi chưa kịp tạo bot, khi mạng trường chặn api.telegram.org, hoặc khi quay video demo
mà không muốn lộ token. Cách dùng:

    python tools/fake_telegram.py                       # cửa sổ 1
    # Trong .env: TELEGRAM_BOT_TOKEN=123:FAKE, TELEGRAM_CHAT_ID=999,
    #             TELEGRAM_API_BASE=http://host.docker.internal:8099
    docker compose up -d --force-recreate backend       # cửa sổ 2
"""
import json
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8099
TAG = re.compile(r"</?[a-z]+>")          # bỏ thẻ HTML để đọc cho dễ


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            text = json.loads(body).get("text", "")
        except ValueError:
            text = body.decode("utf-8", "replace")
        print(f"\n┌─ {datetime.now():%H:%M:%S} ── Telegram nhận được ──────────────")
        for line in TAG.sub("", text).splitlines():
            print(f"│ {line}")
        print("└" + "─" * 52, flush=True)
        self._reply({"ok": True, "result": {"message_id": 1}})

    def do_GET(self) -> None:                                  # trả lời /getMe cho tiện kiểm tra
        self._reply({"ok": True, "result": {"username": "fake_kitchen_bot"}})

    def _reply(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:                      # tắt log mặc định của http.server
        pass


if __name__ == "__main__":
    print(f"Telegram giả đang nghe tại http://127.0.0.1:{PORT} - Ctrl+C để dừng", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
