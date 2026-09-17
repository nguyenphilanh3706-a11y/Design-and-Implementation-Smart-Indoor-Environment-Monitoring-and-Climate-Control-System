#!/usr/bin/env python3
"""
ESP32 GIẢ LẬP - dùng để kiểm thử Backend khi phần cứng của TV1/TV2 chưa xong.

Mô phỏng đúng những gì firmware thật phải làm (Bản thống nhất mục 2 và 3):
  * Publish telemetry mỗi 2 giây (QoS 0)
  * Publish status ONLINE (QoS 1, retain) + đăng ký LWT "OFFLINE" khi rớt mạng
  * Chạy FSM: BẬT khi P >= 40% HOẶC (P >= 28% VÀ T >= 33°C); TẮT khi P < 25% VÀ T < 31°C;
    khoá trạng thái (dwell time) 30 giây; cưỡng bức BẬT khi P >= 75%
  * Nghe lệnh actuator/set, mode/set, config/set rồi phản hồi actuator/state

Cách chạy:
    docker compose --profile sim up simulator                       # dữ liệu bình thường
    SIM_SCENARIO=smoke docker compose --profile sim up simulator    # kịch bản khói (bắn Telegram)
    python tools/simulate_esp32.py --scenario smoke --host localhost # chạy thẳng trên máy
"""
import argparse
import asyncio
import contextlib
import json
import os
import random
import signal
from datetime import datetime, timezone

import aiomqtt

PREFIX = "iot/kitchen"


def now_iso() -> str:
    """ISO 8601 UTC, mili-giây, kết thúc bằng Z - đúng định dạng đã thống nhất."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeKitchen:
    """Mô hình vật lý đơn giản của buồng bếp + FSM điều khiển quạt."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.t = 0.0                    # số giây đã chạy
        self.pollution = 14.0
        self.temperature = 29.5
        self.humidity = 66.0
        self.fan = 0
        self.mode = "AUTO"
        self.reason = "FSM"
        self.pollution_threshold = 40.0
        self.temp_threshold = 33.0
        self.dwell = 30.0
        self.last_change = -999.0
        self.seq = 0                    # số thứ tự bản tin, firmware thật đếm từ 0 sau mỗi lần khởi động
        self.drop_rate = 0.0            # tỉ lệ cố tình bỏ bản tin (dùng khi kiểm chứng phép đo mất gói)
        self.sensor_fault = False       # bật bằng --fault để thử đường xử lý cảm biến hỏng

    # --------------------------- cảm biến ---------------------------
    def step(self, dt: float) -> None:
        self.t += dt
        target = self._target_pollution()
        if self.fan:
            target = max(8.0, target - 18.0)          # quạt hút bớt khói -> nồng độ giảm nhanh hơn
        # Tiến dần về giá trị mục tiêu (lọc thông thấp) + nhiễu ngẫu nhiên của cảm biến
        self.pollution += (target - self.pollution) * min(1.0, dt / 8.0) + random.uniform(-0.6, 0.6)
        self.pollution = max(5.0, min(100.0, self.pollution))
        # Nấu ăn thì bếp vừa nhiều khói vừa nóng lên
        self.temperature = round(29.0 + 0.06 * self.pollution + random.uniform(-0.3, 0.3), 1)
        self.humidity = round(64.0 + 0.08 * self.pollution + random.uniform(-1.0, 1.0), 1)

    def _target_pollution(self) -> float:
        if self.scenario != "smoke":
            return 15.0 + 6.0 * random.random()
        phase = self.t % 180.0                        # một chu kỳ demo dài 3 phút
        if phase < 20:
            return 15.0                               # bình thường
        if phase < 50:
            return 15.0 + (phase - 20) * 2.5          # bắt đầu chiên xào, khói tăng dần
        if phase < 110:
            return 88.0 + random.uniform(-5, 5)       # khói dày: vượt ngưỡng 50% -> Telegram
        return 15.0                                   # tắt bếp, mở cửa sổ -> về mức an toàn

    @property
    def rs_ro_ratio(self) -> float:
        """MQ-135: không khí sạch Rs/R0 ~ 3.6 và giảm dần khi khí độc tăng."""
        return round(max(0.4, 3.6 - 0.03 * self.pollution) + random.uniform(-0.02, 0.02), 3)

    # ----------------------------- FSM ------------------------------
    def update_fan(self) -> bool:
        """Trả về True nếu trạng thái quạt vừa thay đổi."""
        if self.pollution >= 75.0 and not self.fan:                 # an toàn: ưu tiên cao nhất
            return self._set_fan(1, "SAFETY_OVERRIDE", force=True)
        if self.mode != "AUTO":
            return False
        should_on = self.pollution >= self.pollution_threshold or \
            (self.pollution >= 28.0 and self.temperature >= self.temp_threshold)
        should_off = self.pollution < 25.0 and self.temperature < 31.0
        if should_on and not self.fan:
            return self._set_fan(1, "FSM")
        if should_off and self.fan:
            return self._set_fan(0, "FSM")
        return False

    def _set_fan(self, value: int, reason: str, force: bool = False) -> bool:
        # Dwell time: giữ nguyên trạng thái tối thiểu 30 s để quạt không bật/tắt liên tục (chattering)
        if not force and self.t - self.last_change < self.dwell:
            return False
        self.fan, self.reason, self.last_change = value, reason, self.t
        return True

    # --------------------------- payload ----------------------------
    def telemetry(self, device_id: str) -> dict:
        self.seq += 1
        payload = {
            "device_id": device_id, "seq": self.seq, "timestamp": now_iso(),
            "temperature": self.temperature, "humidity": self.humidity,
            "pollution_percent": round(self.pollution, 1), "rs_ro_ratio": self.rs_ro_ratio,
            "fan_state": self.fan, "mode": self.mode, "network_status": "CONNECTED",
        }
        if self.sensor_fault:
            # Đúng quy ước: cảm biến hỏng thì gửi null, KHÔNG gửi -1.
            # (Backend vẫn tự lọc được -1, nhưng lúc đó dữ liệu đã kém tin cậy.)
            payload["humidity"] = None
            payload["temperature"] = None
        return payload

    def config_state(self, device_id: str) -> dict:
        """Ngưỡng FSM đang thực sự áp dụng - phát lại để Backend đối chiếu với CSDL."""
        return {"device_id": device_id, "pollution_threshold": self.pollution_threshold,
                "temp_threshold": self.temp_threshold, "dwell_time_seconds": self.dwell,
                "timestamp": now_iso()}

    def actuator_state(self, device_id: str) -> dict:
        return {"device_id": device_id, "fan_state": self.fan, "mode": self.mode,
                "reason": self.reason, "timestamp": now_iso()}


