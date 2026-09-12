"""
ĐIỂM KHỞI ĐỘNG BACKEND - FastAPI
Vòng đời: kết nối TimescaleDB -> nạp ngưỡng -> bật dịch vụ Telegram -> bật dịch vụ MQTT -> phục vụ REST API.
Tài liệu API tự sinh: http://localhost:8000/docs (Swagger UI) · http://localhost:8000/redoc
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import api
from .alert_service import AlertService
from .config import TOPIC_PREFIX, load_settings
from .db import Database
from .mqtt_service import MqttService
from .schemas import HealthResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
# httpx ghi lại URL của mỗi request ở mức INFO -> sẽ lộ TELEGRAM_BOT_TOKEN trong docker logs
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")

DESCRIPTION = f"""
REST API của **Hệ thống giám sát môi trường & tự động thông gió phòng bếp**
(đồ án IoT - Thành viên 3: Backend, CSDL, Cảnh báo).

### Luồng dữ liệu
```
ESP32 --MQTT--> Mosquitto --> Backend (ingest) --> TimescaleDB
                                  |                    |
                          Telegram Bot            REST API --> Web Dashboard
```

### Quy ước
* Topic MQTT: `{TOPIC_PREFIX}/{{device_id}}/...`
* Mọi mốc thời gian là **ISO 8601 UTC, đuôi `Z`**, độ chính xác mili-giây.
* Mọi endpoint đều nhận tham số `device_id`; bỏ trống thì dùng `DEVICE_ID` trong `.env`.
"""

TAGS = [
    {"name": "Giám sát", "description": "Đọc dữ liệu: trạng thái hiện tại, lịch sử, nhật ký sự kiện."},
    {"name": "Điều khiển", "description": "Gửi lệnh xuống ESP32 qua MQTT (quạt, chế độ AUTO/MANUAL)."},
    {"name": "Cấu hình", "description": "Ngưỡng FSM và ngưỡng cảnh báo, lưu trong bảng `device_config`."},
    {"name": "Cảnh báo", "description": "Kiểm tra kênh gửi tin Telegram."},
    {"name": "Hệ thống", "description": "Kiểm tra sức khoẻ dịch vụ (dùng cho healthcheck của Docker)."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db, alerts = Database(settings), None
    await db.connect()

    alerts = AlertService(settings, db)
    mqtt = MqttService(settings, db, alerts)
    app.state.settings, app.state.db, app.state.alerts, app.state.mqtt = settings, db, alerts, mqtt

    await db.ensure_config(settings.device_id)                     # tạo dòng cấu hình mặc định nếu chưa có
    alerts.set_thresholds(settings.device_id, dict(await db.get_config(settings.device_id)))
    await alerts.start()
    await mqtt.start()
    await db.log_event(settings.device_id, "BACKEND_STARTED", "Backend khởi động", "INFO", "backend")
    log.info("Backend sẵn sàng - Swagger UI tại http://localhost:8000/docs")
    try:
        yield
    finally:
        await db.log_event(settings.device_id, "BACKEND_STOPPED", "Backend dừng", "INFO", "backend")
        await mqtt.stop()
        await alerts.stop()
        await db.close()
        log.info("Đã đóng kết nối MQTT và CSDL")


app = FastAPI(
    title="IoT Kitchen Backend API",
    description=DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS,
    lifespan=lifespan,
    contact={"name": "Thành viên 3 - Backend & CSDL"},
)

# Dashboard (Vite chạy ở cổng 5173/3000) gọi API từ origin khác -> bắt buộc bật CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router, prefix="/api/v1/kitchen")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/health", response_model=HealthResponse, tags=["Hệ thống"], summary="Kiểm tra sức khoẻ dịch vụ")
async def health() -> HealthResponse:
    """`database`: truy vấn thử `SELECT 1` · `mqtt`: Backend có đang kết nối Broker không ·
    `telegram`: đã cấu hình token/chat_id chưa. Docker Desktop dùng endpoint này để hiện trạng thái *healthy*."""
    try:
        await app.state.db.ping()
        database = "connected"
    except Exception as exc:
        database = f"error: {type(exc).__name__}"

    mqtt = "connected" if app.state.mqtt.connected else "disconnected"
    telegram = "configured" if app.state.settings.telegram_enabled else "not_configured"
    ok = database == "connected" and mqtt == "connected"
    return HealthResponse(status="ok" if ok else "degraded", database=database, mqtt=mqtt, telegram=telegram)
