# TRIỂN KHAI LÊN AWS EC2 - CHẠY 24/7

Toàn bộ hệ thống chạy trên **một máy ảo**, dùng đúng `docker compose` đang chạy ở nhà.
Không sửa code, không sửa firmware của TV2.

Thời gian làm: khoảng 50 phút, trong đó 15 phút chờ build.

**Ưu điểm so với Google Cloud**: AWS có vùng Singapore, độ trễ về Việt Nam khoảng 50 ms thay vì
180 ms. Số liệu Bài test 2 sẽ đẹp hơn nhiều.

---

## Phần A: Kiểm tra gói miễn phí

Điều khoản free tier của AWS đã thay đổi trong năm 2025. Tài khoản mở trước thời điểm đó dùng gói
12 tháng kiểu cũ, tài khoản mới nhận credit và thời hạn khác.

Vào **Billing and Cost Management → Free tier** trên console để xem tài khoản của bạn thuộc gói nào
và còn bao nhiêu. Đừng đoán, vì con số này quyết định bạn có phải trả tiền hay không.

Khoản quan trọng nhất là **750 giờ mỗi tháng cho t2.micro hoặc t3.micro**, đủ cho một máy chạy
liên tục cả tháng. Kèm theo 30 GB ổ EBS.

**Bật cảnh báo chi phí ngay**, trước khi tạo máy: Billing → Budgets → Create budget, chọn Zero
spend budget hoặc đặt ngưỡng 1 USD. Có cảnh báo thì không bị bất ngờ.

---

## Phần B: Tạo máy ảo

### B1. Chọn vùng

Góc trên bên phải console, chọn **Asia Pacific (Singapore) ap-southeast-1** trước khi làm bất cứ
việc gì. Chọn sai vùng thì phải làm lại từ đầu.

### B2. Launch instance

Vào **EC2 → Instances → Launch instances**:

| Mục | Giá trị |
|---|---|
| Name | iot-kitchen |
| AMI | Ubuntu Server 24.04 LTS (64-bit x86) |
| Instance type | **t3.micro** (hoặc t2.micro nếu vùng không có t3) |
| Key pair | Create new key pair, kiểu RSA, định dạng `.pem`, tải file về giữ kỹ |
| Storage | 30 GB, gp3 |

File `.pem` mất là không vào được máy nữa, phải tạo máy mới. Cất vào thư mục cố định như
`D:\keys\iot-kitchen.pem`.

### B3. Mở cổng trong Security Group

Ở phần **Network settings** của trang Launch, bấm **Edit**, rồi thêm bốn quy tắc Inbound:

| Type | Port | Source | Ghi chú |
|---|---|---|---|
| SSH | 22 | My IP | Chỉ IP của bạn, an toàn hơn |
| HTTP | 80 | Anywhere `0.0.0.0/0` | Let's Encrypt xác minh tên miền |
| HTTPS | 443 | Anywhere `0.0.0.0/0` | Dashboard và API |
| Custom TCP | **1883** | Anywhere `0.0.0.0/0` | **ESP32, hay bị quên nhất** |

Dòng 1883 là chỗ sai phổ biến nhất. AWS chặn ở Security Group, `ufw` bên trong máy không thay thế
được. Quên dòng này thì ESP32 không bao giờ nối được mà log cũng không báo gì rõ ràng.

Bấm **Launch instance**.

### B4. Cấp Elastic IP

IP mặc định đổi mỗi lần bạn stop rồi start máy, làm tên miền trỏ sai.

**EC2 → Elastic IPs → Allocate Elastic IP address → Allocate**. Chọn IP vừa tạo, bấm
**Actions → Associate Elastic IP address**, chọn instance `iot-kitchen`.

Elastic IP miễn phí khi đang gắn với máy **đang chạy**. Nếu bạn stop máy hoặc để IP không gắn vào
đâu thì AWS tính tiền. Xong đồ án nhớ Release.

Ghi lại địa chỉ IP này.

---

## Phần C: Kết nối và chuẩn bị

### C1. Kết nối SSH

Cách dễ nhất, không cần file `.pem`: chọn instance, bấm **Connect → EC2 Instance Connect → Connect**.
Trình duyệt mở cửa sổ dòng lệnh ngay.

Cách dùng PowerShell trên máy Windows:

```powershell
icacls "D:\keys\iot-kitchen.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i "D:\keys\iot-kitchen.pem" ubuntu@<Elastic IP>
```

Lệnh `icacls` bắt buộc. Thiếu nó thì SSH báo quyền file quá rộng và từ chối kết nối.

Tên đăng nhập là **`ubuntu`**, không phải `root`.

### C2. Chép mã nguồn lên

Qua GitHub là nhanh nhất:

