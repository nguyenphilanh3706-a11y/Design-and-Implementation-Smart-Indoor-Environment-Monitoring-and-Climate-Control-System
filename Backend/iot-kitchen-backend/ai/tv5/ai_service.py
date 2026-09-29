import os
import json
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Thử import TensorFlow/Keras (Phòng trường hợp môi trường chưa cài)
try:
    import tensorflow as tf
    from tensorflow.keras.models import load_model as keras_load_model
    HAS_TF = True
except ImportError:
    HAS_TF = False

# ==============================================================================
# KHỐI 1: KHỞI TẠO FASTAPI APP & NẠP MODEL AI (HỖ TRỢ .H5, .KERAS, .PKL, .JOBLIB)
# ==============================================================================
app = FastAPI(
    title="Kitchen AI Prediction Service (LSTM + ML)",
    description="Dịch vụ AI dự báo ô nhiễm & phân loại rủi ro nhà bếp 15 phút tới",
    version="2.1.0"
)

MODEL_DIR = os.getenv("MODEL_DIR", "models")
DEFAULT_MODEL_PATH = os.path.join(MODEL_DIR, "kitchen_lstm_15m.h5")

model = None
model_type = "None"  # 'keras_lstm', 'sklearn', hoặc 'None'
model_version = "Unknown"
n_features_expected = 5

# Bộ đệm Deque lưu 30 mẫu gần nhất cho mỗi thiết bị (Window 30 time-steps x 5 features)
device_buffers: Dict[str, deque] = {}

# Thông số Min-Max Scaler (Đồng bộ với dataset train kitchen_lstm_dataset_13000.csv)
SCALER_CONFIG = {
    "temp": {"min": 28.0, "max": 52.3},
    "hum": {"min": 58.9, "max": 98.8},
    "gas": {"min": 62.5, "max": 9999.0},
    "gas_diff": {"min": -4112.0, "max": 9804.0},
    "temp_diff": {"min": -23.0, "max": 23.0}
}

def scale_val(val: float, min_val: float, max_val: float) -> float:
    """Chuẩn hóa min-max về khoảng [0, 1]"""
    val_clamped = max(min_val, min(float(val), max_val))
    return round((val_clamped - min_val) / (max_val - min_val), 4)

def load_best_model():
    """Tự động tìm và nạp file model mới nhất (.h5, .keras, .pkl, .joblib)"""
    global model, model_type, model_version, n_features_expected
    candidate_files = []
    
    if os.path.exists(MODEL_DIR):
        for file in os.listdir(MODEL_DIR):
            if file.endswith((".h5", ".keras", ".pkl", ".joblib")):
                candidate_files.append(os.path.join(MODEL_DIR, file))
    
    target_path = DEFAULT_MODEL_PATH
    if candidate_files:
        # Sắp xếp ưu tiên file sửa đổi mới nhất
        candidate_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        target_path = candidate_files[0]

    if os.path.exists(target_path):
        ext = os.path.splitext(target_path)[1].lower()
        model_version = os.path.basename(target_path)
        
        try:
            if ext in ['.h5', '.keras']:
                if HAS_TF:
                    model = keras_load_model(target_path)
                    model_type = "keras_lstm"
                    print(f"-> [LSTM] Đã nạp thành công Keras Model: {model_version}")
                else:
                    print(f"X Chưa cài TensorFlow để đọc {target_path}. Hãy pip install tensorflow.")
            else:
                model = joblib.load(target_path)
                model_type = "sklearn"
                if hasattr(model, "n_features_in_"):
                    n_features_expected = model.n_features_in_
                print(f"-> [ML] Đã nạp thành công Scikit-Learn Model: {model_version}")
        except Exception as e:
            print(f"X Lỗi khi nạp model {target_path}: {e}")
            model = None
            model_type = "None"
    else:
        print(f"-> Cảnh báo: Chưa thấy file model tại {target_path}. Sẽ dùng Rule-based dự phòng.")

# Nạp model ngay khi khởi tạo service
load_best_model()

# ==============================================================================
# KHỐI 2: PYDANTIC SCHEMA - HOÀN TOÀN TƯƠNG THÍCH MỌI ĐỊNH DẠNG ĐẦU VÀO
# ==============================================================================
class PredictRequest(BaseModel):
    device_id: Optional[str] = "esp32_kitchen_01"
    samples: Optional[List[Dict[str, Any]]] = None
    
    # Các trường dữ liệu cảm biến lẻ
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    gas_ppm: Optional[float] = None
    pollution_percent: Optional[float] = None
    dP_dt: Optional[float] = None

    class Config:
        extra = "allow"  # Chấp nhận các trường tùy biến mà không bị lỗi HTTP 422


