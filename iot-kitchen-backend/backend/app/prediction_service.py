import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings, topic
from .db import Database
from .mqtt_service import MqttService, MqttUnavailable


log = logging.getLogger("ai")


# -----------------------------------------------------------------------------
# Contract with train_ai.py / ai/app/main.py
# -----------------------------------------------------------------------------
TIME_STEPS = 30
MODEL_SAMPLE_SECONDS = 2
FORECAST_MINUTES = 15
MAX_GAP_FACTOR = 3.0

EXPECTED_MODEL_TYPE = "keras_lstm"
EXPECTED_N_FEATURES = 5
EXPECTED_FEATURES = [
    "temperature_scaled",
    "humidity_scaled",
    "gas_ppm_scaled",
    "gas_diff_scaled",
    "temp_diff_scaled",
]


class PredictionService:
    def __init__(self, settings: Settings, db: Database, mqtt: MqttService):
        self.s = settings
        self.db = db
        self.mqtt = mqtt

        self.client: httpx.AsyncClient | None = None

        self.model_version: str | None = None
        self.model_type: str | None = None
        self.forecast_minutes: int = FORECAST_MINUTES

        self.last_error: str | None = None
        self.last_skip_reason: str | None = None
        self.last_prediction: dict[str, Any] | None = None

        self._task: asyncio.Task | None = None
        self._last_logged_skip: str | None = None

    # =========================================================================
    # START / STOP
    # =========================================================================

    async def start(self) -> None:
        if not self.s.ai_enabled:
            log.info("AI dang tat (AI_ENABLED=false)")
            return

        self.client = httpx.AsyncClient(
            base_url=self.s.ai_service_url,
            timeout=15.0,
        )

        self._task = asyncio.create_task(
            self._run(),
            name="ai-lstm-15m-loop",
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        if self.client:
            await self.client.aclose()

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    async def _run(self) -> None:
        await self._probe_model(initial=True)

        while True:
            try:
                await self._tick()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Loi vong lap AI: %s", self.last_error)

            # AI_INTERVAL_SECONDS controls how often a new forecast is requested.
            # It does NOT change the 2-second sampling interval used by the model window.
            await asyncio.sleep(self.s.ai_interval_s)

    # =========================================================================
    # AI HEALTH / CONTRACT CHECK
    # =========================================================================

    async def _probe_model(self, initial: bool = False) -> bool:
        if self.client is None:
            self.last_error = "HTTP client cua AI chua duoc khoi tao"
            return False

        try:
            response = await self.client.get("/health")
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            self.last_error = (
                f"Khong goi duoc AI Service tai {self.s.ai_service_url}: {exc}"
            )
            if initial:
                log.warning("%s - se thu lai", self.last_error)
            return False

        if not data.get("model_loaded"):
            self.last_error = (
                "AI Service dang chay nhung model LSTM chua duoc load"
                + (f": {data.get('error')}" if data.get("error") else "")
            )
            log.warning(self.last_error)
            return False

        model_type = data.get("model_type")
        if model_type and model_type != EXPECTED_MODEL_TYPE:
            self.last_error = (
                f"Sai model_type: AI={model_type}, Backend can={EXPECTED_MODEL_TYPE}"
            )
            log.error(self.last_error)
            return False

        n_features = data.get("n_features")
        if n_features is not None and int(n_features) != EXPECTED_N_FEATURES:
            self.last_error = (
                f"Sai so feature: AI={n_features}, Backend can={EXPECTED_N_FEATURES}"
            )
            log.error(self.last_error)
            return False

        time_steps = data.get("time_steps")
        if time_steps is not None and int(time_steps) != TIME_STEPS:
            self.last_error = (
                f"Sai time_steps: AI={time_steps}, Backend can={TIME_STEPS}"
            )
            log.error(self.last_error)
            return False

        ai_features = data.get("features")
        if ai_features and list(ai_features) != EXPECTED_FEATURES:
            self.last_error = (
                "Feature order cua AI khong khop Backend. "
                f"AI={ai_features}, Backend={EXPECTED_FEATURES}"
            )
            log.error(self.last_error)
            return False

        horizon = data.get("forecast_horizon_minutes")
        if horizon is not None:
            self.forecast_minutes = int(horizon)

        new_version = data.get("model_version")
        self.model_type = model_type or EXPECTED_MODEL_TYPE

        if new_version != self.model_version:
            self.model_version = new_version

            log.info(
                "AI model dang dung: %s | type=%s | input=30x5 | horizon=%d min",
                self.model_version,
                self.model_type,
                self.forecast_minutes,
            )

            try:
                await self.db.log_event(
                    self.s.device_id,
                    "AI_MODEL_LOADED",
                    f"Nap model {self.model_version}",
                    "INFO",
                    "ai",
                    data,
                )
            except Exception as exc:
                log.warning("Khong ghi duoc AI_MODEL_LOADED vao DB: %s", exc)

        self.last_error = None
        return True

    # =========================================================================
    # ONE FORECAST ITERATION
    # =========================================================================

    async def _tick(self) -> None:
        device = self.s.device_id

        if self.model_version is None:
            if not await self._probe_model():
                return

        samples = await self._build_sequence(device)
        if samples is None:
            return

        if self.client is None:
            return

        request_payload = {
            "device_id": device,
            "samples": samples,
        }

        try:
            response = await self.client.post(
                "/predict",
                json=request_payload,
            )
        except Exception as exc:
            self.last_error = f"Khong goi duoc AI /predict: {exc}"
            self.model_version = None
            log.warning(self.last_error)
            return

        if response.status_code != 200:
            self.last_error = (
                f"AI /predict tra {response.status_code}: {response.text[:800]}"
            )
            log.warning(self.last_error)

            if response.status_code == 503:
                self.model_version = None

            return

        result = response.json()

        self.last_error = None
        self.last_skip_reason = None
        self._last_logged_skip = None

        await self._publish_and_store(device, result)

    # =========================================================================
    # BUILD 30-SAMPLE RAW WINDOW
    # =========================================================================

    async def _build_sequence(
        self,
        device: str,
    ) -> list[dict[str, Any]] | None:
        """
        Return exactly 30 raw samples in chronological order (oldest -> newest).

        train_ai.py uses a 30-step sequence described as ~60 seconds, therefore the
        production window should be built from the raw ~2-second telemetry stream.

        gas_diff and temp_diff are intentionally NOT calculated here. ai/app/main.py
        calculates them from adjacent raw samples before applying the same min-max scaling
        contract used during training.
        """

        if self.db.pool is None:
            return self._skip("Database chua san sang")

        try:
            rows = await self.db.pool.fetch(
                """
                SELECT
                    time,
                    temperature,
                    humidity,
                    gas_ppm
                FROM telemetry
                WHERE device_id = $1
                ORDER BY time DESC
                LIMIT $2
                """,
                device,
                TIME_STEPS,
            )
        except Exception as exc:
            self.last_error = f"Khong doc duoc telemetry cho AI: {exc}"
            log.warning(self.last_error)
            return None

        if len(rows) < TIME_STEPS:
            return self._skip(
                f"Moi co {len(rows)}/{TIME_STEPS} mau telemetry; "
                f"can khoang {TIME_STEPS * MODEL_SAMPLE_SECONDS}s du lieu"
            )

        # Query is DESC. Reverse to chronological order before sending to the LSTM service.
        rows = list(reversed(rows))

        # Do not silently skip a broken sample because that changes the time sequence.
        required = ("temperature", "humidity", "gas_ppm")
        for index, row in enumerate(rows):
            for field in required:
                if row[field] is None:
                    return self._skip(
                        f"Mau AI thu {index + 1}/{TIME_STEPS} thieu {field}"
                    )

        # Basic continuity check. The device nominally publishes every ~2 seconds.
        expected = timedelta(seconds=MODEL_SAMPLE_SECONDS)
        max_gap = expected * MAX_GAP_FACTOR

        for previous, current in zip(rows, rows[1:]):
            delta = current["time"] - previous["time"]

            if delta.total_seconds() <= 0:
                return self._skip("Thu tu timestamp telemetry khong hop le")

            if delta > max_gap:
                return self._skip(
                    "Du lieu AI bi dut quang: "
                    f"gap={round(delta.total_seconds(), 2)}s > "
                    f"{round(max_gap.total_seconds(), 2)}s"
                )

        samples: list[dict[str, Any]] = []

        for row in rows:
            samples.append(
                {
                    "timestamp": row["time"].isoformat(),
                    "temperature": round(float(row["temperature"]), 4),
                    "humidity": round(float(row["humidity"]), 4),
                    "gas_ppm": round(float(row["gas_ppm"]), 4),
                }
            )

        return samples

    # =========================================================================
    # SKIP HELPER
    # =========================================================================

    def _skip(self, reason: str) -> None:
        self.last_skip_reason = reason

        if reason != self._last_logged_skip:
            self._last_logged_skip = reason
            log.info("Chua chay AI: %s", reason)

        return None

    # =========================================================================
    # PUBLISH RESULT + EVENT LOG
    # =========================================================================

    async def _publish_and_store(
        self,
        device: str,
        result: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)

        prediction_window_minutes = int(
            result.get("prediction_window_minutes", self.forecast_minutes)
        )

        target_time = now + timedelta(minutes=prediction_window_minutes)

        timestamp = result.get(
            "timestamp",
            now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        )

        payload: dict[str, Any] = {
            "device_id": device,
            "timestamp": timestamp,
            "target_time": target_time.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "prediction_kind": result.get(
                "prediction_kind", "future_risk_classification"
            ),
            "prediction_window_minutes": prediction_window_minutes,
            "hazard_level": result.get("hazard_level"),
            "status": result.get("status") or result.get("risk_assessment"),
            "risk_assessment": result.get("risk_assessment")
            or result.get("status"),
            "confidence": result.get("confidence"),
            "probabilities": result.get("probabilities", {}),
            "current_gas_ppm": result.get("current_gas_ppm"),
            "gas_ppm":result.get("current_gas_ppm"),
            "temperature": result.get("temperature"),
            "humidity": result.get("humidity"),
            "gas_diff": result.get("gas_diff"),
            "temp_diff": result.get("temp_diff"),
            "trend": result.get("trend"),
            "recommended_action": result.get("recommended_action"),
            # Compatibility values produced by AI service. These are derived from the
            # predicted class, not a direct LSTM gas regression output.
            "predicted_gas_ppm": result.get("predicted_gas_ppm"),
            "predicted_pollution_percent": result.get(
                "predicted_pollution_percent"
            ),
            "predicted_gas_ppm_source": result.get(
                "predicted_gas_ppm_source"
            ),
            "sequence_length": result.get("sequence_length"),
            "input_shape": result.get("input_shape"),
            "model_version": result.get("model_version", self.model_version),
            "model_type": result.get("model_type", self.model_type),
            "inference_ms": result.get("inference_ms"),
            "detail": result.get("detail"),
        }

        self.last_prediction = payload

        # ------------------------------------------------------------------
        # MQTT -> Dashboard
        # ------------------------------------------------------------------
        try:
            await self.mqtt.publish_json(
                topic(device, "ai_prediction"),
                payload,
                qos=0,
                retain=False,
            )
        except MqttUnavailable as exc:
            log.warning("Khong publish duoc ket qua AI: %s", exc)

        # ------------------------------------------------------------------
        # DB event log
        # ------------------------------------------------------------------
        # The existing ai_predictions schema belongs to the former regression model.
        # Store the new future-risk classification as an event until the DB schema/API
        # are migrated explicitly for the LSTM contract.
        status = str(payload.get("status") or "UNKNOWN").upper()

        if status == "DANGER":
            severity = "CRITICAL"
        elif status == "WARNING":
            severity = "WARNING"
        else:
            severity = "INFO"

        try:
            await self.db.log_event(
                device,
                "AI_FORECAST",
                (
                    f"AI +{prediction_window_minutes}m: {status} "
                    f"(confidence={payload.get('confidence')}, "
                    f"trend={payload.get('trend')})"
                ),
                severity,
                "ai",
                payload,
            )
        except Exception as exc:
            log.warning("Khong ghi duoc AI_FORECAST vao DB: %s", exc)

        log.info(
            "AI +%dm -> %s | confidence=%s | trend=%s | current_gas=%s ppm | %.2f ms",
            prediction_window_minutes,
            status,
            payload.get("confidence"),
            payload.get("trend"),
            payload.get("current_gas_ppm"),
            float(payload.get("inference_ms") or 0.0),
        )

    # =========================================================================
    # STATUS FOR REST API / HEALTH
    # =========================================================================

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.s.ai_enabled,
            "model_version": self.model_version,
            "model_type": self.model_type,
            "prediction_kind": "future_risk_classification",
            "prediction_window_minutes": self.forecast_minutes,
            "window_size": TIME_STEPS,
            "sample_seconds": MODEL_SAMPLE_SECONDS,
            "interval_seconds": self.s.ai_interval_s,
            "last_error": self.last_error,
            "last_skip_reason": self.last_skip_reason,
            "last_prediction": self.last_prediction,
        }
