"""Mô hình dữ liệu (Pydantic): payload MQTT theo Bản thống nhất + request/response của REST API."""
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

Mode = Literal["AUTO", "MANUAL"]
FanState = Literal[0, 1]


def to_iso_utc(value: datetime) -> str:
    """Chuẩn hóa thời gian trả về: ISO 8601 UTC, độ chính xác mili-giây. VD: 2026-09-11T07:30:15.123Z"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDateTime = Annotated[datetime, PlainSerializer(to_iso_utc, return_type=str, when_used="json")]


# =====================================================================
#  PAYLOAD MQTT NHẬN TỪ ESP32
# =====================================================================
class TelemetryIn(BaseModel):
    """Topic iot/kitchen/{device_id}/telemetry - Bản thống nhất mục 2.2.
    Giá trị cảm biến cho phép null (khi cảm biến lỗi) để không mất cả bản tin."""
    model_config = ConfigDict(extra="ignore")

    device_id: str | None = None
    seq: int | None = Field(None, ge=0, description="Số thứ tự bản tin, tăng 1 mỗi lần gửi")
    timestamp: datetime | None = None
    temperature: float | None = Field(None, ge=-40, le=125)
    humidity: float | None = Field(None, ge=0, le=100)
    pollution_percent: float | None = Field(None, ge=0, le=100)
    rs_ro_ratio: float | None = Field(None, ge=0, le=20)
    fan_state: FanState | None = None
    mode: Mode | None = None
    network_status: str | None = None


# ---------------------------------------------------------------------
#  KIỂM TRA TÍNH HỢP LÝ CỦA SỐ ĐO (sanity check)
#  Firmware nên gửi null khi cảm biến lỗi, nhưng thực tế nhiều thư viện trả về
#  giá trị quy ước như -1 / -127 / -999, hoặc NaN. Nếu để nguyên:
#    * -1 lọt vào avg() làm sai số liệu báo cáo và sai dữ liệu huấn luyện AI của TV5
#    * ràng buộc ge=0 sẽ làm Pydantic loại BỎ CẢ BẢN TIN, mất luôn các số đo còn tốt
#  Vì vậy ta đổi riêng trường lỗi thành null rồi mới kiểm tra, và trả về danh sách
#  trường hỏng để Backend ghi sự kiện SENSOR_FAULT.
# ---------------------------------------------------------------------
#  Chỉ dựa vào khoảng giá trị VẬT LÝ, không liệt kê "mã lỗi quy ước": các mã hay gặp
#  (-1, -99, -127, -999) đều nằm ngoài khoảng nên bị bắt sẵn, trong khi liệt kê thêm
#  những số như 85 sẽ vô tình xoá mất số đo thật (độ ẩm 85% là hoàn toàn bình thường).
RANGES: dict[str, tuple[float, float]] = {
    "temperature": (-40.0, 125.0),
    "humidity": (0.0, 100.0),
    "pollution_percent": (0.0, 100.0),
    "rs_ro_ratio": (0.0, 20.0),
}


def sanitize_readings(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Trả về (payload đã làm sạch, danh sách trường bị coi là hỏng)."""
    cleaned, invalid = dict(data), []
    for field, (low, high) in RANGES.items():
        value = cleaned.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            cleaned[field], _ = None, invalid.append(field)
            continue
        number = float(value)
        if number != number or not (low <= number <= high):   # number != number là cách bắt NaN
            cleaned[field] = None
            invalid.append(field)
    return cleaned, invalid


class StatusIn(BaseModel):
    """Topic .../status (LWT). VD: {"device_id": "esp32_kitchen_01", "status": "OFFLINE"}"""
    device_id: str | None = None
    status: Literal["ONLINE", "OFFLINE"]


