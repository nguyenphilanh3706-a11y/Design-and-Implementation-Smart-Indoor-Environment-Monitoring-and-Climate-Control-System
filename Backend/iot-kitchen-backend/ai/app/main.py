import hashlib
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import tensorflow as tf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level="INFO",
    format="%(asctime)s | %(levelname)-7s | ai | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ai")


# ============================================================
# MODEL / TRAINING CONTRACT
# ============================================================
MODEL_DIR = Path(os.getenv("MODEL_DIR", "models"))
MODEL_PATH = Path(
    os.getenv(
        "LSTM_MODEL_PATH",
        str(MODEL_DIR / "kitchen_lstm_15m.h5"),
    )
)

# train_ai.py:
# X = 30 time steps x 5 scaled features
# y = target_risk_15m with 3 classes
TIME_STEPS = 30
N_FEATURES = 5
FORECAST_MINUTES = 15

MODEL_FEATURES = [
    "temperature_scaled",
    "humidity_scaled",
    "gas_ppm_scaled",
    "gas_diff_scaled",
    "temp_diff_scaled",
]

CLASS_MAP = {
    0: "SAFE",
    1: "WARNING",
    2: "DANGER",
}

# Must match the preprocessing used to create kitchen_lstm_dataset_13000.csv.
# These values come from the current AI service supplied with the trained model.
SCALER_CONFIG = {
    "temperature": {"min": 28.0, "max": 52.3},
    "humidity": {"min": 58.9, "max": 98.8},
    "gas_ppm": {"min": 62.5, "max": 9999.0},
    "gas_diff": {"min": -4112.0, "max": 9804.0},
    "temp_diff": {"min": -23.0, "max": 23.0},
}

# When the backend sends one current sample at a time, keep raw samples here.
# Raw values are kept so differences are computed before scaling.
device_buffers: Dict[str, deque] = {}


state: dict[str, Any] = {
    "model": None,
    "version": None,
    "model_type": None,
    "error": None,
}


# ============================================================
# MODEL LOADING
# ============================================================
def _shape_as_list(shape: Any) -> list[Any]:
    return [int(x) if x is not None else None for x in tuple(shape)]


def load_model() -> str:
    """
    Load the Keras/TensorFlow LSTM model saved by train_ai.py.

    Expected:
        input_shape  = (None, 30, 5)
        output_shape = (None, 3)
    """
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH.resolve()}"
        )

    if MODEL_PATH.suffix.lower() not in {".h5", ".keras"}:
        raise ValueError(
            f"Expected a Keras .h5/.keras model, got: {MODEL_PATH.name}"
        )

    try:
        # .h5 is a Keras HDF5 model, NOT a joblib/pickle file.
        model = tf.keras.models.load_model(
            MODEL_PATH,
            compile=False,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Cannot load Keras model: {type(exc).__name__}: {exc}"
        ) from exc

    input_shape = tuple(model.input_shape)
    output_shape = tuple(model.output_shape)

    if len(input_shape) != 3 or input_shape[1] != TIME_STEPS or input_shape[2] != N_FEATURES:
        raise ValueError(
            "Unexpected model input shape. "
            f"Expected (None, {TIME_STEPS}, {N_FEATURES}), got {input_shape}"
        )

    if len(output_shape) != 2 or output_shape[-1] != len(CLASS_MAP):
        raise ValueError(
            "Unexpected model output shape. "
            f"Expected (None, {len(CLASS_MAP)}), got {output_shape}"
        )

    digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()[:8]
    version = f"LSTM15m-{digest}"

    state["model"] = model
    state["version"] = version
    state["model_type"] = "keras_lstm"
    state["error"] = None

    log.info(
        "Loaded model %s | input=%s | output=%s | tensorflow=%s",
        version,
        input_shape,
        output_shape,
        tf.__version__,
    )

    return version


# ============================================================
# INPUT / OUTPUT SCHEMAS
# ============================================================
class PredictRequest(BaseModel):
    """
    Supports two request formats:

    A) Recommended for the LSTM:
       {
         "device_id": "...",
         "samples": [ ... last 30 raw samples ... ]
       }

    B) Backward-compatible single-sample input:
       {
         "device_id": "...",
         "temperature": ...,
         "humidity": ...,
         "gas_ppm": ...
       }

    If format B is used, the service maintains a 30-sample buffer per device.
    """
    model_config = ConfigDict(extra="allow")

    device_id: str = "esp32_kitchen_01"
    samples: Optional[List[Dict[str, Any]]] = None

    temperature: Optional[float] = None
    humidity: Optional[float] = None
    gas_ppm: Optional[float] = None

    # Compatibility with older backend payloads.
    pollution_percent: Optional[float] = None
    dP_dt: Optional[float] = None

    # Optional explicit differences.
    gas_diff: Optional[float] = None
    temp_diff: Optional[float] = None

    fan_state: Optional[int] = None
    fan_duty: Optional[float] = None


