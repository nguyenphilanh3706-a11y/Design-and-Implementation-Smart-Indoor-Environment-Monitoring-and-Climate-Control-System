# CẢNH BÁO QUA SMS

Khi nồng độ khí vượt **1000 ppm**, Backend gửi SMS tới số điện thoại của bạn. Vẫn còn vượt thì
**10 phút nhắc lại một lần**. Không phải bật gì thêm, chỉ cần điền tài khoản SMS vào `.env`.

---

## 1. Nội dung tin nhắn

```
Ô nhiễm! 1011 ppm, tôi sẽ bật quạt. 14:43
Vẫn ô nhiễm 1218 ppm, quạt đang chạy. 14:53
```

Câu "tôi sẽ bật quạt" là đúng sự thật chứ không phải lời hứa: trên 1000 ppm thì FSM của ESP32 tự
đẩy quạt lên 100%, và ở chế độ MANUAL thì cơ chế `SAFETY_OVERRIDE` cũng cưỡng bức bật.

Tin ngắn dưới 70 ký tự nên vừa **một tin SMS** kể cả khi có dấu tiếng Việt.

---

## 2. Thang ppm và tốc độ quạt

| Mức | Nồng độ | Quạt | SMS |
|---|---|---|---|
| GOOD | dưới 800 ppm | 0% | không |
| MID | 800 đến 1000 ppm | 50% | không |
| BAD | trên 1000 ppm | 100% | **có** |

Ba mốc đổi được qua `POST /config`, lưu trong CSDL và đẩy xuống ESP32:

```json
{"ppm_mid_threshold": 800, "ppm_bad_threshold": 1000, "alert_ppm_threshold": 1000}
```

API từ chối nếu mốc MID lớn hơn hoặc bằng mốc BAD.

---

## 3. Cơ chế chống spam

Dữ liệu về mỗi 2 giây. Khói giữ trên 1000 ppm trong 10 phút mà gửi mỗi bản tin thì bạn nhận
**300 tin**, tức là mất vài trăm nghìn đồng. Hệ thống chỉ gửi **1 tin**, nhờ bốn tầng:

| Tầng | Cách hoạt động | Cấu hình |
|---|---|---|
| Chỉ báo khi mới vượt | Gửi ở thời điểm chuyển từ dưới lên trên 1000 ppm | — |
| Dải trễ | Chỉ coi là hết khi xuống dưới 950 ppm, dao động quanh 1000 không gửi lại | cố định 50 ppm |
| Nhắc lại | Vẫn vượt thì cứ 10 phút gửi 1 tin | `ALERT_REMINDER_SECONDS=600` |
| Trần mỗi ngày | Quá số tin này trong ngày thì ngừng hẳn | `SMS_MAX_PER_DAY=20` |

Trần mỗi ngày là lưới an toàn về tiền. Lỡ cấu hình sai hay cảm biến hỏng báo số ảo liên tục thì tối
đa cũng chỉ mất 20 tin.

Đã kiểm thử: dữ liệu về mỗi giây, ô nhiễm giữ trên ngưỡng liên tục, chỉ nhận đúng 1 tin cảnh báo và
1 tin nhắc lại đúng sau khoảng thời gian cài đặt.

---

## 4. Lấy tài khoản Twilio

SMS không có dịch vụ nào miễn phí thật sự. Twilio là lựa chọn dễ nhất cho sinh viên: có tiền thử
miễn phí khi đăng ký, tài liệu đầy đủ.

**Bước 1.** Vào `twilio.com`, bấm Sign up. Đăng ký bằng email, rồi xác minh bằng chính số điện thoại
bạn muốn nhận cảnh báo.

**Bước 2.** Vào Console, phần **Account Info** có hai giá trị:

```
Account SID   -> bắt đầu bằng AC...
Auth Token    -> bấm Show để hiện
```

**Bước 3.** Bấm **Get a phone number**. Twilio cấp một số Mỹ dạng `+1xxxxxxxxxx`, miễn phí trong
thời gian dùng thử.

**Bước 4. Bước hay bị quên nhất.** Vào **Messaging → Settings → Geo permissions**, tìm **Vietnam**
và tick vào. Mặc định Twilio chặn gửi sang Việt Nam, bỏ qua bước này thì mọi tin đều bị từ chối.

**Bước 5.** Điền vào `.env`:

```
ALERT_CHANNEL=sms
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM=+1xxxxxxxxxx
SMS_TO=+84901234567
ALERT_COOLDOWN_SECONDS=600
ALERT_REMINDER_SECONDS=600
```

`SMS_TO` viết dạng quốc tế: bỏ số 0 đầu, thêm `+84`. Số `0901234567` thành `+84901234567`.

```bash
dc up -d --force-recreate backend
```

---

## 5. Thử ngay

```bash
curl -s -X POST http://localhost:8000/api/v1/kitchen/alerts/test \
  -H "X-API-Key: $(grep '^API_KEY=' .env | cut -d= -f2-)"
```

Trả về `sent: true` và điện thoại nhận tin là xong.

Thử cảnh báo thật mà không cần đốt khói, bật thiết bị giả lập ở kịch bản khói:

```bash
dc stop simulator
SIM_SCENARIO=smoke dc --profile sim up -d simulator
```

Khoảng 1 phút sau nồng độ vượt 1000 ppm và bạn nhận tin. Xem lịch sử:

```bash
curl -s "http://localhost:8000/api/v1/kitchen/events?event_type=ALERT_SENT&limit=10"
```

---

## 6. Lỗi hay gặp

| Trường `detail` báo | Nguyên nhân | Cách xử lý |
|---|---|---|
| `SMS chưa cấu hình` | Thiếu một trong bốn biến `TWILIO_*` hoặc `SMS_TO` | Điền đủ rồi `--force-recreate backend` |
| `mã 21408` | Chưa bật Geo permissions cho Việt Nam | Làm lại bước 4 |
| `mã 21608` | Tài khoản dùng thử chỉ gửi được tới số đã xác minh | Vào Verified Caller IDs thêm số nhận |
| `mã 21211` | Số nhận sai định dạng | Dùng `+84...`, không có số 0 đầu, không khoảng trắng |
| `mã 20003` | Sai SID hoặc Auth Token | Chép lại từ Console |
| `Đã dùng hết hạn mức` | Chạm `SMS_MAX_PER_DAY` | Chờ sang ngày mới, hoặc tăng giới hạn |

---

## 7. Ba điều nên biết

**Tài khoản dùng thử tự chèn thêm chữ.** Mỗi tin bị thêm tiền tố
`Sent from your Twilio trial account -`. Cộng với tiếng Việt có dấu thì tin vượt 70 ký tự và bị tách
làm hai, tốn gấp đôi. Đặt `SMS_STRIP_DIACRITICS=true` để gửi không dấu, khi đó giới hạn là 160 ký tự
và vẫn vừa một tin.

**Tin tới Việt Nam không chắc chắn 100%.** Nhà mạng Việt Nam lọc tin từ số nước ngoài, đôi khi thay
tên người gửi hoặc trễ vài phút. Với đồ án thì đủ dùng. Sản phẩm thật nên dùng nhà cung cấp trong
nước có đăng ký brandname.

**Chi phí.** Mỗi tin tới Việt Nam tốn vài nghìn đồng, xem giá cụ thể trên trang bảng giá của Twilio.
Tiền thử miễn phí đủ cho vài trăm tin. Với cơ chế chống spam ở mục 3 thì một buổi demo tốn chưa tới
10 tin.

Muốn dùng lại Telegram song song thì đặt `ALERT_CHANNEL=both`. Code Telegram cũ vẫn giữ nguyên.
