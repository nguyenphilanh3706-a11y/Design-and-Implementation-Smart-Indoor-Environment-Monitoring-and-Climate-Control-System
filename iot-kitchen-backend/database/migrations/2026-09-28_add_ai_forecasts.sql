-- =====================================================================
--  Migration 2026-09-28: lưu dự báo của mô hình LSTM (kitchen_lstm_15m.h5)
--  Mô hình dự báo LỚP rủi ro sau 15 phút (SAFE / WARNING / DANGER), không dự báo giá trị
--  ô nhiễm, nên cần bảng riêng. Bảng ai_predictions cũ (mô hình hồi quy) giữ nguyên.
--  GET /prediction và /prediction/history đọc bảng này.
--
--  Chạy trên máy chủ (CSDL đã có dữ liệu thì init/ không tự chạy lại):
--    dc exec -T timescaledb psql -U iot_admin -d iot_kitchen < database/migrations/2026-09-28_add_ai_forecasts.sql
-- =====================================================================
CREATE TABLE IF NOT EXISTS ai_forecasts (
    time             TIMESTAMPTZ      NOT NULL,          -- lúc dự báo
    target_time      TIMESTAMPTZ      NOT NULL,          -- thời điểm được dự báo (time + 15 phút)
    device_id        TEXT             NOT NULL,
    horizon_minutes  SMALLINT         NOT NULL,
    hazard_level     SMALLINT,                           -- 0 SAFE, 1 WARNING, 2 DANGER
    status           TEXT             NOT NULL,
    confidence       DOUBLE PRECISION,                   -- xác suất của lớp được chọn
    prob_safe        DOUBLE PRECISION,
    prob_warning     DOUBLE PRECISION,
    prob_danger      DOUBLE PRECISION,
    current_gas_ppm  DOUBLE PRECISION,                   -- số đo lúc dự báo
    temperature      DOUBLE PRECISION,
    humidity         DOUBLE PRECISION,
    model_version    TEXT,
    inference_ms     DOUBLE PRECISION
);
SELECT create_hypertable('ai_forecasts', by_range('time', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_forecast_device_time ON ai_forecasts (device_id, time DESC);
SELECT add_retention_policy('ai_forecasts', INTERVAL '90 days', if_not_exists => TRUE);
