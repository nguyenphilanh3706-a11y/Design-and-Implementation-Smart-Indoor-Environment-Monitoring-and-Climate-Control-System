-- =====================================================================
--  XUẤT DỮ LIỆU THẬT ĐỂ TV5 HUẤN LUYỆN LẠI MÔ HÌNH
--
--  Sinh ra file CSV có ĐÚNG 5 cột mà train_ai.py đang đọc, nên TV5 chỉ cần sửa
--  1 dòng data_path rồi chạy lại train_ai.py:
--      data_path = 'data/real_kitchen_data.csv'
--
--  Chạy trên PowerShell (đứng ở thư mục dự án):
--    docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen `
--      -c "\copy (<dán truy vấn bên dưới vào đây, 1 dòng>) TO STDOUT WITH CSV HEADER" `
--      > ai\data\real_kitchen_data.csv
--
--  Hoặc gọn hơn, chạy thẳng file này rồi chuyển hướng đầu ra:
--    Get-Content tools\export_training_data.sql | `
--      docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen -t -A -F"," `
--      > ai\data\real_kitchen_data.csv
--
--  Gộp về 10 giây/mẫu cho khớp chu kỳ mà mô hình được train.
-- =====================================================================
WITH mau AS (
    SELECT time_bucket('10 seconds', time)     AS t,
           round(avg(temperature)::numeric, 1)       AS temperature,
           round(avg(humidity)::numeric, 1)          AS humidity,
           round(avg(pollution_percent)::numeric, 1) AS pollution_percent
    FROM telemetry
    WHERE device_id = 'esp32_kitchen_01'
      AND time > now() - interval '7 days'
      AND pollution_percent IS NOT NULL
      AND temperature IS NOT NULL
      AND humidity IS NOT NULL
    GROUP BY t
)
SELECT to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS timestamp,
       temperature,
       humidity,
       pollution_percent,
       coalesce(pollution_percent - lag(pollution_percent) OVER (ORDER BY t), 0) AS "dP_dt"
FROM mau
ORDER BY t;