```bash
git clone https://github.com/<tài-khoản>/iot-kitchen-backend.git
cd iot-kitchen-backend
```

Chưa có trên GitHub thì dùng `scp` từ máy Windows:

```powershell
scp -i "D:\keys\iot-kitchen.pem" -r D:\Study\IOT\backend_tv3\tuan1_test\iot-kitchen-backend ubuntu@<Elastic IP>:/home/ubuntu/
```

### C3. Chạy script chuẩn bị

```bash
cd ~/iot-kitchen-backend
chmod +x deploy/vm-setup.sh
./deploy/vm-setup.sh
```

Script tạo swap 4 GB, cài Docker, mở tường lửa trong máy, tạo lệnh tắt `dc`.

Swap là phần bắt buộc với máy 1 GB. Bộ nhớ lúc chạy chỉ khoảng 330 MB nên dư sức, nhưng lúc cài
scikit-learn cho dịch vụ AI có thể ngốn tới 600 MB.

Xong thì **thoát rồi SSH vào lại**, để tài khoản vào được nhóm docker:

```bash
exit
```

### C4. Lấy tên miền

Cần tên miền mới có https, vì trang của TV4 chạy https và sẽ bị Chrome chặn nếu gọi sang `http://`.

Vào `duckdns.org`, đăng nhập bằng Google, tạo tên kiểu `iot-kitchen-hcmute`, dán Elastic IP vào ô
`current ip` rồi bấm update ip.

Kiểm tra từ máy chủ:

```bash
nslookup iot-kitchen-hcmute.duckdns.org
```

Phải ra đúng Elastic IP.

---

## Phần D: Cấu hình

### D1. Tạo file `.env`

```bash
cd ~/iot-kitchen-backend
cp .env.example .env
openssl rand -base64 32          # chép chuỗi này làm API_KEY
nano .env
```

Sửa các dòng:

```
POSTGRES_PASSWORD=<mật khẩu mạnh>
MQTT_BACKEND_PASSWORD=<mật khẩu mạnh>
MQTT_DEVICE_PASSWORD=<mật khẩu mạnh>
MQTT_WEB_PASSWORD=<mật khẩu mạnh>

API_KEY=<chuỗi vừa sinh>
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

Lưu bằng Ctrl+O, Enter, Ctrl+X.

### D2. Sửa tên miền trong Caddy

```bash
nano deploy/Caddyfile
```

Dòng đầu, thay `iot.ten-mien-cua-ban.com` thành `iot-kitchen-hcmute.duckdns.org`.

---

## Phần E: Chạy backend

### E1. Khởi động

```bash
source ~/.bashrc
dc up -d --build
```

`dc` là lệnh tắt script vừa tạo, thay cho chuỗi dài gồm ba file cấu hình: bản gốc, bản cho máy chủ
có Caddy, và bản tiết kiệm bộ nhớ cho máy 1 GB.

Lần đầu mất 15 đến 20 phút vì t3.micro chỉ có 2 nhân chia sẻ. Phần lâu nhất là cài scikit-learn.

### E2. Kiểm tra

```bash
dc ps
```

Năm container `Up`, bốn cái có `(healthy)`.

```bash
curl https://iot-kitchen-hcmute.duckdns.org/health
```

Phải thấy `database: connected`, `mqtt: connected`, `ai: ready`. Lần gọi đầu chờ thêm khoảng 30 giây
để Caddy xin chứng chỉ.

```bash
free -h && docker stats --no-stream
```

Tổng bộ nhớ khoảng 330 MB.

### E3. Bật dữ liệu giả lập

```bash
dc --profile sim up -d simulator
curl https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen/status
```

`data_freshness_ms` dưới 3000 là dữ liệu đang chảy. Khi ESP32 thật lên sóng thì `dc stop simulator`.

### E4. Kiểm tra khoá API từ Windows

```powershell
$base = "https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen"
Invoke-RestMethod "$base/status"