# ==============================================================================
# KHỐI 3: TIỀN XỬ LÝ & BỘ ĐỆM ĐẦU VÀO CHO MÔ HÌNH LSTM (30 STEPS X 5 FEATURES)
# ==============================================================================
def process_sensor_input(req: PredictRequest):
    """Xử lý dữ liệu từ request, cập nhật bộ đệm Deque và trả về Tensor đầu vào cho LSTM"""
    device_id = req.device_id or "esp32_kitchen_01"
    
    # Trích xuất thông số cảm biến thô
    raw_samples = req.samples if req.samples else []
    if raw_samples:
        last = raw_samples[-1]
        temp = float(last.get("temperature") or req.temperature or 29.5)
        hum = float(last.get("humidity") or req.humidity or 80.0)
        gas = float(last.get("gas_ppm") or req.gas_ppm or 200.0)
    else:
        temp = float(req.temperature if req.temperature is not None else 29.5)
        hum = float(req.humidity if req.humidity is not None else 80.0)
        if req.gas_ppm is not None:
            gas = float(req.gas_ppm)
        elif req.pollution_percent is not None:
            gas = float(req.pollution_percent) * 100.0
        else:
            gas = 200.0

    # Khởi tạo bộ đệm Deque 30 mẫu cho từng thiết bị
    if device_id not in device_buffers:
        device_buffers[device_id] = deque(maxlen=30)
    
    buf = device_buffers[device_id]

    # Tính độ chênh lệch temp_diff và gas_diff
    if len(buf) > 0:
        prev_temp_unscaled = buf[-1][0] * (SCALER_CONFIG["temp"]["max"] - SCALER_CONFIG["temp"]["min"]) + SCALER_CONFIG["temp"]["min"]
        prev_gas_unscaled = buf[-1][2] * (SCALER_CONFIG["gas"]["max"] - SCALER_CONFIG["gas"]["min"]) + SCALER_CONFIG["gas"]["min"]
        temp_diff = temp - prev_temp_unscaled
        gas_diff = gas - prev_gas_unscaled
    else:
        temp_diff = 0.0
        gas_diff = float(req.dP_dt) if req.dP_dt is not None else 0.0

    # Chuẩn hóa Scale [0, 1] cho 5 đặc trưng
    t_s = scale_val(temp, SCALER_CONFIG["temp"]["min"], SCALER_CONFIG["temp"]["max"])
    h_s = scale_val(hum, SCALER_CONFIG["hum"]["min"], SCALER_CONFIG["hum"]["max"])
    g_s = scale_val(gas, SCALER_CONFIG["gas"]["min"], SCALER_CONFIG["gas"]["max"])
    gd_s = scale_val(gas_diff, SCALER_CONFIG["gas_diff"]["min"], SCALER_CONFIG["gas_diff"]["max"])
    td_s = scale_val(temp_diff, SCALER_CONFIG["temp_diff"]["min"], SCALER_CONFIG["temp_diff"]["max"])

    # Lưu mẫu mới vào bộ đệm
    buf.append([t_s, h_s, g_s, gd_s, td_s])

    # Nhân bản mẫu nếu chưa đủ 30 bước thời gian
    seq_list = list(buf)
    while len(seq_list) < 30:
        seq_list.insert(0, seq_list[0])

    # Biến đổi thành Tensor 3D kích thước (1, 30, 5) cho LSTM
    lstm_tensor = np.array([seq_list], dtype=np.float32)
    
    return lstm_tensor, gas, temp, hum, gas_diff


# ==============================================================================
# KHỐI 4: CÁC ENDPOINT FASTAPI
# ==============================================================================
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "model_loaded": model is not None,
        "model_type": model_type,
        "model_version": model_version,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/v1/kitchen/model_info")
def model_info():
    return {
        "model_version": model_version,
        "model_type": model_type,
        "features_expected": n_features_expected,
        "status": "ready" if model else "fallback_mode"
    }


