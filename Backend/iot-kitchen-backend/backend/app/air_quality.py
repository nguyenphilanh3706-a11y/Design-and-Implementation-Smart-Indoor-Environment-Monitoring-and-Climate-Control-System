"""
THANG CHẤT LƯỢNG KHÔNG KHÍ THEO ppm VÀ TỐC ĐỘ QUẠT TƯƠNG ỨNG

    ppm <  800          -> GOOD -> quạt 0%
    800 <= ppm <= 1000  -> MID  -> quạt 50%
    ppm >  1000         -> BAD  -> quạt 100%

Hai mốc 800 và 1000 lưu trong bảng device_config, đổi được qua POST /config.
Đây là thang hiển thị: ESP32 tự quyết định tốc độ thật bằng FSM, có dwell time
chống bật tắt liên tục khi nồng độ dao động quanh mốc.
"""
from typing import Literal

Level = Literal["GOOD", "MID", "BAD"]
FAN_SPEED: dict[str, int] = {"GOOD": 0, "MID": 50, "BAD": 100}
DEFAULT_MID, DEFAULT_BAD = 800.0, 1000.0


def classify(ppm: float | None, mid: float = DEFAULT_MID, bad: float = DEFAULT_BAD) -> Level | None:
    if ppm is None:
        return None
    if ppm < mid:
        return "GOOD"
    if ppm <= bad:
        return "MID"
    return "BAD"


def fan_speed_for(level: Level | None) -> int | None:
    return FAN_SPEED.get(level) if level else None