async def publish_loop(client: aiomqtt.Client, kitchen: FakeKitchen, device_id: str, interval: float) -> None:
    while True:
        kitchen.step(interval)
        if kitchen.update_fan():
            await client.publish(f"{PREFIX}/{device_id}/actuator/state",
                                 json.dumps(kitchen.actuator_state(device_id)), qos=1, retain=True)
            print(f"[FSM] quạt -> {'BẬT' if kitchen.fan else 'TẮT'} ({kitchen.reason}), "
                  f"P={kitchen.pollution:.1f}% T={kitchen.temperature:.1f}°C", flush=True)
        payload = kitchen.telemetry(device_id)
        if kitchen.drop_rate and random.random() < kitchen.drop_rate:
            # Cố tình KHÔNG gửi (seq vẫn tăng) -> Backend phải phát hiện được lỗ hổng
            print(f"[drop] bỏ qua bản tin seq={payload['seq']}", flush=True)
            await asyncio.sleep(interval)
            continue
        await client.publish(f"{PREFIX}/{device_id}/telemetry", json.dumps(payload), qos=0)
        print(f"[tx] P={payload['pollution_percent']:>5}% T={payload['temperature']:>5}°C "
              f"H={payload['humidity']:>5}% quạt={payload['fan_state']} {kitchen.mode}", flush=True)
        await asyncio.sleep(interval)


