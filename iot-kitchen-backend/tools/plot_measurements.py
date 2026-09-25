#!/usr/bin/env python3
"""
VẼ ĐỒ THỊ SỐ LIỆU ĐO - lấy dữ liệu THẬT từ hệ thống đang chạy.

Khác với việc sinh số ngẫu nhiên rồi vẽ: mọi con số ở đây đọc từ REST API của Backend,
tức là từ bảng telemetry, system_events và ai_predictions trong TimescaleDB.

Chỗ nào chưa đủ dữ liệu thì script BÁO RÕ và bỏ qua biểu đồ đó, không bịa thêm.
Muốn có biểu đồ thì phải chạy hệ thống đủ lâu hoặc thao tác để sinh sự kiện.

Cách chạy:
    pip install matplotlib
    python tools/plot_measurements.py --api https://iot-kitchen-hcmute.duckdns.org
    python tools/plot_measurements.py --api http://localhost:8000 --minutes 120

Ảnh xuất ra thư mục charts/, chèn thẳng vào Chương 4 báo cáo.
"""
import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.linewidth"] = 0.8

OUT = "charts"
SKIPPED: list[str] = []


def get(api: str, path: str, **params):
    url = f"{api.rstrip('/')}/api/v1/kitchen{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def skip(name: str, reason: str) -> None:
    SKIPPED.append(f"{name}: {reason}")
    print(f"  [BỎ QUA] {name} - {reason}")


def save(filename: str) -> None:
    plt.tight_layout()
    plt.savefig(os.path.join(OUT, filename))
    plt.close()
    print(f"  [XONG]   {filename}")


# =====================================================================
#  BÀI 1 - Độ trễ từ lúc cảm biến đo đến lúc Backend ghi vào CSDL
#  Nguồn: cột received_at trừ cột time của từng bản ghi telemetry
# =====================================================================
def chart_ingest_latency(api: str, minutes: int) -> None:
    data = get(api, "/history", limit=2000, order="asc")
    rows = [r for r in data["items"] if r.get("received_at")]
    if len(rows) < 10:
        return skip("Độ trễ ghi dữ liệu", f"mới có {len(rows)} bản ghi, cần ít nhất 10")

    latency = np.array([(parse_time(r["received_at"]) - parse_time(r["timestamp"])).total_seconds() * 1000
                        for r in rows])
    mean, p95 = latency.mean(), np.percentile(latency, 95)

    print(f"  Độ trễ ghi dữ liệu: trung bình {mean:.1f} ms, lớn nhất {latency.max():.1f} ms, "
          f"p95 {p95:.1f} ms, trên {len(latency)} mẫu")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4), dpi=200,
                                   gridspec_kw={"width_ratios": [2, 1]})
    ax1.plot(range(len(latency)), latency, color="#1f77b4", linewidth=0.9)
    ax1.axhline(mean, color="red", linestyle="--", label=f"Trung bình {mean:.1f} ms")
    ax1.axhline(p95, color="orange", linestyle=":", label=f"Phân vị 95 {p95:.1f} ms")
    ax1.set_xlabel("Thứ tự bản tin")
    ax1.set_ylabel("Độ trễ (ms)")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.hist(latency, bins=30, color="#1f77b4", alpha=0.8)
    ax2.set_xlabel("Độ trễ (ms)")
    ax2.set_ylabel("Số bản tin")
    ax2.grid(True, alpha=0.3)

    fig.suptitle(f"Bài đo 1: Độ trễ từ lúc đo đến lúc lưu vào CSDL ({len(latency)} mẫu thực tế)",
                 fontsize=12, fontweight="bold")
    save("chart1_do_tre_ghi_du_lieu.png")


