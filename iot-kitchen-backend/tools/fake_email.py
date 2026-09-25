#!/usr/bin/env python3
"""
MÁY CHỦ THƯ GIẢ - nhận thư qua SMTP rồi in ra màn hình, không gửi đi đâu cả.

Dùng để thử cảnh báo email khi chưa tạo mật khẩu ứng dụng Gmail. Chỉ dùng thư viện
chuẩn của Python nên không phải cài thêm gì.

    python tools/fake_email.py
    # Trong .env: SMTP_HOST=host.docker.internal  SMTP_PORT=1025  SMTP_SECURITY=none
    #             SMTP_USER=test@local  SMTP_PASSWORD=x  EMAIL_TO=ban@gmail.com
"""
import asyncio
import email
from email.policy import default as default_policy
from datetime import datetime

PORT = 1025
count = 0


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global count
    send = lambda line: (writer.write((line + "\r\n").encode()), writer)[1]   # noqa: E731
    send("220 fake-smtp san sang")
    await writer.drain()
    data_mode, lines = False, []
    while not reader.at_eof():
        raw = await reader.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if data_mode:
            if line == ".":
                data_mode = False
                count += 1
                msg = email.message_from_string("\n".join(lines), policy=default_policy)
                body = msg.get_body(preferencelist=("plain",))
                print(f"\n┌─ {datetime.now():%H:%M:%S} ── THƯ #{count} ─────────────────────────────")
                print(f"│ Tới:      {msg['To']}")
                print(f"│ Tiêu đề:  {msg['Subject']}")
                print("│")
                for text in (body.get_content() if body else "").splitlines():
                    print(f"│ {text}")
                print("└" + "─" * 56, flush=True)
                lines = []
                send("250 OK da nhan thu")
            else:
                lines.append(line[1:] if line.startswith("..") else line)
            await writer.drain()
            continue
        cmd = line.split(" ", 1)[0].upper()
        if cmd in ("EHLO", "HELO"):
            send("250 fake-smtp")
        elif cmd in ("MAIL", "RCPT", "RSET", "NOOP"):
            send("250 OK")
        elif cmd == "DATA":
            data_mode = True
            send("354 Gui noi dung, ket thuc bang dau cham tren mot dong")
        elif cmd == "QUIT":
            send("221 Tam biet")
            await writer.drain()
            break
        else:
            send("502 Khong ho tro")
        await writer.drain()
    writer.close()


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    print(f"Máy chủ thư giả đang nghe tại cổng {PORT} - Ctrl+C để dừng", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
