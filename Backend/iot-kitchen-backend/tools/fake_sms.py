#!/usr/bin/env python3
"""
SMS GIẢ - giả làm máy chủ Twilio, in tin nhắn ra màn hình thay vì gửi thật.
Dùng để demo hoặc kiểm thử mà không tốn tiền SMS.

    python tools/fake_sms.py
    # Trong .env: TWILIO_API_BASE=http://host.docker.internal:8098
    #             TWILIO_ACCOUNT_SID=ACfake  TWILIO_AUTH_TOKEN=fake
    #             TWILIO_FROM=+15550000000   SMS_TO=+84900000000
"""
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

PORT = 8098


class Handler(BaseHTTPRequestHandler):
    count = 0

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        Handler.count += 1
        print(f"[{datetime.now():%H:%M:%S}] SMS #{Handler.count} tới {form.get('To', ['?'])[0]}: "
              f"{form.get('Body', [''])[0]}", flush=True)
        data = json.dumps({"sid": f"SMfake{Handler.count:04d}", "status": "queued"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"SMS giả đang nghe tại http://127.0.0.1:{PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