# =====================================================================
#  BÀI 2 - Thời gian khứ hồi của lệnh điều khiển quạt
#  Nguồn: trường rtt_ms mà POST /actuator đo được, lưu trong system_events
# =====================================================================
def chart_control_rtt(api: str) -> None:
    events = get(api, "/events", limit=200, event_type="MANUAL_FAN_COMMAND")
    rtt = [e["details"]["rtt_ms"] for e in events
           if e.get("details") and e["details"].get("rtt_ms") is not None]
    if len(rtt) < 3:
        return skip("Thời gian khứ hồi điều khiển",
                    f"mới có {len(rtt)} lệnh có phản hồi. Bấm bật/tắt quạt vài lần "
                    "(POST /actuator) rồi chạy lại")

    rtt = np.array(rtt[::-1])          # đảo lại cho đúng thứ tự thời gian
    mean = rtt.mean()
    print(f"  Thời gian khứ hồi điều khiển: trung bình {mean:.1f} ms, "
          f"nhỏ nhất {rtt.min():.1f} ms, lớn nhất {rtt.max():.1f} ms, trên {len(rtt)} lệnh")

    plt.figure(figsize=(8, 4), dpi=200)
    plt.plot(range(1, len(rtt) + 1), rtt, marker="o", color="#1f77b4", linewidth=1.5,
             label="Thời gian khứ hồi đo được")
    plt.axhline(mean, color="red", linestyle="--", label=f"Trung bình {mean:.1f} ms")
    plt.title(f"Bài đo 2: Thời gian khứ hồi lệnh điều khiển quạt ({len(rtt)} lần đo thực tế)",
              fontsize=12, fontweight="bold")
    plt.xlabel("Lần điều khiển")
    plt.ylabel("Thời gian khứ hồi (ms)")
    plt.legend(loc="upper right")
    plt.grid(True, alpha=0.3)
    save("chart2_rtt_dieu_khien.png")


# =====================================================================
#  BÀI 3 - Chống nảy công tắc quạt
#  Nguồn: sự kiện FAN_STATE_CHANGED và SAFETY_OVERRIDE, phân loại theo reason
# =====================================================================
def chart_fan_switching(api: str, minutes: int) -> None:
    events = get(api, "/events", limit=500)
    changes = [e for e in events if e["event_type"] in ("FAN_STATE_CHANGED", "SAFETY_OVERRIDE")]
    if len(changes) < 2:
        return skip("Chống nảy công tắc",
                    f"mới có {len(changes)} lần quạt đổi trạng thái, cần ít nhất 2")

    telemetry = get(api, "/history", limit=2000, order="asc")["items"]
    if len(telemetry) < 10:
        return skip("Chống nảy công tắc", "chưa đủ dữ liệu telemetry để vẽ nền")

    t0 = parse_time(telemetry[0]["timestamp"])
    minutes_axis = [(parse_time(r["timestamp"]) - t0).total_seconds() / 60 for r in telemetry]
    pollution = [r["pollution_percent"] for r in telemetry]
    fan = [r["fan_state"] for r in telemetry]

    by_reason: dict[str, int] = {}
    for e in changes:
        reason = (e.get("details") or {}).get("reason") or e["event_type"]
        by_reason[reason] = by_reason.get(reason, 0) + 1

    span_min = max(minutes_axis) if minutes_axis else 1
    rate = len(changes) / max(span_min / 60, 0.01)
    print(f"  Chống nảy công tắc: {len(changes)} lần đổi trạng thái trong {span_min:.1f} phút "
          f"({rate:.1f} lần/giờ). Phân loại: {by_reason}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5), sharex=True, dpi=200)
    ax1.plot(minutes_axis, pollution, color="#ff7f0e", alpha=0.85, label="Nồng độ ô nhiễm (%)")
    ax1.axhline(40, color="red", linestyle="--", alpha=0.7, label="Ngưỡng bật quạt 40%")
    ax1.axhline(25, color="green", linestyle="--", alpha=0.7, label="Ngưỡng tắt quạt 25%")
    ax1.set_ylabel("Ô nhiễm (%)")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.step(minutes_axis, fan, where="post", color="green", linewidth=2,
             label=f"Trạng thái quạt ({len(changes)} lần đổi, {rate:.1f} lần/giờ)")
    ax2.set_xlabel("Thời gian (phút)")
    ax2.set_ylabel("Quạt")
    ax2.set_yticks([0, 1])
    ax2.set_yticklabels(["TẮT", "BẬT"])
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.3)

    chu_thich = " · ".join(f"{k}: {v}" for k, v in by_reason.items())
    fig.suptitle(f"Bài đo 3: Hiệu quả chống nảy công tắc (dwell 30s + hysteresis)\n{chu_thich}",
                 fontsize=11, fontweight="bold")
    save("chart3_chong_nay_cong_tac.png")


