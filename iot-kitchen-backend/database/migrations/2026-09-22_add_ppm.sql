-- =====================================================================
--  MIGRATION 2026-09-22: đổi đơn vị ngưỡng quạt sang ppm, thêm tốc độ quạt
--
--  Chạy khi CSDL đã có dữ liệu và không muốn xoá:
--      dc exec -T timescaledb psql -U iot_admin -d iot_kitchen < database/migrations/2026-09-22_add_ppm.sql
-- =====================================================================
ALTER TABLE telemetry     ADD COLUMN IF NOT EXISTS gas_ppm DOUBLE PRECISION CHECK (gas_ppm >= 0);
ALTER TABLE telemetry     ADD COLUMN IF NOT EXISTS fan_speed_percent SMALLINT
                          CHECK (fan_speed_percent BETWEEN 0 AND 100);
ALTER TABLE device_config ADD COLUMN IF NOT EXISTS ppm_mid_threshold   DOUBLE PRECISION NOT NULL DEFAULT 800;
ALTER TABLE device_config ADD COLUMN IF NOT EXISTS ppm_bad_threshold   DOUBLE PRECISION NOT NULL DEFAULT 1000;
ALTER TABLE device_config ADD COLUMN IF NOT EXISTS alert_ppm_threshold DOUBLE PRECISION NOT NULL DEFAULT 1000;
\d device_config
