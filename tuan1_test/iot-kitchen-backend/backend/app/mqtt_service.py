"""
BACKGROUND SERVICE MQTT
  * Lắng nghe telemetry / status (LWT) / actuator/state từ Broker
  * Ghi telemetry vào TimescaleDB ngay khi nhận + chuyển cho AlertService kiểm tra ngưỡng
  * Cung cấp hàm publish lệnh cho REST API (actuator/set, mode/set, config/set)
  * Tự kết nối lại theo Exponential Backoff (1s, 2s, 4s ... tối đa 30s)
"""
import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import aiomqtt
from pydantic import ValidationError

from .alert_service import AlertService
from .config import TOPIC_PREFIX, Settings, topic
from .db import Database
from .schemas import ActuatorStateIn, ConfigStateIn, StatusIn, TelemetryIn, sanitize_readings

log = logging.getLogger("mqtt")

MAX_PAST_SKEW = timedelta(hours=24)    # gửi bù sau khi mất mạng -> vẫn nhận
MAX_FUTURE_SKEW = timedelta(seconds=60)  # đồng hồ thiết bị chạy nhanh -> không nhận (xem _sample_time)
DEVICE_CONFIG_FIELDS = ("pollution_threshold", "temp_threshold", "dwell_time_seconds")


class MqttUnavailable(RuntimeError):
    """Backend đang mất kết nối Broker."""


