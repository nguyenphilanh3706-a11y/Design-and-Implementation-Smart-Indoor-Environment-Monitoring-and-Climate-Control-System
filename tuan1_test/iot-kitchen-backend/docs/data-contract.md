# (DATA CONTRACT) v1.1

**Gửi: TV2 (firmware ESP32) và TV4 (Web Dashboard)** · **Từ: TV3 (Backend)** · Ngày 12/09/2026

Backend đã được viết và kiểm thử theo đúng tài liệu này. Các mục đánh dấu 🆕 là phần bổ sung
so với *Bản thống nhất kỹ thuật*, đã được kiểm chứng bằng thiết bị giả lập trước khi đề xuất.

> Backend **chấp nhận cả payload cũ**: thiếu `seq`, thiếu `config/state`, dùng key `state` thay
> `fan_state` đều không làm hỏng hệ thống. Nhưng thiếu phần nào thì mất đúng phần số liệu tương
> ứng trong báo cáo. Gọi `GET /api/v1/kitchen/diagnostics` là biết còn thiếu gì.

---

## 1. Bảng tổng hợp topic

Tiền tố: `iot/kitchen/{device_id}/` · `device_id` mặc định: `esp32_kitchen_01`

| Topic | Chiều | QoS | Retain | Chu kỳ |
|---|---|---|---|---|
| `telemetry` | ESP32 → Backend | 0 | false | 2 giây |
| `status` | ESP32 → Backend (có LWT) | 1 | **true** | khi kết nối / mất kết nối |
| `actuator/state` 🆕 chuẩn hoá | ESP32 → Backend | 1 | **true** | mỗi khi quạt đổi trạng thái |
| `config/state` 🆕 topic mới | ESP32 → Backend | 1 | **true** | sau khi nhận config/set và lúc khởi động |
| `actuator/set` | Backend → ESP32 | 1 | false | khi người dùng bấm nút |
| `mode/set` | Backend → ESP32 | 1 | **true** | khi đổi chế độ |
| `config/set` | Backend → ESP32 | 1 | **true** | khi đổi ngưỡng |
| `ai_prediction` | Backend → Web | 0 | false | 10 giây (Tuần 2) |

---

## 2. ESP32 → Backend

### 2.1 `telemetry`

```json
{
  "device_id": "esp32_kitchen_01",
  "seq": 1234,
  "timestamp": "2026-09-12T10:00:00.123Z",
  "temperature": 31.5,
  "humidity": 68.2,
  "pollution_percent": 22.4,
  "rs_ro_ratio": 2.91,
  "fan_state": 0,
  "mode": "AUTO",
  "network_status": "CONNECTED"
}
```

Bốn điểm cần lưu ý:

**🆕 `seq`** — số nguyên tăng 1 mỗi bản tin, bắt đầu từ 1 và đặt lại khi thiết bị khởi động lại.
Với TV2 chỉ là thêm một biến đếm, nhưng nó biến Bài test 4 từ *ước lượng* thành *đo đếm*. Hiện tại
không có `seq` thì Backend phải đoán mất gói bằng cách tìm khoảng trống thời gian lớn hơn 5 giây —
cách này bỏ sót mọi trường hợp mất 1-2 gói lẻ. Backend còn dùng `seq` giảm đột ngột để nhận biết
thiết bị vừa khởi động lại.

**`timestamp` phải có mili-giây.** Định dạng ISO 8601 UTC kết thúc bằng `Z`. Nếu chỉ ghi tới giây,
chỉ số "độ tươi dữ liệu" (ngưỡng 3000 ms trong Bản thống nhất mục 5.2) sai số tới ±1 giây và số đo
độ trễ của Bài test 1 mất ý nghĩa. Thiết bị phải đồng bộ NTP trước khi gửi; chưa đồng bộ được thì
cứ gửi, Backend phát hiện lệch quá 24 giờ sẽ tự dùng giờ server và ghi cảnh báo.

