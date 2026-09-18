-- =====================================================================
--  MIGRATION 2026-09-12: thêm cột seq vào bảng telemetry
--
--  Chỉ cần chạy khi CSDL của bạn ĐÃ có dữ liệu và bạn không muốn xoá đi
--  (vì file database/init/10_schema.sql chỉ chạy lúc volume còn trống).
--
--  Cách chạy:
--      Get-Content database\migrations\2026-09-12_add_seq.sql | `
--        docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen
--
--  Nếu chưa có dữ liệu quan trọng thì đơn giản hơn:
--      docker compose down -v ; docker compose up -d
-- =====================================================================

ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS seq BIGINT;

COMMENT ON COLUMN telemetry.seq IS
    'Số thứ tự bản tin do ESP32 đếm tăng dần, reset về 0 khi thiết bị khởi động lại. '
    'Dùng để tính tỉ lệ mất gói chính xác thay vì suy đoán theo khoảng trống thời gian.';

-- Kiểm tra lại
\d telemetry
