import os
import json
import time
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
import paho.mqtt.client as mqtt

# ==============================================================================
# KHỐI 1: NẠP MÔ HÌNH AI ĐÃ HUẤN LƯỢNG
# ==============================================================================
# 1.1. Đường dẫn tới file model đã lưu ở bước train_ai.py
MODEL_PATH = 'models/pollution_model.pkl'

# 1.2. Kiểm tra file mô hình. Nếu chưa train thì báo lỗi và dừng chương trình
if not os.path.exists(MODEL_PATH):
    print("X Chưa tìm thấy model. Hãy chạy train_ai.py trước!")
    exit()

# 1.3. Nạp mô hình Random Forest từ đĩa cứng vào bộ nhớ RAM để sẵn sàng suy luận
model = joblib.load(MODEL_PATH)
print("-> Đã nạp thành công mô hình AI!")


# ==============================================================================
# KHỐI 2: HÀM API CHÍNH DÙNG ĐỂ SUY LUẬN VÀ DỰ BÁO (PREDICT_FUTURE)
# ==============================================================================
def predict_future(window_20_samples):
    """
    Ý NGHĨA & CHỨC NĂNG:
    Hàm này đóng vai trò là API Interface để Backend (TV3) hoặc Service khác gọi vào.
    - Đầu vào: Danh sách (list) gồm 20 mẫu dữ liệu gần nhất (200 giây quá khứ).
    - Đầu ra: Dictionary chứa kết quả dự báo 15 phút sau đúng Chuẩn Data Contract.
    """
    # 2.1. Chuyển đổi dữ liệu đầu vào thành DataFrame của Pandas
    df = pd.DataFrame(window_20_samples)
    
    # 2.2. Chọn đúng 4 cột đặc trưng theo cam kết kỹ thuật
    feature_cols = ['pollution_percent', 'dP_dt', 'temperature', 'humidity']
    
    # 2.3. Duỗi phẳng ma trận 20x4 thành mảng 1D (80 phần tử) và reshape thành (1, 80)
    # để khớp với định dạng đầu vào mà mô hình Random Forest yêu cầu.
    input_features = df[feature_cols].values.flatten().reshape(1, -1)
    
    # 2.4. Thực hiện suy luận (Inference): AI tính toán ra giá trị ô nhiễm dự báo
    predicted_val = float(model.predict(input_features)[0])
    
    # 2.5. Giới hạn (Clip) giá trị dự báo trong khoảng hợp lệ từ 0.0% đến 100.0%
    predicted_val = max(0.0, min(100.0, round(predicted_val, 1)))
    
    # 2.6. Lấy giá trị ô nhiễm ở mẫu hiện tại (mẫu thứ 20 - mẫu mới nhất trong cửa sổ)
    current_val = float(df.iloc[-1]['pollution_percent'])
    
    # 2.7. Đánh giá xu hướng (Trend) diễn biến của không khí sau 15 phút:
    # - TĂNG (RISING): Nếu tương lai cao hơn hiện tại > 3%
    # - GIẢM (FALLING): Nếu tương lai thấp hơn hiện tại > 3%
    # - ÔN ĐỊNH (STABLE): Nếu dao động trong khoảng ±3%
    if predicted_val > current_val + 3.0:
        trend = "RISING"
    elif predicted_val < current_val - 3.0:
        trend = "FALLING"
    else:
        trend = "STABLE"

    # 2.8. Đóng gói kết quả đầu ra thành JSON Payload chuẩn Data Contract (Mục 2.2 Team Agreement)
    # để sẵn sàng đẩy sang Backend qua MQTT Topic
    payload = {
        "timestamp": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'), # Thời gian chuẩn ISO 8601
        "prediction_window_minutes": 15,                           # Tầm nhìn dự báo (15 phút)
        "predicted_pollution_percent": predicted_val,              # Nồng độ khói dự báo (%)
        "trend": trend                                              # Xu hướng (RISING/FALLING/STABLE)
    }
    return payload


# ==============================================================================
# KHỐI 3: GIẢ LẬP LUỒNG DỮ LIỆU THỜI GIAN THỰC VÀ CHẠY SERVICE (MAIN LOOP)
# ==============================================================================
if __name__ == "__main__":
    # 3.1. Nạp file dữ liệu giả lập để test vòng lặp dịch chuyển cửa sổ trượt
    df_mock = pd.read_csv('data/mock_kitchen_data.csv')
    
    print("-> AI Service đang chạy (Tần suất 10s/lần)... Press Ctrl+C to stop.")
    
    # 3.2. Vòng lặp mô phỏng cảm biến liên tục gửi dữ liệu (mỗi 10 giây trượt thêm 1 mẫu)
    for i in range(len(df_mock) - 20):
        # Trích xuất đúng 20 mẫu dữ liệu liên tiếp (Cửa sổ trượt 20)
        window_data = df_mock.iloc[i : i + 20].to_dict(orient='records')
        
        # Gọi hàm API để lấy kết quả dự báo
        result = predict_future(window_data)
        
        # In kết quả suy luận ra console để kiểm tra
        print(f"[{result['timestamp']}] Predict 15m later: {result['predicted_pollution_percent']}% | Trend: {result['trend']}")
        
        # Nghỉ 10 giây đúng với chu kỳ phát tín hiệu của hệ thống IoT
        time.sleep(10)