import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Tạo 1000 mẫu dữ liệu chuỗi thời gian (~2.7 giờ, 10 giây/mẫu)
np.random.seed(42)
n_samples = 1000

start_time = datetime.now()
timestamps = [(start_time + timedelta(seconds=10*i)).strftime('%Y-%m-%dT%H:%M:%SZ') for i in range(n_samples)]

t = np.linspace(0, 4 * np.pi, n_samples)
pollution = 50 + 30 * np.sin(t) + np.random.normal(0, 2, n_samples)
pollution = np.clip(pollution, 0, 100)

temp = 28 + 5 * (pollution / 100) + np.random.normal(0, 0.5, n_samples)
humidity = 60 + 10 * (pollution / 100) + np.random.normal(0, 1, n_samples)

df = pd.DataFrame({
    'timestamp': timestamps,
    'temperature': np.round(temp, 1),
    'humidity': np.round(humidity, 1),
    'pollution_percent': np.round(pollution, 1)
})

# Tính dP/dt (tốc độ biến thiên nồng độ khói)
df['dP_dt'] = np.round(df['pollution_percent'].diff().fillna(0), 2)

# Lưu vào thư mục data
df.to_csv('data/mock_kitchen_data.csv', index=False)
print("-> Da tao thanh cong file data/mock_kitchen_data.csv!")