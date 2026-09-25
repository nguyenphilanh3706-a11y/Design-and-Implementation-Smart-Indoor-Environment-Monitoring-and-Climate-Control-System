# CẢNH BÁO QUA EMAIL GMAIL - MIỄN PHÍ

Backend gửi thư qua máy chủ SMTP của Gmail. Không tốn tiền, không giới hạn theo tin như SMS,
và điện thoại vẫn rung báo nếu đã cài ứng dụng Gmail.

---

## 1. Nội dung thư

Tiêu đề ngắn để đọc được ngay trên thông báo điện thoại:

```
[CẢNH BÁO] Ô nhiễm 1180 ppm - bếp esp32_kitchen_01
[NHẮC LẠI] Vẫn ô nhiễm 1242 ppm - bếp esp32_kitchen_01
```

Thân thư ghi đủ chi tiết: câu cảnh báo "Ô nhiễm! Tôi sẽ bật quạt.", giá trị đo và ngưỡng, nồng độ
khí, nhiệt độ, độ ẩm, trạng thái quạt, chế độ, thời điểm đo theo giờ Việt Nam. Thư nhắc lại có thêm
dòng "Đã kéo dài bao lâu".

Cơ chế chống spam giữ nguyên như SMS: gửi 1 thư khi vừa vượt ngưỡng, còn vượt thì 10 phút nhắc 1 lần,
tối đa 50 thư mỗi ngày.

---

## 2. Tạo mật khẩu ứng dụng Gmail

Gmail **không cho đăng nhập bằng mật khẩu thường** từ chương trình. Phải tạo mật khẩu ứng dụng riêng,
16 ký tự. Làm một lần, dùng mãi.

**Bước 1: Bật xác minh 2 bước.** Vào `myaccount.google.com` → **Bảo mật** → **Xác minh 2 bước** →
bật lên. Thiếu bước này thì không có mục mật khẩu ứng dụng.

**Bước 2: Tạo mật khẩu ứng dụng.** Vào thẳng `myaccount.google.com/apppasswords`. Đặt tên gợi nhớ,
ví dụ `iot-kitchen`, bấm **Tạo**.

**Bước 3: Chép lại.** Google hiện 16 ký tự dạng `abcd efgh ijkl mnop`. Chỉ hiện đúng một lần, đóng
cửa sổ là mất, phải tạo cái mới.

Nên dùng **Gmail cá nhân** để gửi. Tài khoản trường như `@hcmute.edu.vn` là Google Workspace, quản
trị viên thường tắt tính năng mật khẩu ứng dụng nên không tạo được.

---

## 3. Cấu hình trên máy chủ

Mở `.env`:

```
ALERT_CHANNEL=email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=dia.chi.gui@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop
EMAIL_TO=dia.chi.nhan@gmail.com
```

Dán mật khẩu kèm khoảng trắng cũng được, backend tự bỏ đi.

Muốn nhiều người cùng nhận thì ngăn bằng dấu phẩy:

```
EMAIL_TO=tuan@gmail.com,tv2@gmail.com,giangvien@hcmute.edu.vn
```

Gửi song song qua nhiều kênh:

```
ALERT_CHANNEL=email,telegram
```

Nạp lại:

```bash
dc up -d --force-recreate backend
curl -s http://localhost:8000/health
```

Phải thấy `"email": "configured"`.

---

## 4. Thử ngay

```bash
curl -s -X POST http://localhost:8000/api/v1/kitchen/alerts/test -H "X-API-Key: <khoá>"
```

Ra `sent: true` và hộp thư nhận được thư tiêu đề `[TEST] Kết nối email cảnh báo bếp IoT thành công`.

Thử cảnh báo thật mà không cần đốt khói, hạ ngưỡng xuống thấp hơn mức hiện tại:

```bash
curl -s -X POST http://localhost:8000/api/v1/kitchen/config \
  -H "X-API-Key: <khoá>" -H "Content-Type: application/json" \
  -d '{"alert_ppm_threshold": 100}'
```

Thư cảnh báo tới trong vòng vài giây. **Nhớ trả về 1000 sau khi thử.**

### Thử khi chưa có mật khẩu ứng dụng

Máy chủ thư giả in thư ra màn hình thay vì gửi đi:

```bash
python tools/fake_email.py
```

Trong `.env` đặt `SMTP_HOST=host.docker.internal`, `SMTP_PORT=1025`, `SMTP_SECURITY=none`, còn
`SMTP_USER` và `SMTP_PASSWORD` điền gì cũng được.

---

## 5. Lỗi hay gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `Gmail từ chối đăng nhập` | Dùng mật khẩu Gmail thường | Tạo mật khẩu ứng dụng theo mục 2 |
| Không thấy mục mật khẩu ứng dụng | Chưa bật xác minh 2 bước, hoặc tài khoản trường | Bật xác minh 2 bước; dùng Gmail cá nhân |
| Thư vào mục Spam | Gmail chưa quen người gửi | Mở thư, bấm "Không phải thư rác" một lần |
| `TimeoutError` hoặc `ConnectionRefused` | Dùng cổng 25 | AWS chặn cổng 25 gửi ra ngoài, dùng 587 hoặc 465 |
| `/health` báo `email: disabled` | `ALERT_CHANNEL` không có chữ `email` | Sửa `.env` rồi `--force-recreate` |
| `Đã dùng hết hạn mức 50 thư hôm nay` | Chạm trần `EMAIL_MAX_PER_DAY` | Bình thường, đặt lại vào 0 giờ UTC |

---

## 6. Ba điều nên biết

**Để điện thoại báo ngay.** Cài ứng dụng Gmail, bật thông báo. Muốn chắc không bỏ sót thì tạo bộ
lọc: tiêu đề chứa `[CẢNH BÁO]` thì gắn nhãn Quan trọng.

**Email chậm hơn Telegram một chút**, thường vài giây tới vài chục giây. Với cảnh báo khói bếp thì đủ
nhanh, vì quạt đã được ESP32 bật tại chỗ, thư chỉ để báo cho người vắng nhà.

**Mật khẩu ứng dụng là bí mật.** Ai có nó đều gửi thư được bằng tài khoản của bạn. Đừng đẩy `.env`
lên GitHub, và nếu lỡ lộ thì vào `myaccount.google.com/apppasswords` xoá nó đi.