# =====================================================================
#  BÀI 4 - Tỉ lệ mất gói
#  Nguồn: /diagnostics, so sánh cách đếm theo seq với cách ước lượng theo khoảng trống
# =====================================================================
def chart_packet_loss(api: str, minutes: int) -> None:
    d = get(api, "/diagnostics", minutes=minutes)
    by_seq, by_gap = d["packet_loss"], d["time_gap_estimate"]
    if d["samples"] < 10:
        return skip("Tỉ lệ mất gói", f"mới có {d['samples']} bản ghi trong {minutes} phút")

    print(f"  Tỉ lệ mất gói: theo seq {by_seq['loss_percent']}% "
          f"({by_seq['missing']}/{by_seq['expected']}), "
          f"ước lượng theo khoảng trống {by_gap['loss_percent']}%")
    for check in d["contract"]:
        print(f"    - {check['field']:16} {'ĐẠT' if check['ok'] else 'CHƯA ĐẠT'}: {check['detail']}")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4), dpi=200)
    nhan = ["Đếm theo seq\n(chính xác)", "Ước lượng theo\nkhoảng trống"]
    ty_le = [by_seq["loss_percent"] or 0, by_gap["loss_percent"] or 0]
    ax1.bar(nhan, ty_le, color=["#2ca02c", "#ff7f0e"], width=0.5)
    for i, v in enumerate(ty_le):
        ax1.text(i, v, f"{v}%", ha="center", va="bottom", fontweight="bold")
    ax1.set_ylabel("Tỉ lệ mất gói (%)")
    ax1.set_title("So sánh hai phương pháp đo", fontsize=10)
    ax1.grid(True, alpha=0.3, axis="y")

    ax2.bar(["Nhận được", "Mất"], [by_seq["received"], by_seq["missing"]],
            color=["#1f77b4", "#d62728"], width=0.5)
    for i, v in enumerate([by_seq["received"], by_seq["missing"]]):
        ax2.text(i, v, str(v), ha="center", va="bottom", fontweight="bold")
    ax2.set_ylabel("Số bản tin")
    ax2.set_title(f"Trong {minutes} phút gần nhất", fontsize=10)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.suptitle("Bài đo 4: Tỉ lệ mất gói tin đo từ dữ liệu thật", fontsize=12, fontweight="bold")
    save("chart4_ty_le_mat_goi.png")