class PredictResponse(BaseModel):
    timestamp: str
    device_id: str

    model_version: str
    model_type: str
    prediction_kind: str
    prediction_window_minutes: int

    hazard_level: int
    status: str
    risk_assessment: str

    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float]

    current_gas_ppm: float
    temperature: float
    humidity: float
    gas_diff: float
    temp_diff: float

    trend: str
    recommended_action: str

    # Kept for dashboard compatibility. This is derived from the predicted
    # risk class; the LSTM itself does NOT regress an exact future PPM value.
    predicted_gas_ppm: float
    predicted_pollution_percent: float
    predicted_gas_ppm_source: str

    sequence_length: int
    input_shape: list[int]

    inference_ms: float
    detail: str


# ============================================================
# PREPROCESSING
# ============================================================
def scale_val(value: float, key: str) -> float:
    cfg = SCALER_CONFIG[key]
    low = float(cfg["min"])
    high = float(cfg["max"])

    if high <= low:
        raise ValueError(f"Invalid scaler range for {key}")

    value = float(value)
    value = max(low, min(value, high))
    return (value - low) / (high - low)


def _gas_from_sample(sample: Dict[str, Any]) -> float:
    """
    Prefer gas_ppm. pollution_percent is accepted only for compatibility
    with the previous service (100% -> 10000 ppm assumption).
    """
    if sample.get("gas_ppm") is not None:
        return float(sample["gas_ppm"])

    if sample.get("pollution_percent") is not None:
        return float(sample["pollution_percent"]) * 100.0

    raise ValueError("Sample is missing gas_ppm")


