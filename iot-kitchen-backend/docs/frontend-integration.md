# HƯỚNG DẪN NỐI WEB DASHBOARD VÀO BACKEND

Gửi TV4. Trang mẫu chạy được ngay: mở `tools/dashboard-demo.html` bằng trình duyệt.
Chép phần JavaScript trong đó sang dự án React/Vue là xong.

---

## 1. Hai đường nối, dùng cho hai việc khác nhau

| | MQTT over WebSocket | REST API |
|---|---|---|
| Địa chỉ | `ws://localhost:9001` | `http://localhost:8000/api/v1/kitchen` |
| Dùng cho | Dữ liệu tức thời 2 giây/lần, trạng thái ONLINE/OFFLINE | Lịch sử vẽ biểu đồ, điều khiển, cấu hình |
| Cơ chế | Broker đẩy xuống, không phải hỏi | Trình duyệt hỏi thì server trả |
| Tài khoản | `web_dashboard` / `<MQTT_WEB_PASSWORD>` | không cần |

Vì sao phải dùng cả hai. Nếu chỉ dùng REST và gọi `/status` mỗi giây thì trình duyệt tạo 3600
request mỗi giờ, dữ liệu vẫn trễ tới 1 giây, và không bao giờ bắt kịp chu kỳ 2 giây của cảm biến.
Nếu chỉ dùng WebSocket thì không lấy được dữ liệu quá khứ, vì MQTT chỉ đẩy bản tin mới.

**Điều khiển bắt buộc đi qua REST**, không publish thẳng lên MQTT. Tài khoản `web_dashboard` bị ACL
chặn quyền ghi, publish sẽ nhận `Not authorized`. Đây là cố ý: mật khẩu nằm lộ trong mã JavaScript
của trình duyệt nên không thể cho quyền điều khiển. Đi qua backend thì mọi thao tác mới được ghi
vào bảng `system_events` và được kiểm tra dữ liệu đầu vào.

---

## 2. Nối MQTT over WebSocket

Cài thư viện: `npm install mqtt`

```js
import mqtt from "mqtt";

const DEVICE = "esp32_kitchen_01";
const TOPIC  = `iot/kitchen/${DEVICE}`;

const client = mqtt.connect("ws://localhost:9001", {
  username: "web_dashboard",
  password: "WebDash2026aA",
  reconnectPeriod: 2000,        // tự nối lại sau 2 giây khi rớt mạng
});

client.on("connect", () => client.subscribe(`${TOPIC}/#`));

client.on("message", (topic, buf) => {
  const data = JSON.parse(buf.toString());
  const kind = topic.slice(TOPIC.length + 1);

  if (kind === "telemetry")            setSensors(data);   // 2 giây/lần
  else if (kind === "status")          setOnline(data.status === "ONLINE");
  else if (kind === "actuator/state")  setFan(data.fan_state, data.reason);
  else if (kind === "config/state")    setThresholds(data);
});
```

Đăng ký `${TOPIC}/#` là nhận hết. Nhờ các bản tin retain, vừa kết nối xong đã có ngay trạng thái
thiết bị, chế độ và ngưỡng gần nhất mà không phải chờ 2 giây.

---

## 3. Gọi REST API

```js
const API = "http://localhost:8000/api/v1/kitchen";

// Lịch sử vẽ biểu đồ - bucket_seconds để server gộp sẵn, trình duyệt khỏi vẽ 43.200 điểm
//   1 giờ -> 30 · 6 giờ -> 180 · 24 giờ -> 60
const { items } = await (await fetch(`${API}/history?limit=200&bucket_seconds=30&order=asc`)).json();

// Điều khiển
await fetch(`${API}/mode`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ mode: "MANUAL" }),
});

const r = await (await fetch(`${API}/actuator`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ state: 1 }),
})).json();
// r.confirmed = thiết bị đã xác nhận · r.rtt_ms = thời gian khứ hồi
// r.warning   = lời nhắc khi thiết bị đang ở AUTO nên sẽ bỏ qua lệnh tay
```

Bảng đầy đủ ở `http://localhost:8000/docs`, đặc tả máy đọc được ở `/openapi.json`.

---

## 4. Độ tươi dữ liệu - chỗ dễ làm sai nhất

Bản thống nhất mục 5.2: dưới 3000 ms xanh, 3000 đến 10000 ms vàng, trên 10000 ms đỏ.

