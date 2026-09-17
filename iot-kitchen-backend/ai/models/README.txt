Đặt file pollution_model.pkl của TV5 vào thư mục này.

Tự train lại từ DỮ LIỆU THẬT của hệ thống (khuyến nghị, xem docs/ai-integration.md):
    1. Chạy hệ thống ít nhất 3 tiếng để có đủ dữ liệu
    2. Xuất dữ liệu:
         Get-Content tools\export_training_data.sql | `
           docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen -t -A -F"," `
           > ai\data\real_kitchen_data.csv
    3. Sửa 1 dòng trong tv5/train_ai.py:
         data_path = 'data/real_kitchen_data.csv'
    4. Train ngay trong container:
         docker compose run --rm --entrypoint python ai tv5/train_ai.py
    5. Nạp lại mô hình, không cần khởi động lại container:
         curl.exe -X POST http://localhost:9100/reload

Train trong container còn tránh được lỗi lệch phiên bản scikit-learn giữa hai máy.
