# TRIỂN KHAI LÊN GOOGLE CLOUD - CHẠY 24/7

Toàn bộ hệ thống chạy trên **một máy ảo**, dùng đúng `docker compose` đang chạy ở nhà.
Không phải sửa code, không phải sửa firmware của TV2.

Thời gian làm: khoảng 45 phút, trong đó 15 phút chờ build.

---

## Phần A: Tạo máy ảo

### A1. Chọn gói máy

Tài khoản mới được **300 USD dùng trong 90 ngày**, và riêng máy e2-micro thì **miễn phí vĩnh viễn**.

| | e2-micro | e2-small |
|---|---|---|
| RAM | 1 GB | 2 GB |
| Giá | Miễn phí vĩnh viễn | ~13 USD/tháng, trừ vào 300 USD |
| Chạy đủ 5 container | Được, cần swap | Thoải mái |
| Build dịch vụ AI | Chậm, cần swap | Bình thường |

Khuyến nghị: dùng **e2-small** trong lúc làm đồ án vì có 300 USD, xong rồi hạ xuống e2-micro.
Đồ án kéo dài vài tuần nên tiêu chưa hết 30 USD.

Máy miễn phí vĩnh viễn chỉ có ở 3 vùng của Mỹ: `us-west1`, `us-central1`, `us-east1`. Độ trễ về
Việt Nam khoảng 180 ms. Với hệ thống này không sao vì cảm biến gửi mỗi 2 giây, nhưng nhớ ghi rõ
trong báo cáo là Bài test 2 đo qua Internet xuyên lục địa.

### A2. Tạo Compute Engine

Vào `console.cloud.google.com`, tạo project mới tên `iot-kitchen`.

Menu bên trái chọn **Compute Engine → VM instances → Create instance**:

| Mục | Giá trị |
|---|---|
| Name | iot-kitchen |
| Region | us-central1 (Iowa) |
| Zone | us-central1-a |
| Machine type | e2-small (hoặc e2-micro) |
| Boot disk | Ubuntu 24.04 LTS, Standard persistent disk, **30 GB** |
| Firewall | Tick cả **Allow HTTP traffic** và **Allow HTTPS traffic** |

30 GB là mức tối đa được miễn phí. Bấm **Create**, chờ khoảng một phút.

### A3. Cấp IP tĩnh

Không làm bước này thì IP đổi mỗi lần khởi động lại máy, và tên miền trỏ sai.

**VPC network → IP addresses → External IP addresses**, tìm dòng của máy vừa tạo, cột Type đang là
`Ephemeral`, đổi thành **Static**. Đặt tên rồi Reserve.

Ghi lại địa chỉ IP này.

### A4. Mở cổng 1883 cho ESP32

Đây là bước **hay bị quên nhất**. Google Cloud chặn ở tầng VPC, tường lửa trong máy không cứu được.

**VPC network → Firewall → Create firewall rule**:

| Mục | Giá trị |
|---|---|
| Name | allow-mqtt |
| Direction | Ingress |
| Targets | All instances in the network |
| Source IPv4 ranges | `0.0.0.0/0` |
| Protocols and ports | TCP, nhập `1883` |

Bấm Create.

---

## Phần B: Cấu hình máy chủ

### B1. Kết nối

Ở trang VM instances, bấm nút **SSH** ngay cạnh máy. Trình duyệt mở một cửa sổ dòng lệnh, không cần
cài gì thêm, không cần khoá SSH.

### B2. Chép mã nguồn lên

Cách nhanh nhất là qua GitHub:

```bash
git clone https://github.com/<tài-khoản>/iot-kitchen-backend.git
cd iot-kitchen-backend
```

Chưa có trên GitHub thì dùng nút **Upload file** ở góc trên bên phải cửa sổ SSH, tải file zip lên
rồi giải nén:

```bash
sudo apt update && sudo apt install -y unzip
unzip iot-kitchen-backend.zip
cd iot-kitchen-backend
```

### B3. Chạy script chuẩn bị

```bash
chmod +x deploy/vm-setup.sh
./deploy/vm-setup.sh
```

Script tạo swap 4 GB, cài Docker, mở tường lửa trong máy, và tạo lệnh tắt `dc`.

Xong thì **đăng xuất rồi vào lại** bằng cách đóng cửa sổ SSH và bấm SSH lần nữa. Bước này bắt buộc
để tài khoản của bạn vào được nhóm docker.