**Cảm biến lỗi thì gửi `null`, không gửi `-1`.** Giá trị `-1` lọt vào `avg()` làm sai số liệu báo
cáo và làm bẩn dữ liệu huấn luyện AI của TV5. Backend có lưới an toàn: số đo ngoài khoảng vật lý
(nhiệt độ −40…125 °C, độ ẩm 0…100 %, ô nhiễm 0…100 %, Rs/R0 0…20) sẽ bị đổi thành `null` và ghi sự
kiện `SENSOR_FAULT`, các số đo còn lại trong bản tin vẫn được giữ.

**Giữ cả `rs_ro_ratio` lẫn `pollution_percent`.** `rs_ro_ratio` là giá trị thô sau hiệu chuẩn
(Rs chia R0), `pollution_percent` là giá trị đã quy đổi. Có cả hai thì mới chứng minh được quy trình
burn-in và xác định R0 của TV1 trong báo cáo, thay vì chỉ nói suông.

### 2.2 `status` (kèm Last Will and Testament)

```json
{"device_id": "esp32_kitchen_01", "status": "ONLINE"}
```

LWT đăng ký lúc kết nối với `status: "OFFLINE"`, QoS 1, retain true. Backend ghi sự kiện
`DEVICE_ONLINE` / `DEVICE_OFFLINE` và Dashboard dựa vào đây để hiện huy hiệu trạng thái.

### 2.3 `actuator/state` 🆕 chuẩn hoá payload

```json
{
  "device_id": "esp32_kitchen_01",
  "fan_state": 1,
  "mode": "MANUAL",
  "reason": "MANUAL_COMMAND",
  "timestamp": "2026-09-12T10:00:00.123Z"
}
```

`reason` nhận một trong bốn giá trị:

| Giá trị | Khi nào gửi | Backend làm gì |
|---|---|---|
| `FSM` | Máy trạng thái tự bật/tắt theo ngưỡng | Ghi `FAN_STATE_CHANGED` |
| `MANUAL_COMMAND` | Vừa thực hiện lệnh từ `actuator/set` | Ghi `FAN_STATE_CHANGED`, đo `rtt_ms` |
| `SAFETY_OVERRIDE` | Ô nhiễm ≥ 75 %, cưỡng bức bật quạt | Ghi `SAFETY_OVERRIDE` mức CRITICAL |
| `IGNORED_AUTO_MODE` | Nhận lệnh tay nhưng đang ở AUTO nên bỏ qua | Ghi `COMMAND_IGNORED`, báo cho Web |

Vì sao cần bản tin này dù `telemetry` đã có `fan_state`: telemetry là **lấy mẫu định kỳ** (2 giây,
QoS 0), còn đây là **sự kiện** (QoS 1, retain). Nếu chỉ dựa vào telemetry thì thời điểm quạt đổi
trạng thái sai số tới 2 giây, mất một gói là mất luôn sự kiện, và không bao giờ biết được lý do.
Trường `reason` cho phép tách riêng số lần FSM đổi trạng thái với số lần người bấm tay — đó chính
là con số chứng minh cơ chế chống chattering có tác dụng.

Backend cũng chấp nhận key `state` thay cho `fan_state` để tương thích ngược.

### 2.4 `config/state` 🆕 topic mới

```json
{
  "device_id": "esp32_kitchen_01",
  "pollution_threshold": 40,
  "temp_threshold": 33,
  "dwell_time_seconds": 30,
  "timestamp": "2026-09-12T10:00:00.123Z"
}
```

Gửi lúc khởi động và sau mỗi lần nhận `config/set`, với retain true. Đây là **ngưỡng thiết bị đang
thực sự dùng**, không phải ngưỡng vừa nhận được.

Backend đối chiếu với bảng `device_config` rồi trả kết quả ở `GET /status` qua trường
`config_in_sync`. Không có topic này thì sai tên khoá hay sai đơn vị sẽ trôi qua im lặng: API trả
200, web báo "đã đổi ngưỡng", nhưng quạt vẫn chạy theo ngưỡng cũ và không ai biết cho đến lúc demo.

---

## 3. Backend → ESP32

### 3.1 `actuator/set` (QoS 1, retain false)

