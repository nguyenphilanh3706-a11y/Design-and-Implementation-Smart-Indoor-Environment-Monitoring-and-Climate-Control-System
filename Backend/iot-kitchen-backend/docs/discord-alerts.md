# CẢNH BÁO QUA DISCORD

Kênh thứ hai, chạy song song với Telegram. Miễn phí, gửi được bất cứ lúc nào, và chỉ cần một đường
link, không phải tạo bot hay đăng nhập gì cả.

Cùng cơ chế với Telegram: gửi 1 tin khi nồng độ vừa vượt 1000 ppm, còn vượt thì 10 phút nhắc lại
1 lần, và gửi tin báo khi đã hết ô nhiễm.

---

## 1. Tin nhắn trông thế nào

Dòng chữ đầu hiện trên thông báo điện thoại, thẻ màu bên dưới chứa chi tiết:

```
@everyone 🚨 Ô nhiễm! 1007 ppm, tôi sẽ bật quạt.
▌ Bếp esp32_kitchen_01
▌ Nồng độ khí: 1007 ppm   Nhiệt độ: 32.2°C   Quạt: BẬT 100%
▌ Ngưỡng: 1000 ppm        Chế độ: AUTO
▌ Còn vượt ngưỡng thì 10 phút nhắc lại 1 lần
```

Màu thẻ: đỏ là cảnh báo mới, cam là nhắc lại, xanh lá là đã an toàn, xanh dương là tin thử.

Discord tự hiển thị giờ theo múi giờ của người xem.

---

## 2. Tạo webhook (2 phút)

Cần một server Discord mà bạn có quyền quản lý. Chưa có thì bấm dấu **+** ở cột bên trái Discord,
chọn **Tạo máy chủ của riêng tôi**.

1. Bấm chuột phải vào kênh muốn nhận cảnh báo, ví dụ `#canh-bao-bep`, chọn **Chỉnh sửa kênh**.
2. Chọn **Tích hợp** (Integrations), rồi **Webhook**, rồi **Webhook mới**.
3. Đặt tên như `Bếp IoT`, đổi ảnh đại diện nếu thích.
4. Bấm **Sao chép URL Webhook**.

Đường link có dạng:

```
https://discord.com/api/webhooks/1234567890/abcDEF...
```

**Đường link này là bí mật.** Phần sau dấu `/` cuối cùng là khoá, ai có nó đều gửi được tin vào kênh
của bạn. Đừng chụp màn hình, đừng dán vào nhóm chat.

---

## 3. Cấu hình

Mở `.env` trên máy chủ:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1234567890/abcDEF...
DISCORD_MENTION=@everyone
```

`DISCORD_MENTION` là tuỳ chọn nhưng nên đặt. Discord mặc định có thể không rung với tin trong server,
gọi tên thì điện thoại chắc chắn báo. Chỉ tin cảnh báo đầu mới gọi tên, tin nhắc lại thì không.

Muốn chỉ gọi riêng mình thay vì cả server: bật **Chế độ nhà phát triển** trong Cài đặt → Nâng cao,
bấm chuột phải vào tên mình chọn **Sao chép ID**, rồi đặt `DISCORD_MENTION=<@mã_vừa_chép>`.

Nạp lại:

```bash
dc up -d --force-recreate backend
curl -s http://localhost:8000/health
```

Phải thấy `"discord": "configured"`.

---

## 4. Thử

```bash
curl -s -X POST http://localhost:8000/api/v1/kitchen/alerts/test -H "X-API-Key: <khoá>"
```

Kết quả ghi rõ từng kênh:

```
telegram: Đã gửi Telegram (message_id=...) | discord: Đã gửi Discord (message_id=...)
```

Thử khi chưa tạo webhook: chạy `python tools/fake_discord.py`, đặt
`DISCORD_WEBHOOK_URL=http://host.docker.internal:8097/api/webhooks/1/test`.

---

## 5. Lỗi hay gặp

| Thông báo | Nguyên nhân | Cách xử lý |
|---|---|---|
| `Webhook Discord không tồn tại hoặc đã bị xoá` | Webhook bị xoá, hoặc chép thiếu ký tự | Tạo webhook mới, sao chép bằng nút, đừng gõ tay |
| `/health` báo `discord: not_configured` | `DISCORD_WEBHOOK_URL` trống hoặc không bắt đầu bằng `https://` | Sửa `.env` rồi `--force-recreate backend` |
| Tin tới nhưng điện thoại không rung | Server đang để chế độ chỉ báo khi được gọi tên | Đặt `DISCORD_MENTION` như mục 3 |
| `Discord vẫn quá tải sau khi thử lại` | Gửi quá 30 tin một phút | Hiếm gặp, cơ chế chống spam đã chặn trước |

Nếu lỡ để lộ đường link, vào lại phần Webhook của kênh, bấm **Xoá webhook** rồi tạo cái mới.
