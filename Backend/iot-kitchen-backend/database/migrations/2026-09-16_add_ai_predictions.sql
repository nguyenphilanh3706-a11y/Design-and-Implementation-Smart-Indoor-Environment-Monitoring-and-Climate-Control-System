-- =====================================================================
--  MIGRATION 2026-09-16: thêm bảng ai_predictions (tích hợp mô hình AI của TV5)
--
--  Chạy khi CSDL đã có dữ liệu và bạn không muốn xoá đi làm lại:
--      Get-Content database\migrations\2026-09-16_add_ai_predictions.sql | `
--        docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen
--
--  Chưa có dữ liệu quan trọng thì nhanh hơn: docker compose down -v ; docker compose up -d
-- =====================================================================

CREATE TABLE IF NOT EXISTS ai_predictions (
    time                        TIMESTAMPTZ      NOT NULL,
    device_id                   TEXT             NOT NULL,
    target_time                 TIMESTAMPTZ      NOT NULL,
    prediction_window_minutes   INTEGER          NOT NULL,
    predicted_pollution_percent DOUBLE PRECISION NOT NULL CHECK (predicted_pollution_percent BETWEEN 0 AND 100),
    current_pollution_percent   DOUBLE PRECISION,
    trend                       TEXT             NOT NULL CHECK (trend IN ('RISING', 'FALLING', 'STABLE')),
    model_version               TEXT,
    inference_ms                DOUBLE PRECISION
);

SELECT create_hypertable('ai_predictions', by_range('time', INTERVAL '7 days'), if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_pred_device_time ON ai_predictions (device_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_pred_target ON ai_predictions (device_id, target_time);
SELECT add_retention_policy('ai_predictions', INTERVAL '90 days', if_not_exists => TRUE);

\d ai_predictions
