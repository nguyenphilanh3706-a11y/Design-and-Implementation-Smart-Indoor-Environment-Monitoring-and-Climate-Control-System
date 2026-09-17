"""
DỊCH VỤ CẢNH BÁO TELEGRAM - chống spam / chống nghẽn 2 tầng

Tầng 1 - Theo từng loại cảnh báo (Rule engine):
  * Edge-triggered: chỉ gửi khi CHUYỂN từ bình thường -> vượt ngưỡng,
    không gửi lại mỗi 2 giây khi dữ liệu vẫn cao.
  * Hysteresis: chỉ coi là "đã an toàn" khi giảm dưới (ngưỡng - biên trễ),
    ví dụ ô nhiễm < 45% -> giá trị dao động quanh 50% không gây bắn tin liên tục
    (cùng ý tưởng chống chattering của FSM quạt).
  * Cooldown: 2 cảnh báo MỚI cùng loại cách nhau tối thiểu ALERT_COOLDOWN_SECONDS.
  * Reminder: vẫn vượt ngưỡng quá lâu -> nhắc lại mỗi ALERT_REMINDER_SECONDS.

Tầng 2 - Toàn bộ đường gửi (Transport):
  * Hàng đợi có giới hạn + 1 worker gửi tuần tự: không chặn luồng nhận MQTT.
  * Sliding window: tối đa TELEGRAM_MAX_PER_MINUTE tin / 60 giây (hết lượt thì chờ).
  * Tôn trọng HTTP 429 "retry_after" do Telegram trả về.
"""
import asyncio
import contextlib
import html
import logging
import os
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .db import Database
from .schemas import TelemetryIn, to_iso_utc

log = logging.getLogger("alert")

# Đổi được bằng biến môi trường để chạy thử offline (trỏ vào server Telegram giả) - xem tools/fake_telegram.py
TELEGRAM_API = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
VN_TZ = timezone(timedelta(hours=7))        # Việt Nam không có giờ mùa hè -> +07:00 cố định
QUEUE_MAX = 50


@dataclass(frozen=True)
class AlertRule:
    key: str             # mã loại cảnh báo
    field: str           # trường telemetry được kiểm tra
    threshold_key: str   # cột ngưỡng trong bảng device_config
    hysteresis: float    # biên trễ để coi là đã an toàn
    strict: bool         # True: giá trị > ngưỡng ; False: giá trị >= ngưỡng
    title: str
    label: str
    unit: str


RULES = (
    # Bản thống nhất mục 3: ">= 50%: Nguy hại (bắn tin nhắn Telegram khẩn cấp)"
    AlertRule("POLLUTION_HIGH", "pollution_percent", "alert_pollution_threshold", 5.0, False,
              "Ô NHIỄM CAO", "Nồng độ ô nhiễm", "%"),
    # Kế hoạch TV3: "nhiệt độ > 40°C"
    AlertRule("TEMPERATURE_HIGH", "temperature", "alert_temp_threshold", 2.0, True,
              "NHIỆT ĐỘ CAO", "Nhiệt độ", "°C"),
)
DEFAULT_THRESHOLDS = {"alert_pollution_threshold": 50.0, "alert_temp_threshold": 40.0}


@dataclass
class RuleState:
    active: bool = False               # đang trong một đợt vượt ngưỡng
    notified: bool = False             # đợt này đã gửi cảnh báo chưa
    suppressed_logged: bool = False    # đã ghi log "bị chặn do cooldown" cho đợt này chưa
    since: datetime | None = None      # thời điểm bắt đầu vượt ngưỡng
    last_sent: float = float("-inf")   # time.monotonic() của lần gửi ALERT/REMINDER gần nhất


class SlidingWindowLimiter:
    """Cho phép tối đa max_events trong mỗi cửa sổ window_s giây; hết lượt thì chờ chứ không bỏ tin."""

    def __init__(self, max_events: int, window_s: float = 60.0):
        self.max_events = max(1, max_events)
        self.window_s = window_s
        self._sent: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._sent and now - self._sent[0] >= self.window_s:
                self._sent.popleft()
            if len(self._sent) < self.max_events:
                self._sent.append(now)
                return
            await asyncio.sleep(self.window_s - (now - self._sent[0]) + 0.01)


# ---------------------------------------------------------------------
# Soạn nội dung tin nhắn (HTML của Telegram)
# ---------------------------------------------------------------------
def format_vn_time(value: datetime) -> str:
    return value.astimezone(VN_TZ).strftime("%H:%M:%S %d/%m/%Y") + " (GMT+7)"


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(max(0, seconds)), 60)
    return f"{minutes} phút {secs} giây" if minutes else f"{secs} giây"


