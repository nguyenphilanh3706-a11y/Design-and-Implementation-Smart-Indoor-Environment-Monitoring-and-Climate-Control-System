# ĐƯA BACKEND LÊN MÁY CHỦ ĐÁM MÂY CHẠY 24/7

---

## 1. Ràng buộc quyết định việc chọn nền tảng

ESP32 nối MQTT qua **TCP thuần cổng 1883**, không phải HTTP. Đây là điều loại bỏ phần lớn nền tảng
miễn phí, vì chúng chỉ cho mở cổng HTTP.

| Nền tảng | Chạy được không | Lý do |
|---|---|---|
| **VPS** (DigitalOcean, Oracle, AWS EC2) | **Được, khuyến nghị** | Chạy nguyên `docker compose` hiện có, mở cổng nào cũng được |
| Render bản miễn phí | Không | Chỉ cho cổng HTTP, ESP32 không nối được. Dịch vụ còn ngủ sau 15 phút không ai gọi |
| Railway | Được một phần | Có TCP proxy nhưng hết hạn mức miễn phí sau vài ngày |
| Fly.io | Được | Hỗ trợ TCP và Docker, nhưng phải tách thành nhiều app, cấu hình phức tạp hơn VPS |
| Render + HiveMQ Cloud | Được | Broker tách riêng, nhưng ESP32 phải hỗ trợ TLS, tức TV2 phải sửa firmware |

Vì toàn bộ hệ thống đã đóng gói bằng Docker, **thuê một VPS nhỏ rồi chạy đúng lệnh compose đang
dùng** là đường ngắn nhất. Không phải chia nhỏ dịch vụ, không phải đổi kiến trúc, không phải sửa
firmware.

### Lấy VPS miễn phí ở đâu

**GitHub Student Developer Pack** là lựa chọn tốt nhất cho bạn. Đăng ký bằng email sinh viên
HCMUTE tại `education.github.com/pack`, sẽ được DigitalOcean tặng 200 USD dùng trong 12 tháng. Máy
2 GB RAM giá 12 USD một tháng, tức là chạy thoải mái hơn một năm mà không tốn tiền.

Phương án khác: **Oracle Cloud Always Free** cho máy ARM 4 nhân 24 GB RAM miễn phí vĩnh viễn, nhưng
thủ tục đăng ký khó và hay hết máy. **AWS EC2** free tier 12 tháng chỉ có 1 GB RAM, hơi chật vì
dịch vụ AI cần scikit-learn.

Cấu hình tối thiểu: **2 GB RAM**, Ubuntu 24.04. Dưới 2 GB thì container AI hay bị hệ điều hành kill.

---

## 2. Những gì cần sửa

Đã có sẵn trong gói, bạn không phải viết code:

| File | Vai trò |
|---|---|
| `deploy/docker-compose.prod.yml` | Thêm Caddy làm cổng vào, đặt `restart: always` |
| `deploy/Caddyfile` | Cấu hình https tự động và gom API, WebSocket, Dashboard về một tên miền |
| `MQTT_TLS` trong `.env` | Bật khi dùng broker đám mây; chạy trên VPS thì để `false` |

Caddy tự xin chứng chỉ Let's Encrypt và tự gia hạn. Nó gom REST API và WebSocket về cùng một tên
miền, nhờ đó Dashboard không dính CORS và không dính lỗi chặn nội dung hỗn hợp.

---

## 3. Các bước làm

### 3.1 Tạo máy chủ

Tạo Droplet Ubuntu 24.04, 2 GB RAM, chọn vùng Singapore cho gần Việt Nam. Ghi lại địa chỉ IP.

### 3.2 Trỏ tên miền

Cần một tên miền để có https. Student Pack có Namecheap tặng tên miền `.me` miễn phí một năm.

Trong phần quản lý DNS, thêm bản ghi:

```
Loại: A     Tên: iot     Giá trị: <IP máy chủ>
```

Đợi vài phút rồi kiểm tra bằng `nslookup iot.ten-mien-cua-ban.com`.

### 3.3 Cài Docker trên máy chủ

```bash
ssh root@<IP máy chủ>
curl -fsSL https://get.docker.com | sh
docker --version
```

### 3.4 Chép mã nguồn lên

Cách tốt nhất là qua Git:

```bash
git clone <địa chỉ repo của nhóm> iot-kitchen-backend
cd iot-kitchen-backend
```

Chưa đưa lên Git thì dùng `scp` từ máy Windows:

```powershell
scp -r D:\Study\IOT\backend_tv3\tuan1_test\iot-kitchen-backend root@<IP>:/root/
```

### 3.5 Tạo file `.env` trên máy chủ

```bash
cp .env.example .env
nano .env
```

Sáu chỗ phải sửa, khác hẳn cấu hình chạy ở nhà:

