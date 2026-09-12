# Backend IoT - Giám sát môi trường & tự động thông gió phòng bếp

**Thành viên 3 - Nhiệm vụ Tuần 1**: hạ tầng Docker, CSDL chuỗi thời gian, REST API và dịch vụ cảnh báo Telegram.

Tất cả nội dung dưới đây đã được chạy thử thật (PostgreSQL 16 + TimescaleDB 2.30, Mosquitto 2.1.2,
FastAPI 0.141) trước khi bàn giao - xem số liệu ở [mục 10](#10-kết-quả-đã-kiểm-thử).

---

## 0. Kiến trúc và cấu trúc thư mục

```
   ESP32 (TV1+TV2)                    Backend (TV3 - phần này)                  Web (TV4)
 ┌─────────────────┐   MQTT 1883   ┌──────────────────────────┐              ┌──────────────┐
 │ AHT20 + MQ-135  │ ────────────► │  Mosquitto Broker        │ ◄─ WS 9001 ─ │  Dashboard   │
 │ FSM + quạt 12V  │ ◄──────────── │  (xác thực + ACL)        │              │  (chỉ đọc)   │
 └─────────────────┘   lệnh điều    └───────────┬──────────────┘              └──────┬───────┘
                        khiển                   │ ingest                             │
                                     ┌──────────▼───────────┐   REST 8000             │
                                     │  FastAPI Backend     │ ◄───────────────────────┘
                                     │  - ghi CSDL          │
                                     │  - REST API          │
                                     │  - cảnh báo Telegram │──► 📱 Telegram
                                     └──────────┬───────────┘
                                     ┌──────────▼───────────┐
                                     │ PostgreSQL 16 +      │
                                     │ TimescaleDB 5432     │
                                     └──────────────────────┘
```

```
iot-kitchen-backend/
├── docker-compose.yml          # 3 dịch vụ chính + 1 ESP32 giả lập (tuỳ chọn)
├── .env.example                # mẫu cấu hình -> sao chép thành .env
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py             # khởi động FastAPI, vòng đời ứng dụng
│       ├── api.py              # 8 endpoint REST
│       ├── mqtt_service.py     # nhận telemetry, gửi lệnh, tự kết nối lại
│       ├── alert_service.py    # cảnh báo Telegram + chống spam 2 tầng
│       ├── db.py               # truy vấn TimescaleDB (asyncpg)
│       ├── schemas.py          # kiểm tra payload đúng data contract
│       └── config.py           # đọc biến môi trường
├── database/
│   ├── init/10_schema.sql      # 3 bảng + hypertable + chính sách xoá dữ liệu cũ
│   └── migrations/             # thêm cột cho CSDL đã có dữ liệu, khỏi phải xoá đi làm lại
├── docs/data-contract.md       # HỢP ĐỒNG DỮ LIỆU - gửi file này cho TV2 và TV4
├── mosquitto/config/
│   ├── mosquitto.conf          # 2 listener: MQTT 1883 + WebSocket 9001
│   └── acl.template            # phân quyền theo từng tài khoản
└── tools/
    ├── simulate_esp32.py       # ESP32 giả lập (có kịch bản khói)
    ├── fake_telegram.py        # Telegram giả để demo khi không có mạng
    ├── api.http                # bộ request bấm-là-chạy trong VSCode
    └── queries.sql             # truy vấn kiểm tra CSDL + lấy số liệu báo cáo
```

---

## 1. Chuẩn bị máy (làm 1 lần)

1. **Docker Desktop** phải đang chạy (biểu tượng cá voi ở khay hệ thống màu xanh). Kiểm tra bằng PowerShell:

   ```powershell
   docker version
   docker compose version
   ```

2. **Kiểm tra 4 cổng còn trống** - đây là nguyên nhân lỗi số 1 khi chạy lần đầu:

   ```powershell
   netstat -ano | findstr ":1883 :9001 :8000 :5432"
   ```

   Không ra dòng nào là tốt. Nếu máy đã cài sẵn PostgreSQL (chiếm 5432) thì mở `.env` đổi
   `PG_PORT_HOST=5433` rồi dùng cổng 5433 khi kết nối từ Windows.

3. **Đặt dự án ở đường dẫn không dấu**, ví dụ `C:\iot\iot-kitchen-backend`. Thư mục có dấu
   tiếng Việt đôi khi làm Docker Desktop mount sai.

4. **Mở thư mục trong VSCode** (`File → Open Folder`). VSCode sẽ hỏi cài 4 extension gợi ý:
   Container Tools, Python, REST Client, PostgreSQL. Bấm *Install All*.

---

## 2. Khởi động lần đầu

```powershell
cd C:\iot\iot-kitchen-backend
Copy-Item .env.example .env      # rồi mở .env sửa mật khẩu
docker compose up -d --build     # lần đầu mất 2-4 phút để tải image và build
docker compose ps
```

Kết quả mong đợi: 3 container `kitchen-mosquitto`, `kitchen-timescaledb`, `kitchen-backend`
ở trạng thái `Up ... (healthy)`. Nếu `kitchen-backend` còn `starting` thì chờ thêm 30 giây -
nó đợi CSDL sẵn sàng trước.

```powershell
docker compose logs -f backend   # Ctrl+C để thoát, container vẫn chạy
```

Dòng cần thấy trong log:

```
db      | Đã kết nối TimescaleDB timescaledb:5432/iot_kitchen
mqtt    | Đã kết nối MQTT broker mosquitto:1883 (user=backend_svc)
main    | Backend sẵn sàng - Swagger UI tại http://localhost:8000/docs
```

**Bật ESP32 giả lập** (làm ngay bây giờ nếu phần cứng của TV1/TV2 chưa xong):

```powershell
docker compose --profile sim up -d simulator
docker compose logs -f simulator
```

---

## 3. Kiểm tra lớp 1 - Broker MQTT

### 3.1 Nghe dữ liệu đang chảy qua Broker

```powershell
docker compose exec mosquitto mosquitto_sub -h localhost -u backend_svc -P 'Backend2026aA' -t 'iot/kitchen/#' -v
```

Cứ 2 giây phải có 1 dòng telemetry. Đây là bằng chứng "ESP32 → Broker" đã thông.
(Thay `Backend2026aA` bằng mật khẩu trong `.env` của bạn.)

### 3.2 Gửi tay một bản tin như ESP32 thật

Mở cửa sổ PowerShell thứ hai (giữ cửa sổ subscribe ở trên):

```powershell
$ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
$payload = "{`"device_id`":`"esp32_kitchen_01`",`"seq`":1,`"timestamp`":`"$ts`",`"temperature`":31.5,`"humidity`":68,`"pollution_percent`":22.4,`"rs_ro_ratio`":2.9,`"fan_state`":0,`"mode`":`"AUTO`",`"network_status`":`"CONNECTED`"}"
docker compose exec mosquitto mosquitto_pub -h localhost -u esp32_device -P 'Esp32Dev2026aA' `
  -t 'iot/kitchen/esp32_kitchen_01/telemetry' -m $payload
```

Bản tin phải hiện ở cửa sổ subscribe **và** được ghi vào CSDL (kiểm tra ở mục 4).

> Dòng đầu sinh mốc thời gian ngay lúc chạy. Đừng chép cứng một chuỗi thời gian vào đây:
> nếu chuỗi đó nằm ở tương lai, bản ghi ấy sẽ luôn đứng đầu khi sắp xếp theo thời gian và che
> mất dữ liệu thật. Backend chặn sẵn trường hợp này (lệch về tương lai quá 60 giây thì dùng giờ
> server và ghi sự kiện `CLOCK_SKEW`), nhưng cứ sinh đúng giờ vẫn hơn.

### 3.3 Kiểm tra xác thực - sai mật khẩu phải bị từ chối

```powershell
docker compose exec mosquitto mosquitto_pub -h localhost -u esp32_device -P 'mat-khau-sai' -t 'iot/kitchen/esp32_kitchen_01/telemetry' -m 'test'
```

Kết quả đúng: `Connection Refused: not authorised.`

### 3.4 Kiểm tra ACL - đúng mật khẩu nhưng sai quyền

Tài khoản `web_dashboard` chỉ được đọc. Thử cho nó gửi lệnh bật quạt:

```powershell
docker compose exec mosquitto mosquitto_pub -h localhost -u web_dashboard -P 'WebDash2026aA' -V 5 -q 1 -d `
  -t 'iot/kitchen/esp32_kitchen_01/actuator/set' -m '{\"command\":\"SET_FAN\",\"state\":1}'
```

Kết quả đúng: `Warning: Publish 1 failed: Not authorized.` kèm `PUBACK (Mid: 1, RC:135)`.

> Hai điểm đáng đưa vào báo cáo:
> - Phải thêm `-V 5` (MQTT v5) mới thấy lý do từ chối. Với MQTT 3.1.1, Broker **im lặng bỏ bản tin**,
>   client tưởng đã gửi thành công - đây là lý do nên dùng MQTT v5 khi gỡ lỗi.
> - Broker chỉ ghi dòng `Denied PUBLISH` ở mức log debug. Muốn xem, bỏ dấu `#` ở dòng
>   `log_type debug` trong `mosquitto/config/mosquitto.conf` rồi `docker compose restart mosquitto`.

### 3.5 Kiểm tra LWT - mất kết nối đột ngột

```powershell
docker compose kill simulator          # mô phỏng rút điện, không phải tắt êm
docker compose logs --tail 5 backend
```

Backend phải ghi `Thiết bị esp32_kitchen_01 -> OFFLINE` trong khoảng 1 giây, và
`GET /status` trả `device_status: "OFFLINE"`. Bật lại: `docker compose --profile sim up -d simulator`.

### 3.6 Kiểm tra cổng WebSocket 9001 (TV4 cần cổng này)

```powershell
docker compose exec backend python -c "import paho.mqtt.client as m,time; c=m.Client(m.CallbackAPIVersion.VERSION2,transport='websockets'); c.username_pw_set('web_dashboard','WebDash2026aA'); c.on_connect=lambda cl,u,f,rc,p:(print('WebSocket rc =',rc),cl.subscribe('iot/kitchen/#')); c.on_message=lambda cl,u,msg:print('nhận:',msg.topic); c.connect('mosquitto',9001,30); c.loop_start(); time.sleep(5)"
```

Kết quả đúng: `WebSocket rc = Success` rồi liên tục in `nhận: iot/kitchen/...`.
Báo cho TV4: dùng `ws://localhost:9001`, tài khoản `web_dashboard` (chỉ đọc).

---

## 4. Kiểm tra lớp 2 - CSDL chuỗi thời gian

```powershell
docker compose exec timescaledb psql -U iot_admin -d iot_kitchen
```

Trong psql, gõ lần lượt:

```sql
\dx                                     -- phải thấy timescaledb 2.x
\d telemetry                            -- xem cấu trúc bảng
SELECT * FROM timescaledb_information.hypertables;    -- telemetry phải là hypertable
SELECT count(*) FROM telemetry;         -- chạy 2 lần cách nhau 10 giây, số phải tăng ~5
SELECT time, temperature, pollution_percent, fan_state FROM telemetry ORDER BY time DESC LIMIT 5;
\q                                      -- thoát
```

Chạy cả bộ truy vấn kiểm tra (gồm cả số liệu cho 4 bài test của TV5):

```powershell
Get-Content tools\queries.sql | docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen
```

Ba thứ cần xác nhận:

| Kiểm tra | Câu lệnh | Kết quả đúng |
|---|---|---|
| Hypertable | `SELECT * FROM timescaledb_information.hypertables;` | `telemetry`, `num_chunks` ≥ 1 |
| Xoá dữ liệu cũ sau 90 ngày | `SELECT * FROM timescaledb_information.jobs WHERE proc_name='policy_retention';` | có 1 job |
| Độ trễ ghi dữ liệu | mục 3 trong `queries.sql` | vài mili-giây |

> **Lưu ý quan trọng**: file `database/init/10_schema.sql` **chỉ chạy lần đầu**, khi volume dữ liệu
> còn trống. Sửa schema xong phải `docker compose down -v` rồi `docker compose up -d` (lệnh này
> **xoá sạch dữ liệu**). Đây là cái bẫy hay gặp nhất khi làm việc với image PostgreSQL.

Muốn xem bằng giao diện: DBeaver hoặc pgAdmin, kết nối `localhost:5432`, database `iot_kitchen`,
user `iot_admin`, mật khẩu trong `.env`.

---

## 5. Kiểm tra lớp 3 - REST API

### 5.1 Swagger UI (cách nhanh nhất)

Mở trình duyệt: **http://localhost:8000/docs**

Mỗi endpoint đều có nút *Try it out → Execute* để gửi request thật ngay trên trang.
Thứ tự nên thử:

1. `GET /health` → `database: connected`, `mqtt: connected`
2. `GET /api/v1/kitchen/status` → xem `data_freshness_ms` và `freshness_level`
3. `POST /api/v1/kitchen/mode` với `{"mode": "MANUAL"}`
4. `POST /api/v1/kitchen/actuator` với `{"state": 1}` → xem `rtt_ms` (số liệu Bài test 2)
5. `GET /api/v1/kitchen/history?limit=20&order=desc`
6. `POST /api/v1/kitchen/config` với `{"pollution_threshold": 35}`

### 5.2 Trong VSCode (không cần Postman)

Mở `tools/api.http`, bấm **Send Request** phía trên từng request. File này có sẵn 23 request,
gồm cả 4 trường hợp lỗi để chứng minh API kiểm soát đầu vào (trả 422 kèm mô tả).

### 5.3 Nếu bắt buộc dùng Postman

`Import → Link →` dán `http://localhost:8000/openapi.json`. Postman tự sinh toàn bộ collection.

### 5.4 Ý nghĩa các endpoint (để trả lời giảng viên)

| Endpoint | Dùng để làm gì | Điểm kỹ thuật đáng nói |
|---|---|---|
| `GET /status` | Widget chính của Dashboard | Tính `data_freshness_ms` = giờ server − timestamp bản tin, phân loại FRESH/DELAYED/STALE theo mục 5.2 Bản thống nhất |
| `GET /history` | Vẽ biểu đồ | Có `bucket_seconds` dùng `time_bucket()` của TimescaleDB: 24 giờ dữ liệu 2 giây/mẫu = 43.200 điểm, gộp theo phút còn 1.440 điểm |
| `POST /actuator` | Bật/tắt quạt tay | Gửi QoS 1 rồi **chờ ESP32 phản hồi** trên `actuator/state` tối đa 3 giây → đo được `rtt_ms` |
| `POST /mode` | Đổi AUTO/MANUAL | Publish **retain = true** để ESP32 khởi động lại vẫn nhận đúng chế độ |
| `POST /config` | Đổi ngưỡng | Lưu CSDL trước (còn nguyên sau khi tắt máy) rồi mới đẩy xuống thiết bị |
| `GET /diagnostics` | Chấm điểm chất lượng dữ liệu | Trả tỉ lệ mất gói theo `seq`, độ trễ, và bảng đối chiếu data contract - TV2 gọi 1 lần là biết firmware còn thiếu gì |

---

### 5.5 Chấm điểm firmware bằng một lệnh

```powershell
curl.exe "http://localhost:8000/api/v1/kitchen/diagnostics?minutes=10"
```

Endpoint này trả về `passed: true/false` kèm 5 mục kiểm tra: có `seq` chưa, mốc thời gian có
mili-giây chưa, độ trễ có hợp lý không, có số đo nào bị hỏng không, ngưỡng trên thiết bị có khớp
CSDL không. Gửi nguyên kết quả này cho TV2 là bạn khỏi phải giải thích dài dòng.

Kèm theo đó là số liệu dùng thẳng cho báo cáo: tỉ lệ mất gói tính **chính xác** theo `seq`
(`packet_loss`) đặt cạnh cách **ước lượng cũ** theo khoảng trống thời gian (`time_gap_estimate`),
và độ trễ trung bình / lớn nhất / phân vị 95.

---

## 6. Kiểm tra lớp 4 - Cảnh báo Telegram

### 6.1 Tạo bot (5 phút)

1. Mở Telegram, tìm **@BotFather**, gửi `/newbot`.
2. Đặt tên hiển thị, rồi đặt username kết thúc bằng `bot` (ví dụ `iot_kitchen_hcmute_bot`).
3. BotFather trả về **token** dạng `8123456789:AAF...` → dán vào `TELEGRAM_BOT_TOKEN` trong `.env`.
4. **Nhắn một tin bất kỳ cho bot vừa tạo** (bắt buộc - Telegram không cho bot nhắn trước).
5. Mở trình duyệt: `https://api.telegram.org/bot<TOKEN>/getUpdates`, tìm `"chat":{"id":123456789`
   → dán số đó vào `TELEGRAM_CHAT_ID`.
6. Nạp lại cấu hình:

   ```powershell
   docker compose up -d --force-recreate backend
   ```

> Muốn cả nhóm cùng nhận cảnh báo: tạo một nhóm Telegram, thêm bot vào, nhắn 1 tin trong nhóm
> rồi lấy `chat.id` (số âm, ví dụ `-1001234567890`).

### 6.2 Gửi thử

```powershell
curl.exe -X POST http://localhost:8000/api/v1/kitchen/alerts/test
```

Kết quả đúng: `{"sent": true, ...}` và điện thoại nhận được tin. Nếu `sent: false`, phần `detail`
nói rõ lý do (token sai, chat_id sai, chưa nhắn cho bot).

### 6.3 Thử cảnh báo thật bằng kịch bản khói

```powershell
docker compose down simulator
$env:SIM_SCENARIO="smoke"; docker compose --profile sim up -d simulator
docker compose logs -f simulator
```

Chu kỳ 3 phút: 20 giây sạch → khói tăng dần → vượt 50% (**Telegram báo khẩn cấp**) → vượt 75%
(**ESP32 cưỡng bức bật quạt**) → giảm về mức an toàn (**Telegram báo đã an toàn**).

Cách nhanh hơn, không cần chờ: hạ ngưỡng xuống dưới mức hiện tại.

```powershell
curl.exe -X POST http://localhost:8000/api/v1/kitchen/config -H "Content-Type: application/json" -d "{\"alert_pollution_threshold\": 10}"
```

Nhớ trả về 50 sau khi thử xong.

### 6.4 Chứng minh cơ chế chống spam

Dữ liệu về mỗi 2 giây. Nếu ô nhiễm giữ ở 60% trong 1 phút mà gửi mỗi bản tin thì bạn nhận **30 tin**.
Hệ thống này chỉ gửi **1 tin**. Kiểm chứng:

```powershell
curl.exe "http://localhost:8000/api/v1/kitchen/events?limit=20&event_type=ALERT_SENT"
```

Bốn tầng bảo vệ:

| Cơ chế | Cách hoạt động | Tham số trong `.env` |
|---|---|---|
| Chỉ báo khi mới vượt ngưỡng | Chỉ gửi ở thời điểm *chuyển* từ bình thường sang vượt ngưỡng | — |
| Dải trễ (hysteresis) | Chỉ coi là hết cảnh báo khi xuống dưới 45% (= 50 − 5) | cố định 5% |
| Cooldown | 2 cảnh báo mới cùng loại cách nhau ≥ 60 giây | `ALERT_COOLDOWN_SECONDS` |
| Nhắc lại | Vượt ngưỡng kéo dài thì 5 phút nhắc 1 lần | `ALERT_REMINDER_SECONDS` |
| Trần tốc độ | Tối đa 20 tin/phút cho toàn hệ thống, hàng đợi 50 tin | `TELEGRAM_MAX_PER_MINUTE` |

Ngoài ra khi Telegram trả lỗi 429 (quá tải), Backend đọc `retry_after` rồi chờ đúng số giây đó và gửi lại.

### 6.5 Không có mạng / không muốn lộ token khi quay demo

```powershell
python tools\fake_telegram.py        # cửa sổ riêng, in tin nhắn ra màn hình
```

Trong `.env` đặt `TELEGRAM_BOT_TOKEN=123:FAKE`, `TELEGRAM_CHAT_ID=999`,
`TELEGRAM_API_BASE=http://host.docker.internal:8099` rồi `docker compose up -d --force-recreate backend`.

---

## 7. Kịch bản demo 5 phút (dùng khi bảo vệ)

| Phút | Thao tác | Điều cần chỉ cho giảng viên |
|---|---|---|
| 0:00 | `docker compose ps` | 3 dịch vụ đều *healthy*, không có bước cài đặt thủ công |
| 0:30 | Mở `/docs` | API tự sinh tài liệu theo chuẩn OpenAPI |
| 1:00 | `GET /status` | `data_freshness_ms` nhỏ hơn 3000 → dữ liệu tươi |
| 1:30 | `POST /mode` + `POST /actuator` | `rtt_ms` chứng minh đo được độ trễ điều khiển thật |
| 2:30 | Đốt khói (hoặc kịch bản smoke) | Quạt tự bật khi vượt 40%, Telegram báo khi vượt 50% |
| 3:30 | Rút điện ESP32 | LWT báo OFFLINE, Dashboard chuyển sang Stale |
| 4:00 | `queries.sql` mục 3 và 4 | Số liệu độ trễ và tỷ lệ mất gói lấy thẳng từ CSDL |

---

## 8. Lỗi thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `bind: address already in use` | Cổng đã bị chương trình khác chiếm | Đổi `*_PORT_HOST` trong `.env`, hoặc `netstat -ano \| findstr :5432` rồi tắt tiến trình đó |
| `kitchen-backend` khởi động lại liên tục | Sai mật khẩu CSDL, hoặc CSDL chưa sẵn sàng | `docker compose logs backend`; nếu vừa đổi `POSTGRES_PASSWORD` thì phải `docker compose down -v` vì mật khẩu chỉ được đặt lúc tạo volume |
| Sửa `10_schema.sql` mà không thấy đổi | File init chỉ chạy khi volume trống | `docker compose down -v && docker compose up -d` |
| `column "seq" does not exist` | CSDL tạo từ bản schema cũ | Chạy `database/migrations/2026-09-12_add_seq.sql`, hoặc `docker compose down -v` nếu dữ liệu chưa cần giữ |
| `mqtt: disconnected` trong `/health` | Sai `MQTT_BACKEND_PASSWORD` giữa Broker và Backend | Hai biến phải cùng giá trị trong `.env`; `docker compose up -d --force-recreate` |
| Container mosquitto chết ngay | File cấu hình sai cú pháp | `docker compose logs mosquitto` xem dòng `Error: Unable to open...` |
| Telegram `sent: false` | Chưa nhắn cho bot trước, hoặc sai chat_id | Xem `detail` trong response, làm lại mục 6.1 bước 4 |
| Dashboard báo lỗi CORS | Origin chưa được phép | Đặt `CORS_ORIGINS=http://localhost:5173` trong `.env` |
| `docker compose exec` báo không tìm thấy container | Dịch vụ chưa chạy | `docker compose ps` rồi `docker compose up -d` |
| `/status` hiện dữ liệu cũ, `seq` là `null`, độ tươi luôn bằng 0 | Có bản ghi mang mốc thời gian ở tương lai (thường do chép cứng chuỗi timestamp lúc test tay) | `DELETE FROM telemetry WHERE time > now();` rồi kiểm tra `GET /events?event_type=CLOCK_SKEW` |
| Dữ liệu có nhưng `/status` trả `NO_DATA` | Sai `DEVICE_ID` giữa firmware và `.env` | Xem topic thực tế bằng `mosquitto_sub -t 'iot/kitchen/#' -v` |

Lệnh hay dùng:

```powershell
docker compose logs -f backend        # theo dõi log
docker compose restart backend        # khởi động lại 1 dịch vụ
docker compose down                   # dừng, GIỮ dữ liệu
docker compose down -v                # dừng và XOÁ dữ liệu
docker compose up -d --build          # build lại sau khi sửa code
```

---

## 9. Bàn giao cho TV2 và TV4

Toàn bộ quy ước nằm trong **[`docs/data-contract.md`](docs/data-contract.md)** - gửi thẳng file đó
cho TV2 và TV4. Backend đã được viết và kiểm thử theo đúng tài liệu này, nên hai bạn code theo là khớp.

Tóm tắt phần bổ sung so với Bản thống nhất:

| Bổ sung | Nội dung | Không có thì mất gì |
|---|---|---|
| `actuator/state` có `reason` | `FSM` · `MANUAL_COMMAND` · `SAFETY_OVERRIDE` · `IGNORED_AUTO_MODE` | Không tách được số lần FSM đổi trạng thái với số lần bấm tay, mất luôn con số chứng minh chống chattering |
| `config/state` (topic mới) | Thiết bị phát lại ngưỡng nó đang thực sự dùng, retain true | Sai tên khoá hay sai đơn vị sẽ trôi qua im lặng, đến lúc demo mới lộ |
| `seq` trong telemetry | Số đếm tăng 1 mỗi bản tin | Bài test 4 chỉ còn là ước lượng theo khoảng trống thời gian, bỏ sót mọi lần mất 1-2 gói lẻ |
| `timestamp` có mili-giây | `2026-09-12T10:00:00.123Z` | Độ tươi dữ liệu và Bài test 1 sai số tới ±1 giây |
| Cảm biến lỗi gửi `null` | Không gửi `-1` | `-1` lọt vào trung bình, làm sai báo cáo và bẩn dữ liệu huấn luyện AI của TV5 |

Backend vẫn chạy bình thường nếu firmware chưa có các phần này, chỉ là thiếu số liệu tương ứng.
Riêng số đo phi vật lý thì Backend có lưới an toàn: tự đổi thành `null`, giữ lại các số đo còn tốt
trong cùng bản tin, và ghi sự kiện `SENSOR_FAULT`.

**Cách TV2 tự kiểm tra**: nạp firmware xong gọi `GET /api/v1/kitchen/diagnostics?minutes=10`.
Trả về `passed: true` là đã đúng quy ước; chưa đúng thì mục nào sai sẽ ghi rõ lý do.

**Thông tin kết nối**

```
TV2  Broker <IP>:1883 · esp32_device / <MQTT_DEVICE_PASSWORD> · client_id đặt bằng device_id
TV4  REST http://<IP>:8000/api/v1/kitchen · WebSocket ws://<IP>:9001
     web_dashboard / <MQTT_WEB_PASSWORD> (CHỈ ĐỌC)
```

Dashboard không publish lệnh điều khiển mà gọi `POST /actuator`, `POST /mode`, `POST /config`,
để Backend còn kiểm tra dữ liệu và ghi nhật ký. Vẽ biểu đồ thì dùng `bucket_seconds`:
1 giờ → 30, 6 giờ → 180, 24 giờ → 60. Hai trường mới nên hiện lên giao diện là `config_in_sync`
(cảnh báo khi ngưỡng thiết bị lệch với CSDL) và `device_config`.

---

## 10. Kết quả đã kiểm thử

Chạy thật với PostgreSQL 16 + TimescaleDB 2.30.0, Mosquitto 2.1.2, FastAPI 0.141.1:

| Hạng mục | Kết quả |
|---|---|
| Schema CSDL | Tạo thành công, chạy lại lần 2 không lỗi (idempotent) |
| Độ trễ từ lúc đo đến lúc lưu | trung bình **1,4 ms**, lớn nhất **2,6 ms** |
| Thời gian khứ hồi lệnh bật quạt | **1,6 - 2,2 ms** (Backend → Broker → thiết bị → Backend) |
| REST API | 9 endpoint trả đúng; `state: 5` bị chặn với mã 422 |
| Cảnh báo | Bắn đúng ở 50,9%; cưỡng bức bật quạt ở 75,4% |
| Chống spam | Dãy 52→60→58% chỉ gửi **1 tin** thay vì 29 tin |
| Dải trễ | Giảm về 47% (vẫn trên 45) → không gửi thêm tin nào |
| Lỗi 429 của Telegram | Tự chờ theo `retry_after` rồi gửi lại thành công |
| LWT | `kill -9` thiết bị → `DEVICE_OFFLINE` sau ~1 giây |
| Phân loại độ tươi | Sau 11 giây không có dữ liệu → `STALE` (15.972 ms) |
| ACL | `web_dashboard` publish bị từ chối (`RC:135 Not authorized`) |
| WebSocket 9001 | Kết nối và nhận bản tin thành công |
| Đo mất gói theo `seq` | Thiết bị cố tình bỏ 7/40 bản tin → API báo đúng **7 gói, 17,5%** |
| Đối chiếu ngưỡng | Sửa lén ngưỡng trong CSDL → `config_in_sync` chuyển **False**, ghi `CONFIG_MISMATCH` |
| Lọc số đo hỏng | Gửi `humidity: -1` → lưu thành `null`, nhiệt độ cùng bản tin vẫn giữ nguyên |
| Bỏ qua lệnh khi AUTO | Trả `reason: IGNORED_AUTO_MODE`, ghi `COMMAND_IGNORED` |
| Đồng hồ thiết bị sai | Gửi timestamp ở tương lai 4 giờ → lưu bằng giờ server, ghi `CLOCK_SKEW`, dữ liệu thật không bị che |

---

## 11. Checklist nộp Tuần 1

- [x] `docker-compose.yml` chạy trơn tru bằng một lệnh duy nhất
- [x] Mosquitto 2 listener (1883 + 9001) có xác thực và ACL
- [x] PostgreSQL + TimescaleDB: bảng telemetry (hypertable), nhật ký sự kiện, cấu hình ngưỡng
- [x] Dịch vụ nền ingest MQTT → CSDL, tự kết nối lại khi rớt mạng
- [x] REST API chuẩn OpenAPI/Swagger dùng được qua trình duyệt và Postman
- [x] Bot Telegram cảnh báo khi ô nhiễm ≥ 50% hoặc nhiệt độ > 40°C
- [x] Chống spam tin nhắn
- [ ] Chụp màn hình Swagger + tin nhắn Telegram để đưa vào báo cáo
- [ ] Gửi `docs/data-contract.md` cho TV2 và TV4
