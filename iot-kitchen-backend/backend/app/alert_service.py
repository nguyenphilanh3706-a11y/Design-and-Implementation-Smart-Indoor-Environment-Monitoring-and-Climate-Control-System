"""
DỊCH VỤ CẢNH BÁO QUA TELEGRAM - miễn phí, không giới hạn số tin.

Luồng:  telemetry -> TẦNG 1 kiểm tra ngưỡng -> hàng đợi -> TẦNG 2 worker gửi -> nhật ký

Khi nào gửi:
  * Nồng độ khí VƯỢT 1000 ppm (mốc BAD)  -> "Ô nhiễm! ..., tôi sẽ bật quạt."
  * Nhiệt độ VƯỢT 40°C                    -> "Nhiệt độ cao! ..."
  Hai ngưỡng lưu trong bảng device_config, đổi được qua POST /config.

Chống spam:
  * Chỉ gửi khi MỚI vượt ngưỡng (edge-trigger), không gửi lại mỗi 2 giây
  * Dải trễ (hysteresis): ô nhiễm phải xuống dưới 950 ppm mới coi là hết, tránh
    dao động quanh 1000 ppm làm bắn tin liên tục
  * Cooldown + nhắc lại cùng 10 phút: mỗi loại cảnh báo tối đa 1 tin / 10 phút
  * Trần toàn hệ thống TELEGRAM_MAX_PER_MINUTE (mặc định 20, Telegram giới hạn 30)
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

# Đổi được bằng biến môi trường để chạy thử offline (trỏ vào server giả)
TELEGRAM_API = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
VN_TZ = timezone(timedelta(hours=7))
QUEUE_MAX = 50


# =====================================================================
#  QUY TẮC CẢNH BÁO
# =====================================================================
@dataclass(frozen=True)
class AlertRule:
    key: str
    field: str           # trường telemetry được kiểm tra
    threshold_key: str   # cột ngưỡng trong device_config
    hysteresis: float    # xuống dưới (ngưỡng - hysteresis) mới coi là đã an toàn
    strict: bool         # True: > ngưỡng ; False: >= ngưỡng
    title: str
    unit: str


RULES = (
    # Trên 1000 ppm là mức BAD - quạt phải chạy 100%
    AlertRule("POLLUTION_HIGH", "gas_ppm", "alert_ppm_threshold", 50.0, True, "Ô NHIỄM", " ppm"),
    AlertRule("TEMPERATURE_HIGH", "temperature", "alert_temp_threshold", 2.0, True, "NHIỆT ĐỘ CAO", "°C"),
)
DEFAULT_THRESHOLDS = {"alert_ppm_threshold": 1000.0, "alert_temp_threshold": 40.0}


@dataclass
class RuleState:
    active: bool = False
    notified: bool = False
    suppressed_logged: bool = False
    since: datetime | None = None
    last_sent: float = float("-inf")


class SlidingWindowLimiter:
    """Tối đa max_events trong mỗi window_s giây; hết lượt thì chờ chứ không bỏ tin."""

    def __init__(self, max_events: int, window_s: float = 60.0):
        self.max_events, self.window_s = max(1, max_events), window_s
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




@dataclass
class SendResult:
    status: str          # sent · failed · skipped · capped
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "sent"


# =====================================================================
#  NỘI DUNG TIN NHẮN
# =====================================================================
def format_vn_time(value: datetime) -> str:
    return value.astimezone(VN_TZ).strftime("%H:%M:%S %d/%m/%Y") + " (GMT+7)"


def _hhmm(value: datetime) -> str:
    return value.astimezone(VN_TZ).strftime("%H:%M")


def _num(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}{unit}" if unit.strip() == "ppm" else f"{value:.1f}{unit}"






def build_telegram(kind: str, rule: AlertRule, device_id: str, t: TelemetryIn,
                   sample_time: datetime, threshold: float, since: datetime | None) -> str:
    """Dòng đầu ngắn gọn để đọc được ngay trên thông báo điện thoại, các dòng sau là chi tiết."""
    value = _num(getattr(t, rule.field), rule.unit)
    if rule.key == "POLLUTION_HIGH":
        # "Tôi sẽ bật quạt" là đúng sự thật: ngưỡng cảnh báo trùng mốc BAD, nên ở AUTO thì FSM
        # của ESP32 đẩy quạt lên 100%, ở MANUAL thì cơ chế SAFETY_OVERRIDE cưỡng bức bật.
        head = {"ALERT": f"🚨 <b>Ô nhiễm! {value}, tôi sẽ bật quạt.</b>",
                "REMINDER": f"⏰ <b>Vẫn ô nhiễm {value}, quạt đang chạy.</b>",
                "RESOLVED": f"✅ <b>Đã hết ô nhiễm ({value}).</b>"}[kind]
    else:
        head = {"ALERT": f"🔥 <b>Nhiệt độ cao! {value}, hãy kiểm tra bếp.</b>",
                "REMINDER": f"⏰ <b>Vẫn nhiệt độ cao {value}.</b>",
                "RESOLVED": f"✅ <b>Nhiệt độ đã bình thường ({value}).</b>"}[kind]
    lasted = ""
    if since and kind != "ALERT":
        m, sec = divmod(int(max(0, (sample_time - since).total_seconds())), 60)
        lasted = f"⏱ Đã kéo dài: {m} phút {sec} giây\n"
    fan = {1: "BẬT", 0: "TẮT"}.get(t.fan_state, "—")
    speed = f" {t.fan_speed_percent}%" if getattr(t, "fan_speed_percent", None) is not None else ""
    footer = "\n<i>Còn vượt ngưỡng thì 10 phút nhắc lại 1 lần.</i>" if kind != "RESOLVED" else ""
    return (f"{head}\n"
            f"📍 <code>{html.escape(device_id)}</code> · ngưỡng {threshold:g}{rule.unit}\n"
            f"{lasted}"
            f"💨 {_num(t.gas_ppm, ' ppm')} · 🌡 {_num(t.temperature, '°C')} · 🌀 Quạt {fan}{speed}\n"
            f"🕒 {format_vn_time(sample_time)}{footer}")




# =====================================================================
#  KÊNH GỬI
# =====================================================================




class TelegramNotifier:
    name = "telegram"

    def __init__(self, s: Settings, http: httpx.AsyncClient):
        self.s, self.http = s, http
        self.limiter = SlidingWindowLimiter(s.telegram_max_per_minute)

    @property
    def enabled(self) -> bool:
        return self.s.telegram_enabled

    async def send(self, text: str) -> SendResult:
        if not self.enabled:
            return SendResult("skipped", "Telegram chưa cấu hình")
        await self.limiter.acquire()
        url = f"{TELEGRAM_API}/bot{self.s.telegram_bot_token}/sendMessage"
        body = {"chat_id": self.s.telegram_chat_id, "text": text, "parse_mode": "HTML"}
        for attempt in (1, 2):
            try:
                resp = await self.http.post(url, json=body)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return SendResult("failed", f"Lỗi kết nối tới Telegram ({type(exc).__name__})")
            if data.get("ok"):
                return SendResult("sent", f"Đã gửi Telegram (message_id={data['result']['message_id']})")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry_after and attempt == 1:
                await asyncio.sleep(min(float(retry_after), 60))
                continue
            return SendResult("failed", f"Telegram từ chối: {data.get('description', resp.status_code)}")
        return SendResult("failed", "Telegram vẫn quá tải sau khi thử lại")


# Màu thẻ Discord theo loại tin
DISCORD_COLORS = {"ALERT": 0xE53935, "REMINDER": 0xFB8C00, "RESOLVED": 0x43A047, "TEST": 0x1E88E5}


def build_discord(kind: str, rule: AlertRule, device_id: str, t: TelemetryIn,
                  sample_time: datetime, threshold: float, since: datetime | None) -> dict[str, Any]:
    """Dòng chữ đầu để hiện trên thông báo điện thoại, thẻ màu bên dưới chứa chi tiết."""
    value = _num(getattr(t, rule.field), rule.unit)
    if rule.key == "POLLUTION_HIGH":
        head = {"ALERT": f"🚨 **Ô nhiễm! {value}, tôi sẽ bật quạt.**",
                "REMINDER": f"⏰ **Vẫn ô nhiễm {value}, quạt đang chạy.**",
                "RESOLVED": f"✅ **Đã hết ô nhiễm ({value}).**"}[kind]
    else:
        head = {"ALERT": f"🔥 **Nhiệt độ cao! {value}, hãy kiểm tra bếp.**",
                "REMINDER": f"⏰ **Vẫn nhiệt độ cao {value}.**",
                "RESOLVED": f"✅ **Nhiệt độ đã bình thường ({value}).**"}[kind]
    fan = {1: "BẬT", 0: "TẮT"}.get(t.fan_state, "—")
    speed = f" {t.fan_speed_percent}%" if getattr(t, "fan_speed_percent", None) is not None else ""
    fields = [
        {"name": "Nồng độ khí", "value": _num(t.gas_ppm, " ppm"), "inline": True},
        {"name": "Nhiệt độ", "value": _num(t.temperature, "°C"), "inline": True},
        {"name": "Quạt", "value": f"{fan}{speed}", "inline": True},
        {"name": "Ngưỡng", "value": f"{threshold:g}{rule.unit}", "inline": True},
        {"name": "Chế độ", "value": t.mode or "—", "inline": True},
    ]
    if since and kind != "ALERT":
        m, sec = divmod(int(max(0, (sample_time - since).total_seconds())), 60)
        fields.append({"name": "Đã kéo dài", "value": f"{m} phút {sec} giây", "inline": True})
    embed = {
        "title": f"Bếp {device_id}",
        "color": DISCORD_COLORS[kind],
        "fields": fields,
        "footer": {"text": "Còn vượt ngưỡng thì 10 phút nhắc lại 1 lần" if kind != "RESOLVED"
                   else "Hệ thống giám sát bếp IoT"},
        # Discord tự đổi mốc này sang giờ của người xem, không cần tự cộng 7 tiếng
        "timestamp": sample_time.astimezone(timezone.utc).isoformat(),
    }
    return {"content": head, "embeds": [embed], "_kind": kind}


class DiscordNotifier:
    """Gửi qua Discord webhook. Không cần bot, không cần đăng nhập, chỉ cần một đường link.

    Đường link webhook CHỨA KHOÁ BÍ MẬT (phần sau dấu / cuối cùng). Ai có nó đều gửi được
    tin vào kênh, nên tuyệt đối không in nó ra log.
    """
    name = "discord"

    def __init__(self, s: Settings, http: httpx.AsyncClient):
        self.s, self.http = s, http
        self.limiter = SlidingWindowLimiter(s.discord_max_per_minute)

    @property
    def enabled(self) -> bool:
        return self.s.discord_enabled

    async def send(self, payload: dict[str, Any]) -> SendResult:
        if not self.enabled:
            return SendResult("skipped", "Discord chưa cấu hình")
        body = dict(payload)
        kind = body.pop("_kind", None)
        mention = self.s.discord_mention
        if mention and kind in ("ALERT", "TEST"):
            # Chỉ gọi tên ở tin cảnh báo đầu, không gọi mỗi lần nhắc lại cho khỏi phiền
            body["content"] = f"{mention} {body.get('content', '')}"
            body["allowed_mentions"] = {"parse": ["everyone", "roles", "users"]}
        else:
            body["allowed_mentions"] = {"parse": []}      # không vô tình gọi tên ai
        await self.limiter.acquire()
        url = self.s.discord_webhook_url
        url += ("&" if "?" in url else "?") + "wait=true"   # wait=true để Discord trả về mã tin nhắn
        for attempt in (1, 2):
            try:
                resp = await self.http.post(url, json=body)
            except httpx.HTTPError as exc:
                return SendResult("failed", f"Lỗi kết nối tới Discord ({type(exc).__name__})")
            if resp.status_code in (200, 204):
                try:
                    msg_id = resp.json().get("id") if resp.content else None
                except ValueError:
                    msg_id = None
                return SendResult("sent", "Đã gửi Discord" + (f" (message_id={msg_id})" if msg_id else ""))
            if resp.status_code == 429 and attempt == 1:
                try:
                    retry_after = float(resp.json().get("retry_after", 1))
                except ValueError:
                    retry_after = float(resp.headers.get("Retry-After", 1))
                await asyncio.sleep(min(retry_after, 60))
                continue
            if resp.status_code in (401, 404):
                return SendResult("failed", "Webhook Discord không tồn tại hoặc đã bị xoá, tạo lại rồi dán vào .env")
            return SendResult("failed", f"Discord từ chối: HTTP {resp.status_code} {resp.text[:120]}")
        return SendResult("failed", "Discord vẫn quá tải sau khi thử lại")


# =====================================================================
#  DỊCH VỤ
# =====================================================================




class AlertService:
    def __init__(self, settings: Settings, db: Database):
        self.s, self.db = settings, db
        self._thresholds: dict[str, dict[str, float]] = {}
        self._states: dict[tuple[str, str], RuleState] = {}
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._http: httpx.AsyncClient | None = None
        self._worker: asyncio.Task | None = None
        self.notifiers: list[TelegramNotifier | DiscordNotifier] = []

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self.notifiers.append(TelegramNotifier(self.s, self._http))
        if self.s.discord_enabled:                 # chỉ thêm khi đã điền webhook, khỏi ghi sự kiện bỏ qua thừa
            self.notifiers.append(DiscordNotifier(self.s, self._http))
        self._worker = asyncio.create_task(self._worker_loop(), name="alert-worker")
        for n in self.notifiers:
            log.info("Kênh cảnh báo %s: %s", n.name.upper(),
                     "đã cấu hình" if n.enabled else "CHƯA cấu hình -> chỉ ghi vào system_events")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        if self._http:
            await self._http.aclose()

    def set_thresholds(self, device_id: str, config: Mapping[str, Any]) -> None:
        self._thresholds[device_id] = {k: float(config.get(k, v)) for k, v in DEFAULT_THRESHOLDS.items()}

    def active_alerts(self, device_id: str) -> list[str]:
        return [r.key for r in RULES if (st := self._states.get((device_id, r.key))) and st.active]

    @property
    def any_enabled(self) -> bool:
        return any(n.enabled for n in self.notifiers)

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
                continue

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
                        f"{rule.key}: vừa gửi cách đây {waited:.0f}s, chờ đủ {self.s.alert_cooldown_s}s "
                        f"mới gửi tiếp để tránh spam", "WARNING",
                        details={"rule": rule.key, "value": value, "threshold": threshold})

            # (4) Đã báo mà vẫn vượt ngưỡng -> nhắc lại sau mỗi ALERT_REMINDER_SECONDS (10 phút)
            elif now - st.last_sent >= self.s.alert_reminder_s:
                await self._enqueue(device_id, "REMINDER", rule, t, sample_time, threshold, st.since)
                st.last_sent = now

    async def _enqueue(self, device_id: str, kind: str, rule: AlertRule, t: TelemetryIn,
                       sample_time: datetime, threshold: float, since: datetime | None) -> None:
        details = {"kind": kind, "rule": rule.key, "value": getattr(t, rule.field),
                   "threshold": threshold, "sample_time": to_iso_utc(sample_time)}
        job = {"device_id": device_id, "kind": kind, "rule": rule.key, "details": details,
               "telegram": build_telegram(kind, rule, device_id, t, sample_time, threshold, since),
               "discord": build_discord(kind, rule, device_id, t, sample_time, threshold, since)}
        try:
            self._queue.put_nowait(job)
            log.info("Xếp hàng cảnh báo %s/%s (giá trị %s)", kind, rule.key, details["value"])
        except asyncio.QueueFull:
            await self.db.log_event(device_id, "ALERT_FAILED", f"Hàng đợi cảnh báo đầy ({QUEUE_MAX})",
                                    "WARNING", details=details)

    # ------------------------------------------------------------------
    # TẦNG 2: worker gửi qua từng kênh
    # ------------------------------------------------------------------
    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                for n in self.notifiers:
                    result = await n.send(job[n.name])
                    await self._log_result(job["device_id"], n.name, result,
                                           f"[{job['kind']}] {job['rule']}", "backend",
                                           {**job["details"], "channel": n.name},
                                           critical=job["kind"] != "RESOLVED")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Lỗi không mong muốn trong worker cảnh báo")
            finally:
                self._queue.task_done()

    async def send_test(self, device_id: str) -> tuple[bool, str]:
        now = datetime.now(timezone.utc)
        texts = {"telegram": f"✅ <b>TEST CẢNH BÁO</b>\n📍 <code>{html.escape(device_id)}</code>\n"
                             f"Kết nối Telegram thành công.\n🕒 {format_vn_time(now)}",
                 "discord": {"content": "✅ **TEST CẢNH BÁO** - kết nối Discord thành công.", "_kind": "TEST",
                             "embeds": [{"title": f"Bếp {device_id}", "color": DISCORD_COLORS["TEST"],
                                         "description": "Nhận được tin này nghĩa là cảnh báo qua Discord đã hoạt động.",
                                         "timestamp": now.isoformat()}]}}
        details = []
        all_ok = True
        for n in self.notifiers:
            result = await n.send(texts[n.name])
            await self._log_result(device_id, n.name, result, "[TEST]", "api",
                                   {"kind": "TEST", "channel": n.name}, critical=False)
            details.append(f"{n.name}: {result.detail}")
            all_ok = all_ok and result.ok
        return all_ok, " | ".join(details)

    async def _log_result(self, device_id: str, channel: str, result: SendResult, label: str,
                          source: str, details: dict[str, Any], critical: bool) -> None:
        event, severity = {
            "sent": ("ALERT_SENT", "CRITICAL" if critical else "INFO"),
            "skipped": ("ALERT_SKIPPED", "WARNING"),
            "capped": ("ALERT_SUPPRESSED", "WARNING"),
            "failed": ("ALERT_FAILED", "WARNING"),
        }[result.status]
        message = f"{label} qua {channel.upper()}: {result.detail}"
        log.info("%s - %s", event, message)
        await self.db.log_event(device_id, event, message, severity, source, details)