```
POSTGRES_PASSWORD=<mật khẩu mạnh, khác ở nhà>
MQTT_BACKEND_PASSWORD=<mật khẩu mạnh>
MQTT_DEVICE_PASSWORD=<mật khẩu mạnh>
MQTT_WEB_PASSWORD=<mật khẩu mạnh>

API_KEY=<bắt buộc, sinh bằng: openssl rand -base64 32>
CORS_ORIGINS=https://iot.ten-mien-cua-ban.com

API_PORT_HOST=127.0.0.1:8000
PG_PORT_HOST=127.0.0.1:5432
WS_PORT_HOST=127.0.0.1:9001
AI_PORT_HOST=127.0.0.1:9100
MQTT_PORT_HOST=1883
```

Bốn dòng `127.0.0.1:` là phần quan trọng nhất về bảo mật. Chúng khiến các cổng đó chỉ nghe trong
nội bộ máy chủ, chỉ Caddy gọi được, Internet không chạm tới. Riêng 1883 giữ nguyên vì ESP32 cần nối
thẳng vào.

`API_KEY` bây giờ là bắt buộc. Máy chủ công khai mà không có khoá thì ai cũng bật tắt được quạt.

### 3.6 Sửa tên miền trong Caddyfile

```bash
nano deploy/Caddyfile
```

Thay `iot.ten-mien-cua-ban.com` ở dòng đầu bằng tên miền thật.

### 3.7 Mở tường lửa

```bash
ufw allow 22/tcp      # SSH, quên dòng này là tự khoá mình ra ngoài
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 1883/tcp    # ESP32
ufw enable
```

### 3.8 Khởi động

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose ps
```

Lần đầu mất khoảng 5 phút. Caddy xin chứng chỉ mất thêm khoảng 30 giây.

```bash
curl https://iot.ten-mien-cua-ban.com/health
```

Thấy JSON với `database: connected` là xong.

### 3.9 Chuyển dữ liệu cũ (nếu muốn giữ)

Trên máy Windows:

```powershell
docker compose exec -T timescaledb pg_dump -U iot_admin iot_kitchen > backup.sql
scp backup.sql root@<IP>:/root/
```

Trên máy chủ:

```bash
docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen < backup.sql
```

---

## 4. Gửi TV4 những gì

Chỉ **ba** dòng, ít hơn hẳn phương án tunnel vì giờ tất cả chung một tên miền:

```
VITE_API_URL  = https://iot.ten-mien-cua-ban.com/api/v1/kitchen
VITE_WS_URL   = wss://iot.ten-mien-cua-ban.com/mqtt
VITE_API_KEY  = <API_KEY trên máy chủ>
```

Cộng thêm hai giá trị cho WebSocket:

```
VITE_WS_USER  = web_dashboard
VITE_WS_PASS  = <MQTT_WEB_PASSWORD trên máy chủ>
```

Ba điểm dặn TV4:

Địa chỉ này **cố định**, không đổi mỗi lần khởi động lại như tunnel. Đặt vào Environment Variables
trên Vercel rồi quên luôn.

Nếu muốn gọn hơn nữa, TV4 gửi bạn thư mục `dist` sau khi build, bạn chép vào `web/` trên máy chủ.
Khi đó dashboard chạy luôn tại `https://iot.ten-mien-cua-ban.com`, cùng origin với API, không cần
Vercel và cũng không cần khai CORS.

Mọi lệnh ghi phải kèm header `X-API-Key`. Endpoint đọc thì không cần.

---

## 5. Gửi TV2 những gì

```
mqtt_server   = iot.ten-mien-cua-ban.com
mqtt_port     = 1883
mqtt_user     = esp32_device
mqtt_password = <MQTT_DEVICE_PASSWORD trên máy chủ>
```

Firmware chỉ đổi đúng hai dòng địa chỉ và mật khẩu, không phải sửa gì khác. ESP32 giờ nối qua
Internet nên không cần chung Wi-Fi với ai nữa.

---

## 6. Vận hành

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart backend
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml down
```

Đặt một dòng bí danh cho đỡ dài:

```bash
echo "alias dc='docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml'" >> ~/.bashrc
source ~/.bashrc
dc ps
```

Sao lưu định kỳ:

```bash
dc exec -T timescaledb pg_dump -U iot_admin iot_kitchen | gzip > backup-$(date +%F).sql.gz
```

---

## 7. Điều cần nói thẳng

Cổng 1883 hiện mở ra Internet, dữ liệu MQTT đi ở dạng **không mã hoá**. Ai bắt được gói tin trên
đường truyền sẽ đọc được mật khẩu thiết bị. Trong mạng LAN thì không sao, trên Internet thì đây là
điểm yếu thật.

Với đồ án môn học, mức này chấp nhận được nếu bạn nêu rõ giới hạn khi bảo vệ, và nó còn là một ý
tốt cho phần hướng phát triển. Muốn khắc phục thì bật TLS cho Mosquitto ở cổng 8883 dùng chứng chỉ
Let's Encrypt, nhưng ESP32 phải dùng `WiFiClientSecure` kèm chứng chỉ gốc, tức là TV2 phải sửa
firmware và tốn thêm bộ nhớ của chip.

Điểm còn lại đáng nhớ: ESP32 vẫn tự chủ hoàn toàn. Máy chủ sập thì thiết bị vẫn đo, vẫn chạy FSM,
vẫn bật quạt theo ngưỡng. Chỉ mất phần lưu dữ liệu và xem từ xa.
