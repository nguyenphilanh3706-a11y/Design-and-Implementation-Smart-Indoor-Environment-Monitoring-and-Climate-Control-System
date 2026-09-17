"""
DỊCH VỤ DỰ BÁO - nhiệm vụ Tuần 2 của TV3: đưa mô hình của TV5 vào hệ thống thật.

Mỗi 10 giây:
  1. Lấy cửa sổ trượt từ TimescaleDB, GỘP về đúng chu kỳ mà mô hình được train (10 giây/mẫu)
  2. Tính dP_dt giống hệt cách TV5 tính lúc train (chênh lệch giữa 2 mẫu liền nhau)
  3. Gọi dịch vụ AI qua HTTP để suy luận
  4. Publish kết quả lên iot/kitchen/{device_id}/ai_prediction cho Dashboard
  5. Lưu vào bảng ai_predictions để 15 phút sau còn đối chiếu với giá trị thực đo

Mô hình hỏng hay dịch vụ AI chết thì chỉ mất tính năng dự báo; giám sát và cảnh báo
khẩn cấp vẫn chạy bình thường.
"""
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

MAX_GAP_FACTOR = 1.5      # khung cách nhau quá 1.5 lần chu kỳ -> coi như dữ liệu đứt quãng


class PredictionService:
    def __init__(self, settings: Settings, db: Database, mqtt: MqttService):
        self.s = settings
        self.db = db
        self.mqtt = mqtt
        self.client: httpx.AsyncClient | None = None
        self.window_size = settings.ai_window_size      # sẽ lấy lại từ /health của dịch vụ AI
        self.model_version: str | None = None
        self.last_error: str | None = None
        self.last_skip_reason: str | None = None
        self.last_prediction: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._last_logged_skip: str | None = None

    async def start(self) -> None:
        if not self.s.ai_enabled:
            log.info("Tính năng dự báo đang tắt (AI_ENABLED=false)")
            return
        self.client = httpx.AsyncClient(base_url=self.s.ai_service_url, timeout=10.0)
        self._task = asyncio.create_task(self._run(), name="ai-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.client:
            await self.client.aclose()

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        await self._probe_model(initial=True)
        while True:
            try:
                await self._tick()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.error("Lỗi vòng lặp dự báo: %s", self.last_error)
            await asyncio.sleep(self.s.ai_interval_s)

    async def _probe_model(self, initial: bool = False) -> bool:
        """Hỏi dịch vụ AI xem mô hình cần cửa sổ bao nhiêu mẫu, thay vì ghi cứng 20."""
        try:
            data = (await self.client.get("/health")).json()
        except Exception as exc:
            self.last_error = f"Không gọi được dịch vụ AI tại {self.s.ai_service_url}: {exc}"
            if initial:
                log.warning("%s - sẽ thử lại ở vòng kế tiếp", self.last_error)
            return False

        if not data.get("model_loaded"):
            self.last_error = "Dịch vụ AI chạy nhưng chưa nạp được mô hình (thiếu file .pkl của TV5)"
            log.warning(self.last_error)
            return False

        if data.get("window_size") and data["window_size"] != self.window_size:
            log.info("Mô hình cần cửa sổ %d mẫu (cấu hình đang để %d) -> dùng theo mô hình",
                     data["window_size"], self.window_size)
            self.window_size = data["window_size"]
        if data.get("model_version") != self.model_version:
            self.model_version = data.get("model_version")
            log.info("Mô hình đang dùng: %s (scikit-learn %s)", self.model_version, data.get("sklearn_version"))
            await self.db.log_event(self.s.device_id, "AI_MODEL_LOADED",
                                    f"Nạp mô hình {self.model_version}", "INFO", "ai", data)
        self.last_error = None
        return True

    # ------------------------------------------------------------------
    async def _tick(self) -> None:
        device = self.s.device_id
        if self.model_version is None and not await self._probe_model():
            return

        samples = await self._build_window(device)
        if samples is None:
            return

        try:
            response = await self.client.post("/predict", json={"device_id": device, "samples": samples})
        except Exception as exc:
            self.last_error = f"Không gọi được dịch vụ AI: {exc}"
            self.model_version = None                     # buộc dò lại ở vòng sau
            log.warning(self.last_error)
            return

        if response.status_code != 200:
            self.last_error = f"Dịch vụ AI trả {response.status_code}: {response.text[:150]}"
            log.warning(self.last_error)
            if response.status_code == 503:
                self.model_version = None
            return

        result = response.json()
        self.last_error = self.last_skip_reason = None
        await self._publish_and_store(device, result)

    async def _build_window(self, device: str) -> list[dict[str, float]] | None:
        """Dựng đúng bộ đặc trưng mà mô hình mong đợi, hoặc trả None kèm lý do bỏ qua."""
        rows = await self.db.ai_window(device, self.window_size, self.s.ai_sample_seconds)
        if len(rows) < self.window_size + 1:
            return self._skip(f"mới có {len(rows)}/{self.window_size + 1} khung dữ liệu "
                              f"(cần khoảng {(self.window_size + 1) * self.s.ai_sample_seconds}s dữ liệu liên tục)")

        for row in rows:
            if row["pollution_percent"] is None or row["temperature"] is None or row["humidity"] is None:
                return self._skip("có khung thiếu số đo (cảm biến lỗi) - bỏ qua lượt này")

        # Dữ liệu đứt quãng thì cửa sổ không còn đúng 200 giây liên tục như lúc train
        expected = timedelta(seconds=self.s.ai_sample_seconds)
        for previous, current in zip(rows, rows[1:]):
            if current["time"] - previous["time"] > expected * MAX_GAP_FACTOR:
                return self._skip("dữ liệu bị đứt quãng, cửa sổ không liên tục")

        # dP_dt tính y hệt train_ai.py: chênh lệch nồng độ giữa 2 mẫu LIỀN NHAU của chuỗi 10 giây,
        # đơn vị là %/mẫu chứ không phải %/giây. Sai chỗ này thì đặc trưng lệch 5 lần.
        samples = []
        for previous, current in zip(rows, rows[1:]):
            samples.append({
                "pollution_percent": round(float(current["pollution_percent"]), 2),
                "dP_dt": round(float(current["pollution_percent"]) - float(previous["pollution_percent"]), 2),
                "temperature": round(float(current["temperature"]), 2),
                "humidity": round(float(current["humidity"]), 2),
            })
        return samples

    def _skip(self, reason: str) -> None:
        self.last_skip_reason = reason
        if reason != self._last_logged_skip:              # chỉ ghi log khi lý do thay đổi
            self._last_logged_skip = reason
            log.info("Chưa dự báo được: %s", reason)
        return None

    async def _publish_and_store(self, device: str, result: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        target = now + timedelta(minutes=result["prediction_window_minutes"])

        # Payload theo Bản thống nhất mục 2.2, bổ sung device_id / target_time / hiện tại
        # để Dashboard vẽ được điểm dự báo lên trục thời gian.
        payload = {
            "device_id": device,
            "timestamp": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "target_time": target.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "prediction_window_minutes": result["prediction_window_minutes"],
            "predicted_pollution_percent": result["predicted_pollution_percent"],
            "current_pollution_percent": result.get("current_pollution_percent"),
            "trend": result["trend"],
            "model_version": result.get("model_version"),
        }
        self.last_prediction = payload

        try:
            await self.mqtt.publish_json(topic(device, "ai_prediction"), payload, qos=0, retain=False)
        except MqttUnavailable as exc:
            log.warning("Không publish được dự báo: %s", exc)

        try:
            await self.db.insert_prediction(device, now, target, result)
        except Exception as exc:
            log.error("Không lưu được dự báo vào CSDL: %s", exc)

        log.debug("Dự báo %s%% (%s) trong %s ms", result["predicted_pollution_percent"],
                  result["trend"], result.get("inference_ms"))

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.s.ai_enabled,
            "model_version": self.model_version,
            "window_size": self.window_size,
            "sample_seconds": self.s.ai_sample_seconds,
            "interval_seconds": self.s.ai_interval_s,
            "last_error": self.last_error,
            "last_skip_reason": self.last_skip_reason,
            "last_prediction": self.last_prediction,
        }
