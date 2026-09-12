"""Cấu hình Backend - đọc từ biến môi trường (docker-compose lấy giá trị trong file .env)."""
import os
from dataclasses import dataclass

# ----- Hằng số theo Bản thống nhất -----
TOPIC_PREFIX = "iot/kitchen"          # mục 2.1: iot/kitchen/{device_id}/...
FRESH_MS = 3_000                      # mục 5.2: < 3 s  -> dữ liệu tươi (xanh)
STALE_MS = 10_000                     # mục 5.2: > 10 s -> Stale Data (đỏ)


def topic(device_id: str, suffix: str) -> str:
    """topic("esp32_kitchen_01", "telemetry") -> "iot/kitchen/esp32_kitchen_01/telemetry"."""
    return f"{TOPIC_PREFIX}/{device_id}/{suffix}"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # PostgreSQL / TimescaleDB
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    pg_database: str
    # MQTT (tài khoản của Backend)
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str
    # Thiết bị mặc định
    device_id: str
    # Telegram + chống spam
    telegram_bot_token: str
    telegram_chat_id: str
    alert_cooldown_s: int
    alert_reminder_s: int
    telegram_max_per_minute: int
    # Thời gian chờ ESP32 phản hồi actuator/state sau khi gửi lệnh
    command_ack_timeout_s: float = 3.0

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_settings() -> Settings:
    return Settings(
        pg_host=os.getenv("POSTGRES_HOST", "localhost"),
        pg_port=_int("POSTGRES_PORT", 5432),
        pg_user=os.getenv("POSTGRES_USER", "iot_admin"),
        pg_password=os.getenv("POSTGRES_PASSWORD", ""),
        pg_database=os.getenv("POSTGRES_DB", "iot_kitchen"),
        mqtt_host=os.getenv("MQTT_HOST", "localhost"),
        mqtt_port=_int("MQTT_PORT", 1883),
        mqtt_user=os.getenv("MQTT_USER", "backend_svc"),
        mqtt_password=os.getenv("MQTT_PASSWORD", ""),
        device_id=os.getenv("DEVICE_ID", "esp32_kitchen_01"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        alert_cooldown_s=_int("ALERT_COOLDOWN_SECONDS", 60),
        alert_reminder_s=_int("ALERT_REMINDER_SECONDS", 300),
        telegram_max_per_minute=_int("TELEGRAM_MAX_PER_MINUTE", 20),
    )


settings = load_settings()
