# CÔNG KHAI RA INTERNET - điều khiển quạt từ ngoài mạng LAN

Mục tiêu: TV4 mở dashboard từ bất kỳ đâu, gạt AUTO/MANUAL và bật tắt quạt được, trong khi ESP32 và
máy chạy Docker vẫn nằm trong mạng nhà.

---

## 1. Hai rào cản bắt buộc phải qua

**Trình duyệt chặn nội dung hỗn hợp.** Vercel, Netlify, GitHub Pages đều phục vụ trang qua `https`.
Trang `https` gọi `http://` hoặc `ws://` sẽ bị Chrome chặn thẳng, không có cách bỏ qua. Nghĩa là
không thể đưa IP nội bộ kèm cổng 8000 cho TV4 dùng.

**Endpoint ghi phải có khoá.** Mở `POST /actuator` ra Internet mà không xác thực thì bất kỳ ai biết
địa chỉ đều bật được quạt trong bếp nhà bạn. Bot quét cổng tìm ra một dịch vụ mở trong vòng vài giờ.

Phần khoá đã có sẵn trong backend, chỉ cần bật lên.

---

## 2. Bật khoá cho các endpoint ghi

Sinh khoá ngẫu nhiên:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Dán vào `.env`:

```
API_KEY=<chuỗi vừa sinh>
READ_ONLY=false
```

```powershell
docker compose up -d --force-recreate backend
```

Cách hoạt động:

| Nhóm endpoint | Yêu cầu |
|---|---|
| `/status`, `/history`, `/events`, `/config` (GET), `/prediction`, `/diagnostics` | Không cần khoá |
| `/actuator`, `/mode`, `/config` (POST), `/alerts/test` | Bắt buộc header `X-API-Key` |

Sai khoá trả 401. Sai quá 10 lần trong 1 phút từ cùng một IP thì chuyển sang 429. Mỗi lần từ chối
đều ghi vào bảng `system_events` với loại `AUTH_FAILED` kèm IP, xem bằng
`GET /events?event_type=AUTH_FAILED`.

Đặt `READ_ONLY=true` thì mọi lệnh ghi bị chặn kể cả khi khoá đúng. Dùng khi muốn công khai chỉ để
xem, còn điều khiển giữ trong LAN.

---

## 3. Mở đường ra Internet bằng Cloudflare Tunnel

Không cần mở cổng router, không cần tên miền, tự có `https` và `wss`.

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```

Nó in ra địa chỉ dạng `https://abc-def-ghi.trycloudflare.com`. Mở cửa sổ thứ hai cho WebSocket:

```powershell
cloudflared tunnel --url http://localhost:9001
```

Địa chỉ đổi mỗi lần khởi động lại tunnel. Muốn cố định thì cần tài khoản Cloudflare và một tên miền.

ESP32 không đổi gì cả, vẫn nối broker trong LAN như cũ. Chỉ dashboard đi ra ngoài.

---

## 4. Cách gọn hơn: một tunnel duy nhất

Backend phục vụ luôn dashboard nếu có thư mục web đã build. Khi đó dashboard và API cùng một tên
miền, không dính CORS, không dính chặn nội dung hỗn hợp, và chỉ cần một tunnel.

TV4 build rồi đưa thư mục `dist` sang máy bạn, thêm vào `docker-compose.yml`:

```yaml
  backend:
    volumes:
      - ./web:/app/web:ro
```

Chép `dist` thành `web/` rồi `docker compose up -d --force-recreate backend`. Mở
`http://localhost:8000/` là thấy dashboard. Chạy một tunnel cho cổng 8000 là xong.

Nhược điểm: mỗi lần TV4 sửa giao diện phải gửi lại thư mục build. Trong lúc phát triển thì vẫn nên
tách riêng như mục 3.

---

## 5. Gửi TV4 những gì

```
VITE_API_URL  = https://<tunnel-8000>/api/v1/kitchen
VITE_WS_URL   = wss://<tunnel-9001>
VITE_WS_USER  = web_dashboard
VITE_WS_PASS  = <MQTT_WEB_PASSWORD trong .env>
VITE_API_KEY  = <API_KEY trong .env>
```

Dặn TV4 đặt vào Environment Variables trên Vercel hoặc Netlify, đừng viết cứng trong code vì địa chỉ
tunnel đổi liên tục.

Mọi lệnh ghi phải kèm header:

```js
await fetch(`${API}/actuator`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": import.meta.env.VITE_API_KEY,
  },
  body: JSON.stringify({ state: 1 }),
});
```

Và thêm địa chỉ trang của TV4 vào `.env` của backend:

```
CORS_ORIGINS=https://kitchen-dashboard.vercel.app,http://localhost:5173
```

```powershell
docker compose up -d --force-recreate backend
```

---

## 6. Nói thẳng về mức an toàn

`API_KEY` nằm trong mã JavaScript của trình duyệt nên ai xem mã nguồn trang cũng thấy, giống hệt
mật khẩu `web_dashboard`. Nó chặn được bot quét cổng và người tình cờ tìm thấy địa chỉ, nhưng không
chặn được người cố tình.

Với một đồ án môn học thì mức này chấp nhận được, miễn là bạn biết rõ giới hạn và nói được điều đó
khi bảo vệ. Ba cách siết thêm nếu muốn:

**Cloudflare Access** (miễn phí, không sửa code): đặt trước tên miền tunnel, bắt đăng nhập bằng email
trước khi vào được trang. Chỉ 5 thành viên nhóm và giảng viên được cấp quyền. Cách này chỉ chạy
trơn khi dashboard và API cùng một tên miền như mục 4.

**Chỉ bật tunnel khi cần.** Tắt `cloudflared` là địa chỉ biến mất. Phù hợp khi chỉ demo vài buổi.

**`READ_ONLY=true` khi không cần điều khiển.** Công khai để xem, khi nào demo điều khiển thì tắt đi.

---

## 7. Kiểm tra trước khi giao cho TV4

```powershell
# Đọc: phải 200
curl.exe https://<tunnel-8000>/api/v1/kitchen/status

# Ghi không khoá: phải 401
curl.exe -X POST https://<tunnel-8000>/api/v1/kitchen/mode `
  -H "Content-Type: application/json" -d "{\"mode\":\"AUTO\"}"

# Ghi có khoá: phải 200
curl.exe -X POST https://<tunnel-8000>/api/v1/kitchen/mode `
  -H "X-API-Key: <API_KEY>" -H "Content-Type: application/json" -d "{\"mode\":\"AUTO\"}"

# Nhật ký các lần bị từ chối
curl.exe "https://<tunnel-8000>/api/v1/kitchen/events?event_type=AUTH_FAILED&limit=10"
```

Kiểm tra WebSocket bằng cách mở `tools/dashboard-demo.html`, sửa `WS_URL` thành địa chỉ `wss://`
của tunnel và điền `API_KEY`.