```js
// ĐÚNG: đồng hồ chạy liên tục, độc lập với việc có nhận được bản tin hay không
setInterval(() => {
  if (!lastTimestamp) return setBadge("grey", "Chưa có dữ liệu");
  const ms = Date.now() - new Date(lastTimestamp).getTime();
  setBadge(ms < 3000 ? "green" : ms <= 10000 ? "yellow" : "red", `${ms} ms`);
}, 500);
```

Nếu chỉ cập nhật huy hiệu ở trong hàm `on("message")` thì lúc mất kết nối sẽ không có bản tin nào
về, hàm không chạy, và huy hiệu đứng im ở màu xanh mãi mãi. Đúng lúc giảng viên rút dây ESP32 thì
dashboard vẫn báo bình thường.

Khi chưa nhận được bản tin nào thì hiện trạng thái thứ tư, màu xám. Không để mặc định màu xanh.

---

## 5. Cấu hình CORS

Trình duyệt chặn request sang cổng khác nếu server không cho phép. Mở `.env` của backend:

```
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

```powershell
docker compose up -d --force-recreate backend
```

Trong lúc phát triển có thể để `CORS_ORIGINS=*`, nhưng khi nộp bài nên ghi rõ địa chỉ.

MQTT WebSocket không dính CORS nên không cần cấu hình gì thêm.

---

## 6. Chạy dashboard cùng hệ thống

Cách đơn giản nhất cho Tuần 1: backend chạy trong Docker, dashboard chạy bằng `npm run dev` trên
Windows như bình thường. Hai bên nói chuyện qua `localhost`.

Muốn xem dashboard bằng điện thoại trong cùng Wi-Fi thì thay `localhost` bằng địa chỉ IP của máy
(xem bằng `ipconfig`), cho cả `API` lẫn `WS_URL`, và thêm địa chỉ đó vào `CORS_ORIGINS`.

---

## 7. Lỗi hay gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `blocked by CORS policy` trong Console | Origin chưa được phép | Thêm địa chỉ vào `CORS_ORIGINS` rồi tạo lại container backend |
| WebSocket nối rồi rớt ngay | Sai mật khẩu `web_dashboard` | Đối chiếu với `MQTT_WEB_PASSWORD` trong `.env` |
| Publish bị `Not authorized` | Dashboard publish thẳng lên MQTT | Chuyển sang gọi REST API, đúng thiết kế |
| `Failed to fetch` | Backend chưa chạy, hoặc sai cổng | `docker compose ps`, mở thử `localhost:8000/health` |
| Nhận được bản tin nhưng giao diện không đổi | Payload là Buffer, chưa chuyển chuỗi | `JSON.parse(buf.toString())` |
| Mở bằng `file://` mà WebSocket lỗi | Một số trình duyệt chặn | Chạy qua `npm run dev` hoặc `python -m http.server` |
| Trang https gọi `ws://` bị chặn | Mixed content | Dùng http khi phát triển, hoặc dựng TLS cho broker |

---

## 8. Các trường nên hiển thị

| Trường | Lấy từ | Hiển thị thế nào |
|---|---|---|
| `temperature`, `humidity`, `pollution_percent` | telemetry | Thẻ số lớn |
| `rs_ro_ratio` | telemetry | Dòng chữ nhỏ dưới thẻ ô nhiễm - bằng chứng hiệu chuẩn MQ-135 của TV1 |
| `pollution_percent` | telemetry | Đổi màu: dưới 25 xanh · 25-39 vàng · từ 40 cam · từ 50 đỏ |
| `status` | status (LWT) | Huy hiệu ONLINE/OFFLINE - khác với huy hiệu kết nối của chính trình duyệt |
| `reason` | actuator/state | Dòng chữ dưới thẻ quạt: FSM, MANUAL_COMMAND hay SAFETY_OVERRIDE |
| `active_alerts` | `GET /status` | Băng cảnh báo đỏ ở đầu trang |
| `config_in_sync` | `GET /status` | Cảnh báo khi ngưỡng trên thiết bị lệch với CSDL |
| `GET /events?limit=10` | REST | Bảng nhật ký sự kiện |

Biểu đồ nên dùng hai trục y: nhiệt độ quanh 30 còn ô nhiễm chạy 0 đến 100, để chung một trục thì
đường nhiệt độ bị bẹp thành đường thẳng.