try { Invoke-RestMethod -Method Post -Uri "$base/mode" -ContentType "application/json" -Body '{"mode":"AUTO"}' }
catch { "Bị chặn đúng: $($_.Exception.Response.StatusCode.value__)" }
```

Lệnh đầu ra JSON, lệnh sau ra `Bị chặn đúng: 401`. Đúng hai kết quả này là sẵn sàng giao cho nhóm.

---

## Phần F: Vận hành

```bash
dc ps                      # trạng thái
dc logs -f backend         # xem log, Ctrl+C thoát
dc restart backend
dc up -d --build           # sau khi sửa code
dc down                    # dừng, giữ dữ liệu
```

Sao lưu, nên đặt lịch:

```bash
./deploy/backup.sh
crontab -e
# thêm dòng chạy 2 giờ sáng mỗi ngày:
0 2 * * * cd ~/iot-kitchen-backend && ./deploy/backup.sh >> backups/cron.log 2>&1
```

Máy khởi động lại thì container tự lên nhờ `restart: always`. Riêng simulator bật tay.

---

## Phần G: Gửi cho nhóm

### TV4

```
VITE_API_URL  = https://iot-kitchen-hcmute.duckdns.org/api/v1/kitchen
VITE_WS_URL   = wss://iot-kitchen-hcmute.duckdns.org/mqtt
VITE_WS_USER  = web_dashboard
VITE_WS_PASS  = <MQTT_WEB_PASSWORD>
VITE_API_KEY  = <API_KEY>
```

Deploy xong bảo TV4 gửi lại địa chỉ trang Vercel, rồi sửa `CORS_ORIGINS` thành địa chỉ đó và chạy
`dc up -d --force-recreate backend`.

### TV2

```
mqtt_server   = iot-kitchen-hcmute.duckdns.org
mqtt_port     = 1883
mqtt_user     = esp32_device
mqtt_password = <MQTT_DEVICE_PASSWORD>
```

Cổng vẫn 1883, firmware chỉ đổi hai dòng. ESP32 không cần chung Wi-Fi với ai nữa.

---

## Phần H: Lỗi hay gặp, riêng của AWS

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| ESP32 không nối được 1883 | Quên mở cổng trong Security Group | EC2 → Security Groups → Edit inbound rules, thêm Custom TCP 1883 |
| SSH báo `UNPROTECTED PRIVATE KEY FILE` | Quyền file `.pem` quá rộng | Chạy lệnh `icacls` ở mục C1 |
| SSH báo `Permission denied (publickey)` | Đăng nhập bằng `root` | Phải dùng `ubuntu@<IP>` |
| Không SSH được sau vài ngày | Source đặt `My IP` mà IP nhà bạn đã đổi | Sửa lại quy tắc SSH trong Security Group |
| IP đổi sau khi stop rồi start | Chưa cấp Elastic IP | Làm lại mục B4 |
| Bị tính tiền Elastic IP | IP không gắn vào máy đang chạy | Gắn lại, hoặc Release nếu không dùng |
| Build bị kill giữa chừng | Hết RAM lúc cài scikit-learn | `free -h` xem swap, chạy lại `deploy/vm-setup.sh` |
| `permission denied` khi gõ docker | Chưa thoát SSH sau khi cài Docker | `exit` rồi vào lại |
| `curl` lỗi chứng chỉ | DNS chưa trỏ đúng hoặc cổng 80 bị chặn | `nslookup <tên miền>` và `dc logs caddy` |

---

## Phần I: Khi kết thúc đồ án

AWS tính tiền theo giờ kể cả khi máy đã stop, vì ổ EBS vẫn tồn tại. Muốn ngừng hẳn:

```bash
./deploy/backup.sh
```

Tải file sao lưu về máy:

```powershell
scp -i "D:\keys\iot-kitchen.pem" ubuntu@<IP>:/home/ubuntu/iot-kitchen-backend/backups/*.gz D:\backup\
```

Rồi trên console: **Terminate instance**, và **Release** Elastic IP. Hai việc này phải làm cả hai,
chỉ terminate máy mà quên IP thì vẫn bị tính tiền.

---

## Phần J: Điều cần nói khi bảo vệ

Cổng 1883 đi trên Internet ở dạng **không mã hoá**. Ai bắt được gói tin sẽ đọc được mật khẩu thiết
bị. Trong LAN thì không sao, trên Internet thì đây là điểm yếu thật.

Với đồ án môn học mức này chấp nhận được nếu nêu rõ giới hạn, và là ý tốt cho phần hướng phát triển:
bật TLS cho Mosquitto ở cổng 8883 với chứng chỉ Let's Encrypt, nhưng ESP32 phải dùng
`WiFiClientSecure` kèm chứng chỉ gốc, tốn thêm bộ nhớ chip.

Điểm mạnh nên nhấn: ESP32 vẫn tự chủ hoàn toàn. Máy chủ sập thì thiết bị vẫn đo, vẫn chạy FSM, vẫn
bật quạt theo ngưỡng, vẫn hiện số lên OLED. Chỉ mất phần lưu dữ liệu và xem từ xa. Đây chính là tiêu
chí hoạt động an toàn khi mất kết nối trong bảng đánh giá, và rút mạng giữa buổi demo mà quạt vẫn
chạy đúng là cách chứng minh thuyết phục nhất.

Vì máy đặt ở Singapore, độ trễ khoảng 50 ms. Nên đo `rtt_ms` ở cả hai môi trường, trong LAN và qua
Internet, rồi đặt cạnh nhau trong báo cáo. Có hai con số thì phần đánh giá dày dặn hơn hẳn.
