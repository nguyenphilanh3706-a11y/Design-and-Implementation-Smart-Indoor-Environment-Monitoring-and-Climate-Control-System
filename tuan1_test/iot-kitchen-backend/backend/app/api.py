"""
REST API - Bản thống nhất mục 4: http://<backend-ip>:8000/api/v1/kitchen
  GET  /status     - trạng thái mới nhất + độ tươi dữ liệu (Data Freshness)
  GET  /history    - dữ liệu lịch sử để vẽ biểu đồ (có gộp khung thời gian)
  POST /actuator   - bật/tắt quạt thủ công
  POST /mode       - đổi AUTO / MANUAL
  POST /config     - đổi ngưỡng (lưu DB + đẩy xuống ESP32)
Bổ sung cho việc kiểm thử & báo cáo: GET /config, GET /events, POST /alerts/test
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .alert_service import AlertService
from .config import FRESH_MS, STALE_MS, Settings, topic
from .db import Database
from .mqtt_service import MqttService, MqttUnavailable
from .schemas import (ActuatorRequest, ActuatorResponse, AlertTestResponse, ConfigResponse, ConfigUpdate,
                      ContractCheck, DeviceConfig, DiagnosticsResponse, EventOut, HistoryResponse, Latency,
                      ModeRequest, ModeResponse, PacketLoss, StatusResponse, TelemetryOut)

router = APIRouter()

DEVICE_FIELDS = ("pollution_threshold", "temp_threshold", "dwell_time_seconds")   # 3 trường ESP32 cần biết
MAX_LIMIT = 5_000


# ---------------------------------------------------------------------
# Dependency: lấy các dịch vụ dùng chung đã khởi tạo trong lifespan
# ---------------------------------------------------------------------
def get_db(request: Request) -> Database:
    return request.app.state.db


def get_mqtt(request: Request) -> MqttService:
    return request.app.state.mqtt


def get_alerts(request: Request) -> AlertService:
    return request.app.state.alerts


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DbDep = Annotated[Database, Depends(get_db)]
MqttDep = Annotated[MqttService, Depends(get_mqtt)]
AlertDep = Annotated[AlertService, Depends(get_alerts)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def resolve_device(settings: Settings, device_id: str | None) -> str:
    return device_id or settings.device_id


DeviceQuery = Annotated[str | None, Query(description="Bỏ trống = dùng DEVICE_ID trong .env", examples=[None])]


def _telemetry_out(row: Any | None) -> TelemetryOut | None:
    return TelemetryOut.model_validate(dict(row)) if row is not None else None


async def _publish_or_503(mqtt: MqttService, topic_name: str, payload: dict[str, Any],
                          qos: int, retain: bool) -> None:
    try:
        await mqtt.publish_json(topic_name, payload, qos=qos, retain=retain)
    except MqttUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


# =====================================================================
#  GET /status
# =====================================================================
@router.get("/status", response_model=StatusResponse, tags=["Giám sát"],
            summary="Trạng thái hiện tại của bếp")
async def get_status(db: DbDep, mqtt: MqttDep, alerts: AlertDep, settings: SettingsDep,
                     device_id: DeviceQuery = None) -> StatusResponse:
    """Trả về bản tin telemetry mới nhất kèm `data_freshness_ms`.

    Dashboard dùng `freshness_level` để tô màu widget (mục 5.2 Bản thống nhất):
    **FRESH** < 3000 ms (xanh) · **DELAYED** 3000–10000 ms (vàng) · **STALE** > 10000 ms (đỏ).
    """
    device = resolve_device(settings, device_id)
    row = await db.latest_telemetry(device)
    now = datetime.now(timezone.utc)

    freshness_ms: int | None = None
    level: str = "NO_DATA"
    if row is not None:
        freshness_ms = max(0, int((now - row["timestamp"]).total_seconds() * 1000))
        level = "FRESH" if freshness_ms < FRESH_MS else "DELAYED" if freshness_ms <= STALE_MS else "STALE"

    st = mqtt.device_status.get(device, {})
    return StatusResponse(
        device_id=device,
        device_status=st.get("status", "UNKNOWN"),
        device_status_updated_at=st.get("updated_at"),
        mqtt_connected=mqtt.connected,
        latest=_telemetry_out(row),
        data_freshness_ms=freshness_ms,
        freshness_level=level,
        fan_state=row["fan_state"] if row is not None else None,
        mode=row["mode"] if row is not None else None,
        active_alerts=alerts.active_alerts(device),
        config_in_sync=mqtt.config_in_sync.get(device),
        device_config=mqtt.device_config.get(device),
        server_time=now,
    )


# =====================================================================
#  GET /history
# =====================================================================
@router.get("/history", response_model=HistoryResponse, tags=["Giám sát"],
            summary="Dữ liệu lịch sử (vẽ biểu đồ)")
async def get_history(
    db: DbDep, settings: SettingsDep,
    device_id: DeviceQuery = None,
    start: Annotated[datetime | None, Query(description="ISO 8601, mặc định = end - 1 giờ",
                                            examples=["2026-09-12T00:00:00Z"])] = None,
    end: Annotated[datetime | None, Query(description="ISO 8601, mặc định = bây giờ")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT, description="Số bản ghi tối đa")] = 100,
    offset: Annotated[int, Query(ge=0, description="Bỏ qua bao nhiêu bản ghi (phân trang)")] = 0,
    order: Annotated[Literal["asc", "desc"], Query(description="asc: cũ→mới (vẽ biểu đồ)")] = "asc",
    bucket_seconds: Annotated[int | None, Query(ge=1, le=86_400,
                                                description="Gộp dữ liệu theo khung N giây bằng time_bucket() "
                                                            "của TimescaleDB. VD: 60 = trung bình mỗi phút")] = None,
) -> HistoryResponse:
    """Cảm biến gửi 2 s/lần, tức 1800 điểm/giờ. Vẽ thẳng 24 h (43.200 điểm) sẽ làm treo trình duyệt,
    nên với khoảng thời gian dài hãy dùng `bucket_seconds` để server gộp sẵn."""
    device = resolve_device(settings, device_id)
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=1)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start phải nhỏ hơn end")

    total, rows = await db.history(device, start, end, limit, offset, order, bucket_seconds)
    items = [TelemetryOut.model_validate(dict(r)) for r in rows]
    served = offset + len(items)
    return HistoryResponse(
        device_id=device, start=start, end=end, order=order, bucket_seconds=bucket_seconds,
        limit=limit, offset=offset, total=total,
        next_offset=served if served < total else None, items=items,
    )


# =====================================================================
#  POST /actuator
# =====================================================================
@router.post("/actuator", response_model=ActuatorResponse, tags=["Điều khiển"],
             summary="Bật/tắt quạt thủ công")
async def post_actuator(body: ActuatorRequest, db: DbDep, mqtt: MqttDep, settings: SettingsDep,
                        device_id: DeviceQuery = None) -> ActuatorResponse:
    """Gửi `{"command":"SET_FAN","state":0|1}` lên `.../actuator/set` (QoS 1, retain=false).

    Backend chờ tối đa 3 giây để ESP32 phản hồi trên `.../actuator/state`, nhờ đó đo được
    `rtt_ms` (số liệu cho Bài test 2 - độ trễ điều khiển). ESP32 chỉ nghe lệnh khi đang ở
    chế độ MANUAL, nên nếu thiết bị đang AUTO, API vẫn gửi nhưng kèm cảnh báo trong `warning`.
    """
    device = resolve_device(settings, device_id)
    warning = None
    if mqtt.latest_mode.get(device) == "AUTO":
        warning = "Thiết bị đang ở chế độ AUTO nên sẽ bỏ qua lệnh tay. Hãy gọi POST /mode {\"mode\":\"MANUAL\"} trước."

    result = await _send_fan(mqtt, device, body.state)
    fan_text = "BẬT" if body.state == 1 else "TẮT"
    confirmed = result["acknowledged"] and result["fan_state"] == body.state
    if result["acknowledged"]:
        message = (f"ESP32 đã xác nhận {fan_text} quạt sau {result['rtt_ms']} ms" if confirmed
                   else f"ESP32 phản hồi nhưng quạt đang ở trạng thái {result['fan_state']} "
                        f"(lý do: {result['reason'] or 'không rõ'})")
    else:
        message = (f"Đã gửi lệnh {fan_text} quạt lên Broker nhưng ESP32 chưa phản hồi trong "
                   f"{settings.command_ack_timeout_s:g}s - kiểm tra thiết bị còn ONLINE không")

    await db.log_event(device, "MANUAL_FAN_COMMAND", f"Web yêu cầu {fan_text} quạt - {message}",
                       "INFO" if confirmed else "WARNING", "api",
                       {"requested_state": body.state, **{k: result[k] for k in ("acknowledged", "rtt_ms")}})
    return ActuatorResponse(success=True, confirmed=confirmed, requested_state=body.state,
                            warning=warning, message=message, **result)


async def _send_fan(mqtt: MqttService, device: str, state: int) -> dict[str, Any]:
    try:
        return await mqtt.send_fan_command(device, state)
    except MqttUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


# =====================================================================
#  POST /mode
# =====================================================================
@router.post("/mode", response_model=ModeResponse, tags=["Điều khiển"],
             summary="Chuyển chế độ AUTO / MANUAL")
async def post_mode(body: ModeRequest, db: DbDep, mqtt: MqttDep, settings: SettingsDep,
                    device_id: DeviceQuery = None) -> ModeResponse:
    """Publish `{"mode":"AUTO"|"MANUAL"}` lên `.../mode/set` với **retain = true**:
    ESP32 khởi động lại vẫn nhận được chế độ đã chọn ngay khi subscribe."""
    device = resolve_device(settings, device_id)
    topic_name, payload = topic(device, "mode/set"), {"mode": body.mode}
    await _publish_or_503(mqtt, topic_name, payload, qos=1, retain=True)

    mqtt.latest_mode[device] = body.mode   # cập nhật ngay, telemetry kế tiếp sẽ xác nhận lại
    message = ("Đã chuyển sang AUTO: FSM trên ESP32 tự điều khiển quạt" if body.mode == "AUTO"
               else "Đã chuyển sang MANUAL: quạt chỉ đổi trạng thái khi có lệnh từ Web")
    await db.log_event(device, "MODE_CHANGED", message, "INFO", "api", payload)
    return ModeResponse(success=True, mode=body.mode, topic=topic_name, payload=payload, message=message)


# =====================================================================
#  GET/POST /config
# =====================================================================
@router.get("/config", response_model=DeviceConfig, tags=["Cấu hình"],
            summary="Xem ngưỡng đang áp dụng")
async def get_config(db: DbDep, settings: SettingsDep, device_id: DeviceQuery = None) -> DeviceConfig:
    device = resolve_device(settings, device_id)
    await db.ensure_config(device)
    return DeviceConfig.model_validate(dict(await db.get_config(device)))


@router.post("/config", response_model=ConfigResponse, tags=["Cấu hình"],
             summary="Đổi ngưỡng (lưu DB + đẩy xuống ESP32)")
async def post_config(body: ConfigUpdate, db: DbDep, mqtt: MqttDep, alerts: AlertDep, settings: SettingsDep,
                      device_id: DeviceQuery = None) -> ConfigResponse:
    """Ngưỡng được lưu vào bảng `device_config` (còn nguyên sau khi khởi động lại) rồi mới publish
    xuống `.../config/set` (QoS 1, retain = true).

    `pollution_threshold`, `temp_threshold`, `dwell_time_seconds` là tham số FSM của ESP32;
    `alert_pollution_threshold`, `alert_temp_threshold` là ngưỡng bắn Telegram, chỉ Backend dùng.
    """
    device = resolve_device(settings, device_id)
    fields = body.model_dump(exclude_none=True)
    row = await db.update_config(device, fields, updated_by="api")
    config = DeviceConfig.model_validate(dict(row))
    alerts.set_thresholds(device, dict(row))   # áp dụng ngay cho bản tin telemetry kế tiếp

    device_payload = {k: fields[k] for k in DEVICE_FIELDS if k in fields}
    topic_name = topic(device, "config/set") if device_payload else None
    if device_payload:
        await _publish_or_503(mqtt, topic_name, device_payload, qos=1, retain=True)

    changed = ", ".join(f"{k}={v:g}" for k, v in fields.items())
    message = (f"Đã cập nhật {changed}. "
               + ("Đã gửi tham số FSM xuống ESP32." if device_payload
                  else "Chỉ là ngưỡng cảnh báo của Backend nên không cần gửi xuống ESP32."))
    await db.log_event(device, "CONFIG_UPDATED", message, "INFO", "api", fields)
    return ConfigResponse(success=True, config=config, published_to_device=bool(device_payload),
                          topic=topic_name, payload=device_payload or None, message=message)


# =====================================================================
#  Nhật ký & kiểm thử cảnh báo
# =====================================================================
@router.get("/events", response_model=list[EventOut], tags=["Giám sát"],
            summary="Nhật ký sự kiện gần đây")
async def get_events(db: DbDep, settings: SettingsDep, device_id: DeviceQuery = None,
                     limit: Annotated[int, Query(ge=1, le=500)] = 50,
                     event_type: Annotated[str | None, Query(
                         description="Lọc 1 loại, VD: ALERT_SENT, DEVICE_OFFLINE")] = None) -> list[EventOut]:
    rows = await db.recent_events(resolve_device(settings, device_id), limit, event_type)
    return [EventOut.model_validate(dict(r)) for r in rows]


@router.get("/diagnostics", response_model=DiagnosticsResponse, tags=["Giám sát"],
            summary="Chấm điểm chất lượng dữ liệu & đối chiếu data contract")
async def get_diagnostics(
    db: DbDep, mqtt: MqttDep, settings: SettingsDep, device_id: DeviceQuery = None,
    minutes: Annotated[int, Query(ge=1, le=1440, description="Xét dữ liệu trong bao nhiêu phút gần đây")] = 10,
    expected_interval_s: Annotated[float, Query(gt=0, le=60,
                                                description="Chu kỳ gửi telemetry đã thống nhất (giây)")] = 2.0,
) -> DiagnosticsResponse:
    """Một lần gọi là biết firmware có gửi đúng quy ước không, kèm sẵn số liệu cho Bài test 1 và 4.

    * **packet_loss** tính chính xác theo trường `seq`; **time_gap_estimate** là cách ước lượng
      cũ theo khoảng trống thời gian - đưa cả hai vào báo cáo để so sánh.
    * **contract** liệt kê từng mục đạt/chưa đạt để TV2 biết cần sửa gì.
    """
    device = resolve_device(settings, device_id)
    d = await db.diagnostics(device, minutes, expected_interval_s)
    samples = d["samples"] or 0

    # --- Mất gói theo số thứ tự ---
    seq_missing = int(d["seq_missing"] or 0)
    seq_received = int(d["with_seq"] or 0)
    seq_expected = seq_received + seq_missing if seq_received else None
    by_seq = PacketLoss(
        method="seq", received=seq_received, expected=seq_expected, missing=seq_missing,
        loss_percent=round(100 * seq_missing / seq_expected, 2) if seq_expected else None,
        reboots=int(d["reboots"] or 0))

    # --- Ước lượng theo khoảng trống thời gian ---
    gap_missing = int(d["gap_missing"] or 0)
    gap_expected = samples + gap_missing if samples else None
    by_gap = PacketLoss(
        method="time_gap", received=samples, expected=gap_expected, missing=gap_missing,
        loss_percent=round(100 * gap_missing / gap_expected, 2) if gap_expected else None, reboots=0)

    nulls = {f: int(d[f"null_{f}"] or 0) for f in
             ("temperature", "humidity", "pollution_percent", "rs_ro_ratio")}
    latency = Latency(samples=samples,
                      **{k: round(float(d[v]), 1) if d[v] is not None else None
                         for k, v in (("avg_ms", "lat_avg"), ("max_ms", "lat_max"), ("p95_ms", "lat_p95"))})

    checks = [
        ContractCheck(field="seq", ok=samples > 0 and seq_received == samples,
                      detail=f"{seq_received}/{samples} bản tin có trường seq"
                             + ("" if seq_received == samples else " - thiếu seq thì phải ước lượng mất gói")),
        ContractCheck(field="timestamp_ms", ok=samples > 0 and int(d["with_ms"] or 0) > samples * 0.5,
                      detail=f"{int(d['with_ms'] or 0)}/{samples} mốc thời gian có phần mili-giây"
                             " (chỉ tới giây thì độ tươi dữ liệu sai số tới ±1 s)"),
        ContractCheck(field="latency", ok=latency.max_ms is not None and -1000 < latency.max_ms < 5000,
                      detail=f"độ trễ lớn nhất {latency.max_ms} ms"
                             " (âm nghĩa là đồng hồ ESP32 chạy nhanh hơn server, cần đồng bộ NTP)"),
        ContractCheck(field="sensor_readings", ok=all(v == 0 for v in nulls.values()),
                      detail="không có số đo thiếu" if all(v == 0 for v in nulls.values())
                             else f"thiếu số đo: {', '.join(f'{k}={v}' for k, v in nulls.items() if v)}"),
        ContractCheck(field="config_state", ok=mqtt.config_in_sync.get(device) is True,
                      detail={None: "firmware chưa publish topic .../config/state",
                              True: "ngưỡng trên thiết bị khớp CSDL",
                              False: "ngưỡng trên thiết bị KHÁC với CSDL"}[mqtt.config_in_sync.get(device)]),
    ]
    return DiagnosticsResponse(
        device_id=device, window_minutes=minutes, samples=samples, packet_loss=by_seq,
        time_gap_estimate=by_gap, latency=latency, null_readings=nulls, contract=checks,
        passed=all(c.ok for c in checks))


@router.post("/alerts/test", response_model=AlertTestResponse, tags=["Cảnh báo"],
             summary="Gửi thử tin nhắn Telegram")
async def post_alert_test(alerts: AlertDep, settings: SettingsDep,
                          device_id: DeviceQuery = None) -> AlertTestResponse:
    """Kiểm tra `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` mà không cần chờ ô nhiễm vượt ngưỡng."""
    ok, detail = await alerts.send_test(resolve_device(settings, device_id))
    return AlertTestResponse(sent=ok, detail=detail)