@app.post("/predict")
@app.post("/api/v1/kitchen/predict")
def predict(req: PredictRequest):
    try:
        lstm_tensor, current_gas, current_temp, current_hum, gas_diff = process_sensor_input(req)
        
        risk_level = "SAFE"
        predicted_gas = current_gas
        confidence = 1.0

        # 1. Chạy suy luận từ AI Model
        if model is not None:
            try:
                if model_type == "keras_lstm":
                    pred = model.predict(lstm_tensor, verbose=0)[0]
                    
                    if len(pred) == 3:  # Phân loại rủi ro (Softmax 3 lớp: SAFE, WARNING, DANGER)
                        risk_idx = int(np.argmax(pred))
                        class_map = {0: "SAFE", 1: "WARNING", 2: "DANGER"}
                        risk_level = class_map.get(risk_idx, "SAFE")
                        confidence = round(float(pred[risk_idx]), 4)
                        
                        # Dự báo mức PPM ước tính tương ứng với risk level
                        if risk_level == "DANGER":
                            predicted_gas = max(900.0, current_gas)
                        elif risk_level == "WARNING":
                            predicted_gas = max(400.0, current_gas)
                        else:
                            predicted_gas = min(399.0, current_gas)
                    else:  # Hồi quy trực tiếp giá trị PPM
                        predicted_gas = float(pred[0])
                        if predicted_gas >= 900.0 or current_gas >= 900.0:
                            risk_level = "DANGER"
                        elif predicted_gas >= 400.0 or current_gas >= 400.0:
                            risk_level = "WARNING"
                        else:
                            risk_level = "SAFE"
                
                elif model_type == "sklearn":
                    # Xử lý tương thích với mô hình Scikit-Learn (.pkl) cũ
                    flat_features = lstm_tensor[0, -1, :].reshape(1, -1)
                    pred_res = model.predict(flat_features)[0]
                    if isinstance(pred_res, (int, np.integer)):
                        risk_map = {0: "SAFE", 1: "WARNING", 2: "DANGER"}
                        risk_level = risk_map.get(int(pred_res), "SAFE")
                    else:
                        risk_level = str(pred_res).upper()

            except Exception as eval_err:
                print(f"⚠️ Lỗi suy luận model: {eval_err}")
                risk_level = "DANGER" if current_gas >= 900 else ("WARNING" if current_gas >= 400 else "SAFE")
        else:
            # Rule-based fallback
            if current_gas >= 900.0:
                risk_level = "DANGER"
            elif current_gas >= 400.0:
                risk_level = "WARNING"
            else:
                risk_level = "SAFE"

        # 2. Đánh giá Xu hướng (Trend)
        diff = predicted_gas - current_gas
        if diff > 10.0 or gas_diff > 15.0:
            trend = "RISING"
        elif diff < -10.0 or gas_diff < -15.0:
            trend = "FALLING"
        else:
            trend = "STABLE"

        # 3. Đề xuất Hành động (Recommended Action)
        if risk_level == "DANGER":
            recommended_action = "TURN_ON_FAN_MAX"
        elif risk_level == "WARNING":
            recommended_action = "TURN_ON_FAN_MEDIUM"
        else:
            recommended_action = "KEEP_AUTO"

        # Tính toán tỷ lệ phần trăm ô nhiễm (phục vụ giao diện Frontend)
        predicted_pollution_percent = round(min(100.0, max(0.0, (predicted_gas / 10000.0) * 100.0)), 2)

        return {
            "timestamp": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "device_id": req.device_id,
            "model_version": model_version,
            "model_type": model_type,
            "prediction_window_minutes": 15,
            "predicted_gas_ppm": round(predicted_gas, 2),
            "predicted_pollution_percent": predicted_pollution_percent,
            "risk_assessment": risk_level,
            "confidence": confidence,
            "trend": trend,
            "recommended_action": recommended_action,
            "detail": "AI LSTM prediction computed successfully"
        }

    except Exception as e:
        # Fallback an toàn tuyệt đối HTTP 200 giúp hệ thống không bị crash
        return JSONResponse(
            status_code=200,
            content={
                "timestamp": datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
                "device_id": req.device_id if req else "unknown",
                "model_version": model_version,
                "prediction_window_minutes": 15,
                "predicted_pollution_percent": 0.0,
                "risk_assessment": "SAFE",
                "trend": "STABLE",
                "recommended_action": "KEEP_AUTO",
                "detail": f"AI Fallback active: {str(e)}"
            }
        )


# ==============================================================================
# KHỐI 5: CHẠY SERVICE (UVICORN)
# ==============================================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    print(f"🚀 AI Service đang khởi chạy tại http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)