# TRIỂN KHAI LÊN RAILWAY

---

## 1. Bốn điều phải biết trước

**Railway không chạy `docker-compose`.** Mỗi container phải là một service riêng trong project.
Hệ thống này có 4 container nên bạn sẽ tạo 4 service, cấu hình biến môi trường cho từng cái.

**Railway không còn gói miễn phí.** Gói dùng thử cho 5 USD một lần, sau đó gói Hobby 5 USD mỗi
tháng đã bao gồm 5 USD dung lượng sử dụng, vượt thì tính thêm. Bốn service chạy 24/7 tốn khoảng
**10 đến 20 USD một tháng**, phần lớn do container AI ngốn RAM vì chứa scikit-learn. Muốn rẻ hơn thì
bỏ service AI, chỉ bật khi cần demo.

**Mạng nội bộ của Railway dùng IPv6.** Các service gọi nhau qua tên `<tên-service>.railway.internal`,
và ứng dụng phải lắng nghe trên `::` chứ không phải `0.0.0.0`. Vì vậy lệnh khởi động phải sửa lại,
mục 4 nói rõ.

**Cổng MQTT sẽ là một số ngẫu nhiên.** Railway cấp TCP Proxy dạng
`shortline.proxy.rlwy.net:43217`. ESP32 phải dùng đúng host và cổng đó, không phải 1883.

So với thuê một VPS rồi chạy `docker compose` như cũ thì Railway tốn công cấu hình hơn và tốn tiền
tương đương. Ưu điểm là không phải quản trị máy chủ, không phải lo cập nhật hệ điều hành, và deploy
lại chỉ bằng một lần `git push`.

---

## 2. Chuẩn bị mã nguồn

### 2.1 Đưa lên GitHub

Railway build từ repo, nên mã nguồn phải nằm trên GitHub.

```powershell
git init
git add .
git commit -m "Backend IoT phòng bếp"
git remote add origin https://github.com/<tài-khoản>/iot-kitchen-backend.git
git push -u origin main
```

### 2.2 Đưa cả file mô hình AI lên

File `.pkl` đang bị `.gitignore` bỏ qua, mà Railway không có cách nào chép file vào container sau khi
build. Phải ép thêm vào repo:

```powershell
git add -f ai/models/pollution_model.pkl
git commit -m "Them mo hinh AI de trien khai"
git push
```

Không muốn đẩy file 6 MB lên Git thì bỏ hẳn service AI, hệ thống vẫn chạy bình thường, chỉ mất phần
dự báo.

### 2.3 Kiểm tra file `.gitattributes`

Gói đã có sẵn file này với dòng `*.sh text eol=lf`. Thiếu nó thì script khởi động Mosquitto bị lỗi
`bad interpreter` khi build trên Linux, vì repo checkout từ Windows có dấu xuống dòng kiểu CRLF.

---

## 3. Tạo project và 4 service

Vào `railway.app`, đăng nhập bằng GitHub, bấm **New Project** rồi **Deploy from GitHub repo**, chọn
repo vừa đẩy lên. Railway tạo service đầu tiên. Ba service còn lại bấm **+ New** trong project rồi
chọn cùng repo đó.

Với mỗi service, vào tab **Settings** đặt như bảng sau:

| Service | Root Directory | Dockerfile Path |
|---|---|---|
| `timescaledb` | `/` | `deploy/railway/timescaledb.Dockerfile` |
| `mosquitto` | `/` | `deploy/railway/mosquitto.Dockerfile` |
| `backend` | `backend` | `backend/Dockerfile` |
| `ai` | `ai` | `ai/Dockerfile` |

Đổi tên service trong Settings cho đúng bốn tên trên. Tên này chính là tên miền nội bộ, đặt sai thì
các service không tìm thấy nhau.

---

## 4. Cấu hình từng service

### 4.1 timescaledb

Tab **Variables**:

```
POSTGRES_DB=iot_kitchen
POSTGRES_USER=iot_admin
POSTGRES_PASSWORD=<mật khẩu mạnh>
TIMESCALEDB_TELEMETRY=off
```

Tab **Settings**, phần Volumes, bấm **Add Volume**, mount vào:

```
/var/lib/postgresql/data
```

Bước này bắt buộc. Không có volume thì mỗi lần deploy lại là mất sạch dữ liệu.

File tạo bảng đã nằm sẵn trong image nên chạy tự động ở lần khởi động đầu tiên. Không cần làm gì thêm.

### 4.2 mosquitto

Tab **Variables**:

```
MQTT_BACKEND_PASSWORD=<mật khẩu mạnh>
MQTT_DEVICE_PASSWORD=<mật khẩu mạnh>
MQTT_WEB_PASSWORD=<mật khẩu mạnh>
DEVICE_ID=esp32_kitchen_01
```

Thêm volume mount vào `/mosquitto/data` để giữ các bản tin retain sau khi deploy lại.

Tab **Settings**, phần Networking, làm hai việc:

**TCP Proxy** cho ESP32: bấm **TCP Proxy**, nhập cổng đích `1883`. Railway trả về địa chỉ dạng
`shortline.proxy.rlwy.net:43217`. Ghi lại, đây là thứ gửi cho TV2.

**Public Domain** cho Dashboard: bấm **Generate Domain**, chọn cổng đích `9001`. Railway trả về
`mosquitto-abc123.up.railway.app`. Dashboard sẽ nối tới `wss://mosquitto-abc123.up.railway.app`.

### 4.3 ai

Tab **Settings**, phần Deploy, đặt **Custom Start Command**:

```
uvicorn app.main:app --host :: --port 9100
```

