-- =====================================================================
--  LƯỢC ĐỒ CSDL - HỆ THỐNG IoT GIÁM SÁT & THÔNG GIÓ PHÒNG BẾP  (TV3)
--  PostgreSQL 17 + TimescaleDB
--
--  File này được container TimescaleDB tự chạy MỘT LẦN DUY NHẤT,
--  khi volume dữ liệu còn trống (lần "docker compose up" đầu tiên).
--  Sửa file này và muốn áp dụng lại:  docker compose down -v   (XÓA HẾT dữ liệu)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------
-- 1) TELEMETRY: dữ liệu cảm biến gửi định kỳ 2 giây/lần  -> HYPERTABLE
--    Tên cột khớp 100% với payload JSON trong Bản thống nhất (mục 2.2)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry (
    time               TIMESTAMPTZ      NOT NULL,               -- thời điểm lấy mẫu (trường "timestamp" do ESP32 gửi)
    received_at        TIMESTAMPTZ      NOT NULL DEFAULT now(), -- thời điểm Backend nhận được -> dùng đo độ trễ (Bài test 1)
    device_id          TEXT             NOT NULL,
    seq                BIGINT,                                   -- số thứ tự bản tin do ESP32 đếm tăng dần (Bài test 4: đo mất gói chính xác)
    temperature        DOUBLE PRECISION,                         -- °C
    humidity           DOUBLE PRECISION,                         -- %RH
    pollution_percent  DOUBLE PRECISION CHECK (pollution_percent BETWEEN 0 AND 100),
    rs_ro_ratio        DOUBLE PRECISION,                         -- tỉ số Rs/R0 của MQ-135
    fan_state          SMALLINT         CHECK (fan_state IN (0, 1)),
    mode               TEXT             CHECK (mode IN ('AUTO', 'MANUAL')),
    network_status     TEXT
);

-- Biến bảng thường thành hypertable: TimescaleDB tự chia dữ liệu thành các "chunk" 1 ngày
SELECT create_hypertable('telemetry', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);

-- Truy vấn phổ biến nhất: dữ liệu của 1 thiết bị, mới nhất trước
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time ON telemetry (device_id, time DESC);

-- Tự động xóa chunk cũ hơn 90 ngày (retention policy - tính năng của TimescaleDB)
SELECT add_retention_policy('telemetry', INTERVAL '90 days', if_not_exists => TRUE);


-- ---------------------------------------------------------------------
-- 2) SYSTEM_EVENTS: nhật ký sự kiện hệ thống
--    event_type: DEVICE_ONLINE, DEVICE_OFFLINE, FAN_STATE_CHANGED, SAFETY_OVERRIDE,
--                ACTUATOR_COMMAND, MODE_CHANGE, CONFIG_UPDATED,
--                ALERT_SENT, ALERT_SUPPRESSED, ALERT_SKIPPED, ALERT_FAILED
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_events (
    id          BIGSERIAL    PRIMARY KEY,
    time        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    device_id   TEXT         NOT NULL,
    event_type  TEXT         NOT NULL,
    severity    TEXT         NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    source      TEXT         NOT NULL DEFAULT 'backend',        -- esp32 | api | backend
    message     TEXT,
    details     JSONB                                           -- dữ liệu kèm theo (payload, giá trị đo...)
);

CREATE INDEX IF NOT EXISTS idx_events_device_time ON system_events (device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_events_type_time   ON system_events (event_type, time DESC);


-- ---------------------------------------------------------------------
-- 3) DEVICE_CONFIG: cấu hình ngưỡng, mỗi thiết bị 1 dòng
--    (Backend tự tạo dòng mặc định cho DEVICE_ID khi khởi động)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_config (
    device_id                  TEXT             PRIMARY KEY,
    -- Tham số FSM: gửi xuống ESP32 qua topic .../config/set
    pollution_threshold        DOUBLE PRECISION NOT NULL DEFAULT 40 CHECK (pollution_threshold BETWEEN 0 AND 100),
    temp_threshold             DOUBLE PRECISION NOT NULL DEFAULT 33 CHECK (temp_threshold BETWEEN 0 AND 100),
    dwell_time_seconds         INTEGER          NOT NULL DEFAULT 30 CHECK (dwell_time_seconds BETWEEN 0 AND 600),
    -- Ngưỡng cảnh báo Telegram: chỉ Backend sử dụng
    alert_pollution_threshold  DOUBLE PRECISION NOT NULL DEFAULT 50 CHECK (alert_pollution_threshold BETWEEN 0 AND 100),
    alert_temp_threshold       DOUBLE PRECISION NOT NULL DEFAULT 40 CHECK (alert_temp_threshold BETWEEN 0 AND 100),
    updated_at                 TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_by                 TEXT             NOT NULL DEFAULT 'system'
);