```json
{"command": "SET_FAN", "state": 1}
```

ESP32 chỉ thực hiện khi đang ở `MANUAL`. Đang ở `AUTO` thì bỏ qua nhưng **vẫn phải trả lời**
`actuator/state` với `reason: "IGNORED_AUTO_MODE"`. Backend chờ phản hồi tối đa 3 giây để đo
`rtt_ms` — không có phản hồi thì API báo `acknowledged: false`.

### 3.2 `mode/set` (QoS 1, retain true)

```json
{"mode": "AUTO"}
```

### 3.3 `config/set` (QoS 1, retain true)

```json
{"pollution_threshold": 40, "temp_threshold": 33, "dwell_time_seconds": 30}
```

Đơn vị: phần trăm, độ C, **giây**. Web có thể chỉ gửi một trường; các trường không xuất hiện thì
giữ nguyên giá trị cũ, đừng đặt về mặc định. Nhận xong thì phát lại `config/state`.

Nhờ retain true, ESP32 khởi động lại là nhận ngay ngưỡng mới nhất, không quay về giá trị nạp cứng
trong firmware.

---

## 4. Thông tin kết nối

**TV2 — firmware**

```
Broker:     <IP máy chạy Docker>:1883      (xem bằng lệnh ipconfig)
Tài khoản:  esp32_device / <MQTT_DEVICE_PASSWORD trong .env>
client_id:  nên đặt bằng device_id
Được PUBLISH:   telemetry · status · actuator/state · config/state
Được SUBSCRIBE: actuator/set · mode/set · config/set
```

**TV4 — dashboard**

```
REST API:   http://<IP>:8000/api/v1/kitchen     (tài liệu /docs · đặc tả /openapi.json)
WebSocket:  ws://<IP>:9001
Tài khoản:  web_dashboard / <MQTT_WEB_PASSWORD>   (CHỈ ĐỌC)
```

Dashboard **không publish** lệnh điều khiển. Mọi thao tác đi qua `POST /actuator`, `POST /mode`,
`POST /config` để Backend còn kiểm tra dữ liệu và ghi nhật ký. Tài khoản `web_dashboard` cố tình
không có quyền ghi vì mật khẩu của nó nằm lộ trong mã JavaScript.

Gợi ý cho biểu đồ: dùng `GET /history?bucket_seconds=N` — 1 giờ dùng 30, 6 giờ dùng 180, 24 giờ
dùng 60. Server gộp sẵn nên trình duyệt không phải vẽ 43.200 điểm.

Các trường mới mà Dashboard nên hiển thị: `config_in_sync` (cảnh báo khi ngưỡng lệch),
`device_config` (ngưỡng thiết bị đang dùng), và `seq` trong `latest`.

---

## 5. Bảng kiểm cho TV2

Nạp firmware xong, chạy một lệnh này là biết còn thiếu gì:

```
GET http://<IP>:8000/api/v1/kitchen/diagnostics?minutes=10
```

Kết quả trả về `passed: true` khi đủ 5 mục:

- [ ] `seq` — mọi bản tin telemetry đều có, tăng đều
- [ ] `timestamp_ms` — mốc thời gian có phần mili-giây
- [ ] `latency` — độ trễ dương và dưới 5000 ms (âm nghĩa là chưa đồng bộ NTP)
- [ ] `sensor_readings` — không có số đo bị đổi thành null
- [ ] `config_state` — ngưỡng trên thiết bị khớp với CSDL

Endpoint này cũng trả sẵn tỉ lệ mất gói và số liệu độ trễ, dùng thẳng cho Bài test 1 và Bài test 4.

---

## 6. Nếu nhóm muốn đổi gì trong tài liệu này

Báo sớm cho TV3, vì đổi tên trường kéo theo sửa cả `schemas.py`, cột trong CSDL và mã Dashboard.
Riêng `seq` và `config/state`, nếu TV2 không kịp làm trong Tuần 1 thì hệ thống vẫn chạy bình
thường — chỉ là báo cáo sẽ thiếu phần đo mất gói chính xác và phần đối chiếu ngưỡng.
