-- =====================================================================
--  CÂU TRUY VẤN KIỂM TRA CSDL + LẤY SỐ LIỆU CHO BÁO CÁO
--  Chạy bằng:
--     docker compose exec timescaledb psql -U iot_admin -d iot_kitchen -f /dev/stdin < tools/queries.sql
--  hoặc dán từng câu vào DBeaver / pgAdmin (kết nối localhost:5432).
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. HẠ TẦNG: extension và hypertable đã tạo đúng chưa?
-- ---------------------------------------------------------------------
\dx
SELECT hypertable_name, num_chunks, compression_enabled
FROM timescaledb_information.hypertables;

-- Chính sách xoá dữ liệu cũ (90 ngày) do add_retention_policy tạo ra
SELECT job_id, application_name, schedule_interval, config
FROM timescaledb_information.jobs
WHERE proc_name = 'policy_retention';

-- Dữ liệu được chia thành các "chunk" theo ngày - đây là điểm khác biệt
-- so với bảng PostgreSQL thường, nên đưa vào báo cáo Chương 4.
SELECT chunk_name, range_start, range_end
FROM timescaledb_information.chunks
WHERE hypertable_name = 'telemetry'
ORDER BY range_start DESC
LIMIT 5;

-- ---------------------------------------------------------------------
-- 2. DỮ LIỆU ĐANG VỀ CÓ ĐÚNG KHÔNG?
-- ---------------------------------------------------------------------
SELECT count(*) AS so_ban_ghi,
       min(time) AS som_nhat,
       max(time) AS moi_nhat,
       round(avg(pollution_percent)::numeric, 1) AS o_nhiem_tb,
       max(pollution_percent) AS o_nhiem_dinh
FROM telemetry;

-- 10 bản ghi mới nhất
SELECT time, temperature, humidity, pollution_percent, rs_ro_ratio, fan_state, mode
FROM telemetry
ORDER BY time DESC
LIMIT 10;

-- ---------------------------------------------------------------------
-- 3. BÀI TEST 1 - ĐỘ TRỄ TỪ LÚC ĐO ĐẾN LÚC LƯU (ms)
--    received_at do Backend ghi, time là timestamp ESP32 gắn vào payload.
--    Lưu ý: chỉ chính xác khi ESP32 đã đồng bộ NTP.
-- ---------------------------------------------------------------------
SELECT count(*) AS mau,
       round(avg (extract(epoch FROM received_at - time) * 1000)::numeric, 1) AS trung_binh_ms,
       round(min (extract(epoch FROM received_at - time) * 1000)::numeric, 1) AS nho_nhat_ms,
       round(max (extract(epoch FROM received_at - time) * 1000)::numeric, 1) AS lon_nhat_ms,
       round(percentile_cont(0.95) WITHIN GROUP (
             ORDER BY extract(epoch FROM received_at - time) * 1000)::numeric, 1) AS p95_ms
FROM telemetry
WHERE time > now() - interval '1 hour';

-- ---------------------------------------------------------------------
-- 4a. BÀI TEST 4 - TỶ LỆ MẤT GÓI TÍNH CHÍNH XÁC THEO seq
--     Firmware đếm seq tăng 1 mỗi bản tin. Thiếu bao nhiêu số là mất bấy nhiêu gói,
--     không phải suy đoán. Kết quả này trùng với endpoint GET /diagnostics.
-- ---------------------------------------------------------------------
WITH lien_tiep AS (
    SELECT seq, lag(seq) OVER (ORDER BY time) AS seq_truoc
    FROM telemetry
    WHERE time > now() - interval '1 hour' AND seq IS NOT NULL
)
SELECT count(*)                                                             AS so_ban_ghi,
       coalesce(sum(seq - seq_truoc - 1) FILTER (WHERE seq > seq_truoc + 1), 0) AS so_goi_mat,
       count(*) FILTER (WHERE seq > seq_truoc + 1)                          AS so_lan_dut,
       count(*) FILTER (WHERE seq < seq_truoc)                              AS so_lan_khoi_dong_lai,
       round(100.0 * coalesce(sum(seq - seq_truoc - 1) FILTER (WHERE seq > seq_truoc + 1), 0)
             / nullif(count(*) + coalesce(sum(seq - seq_truoc - 1)
                      FILTER (WHERE seq > seq_truoc + 1), 0), 0), 2)        AS ty_le_mat_phan_tram
FROM lien_tiep
WHERE seq_truoc IS NOT NULL;

-- ---------------------------------------------------------------------
-- 4b. CÁCH ƯỚC LƯỢNG CŨ - dùng khi firmware chưa gửi seq
--     Đếm số khoảng cách > 5 giây giữa 2 bản ghi liên tiếp (chu kỳ chuẩn là 2 giây).
--     Đưa cả 4a và 4b vào báo cáo để so sánh hai phương pháp đo.
-- ---------------------------------------------------------------------
WITH khoang_cach AS (
    SELECT time,
           extract(epoch FROM time - lag(time) OVER (ORDER BY time)) AS giay
    FROM telemetry
    WHERE time > now() - interval '1 hour'
)
SELECT count(*)                                        AS so_khoang,
       count(*) FILTER (WHERE giay > 5)                AS so_lan_dut,
       round(100.0 * count(*) FILTER (WHERE giay > 5) / nullif(count(*), 0), 2) AS ty_le_dut_phan_tram,
       round(max(giay)::numeric, 1)                    AS dut_lau_nhat_giay
FROM khoang_cach
WHERE giay IS NOT NULL;

-- ---------------------------------------------------------------------
-- 5. DỮ LIỆU CHO BIỂU ĐỒ - time_bucket() gộp trung bình mỗi phút
--    Đây chính là truy vấn mà endpoint /history?bucket_seconds=60 dùng.
-- ---------------------------------------------------------------------
SELECT time_bucket('1 minute', time)                    AS phut,
       round(avg(pollution_percent)::numeric, 1)        AS o_nhiem,
       round(avg(temperature)::numeric, 1)              AS nhiet_do,
       last(fan_state, time)                            AS quat_cuoi_phut
FROM telemetry
WHERE time > now() - interval '30 minutes'
GROUP BY phut
ORDER BY phut DESC;

-- ---------------------------------------------------------------------
-- 6. NHẬT KÝ SỰ KIỆN
-- ---------------------------------------------------------------------
SELECT event_type, severity, count(*) AS so_lan, max(time) AS gan_nhat
FROM system_events
GROUP BY event_type, severity
ORDER BY so_lan DESC;

-- Các lần đã bắn cảnh báo Telegram
SELECT time, severity, message, details
FROM system_events
WHERE event_type IN ('ALERT_SENT', 'ALERT_SUPPRESSED', 'ALERT_FAILED', 'ALERT_SKIPPED')
ORDER BY time DESC
LIMIT 20;

-- Số lần quạt đổi trạng thái mỗi giờ - dùng để chứng minh FSM chống chattering
SELECT date_trunc('hour', time) AS gio,
       count(*) FILTER (WHERE event_type = 'FAN_STATE_CHANGED') AS so_lan_doi_trang_thai,
       count(*) FILTER (WHERE event_type = 'SAFETY_OVERRIDE')   AS so_lan_cuong_buc
FROM system_events
GROUP BY gio
ORDER BY gio DESC
LIMIT 12;

-- ---------------------------------------------------------------------
-- 7. CẤU HÌNH NGƯỠNG ĐANG ÁP DỤNG
-- ---------------------------------------------------------------------
SELECT * FROM device_config;
