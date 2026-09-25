#!/usr/bin/env python3
"""
DISCORD GIẢ - in tin nhắn ra màn hình thay vì gửi lên Discord.

Dùng để thử cảnh báo khi chưa tạo webhook, hoặc khi quay video demo mà không muốn lộ đường link.
    python tools/fake_discord.py
    # Trong .env: DISCORD_WEBHOOK_URL=http://host.docker.internal:8097/api/webhooks/1/test
"""
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8097


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        print(f"\n┌─ {datetime.now():%H:%M:%S} ── Discord nhận được ─────────────────")
        print(f"│ {body.get('content', '')}")
        for embed in body.get("embeds", []):
            print(f"│ ▌ {embed.get('title', '')}  (màu #{embed.get('color', 0):06X})")
            for f in embed.get("fields", []):
                print(f"│ ▌   {f['name']}: {f['value']}")
            if embed.get("description"):
                print(f"│ ▌   {embed['description']}")
            if embed.get("footer"):
                print(f"│ ▌   {embed['footer']['text']}")
        print("└" + "─" * 54, flush=True)
        data = json.dumps({"id": "1234567890"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"Discord giả đang nghe tại cổng {PORT} - Ctrl+C để dừng", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
