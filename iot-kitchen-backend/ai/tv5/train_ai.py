import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import matplotlib.pyplot as plt
from google.colab import files

print("🚀 Đang tải dữ liệu...")
df = pd.read_csv('kitchen_lstm_dataset_13000.csv')

# 1. Lấy 5 đặc trưng đã chuẩn hóa (Scaled 0 - 1)
feature_cols = ['temperature_scaled', 'humidity_scaled', 'gas_ppm_scaled', 'gas_diff_scaled', 'temp_diff_scaled']
X_data = df[feature_cols].values
y_data = df['target_risk_15m'].values # Nhãn rủi ro sau 15 phút (0, 1, 2)

# 2. Tạo cửa sổ trượt (Sliding Window - 30 bước thời gian = 60s)
def create_sequences(X, y, time_steps=30):
    Xs, ys = [], []
    for i in range(len(X) - time_steps + 1):
        Xs.append(X[i:(i + time_steps)])
        ys.append(y[i + time_steps - 1])
    return np.array(Xs), np.array(ys)

TIME_STEPS = 30
X_seq, y_seq = create_sequences(X_data, y_data, time_steps=TIME_STEPS)

print(f"📊 Kích thước Tensor đầu vào LSTM: {X_seq.shape}") # (12868, 30, 5)

# 3. Xây dựng mô hình LSTM
model = Sequential([
    LSTM(64, return_sequences=True, input_shape=(TIME_STEPS, len(feature_cols))),
    Dropout(0.2),
    LSTM(32, return_sequences=False),
    Dropout(0.2),
    Dense(16, activation='relu'),
    Dense(3, activation='softmax') # Đầu ra 3 lớp phân loại rủi ro
])

model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

# 4. Train Model
print("\n🔥 Đang huấn luyện LSTM...")
history = model.fit(
    X_seq, y_seq, 
    epochs=20, 
    batch_size=64, 
    validation_split=0.2,
    verbose=1
)

# 5. Lưu và Tải file Model (.h5) tự động về máy tính
model_filename = 'kitchen_lstm_15m.h5'
model.save(model_filename)
print(f"\n✅ Đã lưu model: {model_filename}")

# Tự động bật cửa sổ download file về máy
files.download(model_filename)