class MqttService:
    def __init__(self, settings: Settings, db: Database, alerts: AlertService):
        self.s = settings
        self.db = db
        self.alerts = alerts
        self.client: aiomqtt.Client | None = None
        self.connected = False
        self.device_status: dict[str, dict[str, Any]] = {}   # device_id -> {"status", "updated_at"}
        self.latest_mode: dict[str, str] = {}                # device_id -> "AUTO"/"MANUAL" (từ telemetry)
        self.device_config: dict[str, dict[str, float]] = {}  # ngưỡng ESP32 báo đang áp dụng (config/state)
        self.config_in_sync: dict[str, bool] = {}             # có khớp bảng device_config không
        self._ack_waiters: dict[str, list[asyncio.Future]] = {}
        self._last_seq: dict[str, int] = {}
        self._last_faults: dict[str, list[str]] = {}          # để không ghi lại sự kiện y hệt mỗi 2 giây
        self._task: asyncio.Task | None = None
        self._last_skew_warning = 0.0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="mqtt-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    # ------------------------------------------------------------------
    # Vòng lặp kết nối + nhận bản tin
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        delay = 1
        while True:
            client = aiomqtt.Client(
                hostname=self.s.mqtt_host, port=self.s.mqtt_port,
                username=self.s.mqtt_user, password=self.s.mqtt_password,
                identifier=f"backend-{uuid.uuid4().hex[:8]}", keepalive=30,
            )
            try:
                async with client:
                    self.client, self.connected, delay = client, True, 1
                    log.info("Đã kết nối MQTT broker %s:%s (user=%s)",
                             self.s.mqtt_host, self.s.mqtt_port, self.s.mqtt_user)
                    await client.subscribe(f"{TOPIC_PREFIX}/+/telemetry", qos=0)
                    await client.subscribe(f"{TOPIC_PREFIX}/+/status", qos=1)
                    await client.subscribe(f"{TOPIC_PREFIX}/+/actuator/state", qos=1)
                    await client.subscribe(f"{TOPIC_PREFIX}/+/config/state", qos=1)
                    async for message in client.messages:
                        await self._dispatch(message)
            except aiomqtt.MqttError as exc:
                log.warning("Mất kết nối MQTT (%s) - thử lại sau %ss", exc, delay)
            finally:
                self.client, self.connected = None, False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _dispatch(self, message: aiomqtt.Message) -> None:
        topic_name = message.topic.value
        parts = topic_name.split("/")
        if len(parts) < 4:
            return
        device_id, kind = parts[2], "/".join(parts[3:])
        try:
            data = json.loads(message.payload)
            if not isinstance(data, dict):
                raise ValueError("payload không phải JSON object")
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("Bỏ qua bản tin sai JSON trên %s: %s", topic_name, exc)
            return
        try:
            if kind == "telemetry":
                await self._on_telemetry(device_id, data)
            elif kind == "status":
                await self._on_status(device_id, data, message.retain)
            elif kind == "actuator/state":
                await self._on_actuator_state(device_id, data, message.retain)
            elif kind == "config/state":
                await self._on_config_state(device_id, data)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            log.warning("Sai data contract trên %s -> bỏ qua. Lỗi: %s", topic_name, "; ".join(errors))
        except Exception:
            log.exception("Lỗi khi xử lý bản tin %s", topic_name)   # không để 1 bản tin lỗi làm sập vòng lặp

    # ------------------------------------------------------------------
    # Xử lý từng loại topic
    # ------------------------------------------------------------------
    async def _on_telemetry(self, device_id: str, data: dict[str, Any]) -> None:
        # Đổi số đo phi vật lý (-1, -999, NaN...) thành null TRƯỚC khi kiểm tra,
        # để một cảm biến hỏng không làm mất cả bản tin
        cleaned, invalid = sanitize_readings(data)
        t = TelemetryIn.model_validate(cleaned)
        if t.device_id and t.device_id != device_id:
            log.warning("device_id trong payload (%s) khác topic (%s) -> dùng theo topic", t.device_id, device_id)

        received_at = datetime.now(timezone.utc)
        sample_time = await self._sample_time(device_id, t.timestamp, received_at)

        await self._check_sensor_faults(device_id, invalid, data)
        self._check_seq(device_id, t.seq)

        try:
            await self.db.insert_telemetry(device_id, sample_time, received_at, t)
        except Exception as exc:
            log.error("Không ghi được telemetry vào DB: %s", exc)

        if t.mode:
            self.latest_mode[device_id] = t.mode
        await self.alerts.evaluate(device_id, t, sample_time)

    async def _check_sensor_faults(self, device_id: str, invalid: list[str], raw: dict[str, Any]) -> None:
        """Ghi sự kiện khi danh sách cảm biến hỏng THAY ĐỔI (tránh ghi lại y hệt mỗi 2 giây)."""
        if invalid == self._last_faults.get(device_id, []):
            return
        self._last_faults[device_id] = invalid
        if invalid:
            await self.db.log_event(
                device_id, "SENSOR_FAULT",
                f"Số đo không hợp lệ, đã lưu thành null: {', '.join(invalid)}", "WARNING", "esp32",
                {"fields": invalid, "raw": {k: raw.get(k) for k in invalid}})
        else:
            await self.db.log_event(device_id, "SENSOR_RECOVERED", "Các cảm biến đã báo số đo hợp lệ trở lại",
                                    "INFO", "esp32")

    def _check_seq(self, device_id: str, seq: int | None) -> None:
        """Phát hiện mất gói và khởi động lại thiết bị. Thống kê chi tiết tính bằng SQL ở /diagnostics."""
        if seq is None:
            return
        previous = self._last_seq.get(device_id)
        self._last_seq[device_id] = seq
        if previous is None:
            return
        if seq < previous:
            log.info("Thiết bị %s khởi động lại (seq %d -> %d)", device_id, previous, seq)
        elif seq > previous + 1:
            log.warning("Mất %d bản tin của %s (seq %d -> %d)", seq - previous - 1, device_id, previous, seq)

    async def _sample_time(self, device_id: str, timestamp: datetime | None, received_at: datetime) -> datetime:
        """Chọn mốc thời gian đáng tin để lưu vào cột `time`.

        * Lệch về QUÁ KHỨ tới 24 giờ: chấp nhận, vì thiết bị có thể đệm dữ liệu lúc mất mạng
          rồi gửi bù sau. Đây là dữ liệu thật, chỉ về muộn.
        * Lệch về TƯƠNG LAI: KHÔNG BAO GIỜ hợp lệ. Nếu nhận thì bản tin đó sẽ vĩnh viễn là
          "mới nhất" (truy vấn sắp xếp theo time DESC), che hết dữ liệu thật, và độ tươi dữ liệu
          luôn hiện FRESH kể cả khi thiết bị đã chết. Chỉ nới 60 giây cho sai lệch đồng hồ thông thường.
        """
        if timestamp is None:
            return received_at
        if timestamp.tzinfo is None:                      # không có "Z" -> coi là UTC
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        lech = timestamp - received_at                    # dương = đồng hồ thiết bị chạy nhanh
        if lech > MAX_FUTURE_SKEW:
            await self._log_clock_skew(device_id, timestamp, lech,
                                       "đồng hồ thiết bị chạy NHANH hơn server")
            return received_at
        if -lech > MAX_PAST_SKEW:
            await self._log_clock_skew(device_id, timestamp, lech,
                                       "đồng hồ thiết bị chạy CHẬM hơn server quá 24 giờ")
            return received_at
        return timestamp

    async def _log_clock_skew(self, device_id: str, timestamp: datetime, lech: timedelta, ly_do: str) -> None:
        """Ghi cảnh báo tối đa 1 lần/phút để không làm ngập log và bảng sự kiện."""
        if time.monotonic() - self._last_skew_warning < 60:
            return
        self._last_skew_warning = time.monotonic()
        giay = round(lech.total_seconds(), 1)
        message = (f"{ly_do} {abs(giay):.0f} giây -> tạm dùng giờ server để lưu. "
                   f"Thiết bị cần đồng bộ NTP trước khi gửi telemetry.")
        log.warning("%s (timestamp nhận được: %s)", message, timestamp.isoformat())
        await self.db.log_event(device_id, "CLOCK_SKEW", message, "WARNING", "esp32",
                                {"timestamp": timestamp.isoformat(), "lech_giay": giay})

    async def _on_status(self, device_id: str, data: dict[str, Any], retained: bool) -> None:
        st = StatusIn.model_validate(data)
        now = datetime.now(timezone.utc)
        self.device_status[device_id] = {"status": st.status, "updated_at": now}
        log.info("Thiết bị %s -> %s%s", device_id, st.status, " (retained)" if retained else "")
        if retained:
            return  # bản tin retained nhận lại khi Backend vừa kết nối -> không ghi trùng sự kiện
        if st.status == "ONLINE":
            await self.db.log_event(device_id, "DEVICE_ONLINE", "Thiết bị kết nối Broker", "INFO", "esp32", data)
        else:
            await self.db.log_event(device_id, "DEVICE_OFFLINE",
                                    "Thiết bị mất kết nối (Last Will do Broker phát)", "WARNING", "esp32", data)

    async def _on_actuator_state(self, device_id: str, data: dict[str, Any], retained: bool) -> None:
        state = ActuatorStateIn.model_validate(data)
        if state.value is None:
            log.warning("actuator/state thiếu fan_state: %s", data)
            return
        if retained:
            return
        for future in self._ack_waiters.pop(device_id, []):   # đánh thức các lệnh đang chờ phản hồi
            if not future.done():
                future.set_result(state)
        fan_text = "BẬT" if state.value == 1 else "TẮT"
        if state.reason == "SAFETY_OVERRIDE":
            await self.db.log_event(device_id, "SAFETY_OVERRIDE",
                                    f"ESP32 cưỡng bức {fan_text} quạt vì ô nhiễm nguy hiểm", "CRITICAL", "esp32", data)
        elif state.reason == "IGNORED_AUTO_MODE":
            # Không phải lần đổi trạng thái: thiết bị báo "tôi bỏ qua lệnh tay vì đang ở AUTO"
            await self.db.log_event(device_id, "COMMAND_IGNORED",
                                    "ESP32 bỏ qua lệnh điều khiển tay vì đang ở chế độ AUTO",
                                    "WARNING", "esp32", data)
        else:
            await self.db.log_event(device_id, "FAN_STATE_CHANGED",
                                    f"Quạt {fan_text} (lý do: {state.reason or 'không rõ'})", "INFO", "esp32", data)

    async def _on_config_state(self, device_id: str, data: dict[str, Any]) -> None:
        """Đối chiếu ngưỡng ESP32 đang dùng với ngưỡng lưu trong CSDL."""
        applied = ConfigStateIn.model_validate(data).as_dict()
        if not applied:
            log.warning("config/state của %s không có trường ngưỡng nào", device_id)
            return
        self.device_config[device_id] = applied

        row = await self.db.get_config(device_id)
        desired = {k: float(row[k]) for k in DEVICE_CONFIG_FIELDS} if row else {}
        lech = {k: {"mong_muon": v, "thiet_bi_dang_dung": applied[k]}
                for k, v in desired.items() if k in applied and abs(v - applied[k]) > 0.01}
        thieu = [k for k in desired if k not in applied]
        in_sync = not lech and not thieu

        if self.config_in_sync.get(device_id) == in_sync:
            return                                   # trạng thái không đổi -> không ghi lại sự kiện
        self.config_in_sync[device_id] = in_sync
        if in_sync:
            await self.db.log_event(device_id, "CONFIG_CONFIRMED",
                                    "ESP32 xác nhận đã áp dụng đúng ngưỡng", "INFO", "esp32", applied)
        else:
            await self.db.log_event(
                device_id, "CONFIG_MISMATCH",
                f"Ngưỡng trên ESP32 KHÔNG khớp CSDL (lệch: {', '.join(lech) or 'không'}; "
                f"thiếu: {', '.join(thieu) or 'không'})", "WARNING", "esp32",
                {"lech": lech, "thieu": thieu})

    # ------------------------------------------------------------------
    # Publish (dùng cho REST API)
    # ------------------------------------------------------------------
    async def publish_json(self, topic_name: str, payload: dict[str, Any], qos: int, retain: bool) -> None:
        client = self.client
        if client is None or not self.connected:
            raise MqttUnavailable("Backend chưa kết nối được MQTT Broker")
        try:
            # QoS 1: hàm chỉ trả về khi Broker đã gửi PUBACK
            await client.publish(topic_name, json.dumps(payload), qos=qos, retain=retain, timeout=5)
        except (aiomqtt.MqttError, TimeoutError) as exc:
            raise MqttUnavailable(f"Không publish được lên Broker: {exc}") from exc

    async def send_fan_command(self, device_id: str, state: int) -> dict[str, Any]:
        topic_name = topic(device_id, "actuator/set")
        payload = {"command": "SET_FAN", "state": state}      # Bản thống nhất mục 2.2

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._ack_waiters.setdefault(device_id, []).append(future)   # đăng ký chờ TRƯỚC khi gửi
        started = time.perf_counter()
        try:
            await self.publish_json(topic_name, payload, qos=1, retain=False)
            ack: ActuatorStateIn = await asyncio.wait_for(future, timeout=self.s.command_ack_timeout_s)
            return {"acknowledged": True, "fan_state": ack.value, "reason": ack.reason,
                    "rtt_ms": round((time.perf_counter() - started) * 1000, 1),
                    "topic": topic_name, "payload": payload}
        except TimeoutError:
            return {"acknowledged": False, "fan_state": None, "reason": None, "rtt_ms": None,
                    "topic": topic_name, "payload": payload}
        finally:
            waiters = self._ack_waiters.get(device_id, [])
            if future in waiters:
                waiters.remove(future)
