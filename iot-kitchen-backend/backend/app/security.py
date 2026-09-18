"""
BẢO VỆ CÁC ENDPOINT GHI - cần thiết khi mở REST API ra Internet.

Endpoint đọc (/status, /history, /events, /prediction...) để mở: chúng không đổi
trạng thái gì, và Dashboard cần gọi liên tục.

Endpoint ghi (/actuator, /mode, /config, /alerts/test) bắt buộc có khoá khi chạy
public: thiếu nó thì bất kỳ ai biết địa chỉ đều bật được quạt trong bếp nhà bạn.
Bot quét cổng tìm ra một dịch vụ mở trong vòng vài giờ, đây không phải rủi ro lý thuyết.

Hai mức:
  * API_KEY rỗng  -> không kiểm tra (chế độ chạy trong LAN, tiện lúc phát triển)
  * API_KEY có    -> mọi lệnh ghi phải kèm header X-API-Key
  * READ_ONLY=true-> chặn hết lệnh ghi, kể cả khi có khoá đúng
"""
import logging
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

log = logging.getLogger("auth")

MAX_FAILS_PER_MINUTE = 10          # chặn dò khoá bằng cách thử liên tục
_fails: dict[str, deque[float]] = defaultdict(deque)

api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False,
    description="Bắt buộc khi biến API_KEY được đặt trong .env. Bấm Authorize để nhập.")


def _client_ip(request: Request) -> str:
    # Sau Cloudflare Tunnel hay reverse proxy, IP thật nằm ở X-Forwarded-For
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _too_many_fails(ip: str) -> bool:
    now = time.monotonic()
    recent = _fails[ip]
    while recent and now - recent[0] > 60:
        recent.popleft()
    recent.append(now)
    return len(recent) > MAX_FAILS_PER_MINUTE


async def require_write_access(request: Request,
                               key: str | None = Depends(api_key_header)) -> None:
    settings = request.app.state.settings

    if settings.read_only:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Hệ thống đang ở chế độ chỉ đọc (READ_ONLY=true)")

    if not settings.api_key:                    # chưa bật khoá -> chạy như cũ trong LAN
        return

    # so sánh theo kiểu chống đo thời gian: không để lộ khoá đúng được bao nhiêu ký tự đầu
    if key and secrets.compare_digest(key, settings.api_key):
        return

    ip = _client_ip(request)
    blocked = _too_many_fails(ip)
    log.warning("Từ chối lệnh ghi %s từ %s (%s)", request.url.path, ip,
                "thiếu X-API-Key" if not key else "khoá sai")
    await request.app.state.db.log_event(
        settings.device_id, "AUTH_FAILED",
        f"Từ chối {request.method} {request.url.path} từ {ip}: "
        + ("thiếu X-API-Key" if not key else "khoá sai"),
        "WARNING", "api", {"ip": ip, "path": request.url.path})

    if blocked:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Sai khoá quá nhiều lần, thử lại sau 1 phút")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Thiếu hoặc sai header X-API-Key")