class ActuatorStateIn(BaseModel):
    """Topic .../actuator/state - Bản thống nhất CHƯA quy định payload.
    Đề xuất với TV2: {"device_id", "fan_state": 0|1, "mode", "reason", "timestamp"}.
    Chấp nhận cả key "state" để tương thích."""
    model_config = ConfigDict(extra="ignore")

    device_id: str | None = None
    fan_state: FanState | None = None
    state: FanState | None = None
    mode: Mode | None = None
    reason: str | None = None          # FSM | MANUAL_COMMAND | SAFETY_OVERRIDE | IGNORED_AUTO_MODE
    timestamp: datetime | None = None

    @property
    def value(self) -> int | None:
        return self.fan_state if self.fan_state is not None else self.state


class ConfigStateIn(BaseModel):
    """Topic .../config/state (retain) - ĐỀ XUẤT MỚI, Bản thống nhất chưa có.

    ESP32 phát lại 3 ngưỡng FSM mà nó ĐANG THỰC SỰ dùng, sau mỗi lần nhận config/set
    và sau mỗi lần khởi động. Nhờ đó Backend đối chiếu được với bảng device_config:
    lệnh gửi đi mà thiết bị không áp dụng (sai tên khoá, sai đơn vị, mất gói) sẽ bị
    phát hiện ngay thay vì đến lúc demo mới biết."""
    model_config = ConfigDict(extra="ignore")

    device_id: str | None = None
    pollution_threshold: float | None = None
    temp_threshold: float | None = None
    dwell_time_seconds: float | None = None
    timestamp: datetime | None = None

    def as_dict(self) -> dict[str, float]:
        return {k: v for k, v in
                (("pollution_threshold", self.pollution_threshold),
                 ("temp_threshold", self.temp_threshold),
                 ("dwell_time_seconds", self.dwell_time_seconds))
                if v is not None}


# =====================================================================
#  REST API - REQUEST
# =====================================================================
class ActuatorRequest(BaseModel):
    state: FanState = Field(description="1 = BẬT quạt, 0 = TẮT quạt", examples=[1])


class ModeRequest(BaseModel):
    mode: Mode = Field(description="AUTO: FSM tự điều khiển; MANUAL: điều khiển từ Web", examples=["MANUAL"])


class ConfigUpdate(BaseModel):
    """Chỉ cần gửi các trường muốn đổi. 3 trường đầu được gửi xuống ESP32, 2 trường alert_* chỉ Backend dùng."""
    model_config = ConfigDict(json_schema_extra={"examples": [{"pollution_threshold": 40, "temp_threshold": 33}]})

    pollution_threshold: float | None = Field(None, ge=0, le=100, description="Ngưỡng % ô nhiễm bật quạt (FSM)")
    temp_threshold: float | None = Field(None, ge=0, le=100, description="Ngưỡng nhiệt độ °C (luật P ≥ 28% VÀ T ≥ ngưỡng)")
    dwell_time_seconds: int | None = Field(None, ge=0, le=600, description="Thời gian khóa trạng thái quạt (giây)")
    alert_pollution_threshold: float | None = Field(None, ge=0, le=100, description="Ngưỡng % ô nhiễm gửi Telegram")
    alert_temp_threshold: float | None = Field(None, ge=0, le=100, description="Ngưỡng nhiệt độ °C gửi Telegram")

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Cần gửi ít nhất 1 tham số cấu hình")
        return self


# =====================================================================
#  REST API - RESPONSE
# =====================================================================
class TelemetryOut(BaseModel):
    timestamp: UtcDateTime
    received_at: UtcDateTime | None = None
    seq: int | None = None
    temperature: float | None = None
    humidity: float | None = None
    pollution_percent: float | None = None
    rs_ro_ratio: float | None = None
    fan_state: int | None = None
    mode: str | None = None
    network_status: str | None = None