def _raw_sample_from_dict(
    sample: Dict[str, Any],
    previous: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    if sample.get("temperature") is None:
        raise ValueError("Sample is missing temperature")
    if sample.get("humidity") is None:
        raise ValueError("Sample is missing humidity")

    temperature = float(sample["temperature"])
    humidity = float(sample["humidity"])
    gas_ppm = _gas_from_sample(sample)

    provided_gas_diff = sample.get("gas_diff")
    if provided_gas_diff is None:
        provided_gas_diff = sample.get("dP_dt")

    if provided_gas_diff is not None:
        gas_diff = float(provided_gas_diff)
    elif previous is not None:
        gas_diff = gas_ppm - previous["gas_ppm"]
    else:
        gas_diff = 0.0

    if sample.get("temp_diff") is not None:
        temp_diff = float(sample["temp_diff"])
    elif previous is not None:
        temp_diff = temperature - previous["temperature"]
    else:
        temp_diff = 0.0

    return {
        "temperature": temperature,
        "humidity": humidity,
        "gas_ppm": gas_ppm,
        "gas_diff": gas_diff,
        "temp_diff": temp_diff,
    }


def _scale_raw_sample(sample: Dict[str, float]) -> list[float]:
    return [
        scale_val(sample["temperature"], "temperature"),
        scale_val(sample["humidity"], "humidity"),
        scale_val(sample["gas_ppm"], "gas_ppm"),
        scale_val(sample["gas_diff"], "gas_diff"),
        scale_val(sample["temp_diff"], "temp_diff"),
    ]


def _sequence_from_samples(
    samples: List[Dict[str, Any]],
) -> tuple[np.ndarray, Dict[str, float], int]:
    """
    Build an LSTM tensor directly from request.samples.

    If >=30 samples are supplied, only the most recent 30 are used.
    If <30 samples are supplied, the earliest available sample is repeated
    at the front so the tensor still has shape (1, 30, 5).
    """
    if not samples:
        raise ValueError("samples is empty")

    raw_sequence: list[Dict[str, float]] = []
    previous: Optional[Dict[str, float]] = None

    for item in samples:
        raw = _raw_sample_from_dict(item, previous)
        raw_sequence.append(raw)
        previous = raw

    actual_count = min(len(raw_sequence), TIME_STEPS)
    raw_sequence = raw_sequence[-TIME_STEPS:]

    while len(raw_sequence) < TIME_STEPS:
        pad = dict(raw_sequence[0])
        pad["gas_diff"] = 0.0
        pad["temp_diff"] = 0.0
        raw_sequence.insert(0, pad)

    scaled = np.asarray(
        [_scale_raw_sample(item) for item in raw_sequence],
        dtype=np.float32,
    )

    tensor = np.expand_dims(scaled, axis=0)
    return tensor, raw_sequence[-1], actual_count


def _sequence_from_single_request(
    req: PredictRequest,
) -> tuple[np.ndarray, Dict[str, float], int]:
    """
    Backward-compatible mode: one request = one raw sensor sample.
    A per-device deque collects 30 samples.

    Note: to match train_ai.py's "30 steps = 60 s", calls should arrive
    every 2 seconds. If the backend calls every 10 seconds, 30 steps span
    ~5 minutes and no longer match the training window.
    """
    if req.temperature is None:
        raise ValueError("temperature is required")
    if req.humidity is None:
        raise ValueError("humidity is required")

    sample: Dict[str, Any] = {
        "temperature": req.temperature,
        "humidity": req.humidity,
        "gas_ppm": req.gas_ppm,
        "pollution_percent": req.pollution_percent,
        "gas_diff": req.gas_diff,
        "dP_dt": req.dP_dt,
        "temp_diff": req.temp_diff,
    }

    buf = device_buffers.setdefault(
        req.device_id,
        deque(maxlen=TIME_STEPS),
    )

    previous = buf[-1] if buf else None
    raw = _raw_sample_from_dict(sample, previous)
    buf.append(raw)

    actual_count = len(buf)
    raw_sequence = [dict(x) for x in buf]

    while len(raw_sequence) < TIME_STEPS:
        pad = dict(raw_sequence[0])
        pad["gas_diff"] = 0.0
        pad["temp_diff"] = 0.0
        raw_sequence.insert(0, pad)

    scaled = np.asarray(
        [_scale_raw_sample(item) for item in raw_sequence],
        dtype=np.float32,
    )

    tensor = np.expand_dims(scaled, axis=0)
    return tensor, raw, actual_count


def process_sensor_input(
    req: PredictRequest,
) -> tuple[np.ndarray, Dict[str, float], int]:
    if req.samples:
        return _sequence_from_samples(req.samples)

    return _sequence_from_single_request(req)


# ============================================================
# PREDICTION HELPERS
# ============================================================
def _class_based_ppm_estimate(
    risk_level: str,
    current_gas: float,
) -> float:
    """
    Compatibility only.

    The trained LSTM predicts a future risk CLASS, not an exact future PPM.
    This estimate is derived from class thresholds and must not be reported
    as a direct regression output of the neural network.
    """
    if risk_level == "DANGER":
        return max(900.0, current_gas)

    if risk_level == "WARNING":
        return max(400.0, min(current_gas, 899.0))

    return min(399.0, current_gas)


def _trend_from_values(
    current_gas: float,
    estimated_future_gas: float,
    gas_diff: float,
) -> str:
    delta = estimated_future_gas - current_gas

    if delta > 10.0 or gas_diff > 15.0:
        return "RISING"

    if delta < -10.0 or gas_diff < -15.0:
        return "FALLING"

    return "STABLE"


def _recommended_action(risk_level: str) -> str:
    if risk_level == "DANGER":
        return "TURN_ON_FAN_MAX"

    if risk_level == "WARNING":
        return "TURN_ON_FAN_MEDIUM"

    return "KEEP_AUTO"


# ============================================================
# FASTAPI
# ============================================================
app = FastAPI(
    title="Kitchen LSTM AI Service",
    version="3.0.0",
    description=(
        "LSTM service for 15-minute future risk classification "
        "using 30 time steps x 5 scaled features."
    ),
)


@app.on_event("startup")
def startup() -> None:
    try:
        load_model()
    except Exception as exc:
        state["model"] = None
        state["version"] = None
        state["model_type"] = None
        state["error"] = str(exc)

        log.exception(
            "Cannot load AI model: %s",
            exc,
        )


@app.get("/health")
def health() -> dict[str, Any]:
    model = state["model"]

    return {
        "status": "ok" if model is not None else "no_model",
        "model_loaded": model is not None,
        "model_type": state["model_type"],
        "model_version": state["version"],
        "model_path": str(MODEL_PATH.resolve()),
        "tensorflow_version": tf.__version__,
        "input_shape": (
            _shape_as_list(model.input_shape)
            if model is not None
            else None
        ),
        "output_shape": (
            _shape_as_list(model.output_shape)
            if model is not None
            else None
        ),
        "time_steps": TIME_STEPS,
        "n_features": N_FEATURES,
        "features": MODEL_FEATURES,
        "classes": CLASS_MAP,
        "forecast_horizon_minutes": FORECAST_MINUTES,
        "error": state["error"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/kitchen/model_info")
def model_info() -> dict[str, Any]:
    model = state["model"]

    return {
        "status": "ready" if model is not None else "no_model",
        "model_version": state["version"],
        "model_type": state["model_type"],
        "input_shape": (
            _shape_as_list(model.input_shape)
            if model is not None
            else None
        ),
        "output_shape": (
            _shape_as_list(model.output_shape)
            if model is not None
            else None
        ),
        "time_steps": TIME_STEPS,
        "features_expected": N_FEATURES,
        "features": MODEL_FEATURES,
        "classes": CLASS_MAP,
        "prediction_window_minutes": FORECAST_MINUTES,
        "error": state["error"],
    }


@app.post("/reload")
def reload_model() -> dict[str, Any]:
    try:
        version = load_model()
        device_buffers.clear()

        return {
            "reloaded": True,
            "model_version": version,
            "model_type": state["model_type"],
        }

    except Exception as exc:
        state["model"] = None
        state["version"] = None
        state["model_type"] = None
        state["error"] = str(exc)

        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@app.post(
    "/predict",
    response_model=PredictResponse,
)
@app.post(
    "/api/v1/kitchen/predict",
    response_model=PredictResponse,
)
def predict(req: PredictRequest) -> PredictResponse:
    model = state["model"]

    if model is None:
        raise HTTPException(
            status_code=503,
            detail=state["error"] or "Model is not loaded",
        )

    started = time.perf_counter()

    try:
        lstm_tensor, current, sequence_length = process_sensor_input(req)

        if tuple(lstm_tensor.shape) != (1, TIME_STEPS, N_FEATURES):
            raise ValueError(
                f"Invalid LSTM tensor shape: {lstm_tensor.shape}"
            )

        prediction = np.asarray(
            model.predict(
                lstm_tensor,
                verbose=0,
            )
        )

        if prediction.shape != (1, len(CLASS_MAP)):
            raise ValueError(
                "Unexpected prediction shape. "
                f"Expected (1, {len(CLASS_MAP)}), got {prediction.shape}"
            )

        probabilities_raw = prediction[0].astype(float)
        class_id = int(np.argmax(probabilities_raw))
        risk_level = CLASS_MAP[class_id]
        confidence = float(probabilities_raw[class_id])

        probabilities = {
            CLASS_MAP[index]: round(float(probabilities_raw[index]), 6)
            for index in range(len(CLASS_MAP))
        }

        # The network predicts target_risk_15m, so this is a 15-minute
        # future risk classification.
        estimated_future_gas = _class_based_ppm_estimate(
            risk_level,
            current["gas_ppm"],
        )

        trend = _trend_from_values(
            current["gas_ppm"],
            estimated_future_gas,
            current["gas_diff"],
        )

        action = _recommended_action(
            risk_level
        )

        predicted_pollution_percent = round(
            min(
                100.0,
                max(
                    0.0,
                    estimated_future_gas / 10000.0 * 100.0,
                ),
            ),
            2,
        )

    except HTTPException:
        raise

    except Exception as exc:
        log.exception(
            "Prediction failed: %s",
            exc,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Prediction error: {type(exc).__name__}: {exc}",
        ) from exc

    inference_ms = round(
        (time.perf_counter() - started) * 1000.0,
        2,
    )

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    log.info(
        "15m prediction | device=%s | class=%d | status=%s | "
        "confidence=%.4f | gas=%.2f ppm | sequence=%d/%d",
        req.device_id,
        class_id,
        risk_level,
        confidence,
        current["gas_ppm"],
        sequence_length,
        TIME_STEPS,
    )

    return PredictResponse(
        timestamp=timestamp,
        device_id=req.device_id,

        model_version=state["version"],
        model_type=state["model_type"],
        prediction_kind="future_risk_classification",
        prediction_window_minutes=FORECAST_MINUTES,

        hazard_level=class_id,
        status=risk_level,
        risk_assessment=risk_level,

        confidence=round(confidence, 6),
        probabilities=probabilities,

        current_gas_ppm=round(current["gas_ppm"], 3),
        temperature=round(current["temperature"], 3),
        humidity=round(current["humidity"], 3),
        gas_diff=round(current["gas_diff"], 3),
        temp_diff=round(current["temp_diff"], 3),

        trend=trend,
        recommended_action=action,

        predicted_gas_ppm=round(estimated_future_gas, 2),
        predicted_pollution_percent=predicted_pollution_percent,
        predicted_gas_ppm_source="class_threshold_estimate",

        sequence_length=sequence_length,
        input_shape=[
            int(x)
            for x in lstm_tensor.shape
        ],

        inference_ms=inference_ms,
        detail=(
            "LSTM predicts the risk class at t+15 minutes. "
            "predicted_gas_ppm is a class-threshold estimate, "
            "not a direct regression output."
        ),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "9100"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
