import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split

# ==============================================================================
# KHỐI 1: NẠP DỮ LIỆU VÀ CẤU HÌNH THAM SỐ DỰ BÁO
# ==============================================================================
# 1.1. Đường dẫn đến file chứa dữ liệu chuỗi thời gian môi trường
data_path = 'data/mock_kitchen_data.csv'

# 1.2. Nạp file CSV vào DataFrame của Pandas để thao tác dạng bảng
df = pd.read_csv(data_path)

# 1.3. Cấu hình Cửa sổ trượt (Sliding Window): 
# Lấy 20 mẫu quá khứ liên tiếp (tương đương: 20 mẫu x 10 giây/mẫu = 200 giây ~ 3.3 phút)
WINDOW_SIZE = 20   

# 1.4. Cấu hình Khoảng thời gian dự báo tương lai (Prediction Window):
# Dự báo nồng độ ô nhiễm sau 15 phút (tương đương: 15 phút x 60 giây / 10 giây = 90 mẫu)
PREDICT_STEPS = 90 

# 1.5. Khai báo 4 đặc trưng (Features) đầu vào theo chuẩn Data Contract của nhóm
feature_cols = ['pollution_percent', 'dP_dt', 'temperature', 'humidity']

# 1.6. Khởi tạo danh sách chứa dữ liệu đầu vào (X) và đáp án target (y)
X_list, y_list = [], []


# ==============================================================================
# KHỐI 2: XÂY DỰNG TẬP DỮ LIỆU DẠNG CỬA SỔ TRƯỢT (SLIDING WINDOW LOGIC)
# ==============================================================================
# Duyệt qua từng khoảng thời gian trong dataset. 
# Điểm dừng là len(df) - WINDOW_SIZE - PREDICT_STEPS để không bị lỗi vượt quá chỉ số mảng (IndexError).
for i in range(len(df) - WINDOW_SIZE - PREDICT_STEPS):
    
    # Cắt 20 dòng dữ liệu liên tiếp, lấy 4 cột đặc trưng (Ma trận 20x4).
    # .values.flatten() duỗi phẳng ma trận (20x4) thành mảng 1D gồm 80 phần tử liên tiếp làm Input cho AI.
    window = df.iloc[i : i + WINDOW_SIZE][feature_cols].values.flatten()
    
    # Lấy đáp án (Target): Giá trị nồng độ ô nhiễm (%) tại thời điểm 15 phút sau điểm cuối của cửa sổ
    target = df.iloc[i + WINDOW_SIZE + PREDICT_STEPS - 1]['pollution_percent']
    
    # Đưa cặp (Input -> Target) vào danh sách huấn luyện
    X_list.append(window)
    y_list.append(target)

# Chuyển đổi sang mảng NumPy (NumPy Array) để tối ưu hóa hiệu năng tính toán toán học
X = np.array(X_list)
y = np.array(y_list)


# ==============================================================================
# KHỐI 3: PHÂN CHIA TẬP DỮ LIỆU HUẤN LƯỢNG (TRAIN) VÀ KIỂM THỬ (TEST)
# ==============================================================================
# Chia dữ liệu: 80% dùng để huấn luyện AI, 20% giữ lại làm bài test độc lập.
# QUY N TẮC QUAN TRỌNG: shuffle=False bắt buộc dùng cho chuỗi thời gian (Time-Series) 
# để giữ nguyên thứ tự thời gian thực tế, tránh rò rỉ dữ liệu tương lai vào quá khứ.
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, shuffle=False
)


# ==============================================================================
# KHỐI 4: KHỞI TẠO VÀ HUẤN LƯỢNG MÔ HÌNH AI (RANDOM FOREST REGRESSOR)
# ==============================================================================
print("-> Đang train AI với Cửa sổ trượt 20 mẫu...")

# Khởi tạo mô hình Random Forest Regressor với 100 cây quyết định (n_estimators=100).
# random_state=42 đảm bảo kết quả huấn luyện ổn định và tái lặp lại được.
model = RandomForestRegressor(n_estimators=100, random_state=42)

# Cho AI học mối quan hệ toán học giữa 80 tham số đầu vào và giá trị ô nhiễm 15 phút sau
model.fit(X_train, y_train)


# ==============================================================================
# KHỐI 5: ĐÁNH GIÁ ĐỘ CHÍNH XÁC CỦA AI (TÍNH CHỈ SỐ SAI SỐ MAE & RMSE)
# ==============================================================================
# Cho mô hình chạy dự đoán thử trên tập dữ liệu kiểm thử (X_test)
y_pred = model.predict(X_test)

# 5.1. MAE (Mean Absolute Error): Sai số tuyệt đối trung bình.
# Cho biết mô hình dự báo lệch trung bình bao nhiêu % so với thực tế.
mae = mean_absolute_error(y_test, y_pred)

# 5.2. RMSE (Root Mean Squared Error): Sai số căn bậc hai trung bình.
# Đánh giá mức độ ảnh hưởng của các điểm dự báo sai lệch lớn (phạt nặng sai số lớn).
rmse = np.sqrt(mean_squared_error(y_test, y_pred))

# In kết quả đánh giá số liệu ra màn hình (Chỉ số này dùng để đưa vào Báo cáo kỹ thuật)
print("\n=== KẾT QUẢ ĐÁNH GIÁ MÔ HÌNH AI (SLIDING WINDOW 20) ===")
print(f"MAE (Sai số tuyệt đối trung bình) : {mae:.2f}%")
print(f"RMSE (Sai số căn bậc hai trung bình): {rmse:.2f}%")
print("=====================================================\n")


# ==============================================================================
# KHỐI 6: ĐÓNG GÓI VÀ LƯU TRỮ MÔ HÌNH RA FILE ĐĨA CỨNG (.PKL)
# ==============================================================================
# Tạo thư mục 'models' nếu chưa có
os.makedirs('models', exist_ok=True)

# Đóng gói và lưu mô hình đã train thành file 'pollution_model.pkl'.
# File này sẽ được nạp lại trong ai_service.py để phục vụ dự đoán thời gian thực.
joblib.dump(model, 'models/pollution_model.pkl')

print("-> Đã hoàn thành 100% PHẦN 3!")