### B4. Lấy tên miền

Cần tên miền mới có https. Trang của TV4 chạy https, gọi sang `http://` sẽ bị Chrome chặn thẳng.

Vào `duckdns.org` bằng trình duyệt, đăng nhập bằng Google, tạo tên kiểu `iot-kitchen-hcmute`, dán IP
tĩnh ở bước A3 vào ô `current ip` rồi bấm update ip.

Bạn được `iot-kitchen-hcmute.duckdns.org`, miễn phí và dùng được ngay.

### B5. Tạo file cấu hình

```bash
cp .env.example .env
nano .env
```

Sửa các dòng sau. Sinh khoá bằng cách mở một cửa sổ SSH khác gõ `openssl rand -base64 32`:

```
POSTGRES_PASSWORD=<mật khẩu mạnh>
MQTT_BACKEND_PASSWORD=<mật khẩu mạnh>
MQTT_DEVICE_PASSWORD=<mật khẩu mạnh>
MQTT_WEB_PASSWORD=<mật khẩu mạnh>

API_KEY=<chuỗi sinh bằng openssl>
CORS_ORIGINS=*

API_PORT_HOST=127.0.0.1:8000
PG_PORT_HOST=127.0.0.1:5432
WS_PORT_HOST=127.0.0.1:9001
AI_PORT_HOST=127.0.0.1:9100
MQTT_PORT_HOST=1883

TELEGRAM_BOT_TOKEN=<token của bạn>
TELEGRAM_CHAT_ID=<chat id của bạn>
```

Bốn dòng có `127.0.0.1:` là phần bảo mật quan trọng nhất. Chúng khiến các cổng đó chỉ nghe trong nội
bộ máy chủ, chỉ Caddy gọi được, Internet không chạm tới. Riêng 1883 để nguyên vì ESP32 phải nối thẳng.

Lưu bằng Ctrl+O, Enter, rồi Ctrl+X.

### B6. Sửa tên miền trong Caddy

```bash
nano deploy/Caddyfile
```

Không cần sửa file này nữa. Tên miền đọc từ biến `SITE_DOMAIN` trong `.env`.

---

## Phần C: Chạy backend

### C1. Khởi động

```bash
source ~/.bashrc
dc up -d --build
```

Lệnh `dc` là lệnh tắt mà script đã tạo, thay cho chuỗi dài gồm ba file cấu hình: bản gốc, bản cho
máy chủ có Caddy, và bản tiết kiệm bộ nhớ.

Lần đầu mất 10 đến 15 phút. Phần lâu nhất là cài scikit-learn cho dịch vụ AI.

### C2. Kiểm tra

```bash
dc ps
```

Năm container phải ở trạng thái `Up` và bốn cái có `(healthy)`.

```bash
curl https://iot-kitchen-hcmute.duckdns.org/health
```

Phải thấy `database: connected`, `mqtt: connected`, `ai: ready`. Lần gọi đầu chờ thêm khoảng 30 giây
để Caddy xin chứng chỉ Let's Encrypt.

```bash
free -h
docker stats --no-stream
```

Tổng bộ nhớ dùng khoảng 330 MB.

### C3. Bật dữ liệu giả lập để thử

```bash
dc --profile sim up -d simulator
curl https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen/status
```

`data_freshness_ms` dưới 3000 là dữ liệu đang chảy.

Khi ESP32 thật của TV2 lên sóng thì tắt giả lập: `dc stop simulator`.

### C4. Kiểm tra khoá API từ máy Windows

```powershell
$base = "https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen"
Invoke-RestMethod "$base/status"

try { Invoke-RestMethod -Method Post -Uri "$base/mode" -ContentType "application/json" -Body '{"mode":"AUTO"}' }
catch { "Bị chặn đúng: $($_.Exception.Response.StatusCode.value__)" }
```

Lệnh đầu ra JSON, lệnh sau phải ra `Bị chặn đúng: 401`. Đúng hai kết quả này thì hệ thống sẵn sàng.

---

## Phần D: Vận hành hằng ngày

```bash
dc ps                      # xem trạng thái
dc logs -f backend         # xem log, Ctrl+C để thoát
dc restart backend         # khởi động lại 1 dịch vụ
dc up -d --build backend   # build lại sau khi sửa code
dc down                    # dừng hết, giữ dữ liệu
```