# =====================================================================
#  ĐỒ THỊ 5 - Sai số mô hình AI trên dữ liệu thật
#  Nguồn: /prediction/history ghép với giá trị thực đo tại đúng thời điểm đã dự báo
# =====================================================================
def chart_ai_accuracy(api: str, hours: int) -> None:
    acc = get(api, "/prediction/accuracy", hours=hours)
    if acc["evaluated"] < 3:
        return skip("Sai số mô hình AI",
                    f"mới đối chiếu được {acc['evaluated']} dự báo. {acc['note']}")

    predictions = get(api, "/prediction/history", minutes=hours * 60, limit=500)
    telemetry = get(api, "/history", limit=5000, order="asc")["items"]
    if not telemetry:
        return skip("Sai số mô hình AI", "chưa có dữ liệu telemetry để đối chiếu")

    mau_thuc = [(parse_time(r["timestamp"]), r["pollution_percent"]) for r in telemetry
                if r["pollution_percent"] is not None]

    cap = []
    for p in predictions:
        target = parse_time(p["target_time"])
        gan_nhat = min(mau_thuc, key=lambda x: abs((x[0] - target).total_seconds()))
        if abs((gan_nhat[0] - target).total_seconds()) <= 30:
            cap.append((target, p["predicted_pollution_percent"], gan_nhat[1]))
    if len(cap) < 3:
        return skip("Sai số mô hình AI", "chưa ghép được dự báo với giá trị thực đo")

    cap.sort()
    du_bao = [c[1] for c in cap]
    thuc_te = [c[2] for c in cap]
    print(f"  Sai số mô hình AI trên dữ liệu thật: MAE {acc['mae']}%, RMSE {acc['rmse']}%, "
          f"độ lệch hệ thống {acc['bias']}%, đoán đúng xu hướng {acc['trend_accuracy_percent']}% "
          f"trên {acc['evaluated']} dự báo")

    plt.figure(figsize=(9, 4), dpi=200)
    plt.plot(range(1, len(cap) + 1), thuc_te, "b-o", markersize=3, label="Thực đo tại thời điểm dự báo")
    plt.plot(range(1, len(cap) + 1), du_bao, "r--s", markersize=3, label="Mô hình dự báo trước 15 phút")
    plt.title(f"Sai số mô hình AI đo trên dữ liệu thật: MAE {acc['mae']}%, RMSE {acc['rmse']}% "
              f"({acc['evaluated']} dự báo)", fontsize=11, fontweight="bold")
    plt.xlabel("Lần dự báo")
    plt.ylabel("Nồng độ ô nhiễm (%)")
    plt.legend(loc="upper right")
    plt.grid(True, alpha=0.3)
    save("chart5_sai_so_mo_hinh_ai.png")


# =====================================================================
def main() -> None:
    p = argparse.ArgumentParser(description="Vẽ đồ thị số liệu đo từ hệ thống đang chạy")
    p.add_argument("--api", default="http://localhost:8000", help="địa chỉ gốc của Backend")
    p.add_argument("--minutes", type=int, default=60, help="khoảng thời gian xét, đơn vị phút")
    p.add_argument("--hours", type=int, default=24, help="khoảng thời gian xét phần AI, đơn vị giờ")
    args = p.parse_args()

    os.makedirs(OUT, exist_ok=True)
    print("=" * 62)
    print("  VẼ ĐỒ THỊ TỪ SỐ LIỆU THẬT CỦA HỆ THỐNG")
    print(f"  Backend: {args.api}")
    print("=" * 62)

    try:
        health = json.loads(urllib.request.urlopen(f"{args.api.rstrip('/')}/health", timeout=15).read())
        print(f"  Trạng thái: CSDL {health['database']} · MQTT {health['mqtt']} · AI {health['ai']}\n")
    except Exception as exc:
        print(f"  Không gọi được Backend tại {args.api}: {exc}")
        return

    for ten_ham, ham, doi_so in [
        ("Bài 1", chart_ingest_latency, (args.api, args.minutes)),
        ("Bài 2", chart_control_rtt, (args.api,)),
        ("Bài 3", chart_fan_switching, (args.api, args.minutes)),
        ("Bài 4", chart_packet_loss, (args.api, args.minutes)),
        ("Đồ thị 5", chart_ai_accuracy, (args.api, args.hours)),
    ]:
        print(f"{ten_ham}:")
        try:
            ham(*doi_so)
        except Exception as exc:
            skip(ten_ham, f"lỗi khi xử lý: {type(exc).__name__}: {exc}")
        print()

    print("=" * 62)
    if SKIPPED:
        print("  CHƯA VẼ ĐƯỢC (thiếu dữ liệu, KHÔNG bịa số để bù):")
        for line in SKIPPED:
            print(f"    - {line}")
    print(f"  Ảnh đã xuất tại thư mục {OUT}/")
    print("=" * 62)


if __name__ == "__main__":
    main()