async def command_loop(client: aiomqtt.Client, kitchen: FakeKitchen, device_id: str) -> None:
    async for message in client.messages:
        topic, raw = message.topic.value, message.payload
        try:
            data = json.loads(raw)
        except ValueError:
            print(f"[rx] payload sai JSON trên {topic}", flush=True)
            continue
        print(f"[rx] {topic} <- {data}", flush=True)

        if topic.endswith("/actuator/set"):
            if kitchen.mode != "MANUAL":
                # Firmware thật cũng phải làm vậy: ở AUTO thì FSM giữ quyền quyết định
                kitchen.reason = "IGNORED_AUTO_MODE"
            else:
                kitchen.fan, kitchen.reason, kitchen.last_change = int(data["state"]), "MANUAL_COMMAND", kitchen.t
            await client.publish(f"{PREFIX}/{device_id}/actuator/state",
                                 json.dumps(kitchen.actuator_state(device_id)), qos=1, retain=True)
        elif topic.endswith("/mode/set"):
            kitchen.mode = data["mode"]
        elif topic.endswith("/config/set"):
            kitchen.pollution_threshold = float(data.get("pollution_threshold", kitchen.pollution_threshold))
            kitchen.temp_threshold = float(data.get("temp_threshold", kitchen.temp_threshold))
            kitchen.dwell = float(data.get("dwell_time_seconds", kitchen.dwell))
            await client.publish(f"{PREFIX}/{device_id}/config/state",
                                 json.dumps(kitchen.config_state(device_id)), qos=1, retain=True)


async def run(args: argparse.Namespace) -> None:
    device_id = args.device_id
    status_topic = f"{PREFIX}/{device_id}/status"
    kitchen = FakeKitchen(args.scenario)
    kitchen.drop_rate, kitchen.sensor_fault = args.drop_rate, args.fault
    # Last Will and Testament: Broker tự phát tin này nếu thiết bị mất kết nối đột ngột
    will = aiomqtt.Will(status_topic, json.dumps({"device_id": device_id, "status": "OFFLINE"}),
                        qos=1, retain=True)

    while True:
        try:
            async with aiomqtt.Client(hostname=args.host, port=args.port, username=args.user,
                                      password=args.password, identifier=device_id, will=will) as client:
                print(f"Đã kết nối {args.host}:{args.port} với tư cách {device_id} "
                      f"(kịch bản: {args.scenario})", flush=True)
                await client.publish(status_topic, json.dumps({"device_id": device_id, "status": "ONLINE"}),
                                     qos=1, retain=True)
                await client.publish(f"{PREFIX}/{device_id}/config/state",
                                     json.dumps(kitchen.config_state(device_id)), qos=1, retain=True)
                for suffix in ("actuator/set", "mode/set", "config/set"):
                    await client.subscribe(f"{PREFIX}/{device_id}/{suffix}", qos=1)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(publish_loop(client, kitchen, device_id, args.interval))
                    tg.create_task(command_loop(client, kitchen, device_id))
        except* aiomqtt.MqttError as exc:
            print(f"Mất kết nối ({exc.exceptions[0]}) - thử lại sau 3 giây", flush=True)
            await asyncio.sleep(3)


def main() -> None:
    p = argparse.ArgumentParser(description="ESP32 giả lập cho đồ án IoT phòng bếp")
    p.add_argument("--host", default=os.getenv("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    p.add_argument("--user", default=os.getenv("MQTT_USER", "esp32_device"))
    p.add_argument("--password", default=os.getenv("MQTT_PASSWORD", ""))
    p.add_argument("--device-id", default=os.getenv("DEVICE_ID", "esp32_kitchen_01"))
    p.add_argument("--interval", type=float, default=2.0, help="chu kỳ gửi telemetry (giây)")
    p.add_argument("--scenario", choices=["normal", "smoke"], default="normal",
                   help="normal: dữ liệu sạch · smoke: chu kỳ 3 phút có khói vượt 50%% để thử cảnh báo")
    p.add_argument("--drop-rate", type=float, default=0.0,
                   help="tỉ lệ cố tình bỏ bản tin (0.1 = 10%%) để kiểm chứng phép đo mất gói")
    p.add_argument("--fault", action="store_true", help="giả lập AHT20 hỏng: gửi null thay vì số đo")
    args = p.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run(args))
    with contextlib.suppress(NotImplementedError):          # Windows không hỗ trợ add_signal_handler
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        loop.run_until_complete(task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nDừng thiết bị giả lập.", flush=True)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
