"""
DỊCH VỤ SUY LUẬN AI - lớp HTTP mỏng bọc quanh code của TV5.

Điểm quan trọng: file này KHÔNG viết lại logic suy luận. Nó gọi thẳng hàm
`predict_future()` trong tv5/ai_service.py - đúng vai trò mà TV5 đã ghi trong
chú thích của hàm: "API Interface để Backend (TV3) hoặc Service khác gọi vào".

Nhờ vậy chỉ có một nguồn sự thật duy nhất: TV5 đổi thứ tự đặc trưng, đổi ngưỡng
phân loại xu hướng hay đổi tầm nhìn dự báo thì hệ thống chạy theo ngay, Backend
không phải sửa gì.

Vì sao tách thành container riêng thay vì nhét vào Backend:
  * scikit-learn + pandas + numpy nặng khoảng 400 MB. Backend giữ nhẹ thì mỗi lần
    sửa REST API build lại chỉ mất vài giây.
  * File .pkl của joblib phụ thuộc phiên bản scikit-learn lúc train. Tách ra thì
    TV5 nâng phiên bản cũng không làm hỏng Backend.
  * Mô hình lỗi hoặc thiếu file thì chỉ mất tính năng dự báo; giám sát và cảnh báo
    khẩn cấp vẫn chạy.
"""
import hashlib
import importlib
import logging
import time
from pathlib import Path
from typing import Any

import sklearn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level="INFO", format="%(asctime)s | %(levelname)-7s | ai | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ai")

# Phải trùng hằng số MODEL_PATH trong tv5/ai_service.py (đường dẫn tương đối so với /app).
MODEL_PATH = Path("models/pollution_model.pkl")
TV5_MODULE = "tv5.ai_service"
# Chỉ dùng để mô tả trong /health và đặt tên trường. Thứ tự thật do TV5 quyết định
# bên trong predict_future().
FEATURES = ("pollution_percent", "dP_dt", "temperature", "humidity")

state: dict[str, Any] = {"tv5": None, "version": None, "window_size": None, "error": None}


# ---------------------------------------------------------------------
#  Nạp code + mô hình của TV5
# ---------------------------------------------------------------------
def load_tv5() -> str:
    """Import (hoặc import lại) tv5/ai_service.py. Chính module đó tự nạp file .pkl."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Chưa có file mô hình tại {MODEL_PATH.resolve()}. "
            "Xin file pollution_model.pkl của TV5, hoặc train lại bằng tv5/train_ai.py.")

    try:
        module = state["tv5"]
        # reload để lần gọi /reload sau khi chép file .pkl mới cũng nạp lại mô hình
        module = importlib.reload(module) if module is not None else importlib.import_module(TV5_MODULE)
    except SystemExit as exc:
        # ai_service.py của TV5 gọi exit() khi không thấy file model. Không bắt lại
        # thì cả dịch vụ tắt theo và /health mất luôn khả năng báo lý do.
        raise RuntimeError("tv5/ai_service.py dừng khi nạp mô hình (exit) - kiểm tra file .pkl") from exc

    model = getattr(module, "model", None)
    n_features = getattr(model, "n_features_in_", None)
    if not n_features or n_features % len(FEATURES) != 0:
        raise ValueError(f"Mô hình nhận {n_features} đặc trưng, không chia hết cho {len(FEATURES)}")

    # Suy ra kích thước cửa sổ từ chính mô hình thay vì ghi cứng 20: TV5 đổi sang
    # 30 mẫu thì Backend tự biết mà lấy đúng số khung dữ liệu.
    digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()[:8]
    state.update(tv5=module, window_size=n_features // len(FEATURES), error=None,
                 version=f"{type(model).__name__}-{digest}")
    log.info("Đã nạp %s qua tv5/ai_service.py (cửa sổ %d mẫu x %d đặc trưng, scikit-learn %s)",
             state["version"], state["window_size"], len(FEATURES), sklearn.__version__)
    return state["version"]


# ---------------------------------------------------------------------
#  Kiểu dữ liệu
# ---------------------------------------------------------------------
class Sample(BaseModel):
    pollution_percent: float
    dP_dt: float = Field(description="Chênh lệch nồng độ so với mẫu liền trước, đơn vị %/mẫu như train_ai.py")
    temperature: float
    humidity: float


class PredictRequest(BaseModel):
    device_id: str | None = None
    samples: list[Sample] = Field(description="Cửa sổ trượt, mẫu cũ nhất đứng đầu")


class PredictResponse(BaseModel):
    predicted_pollution_percent: float
    current_pollution_percent: float
    trend: str
    prediction_window_minutes: int
    model_version: str
    inference_ms: float


app = FastAPI(title="AI Prediction Service", version="1.0.0",
              description="Dự báo nồng độ ô nhiễm 15 phút tới - gọi predict_future() của TV5")


@app.on_event("startup")
def startup() -> None:
    try:
        load_tv5()
    except Exception as exc:                      # thiếu model thì vẫn chạy để /health báo rõ lý do
        state["error"] = str(exc)
        log.warning("Chưa dùng được mô hình: %s", exc)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if state["tv5"] is not None else "no_model",
        "model_loaded": state["tv5"] is not None,
        "model_version": state["version"],
        "window_size": state["window_size"],
        "features": list(FEATURES),
        "sklearn_version": sklearn.__version__,
        "model_path": str(MODEL_PATH.resolve()),
        "inference_source": f"{TV5_MODULE}.predict_future",
        "error": state["error"],
    }


@app.post("/reload")
def reload_model() -> dict[str, Any]:
    """Gọi sau khi chép file .pkl mới vào ai/models/, khỏi phải khởi động lại container."""
    try:
        return {"reloaded": True, "model_version": load_tv5()}
    except Exception as exc:
        state["error"] = str(exc)
        raise HTTPException(503, str(exc)) from exc


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest) -> PredictResponse:
    module, window_size = state["tv5"], state["window_size"]
    if module is None:
        raise HTTPException(503, state["error"] or "Chưa nạp được mô hình")
    if len(req.samples) != window_size:
        raise HTTPException(422, f"Cần đúng {window_size} mẫu, nhận được {len(req.samples)}")

    started = time.perf_counter()
    try:
        # Toàn bộ phần suy luận, kẹp giá trị về 0-100 và phân loại xu hướng đều nằm
        # trong hàm này của TV5. Ở đây chỉ đo thời gian và chuyển đổi định dạng.
        result = module.predict_future([s.model_dump() for s in req.samples])
    except Exception as exc:
        log.exception("Lỗi khi gọi predict_future")
        raise HTTPException(500, f"predict_future lỗi: {type(exc).__name__}: {exc}") from exc
    inference_ms = round((time.perf_counter() - started) * 1000, 2)

    # Bỏ trường "timestamp" của TV5: nó lấy giờ máy rồi gắn chữ Z (nghĩa là giờ UTC),
    # chạy ở Việt Nam sẽ lệch 7 tiếng và không có mili-giây. Backend tự sinh mốc thời
    # gian đúng chuẩn khi publish lên MQTT.
    return PredictResponse(
        predicted_pollution_percent=float(result["predicted_pollution_percent"]),
        current_pollution_percent=round(float(req.samples[-1].pollution_percent), 1),
        trend=str(result["trend"]),
        prediction_window_minutes=int(result["prediction_window_minutes"]),
        model_version=state["version"], inference_ms=inference_ms)