def _reading(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{value:.1f}{unit}"


def build_message(kind: str, rule: AlertRule, device_id: str, t: TelemetryIn,
                  sample_time: datetime, threshold: float, since: datetime | None) -> str:
    value = getattr(t, rule.field)
    readings = {
        "pollution_percent": f"💨 Ô nhiễm: {_reading(t.pollution_percent, '%')}",
        "temperature": f"🌡 Nhiệt độ: {_reading(t.temperature, '°C')}",
        "humidity": f"💧 Độ ẩm: {_reading(t.humidity, '%')}",
    }
    others = " · ".join(text for field, text in readings.items() if field != rule.field)
    fan = {1: "BẬT", 0: "TẮT"}.get(t.fan_state, "—")
    lasted = _duration((sample_time - since).total_seconds()) if since else "—"
    op = ">" if rule.strict else "≥"
    lines_by_kind = {
        "ALERT": [f"🚨 <b>CẢNH BÁO KHẨN CẤP: {rule.title}</b>",
                  f"⚠️ {rule.label}: <b>{_reading(value, rule.unit)}</b> (ngưỡng {op} {threshold:g}{rule.unit})"],
        "REMINDER": [f"⏰ <b>NHẮC LẠI: VẪN ĐANG {rule.title}</b>",
                     f"⚠️ {rule.label}: <b>{_reading(value, rule.unit)}</b> (ngưỡng {op} {threshold:g}{rule.unit})",
                     f"⏱ Đã kéo dài: {lasted}"],
        "RESOLVED": [f"✅ <b>ĐÃ AN TOÀN: HẾT {rule.title}</b>",
                     f"{rule.label}: <b>{_reading(value, rule.unit)}</b> "
                     f"(dưới {threshold - rule.hysteresis:g}{rule.unit})",
                     f"⏱ Thời gian vượt ngưỡng: {lasted}"],
    }
    return "\n".join([
        *lines_by_kind[kind][:1],
        f"📍 Thiết bị: <code>{html.escape(device_id)}</code>",
        *lines_by_kind[kind][1:],
        others,
        f"🌀 Quạt: {fan} · Chế độ: {html.escape(t.mode or '—')}",
        f"🕒 Thời điểm đo: {format_vn_time(sample_time)}",
    ])


# ---------------------------------------------------------------------
class AlertService:
    def __init__(self, settings: Settings, db: Database):
        self.s = settings
        self.db = db
        self._thresholds: dict[str, dict[str, float]] = {}
        self._states: dict[tuple[str, str], RuleState] = {}
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._limiter = SlidingWindowLimiter(settings.telegram_max_per_minute)
        self._http: httpx.AsyncClient | None = None
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=10.0)
        self._worker = asyncio.create_task(self._worker_loop(), name="telegram-worker")
        log.info("Telegram: %s", "đã cấu hình" if self.s.telegram_enabled
                 else "CHƯA cấu hình -> cảnh báo chỉ được ghi vào system_events")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        if self._http:
            await self._http.aclose()

    def set_thresholds(self, device_id: str, config: Mapping[str, Any]) -> None:
        self._thresholds[device_id] = {key: float(config[key]) for key in DEFAULT_THRESHOLDS}

    def active_alerts(self, device_id: str) -> list[str]:
        return [r.key for r in RULES if (st := self._states.get((device_id, r.key))) and st.active]

    # ------------------------------------------------------------------
    # TẦNG 1: kiểm tra ngưỡng cho mỗi bản tin telemetry
    # ------------------------------------------------------------------
    async def evaluate(self, device_id: str, t: TelemetryIn, sample_time: datetime) -> None:
        thresholds = self._thresholds.get(device_id, DEFAULT_THRESHOLDS)
        now = time.monotonic()
        for rule in RULES:
            value = getattr(t, rule.field)
            if value is None:
                continue
            threshold = thresholds[rule.threshold_key]
            over = value > threshold if rule.strict else value >= threshold
            st = self._states.setdefault((device_id, rule.key), RuleState())

            # (1) Đang cảnh báo và đã xuống dưới mức an toàn -> kết thúc đợt
            if st.active and value < threshold - rule.hysteresis:
                if st.notified:
                    await self._enqueue(device_id, "RESOLVED", rule, t, sample_time, threshold, st.since)
                st.active = st.notified = st.suppressed_logged = False
                st.since = None
                continue

            # (2) Bắt đầu một đợt vượt ngưỡng mới
            if over and not st.active:
                st.active, st.notified, st.suppressed_logged, st.since = True, False, False, sample_time

            if not (st.active and over):
                continue  # bình thường, hoặc đang ở dải trễ (chưa an toàn nhưng không vượt ngưỡng)

            # (3) Đợt này chưa báo -> gửi nếu đã hết cooldown
            if not st.notified:
                waited = now - st.last_sent
                if waited >= self.s.alert_cooldown_s:
                    await self._enqueue(device_id, "ALERT", rule, t, sample_time, threshold, st.since)
                    st.notified, st.last_sent = True, now
                elif not st.suppressed_logged:
                    st.suppressed_logged = True
                    await self.db.log_event(
                        device_id, "ALERT_SUPPRESSED",
                        f"{rule.key}: đang trong cooldown, sẽ báo sau {self.s.alert_cooldown_s - waited:.0f}s "
                        f"nếu vẫn vượt ngưỡng", "WARNING",
                        details={"rule": rule.key, "value": value, "threshold": threshold})

            # (4) Đã báo nhưng vẫn vượt ngưỡng quá lâu -> nhắc lại
            elif now - st.last_sent >= self.s.alert_reminder_s:
                await self._enqueue(device_id, "REMINDER", rule, t, sample_time, threshold, st.since)
                st.last_sent = now

    async def _enqueue(self, device_id: str, kind: str, rule: AlertRule, t: TelemetryIn,
                       sample_time: datetime, threshold: float, since: datetime | None) -> None:
        details = {"kind": kind, "rule": rule.key, "value": getattr(t, rule.field),
                   "threshold": threshold, "sample_time": to_iso_utc(sample_time)}
        job = {"device_id": device_id, "kind": kind, "rule": rule.key, "details": details,
               "text": build_message(kind, rule, device_id, t, sample_time, threshold, since)}
        try:
            self._queue.put_nowait(job)
            log.info("Xếp hàng tin Telegram %s/%s (giá trị %s)", kind, rule.key, details["value"])
        except asyncio.QueueFull:
            await self.db.log_event(device_id, "ALERT_FAILED", f"Hàng đợi Telegram đầy ({QUEUE_MAX}) - bỏ tin",
                                    "WARNING", details=details)

    # ------------------------------------------------------------------
    # TẦNG 2: worker gửi tuần tự qua bộ giới hạn tốc độ
    # ------------------------------------------------------------------
    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                ok, detail = await self.send_text(job["text"])
                await self._log_result(job["device_id"], ok, f"[{job['kind']}] {job['rule']}: {detail}",
                                       "backend", job["details"], critical=job["kind"] != "RESOLVED")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Lỗi không mong muốn trong worker Telegram")
            finally:
                self._queue.task_done()

    async def send_text(self, text: str) -> tuple[bool, str]:
        if not self.s.telegram_enabled:
            return False, "Telegram chưa cấu hình (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID trống)"
        await self._limiter.acquire()
        url = f"{TELEGRAM_API}/bot{self.s.telegram_bot_token}/sendMessage"
        body = {"chat_id": self.s.telegram_chat_id, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True}
        for attempt in (1, 2):
            try:
                resp = await self._http.post(url, json=body)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                # Không in chi tiết exception: có thể chứa URL kèm token bí mật
                return False, f"Lỗi kết nối tới Telegram ({type(exc).__name__})"
            if data.get("ok"):
                return True, f"Đã gửi (message_id={data['result']['message_id']})"
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry_after and attempt == 1:
                log.warning("Telegram báo quá tải (429) - chờ %ss rồi gửi lại", retry_after)
                await asyncio.sleep(min(float(retry_after), 60))
                continue
            return False, f"Telegram từ chối: {data.get('description', resp.status_code)}"
        return False, "Telegram vẫn báo quá tải sau khi thử lại"

    async def send_test(self, device_id: str) -> tuple[bool, str]:
        text = (f"✅ <b>TEST BOT CẢNH BÁO</b>\n"
                f"Backend IoT Kitchen đã kết nối Telegram thành công.\n"
                f"📍 Thiết bị: <code>{html.escape(device_id)}</code>\n"
                f"🕒 {format_vn_time(datetime.now(timezone.utc))}")
        ok, detail = await self.send_text(text)
        await self._log_result(device_id, ok, f"[TEST] {detail}", "api", {"kind": "TEST"}, critical=False)
        return ok, detail

    async def _log_result(self, device_id: str, ok: bool, message: str, source: str,
                          details: dict[str, Any], critical: bool) -> None:
        if ok:
            event, severity = "ALERT_SENT", "CRITICAL" if critical else "INFO"
        elif not self.s.telegram_enabled:
            event, severity = "ALERT_SKIPPED", "WARNING"
        else:
            event, severity = "ALERT_FAILED", "WARNING"
        log.info("%s - %s", event, message)
        await self.db.log_event(device_id, event, message, severity, source, details)