Dấu `::` là chỗ quan trọng. Để nguyên `0.0.0.0` như trong Dockerfile thì backend không gọi được vì
mạng nội bộ Railway dùng IPv6.

Không cần biến môi trường, không cần domain công khai. Service này chỉ backend gọi.

### 4.4 backend

Tab **Variables**:

```
POSTGRES_HOST=timescaledb.railway.internal
POSTGRES_PORT=5432
POSTGRES_DB=iot_kitchen
POSTGRES_USER=iot_admin
POSTGRES_PASSWORD=<giống mục 4.1>

MQTT_HOST=mosquitto.railway.internal
MQTT_PORT=1883
MQTT_USER=backend_svc
MQTT_PASSWORD=<MQTT_BACKEND_PASSWORD ở mục 4.2>

AI_SERVICE_URL=http://ai.railway.internal:9100
DEVICE_ID=esp32_kitchen_01

API_KEY=<bắt buộc, sinh bằng: python -c "import secrets; print(secrets.token_urlsafe(32))">
CORS_ORIGINS=https://<trang-vercel-của-TV4>

TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat id>
```

Tab **Settings**, phần Deploy, đặt **Custom Start Command**:

```
uvicorn app.main:app --host :: --port 8000
```

Phần Networking bấm **Generate Domain**, chọn cổng đích `8000`. Được địa chỉ dạng
`backend-abc123.up.railway.app`.

Phần Healthcheck đặt đường dẫn `/health`.

---

## 5. Kiểm tra

```powershell
curl.exe https://backend-abc123.up.railway.app/health
```

Phải thấy `database: connected` và `mqtt: connected`. Nếu `mqtt: disconnected` thì xem log service
backend, thường do sai `MQTT_PASSWORD` hoặc gõ nhầm tên service trong `MQTT_HOST`.

Mạng nội bộ Railway mất khoảng một trăm mili-giây mới sẵn sàng sau khi container khởi động, nên vài
dòng log đầu báo lỗi kết nối là bình thường. Backend tự thử lại theo cơ chế chờ tăng dần.

Thử khoá API:

```powershell
$base = "https://backend-abc123.up.railway.app/api/v1/kitchen"
Invoke-RestMethod "$base/status"

try { Invoke-RestMethod -Method Post -Uri "$base/mode" -ContentType "application/json" -Body '{"mode":"AUTO"}' }
catch { "Bị chặn đúng: $($_.Exception.Response.StatusCode.value__)" }
```

Lệnh đầu ra JSON, lệnh sau phải ra `Bị chặn đúng: 401`.

---

## 6. Gửi TV4

```
VITE_API_URL  = https://backend-abc123.up.railway.app/api/v1/kitchen
VITE_WS_URL   = wss://mosquitto-abc123.up.railway.app
VITE_WS_USER  = web_dashboard
VITE_WS_PASS  = <MQTT_WEB_PASSWORD>
VITE_API_KEY  = <API_KEY>
```

Địa chỉ Railway cố định, không đổi mỗi lần deploy lại. Dặn TV4 đặt vào Environment Variables trên
Vercel, và deploy xong gửi lại địa chỉ trang để bạn điền vào `CORS_ORIGINS`.

---

## 7. Gửi TV2

```
mqtt_server   = shortline.proxy.rlwy.net      (lấy từ TCP Proxy ở mục 4.2)
mqtt_port     = 43217                          (số ngẫu nhiên Railway cấp, KHÔNG phải 1883)
mqtt_user     = esp32_device
mqtt_password = <MQTT_DEVICE_PASSWORD>
```

Nhấn mạnh với TV2 rằng cổng không còn là 1883. Đây là lỗi chắc chắn xảy ra nếu không dặn trước.

ESP32 giờ nối qua Internet nên không cần chung Wi-Fi với ai nữa.

---

## 8. Mấy chỗ dễ vướng

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Backend không nối được CSDL hoặc AI | Service lắng nghe trên `0.0.0.0` | Đặt Custom Start Command với `--host ::` |
| `bad interpreter` khi build mosquitto | Repo checkout từ Windows có CRLF | Kiểm tra file `.gitattributes` đã được commit |
| Dữ liệu mất sau khi deploy lại | Chưa gắn volume cho timescaledb | Thêm volume mount `/var/lib/postgresql/data` |
| Sửa `10_schema.sql` mà không thấy đổi | File init chỉ chạy khi volume trống | Xoá volume rồi deploy lại, mất hết dữ liệu cũ |
| AI báo thiếu mô hình | File `.pkl` chưa lên Git | `git add -f ai/models/pollution_model.pkl` |
| Hoá đơn tăng nhanh | Bốn service chạy 24/7 | Tắt service AI khi không demo, hoặc chuyển sang VPS |

---

## 9. Phương án rẻ hơn nếu thấy tốn

Bỏ service `mosquitto` trên Railway, dùng **HiveMQ Cloud** bản miễn phí. Nó cho 100 kết nối, có sẵn
TLS cổng 8883 và WebSocket bảo mật cổng 8884, không tốn tiền.

Khi đó trong biến môi trường của backend đặt thêm:

```
MQTT_TLS=true
MQTT_HOST=<địa chỉ HiveMQ>
MQTT_PORT=8883
```

Backend đã hỗ trợ sẵn phần TLS này. Đổi lại thì TV2 phải sửa firmware dùng `WiFiClientSecure` kèm
chứng chỉ gốc, và phần phân quyền ACL chuyển sang khai báo trong bảng điều khiển của HiveMQ thay vì
file `acl.template`.