class StatusResponse(BaseModel):
    device_id: str
    device_status: Literal["ONLINE", "OFFLINE", "UNKNOWN"] = Field(description="Theo bản tin status/LWT gần nhất")
    device_status_updated_at: UtcDateTime | None
    mqtt_connected: bool = Field(description="Backend có đang kết nối Broker không")
    latest: TelemetryOut | None
    data_freshness_ms: int | None = Field(description="Thời gian hiện tại của server - timestamp bản tin mới nhất")
    freshness_level: Literal["FRESH", "DELAYED", "STALE", "NO_DATA"] = Field(
        description="FRESH < 3000 ms ≤ DELAYED ≤ 10000 ms < STALE")
    fan_state: int | None
    mode: str | None
    active_alerts: list[str]
    config_in_sync: bool | None = Field(
        description="Ngưỡng ESP32 báo về trên .../config/state có khớp bảng device_config không. "
                    "null = firmware chưa hỗ trợ topic này")
    device_config: dict[str, float] | None = Field(description="Ngưỡng ESP32 đang thực sự áp dụng")
    server_time: UtcDateTime


class HistoryResponse(BaseModel):
    device_id: str
    start: UtcDateTime
    end: UtcDateTime
    order: Literal["asc", "desc"]
    bucket_seconds: int | None
    limit: int
    offset: int
    total: int = Field(description="Tổng số bản ghi (hoặc số khung thời gian nếu có bucket_seconds)")
    next_offset: int | None = Field(description="Offset của trang tiếp theo, null nếu đã hết")
    items: list[TelemetryOut]


class ActuatorResponse(BaseModel):
    success: bool = Field(description="Broker đã nhận lệnh (PUBACK QoS 1)")
    acknowledged: bool = Field(description="ESP32 đã phản hồi actuator/state trong thời gian chờ")
    confirmed: bool = Field(description="ESP32 phản hồi VÀ trạng thái quạt đúng như yêu cầu")
    requested_state: int
    fan_state: int | None
    reason: str | None
    rtt_ms: float | None = Field(description="Round-trip time: Backend gửi lệnh -> nhận actuator/state")
    topic: str
    payload: dict[str, Any]
    warning: str | None
    message: str


class ModeResponse(BaseModel):
    success: bool
    mode: Mode
    topic: str
    payload: dict[str, Any]
    message: str


class DeviceConfig(BaseModel):
    device_id: str
    pollution_threshold: float
    temp_threshold: float
    dwell_time_seconds: int
    alert_pollution_threshold: float
    alert_temp_threshold: float
    updated_at: UtcDateTime
    updated_by: str


class ConfigResponse(BaseModel):
    success: bool
    config: DeviceConfig
    published_to_device: bool
    topic: str | None
    payload: dict[str, Any] | None
    message: str


class EventOut(BaseModel):
    id: int
    time: UtcDateTime
    device_id: str
    event_type: str
    severity: str
    source: str
    message: str | None
    details: dict[str, Any] | None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: str
    mqtt: str
    telegram: str


class AlertTestResponse(BaseModel):
    sent: bool
    detail: str


# =====================================================================
#  CHẨN ĐOÁN - dùng để TV2 tự kiểm tra firmware có đúng data contract không
# =====================================================================
class PacketLoss(BaseModel):
    method: Literal["seq", "time_gap"] = Field(
        description="seq: đếm chính xác theo số thứ tự · time_gap: ước lượng theo khoảng trống thời gian")
    received: int
    expected: int | None
    missing: int
    loss_percent: float | None
    reboots: int = Field(description="Số lần seq bị đặt lại -> thiết bị khởi động lại")


class Latency(BaseModel):
    samples: int
    avg_ms: float | None
    max_ms: float | None
    p95_ms: float | None


class ContractCheck(BaseModel):
    field: str
    ok: bool
    detail: str


class DiagnosticsResponse(BaseModel):
    device_id: str
    window_minutes: int
    samples: int
    packet_loss: PacketLoss
    time_gap_estimate: PacketLoss
    latency: Latency
    null_readings: dict[str, int] = Field(description="Số bản ghi thiếu số đo của từng cảm biến")
    contract: list[ContractCheck] = Field(description="Kết quả đối chiếu với data contract đã thống nhất")
    passed: bool