Cập nhật code mới:

```bash
git pull
dc up -d --build
```

Sao lưu CSDL, nên đặt lịch tự động:

```bash
./deploy/backup.sh
crontab -e
# thêm dòng, chạy 2 giờ sáng mỗi ngày:
0 2 * * * cd ~/iot-kitchen-backend && ./deploy/backup.sh >> backups/cron.log 2>&1
```

Máy chủ khởi động lại thì container tự lên vì đã đặt `restart: always`. Riêng simulator phải bật tay.

---

## Phần E: Gửi cho nhóm

### TV4 - Dashboard

```
VITE_API_URL  = https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen
VITE_WS_URL   = wss://iot-kitchen-hcmute.duckdns.org/mqtt
VITE_WS_USER  = web_dashboard
VITE_WS_PASS  = <MQTT_WEB_PASSWORD>
VITE_API_KEY  = <API_KEY>
```

Deploy xong bảo TV4 gửi lại địa chỉ trang Vercel, rồi bạn sửa `CORS_ORIGINS` thành địa chỉ đó và
chạy `dc up -d --force-recreate backend`.

Muốn gọn hơn thì TV4 gửi bạn thư mục `dist` sau khi build, bạn chép vào `web/` trên máy chủ. Khi đó
dashboard chạy luôn tại `https://iot-kitchen-hcmute.duckdns.org`, cùng tên miền với API, không cần
Vercel và không cần khai CORS.

### TV2 - Firmware

```
mqtt_server   = iot-kitchen-hcmute.duckdns.org
mqtt_port     = 1883
mqtt_user     = esp32_device
mqtt_password = <MQTT_DEVICE_PASSWORD>
```

Cổng vẫn là 1883, firmware chỉ đổi hai dòng. ESP32 không cần chung Wi-Fi với ai nữa.

---

## Phần F: Lỗi hay gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| ESP32 không nối được 1883 | Quên tạo firewall rule ở bước A4 | Tạo lại, tường lửa trong máy không thay thế được |
| `curl` báo lỗi chứng chỉ | DNS chưa trỏ đúng, hoặc cổng 80 bị chặn | `nslookup <tên miền>` và `dc logs caddy` |
| Build bị kill giữa chừng | Hết RAM lúc cài scikit-learn | Kiểm tra swap bằng `free -h`, chạy lại `deploy/vm-setup.sh` |
| `permission denied` khi gõ docker | Chưa đăng xuất sau khi cài Docker | Đóng cửa sổ SSH rồi mở lại |
| Địa chỉ IP đổi sau khi khởi động lại | Chưa cấp IP tĩnh | Làm lại bước A3 |
| Dashboard báo lỗi CORS | Địa chỉ Vercel chưa khai | Sửa `CORS_ORIGINS` rồi `dc up -d --force-recreate backend` |
| `ai: no_model` trong `/health` | Thiếu file `.pkl` | Chép file của TV5 vào `ai/models/` rồi `curl -X POST http://localhost:9100/reload` |

---

## Phần G: Điều cần nói khi bảo vệ

Cổng 1883 đi trên Internet ở dạng **không mã hoá**. Ai bắt được gói tin trên đường truyền sẽ đọc
được mật khẩu thiết bị. Trong mạng LAN thì không sao, trên Internet thì đây là điểm yếu thật.

Với đồ án môn học mức này chấp nhận được nếu bạn nêu rõ giới hạn, và nó là một ý tốt cho phần hướng
phát triển: bật TLS cho Mosquitto ở cổng 8883 dùng chứng chỉ Let's Encrypt, nhưng ESP32 phải dùng
`WiFiClientSecure` kèm chứng chỉ gốc, tốn thêm bộ nhớ chip.

Điểm mạnh nên nhấn: ESP32 vẫn tự chủ hoàn toàn. Máy chủ sập thì thiết bị vẫn đo, vẫn chạy FSM, vẫn
bật quạt theo ngưỡng, vẫn hiện số lên OLED. Chỉ mất phần lưu dữ liệu và xem từ xa. Đây chính là tiêu
chí "hoạt động an toàn khi mất kết nối" trong bảng đánh giá, và rút mạng giữa buổi demo mà quạt vẫn
chạy đúng là cách chứng minh thuyết phục nhất.
