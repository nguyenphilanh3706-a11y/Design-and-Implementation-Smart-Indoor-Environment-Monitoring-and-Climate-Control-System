# TÍCH HỢP MÔ HÌNH AI - Nhiệm vụ Tuần 2 của TV3

Gửi TV5 và dùng cho Chương 4 báo cáo. Mô hình Random Forest của TV5 đã chạy trong hệ thống thật:
Backend lấy dữ liệu từ TimescaleDB, gọi dịch vụ AI suy luận, publish kết quả lên MQTT và lưu lại
để đo độ chính xác.

---

## 1. Kiến trúc

```
TimescaleDB ──(1) cửa sổ 21 khung x 10 giây──> Backend ──(2) HTTP /predict──> Dịch vụ AI
                                                  │                          (Random Forest)
                    (4) lưu ai_predictions <───────┤<──(3) kết quả suy luận──────┘
                                                  │
                    (5) publish MQTT .../ai_prediction ──> Web Dashboard
```

Dịch vụ AI **không viết lại logic suy luận**. Nó gọi thẳng hàm `predict_future()` trong
`tv5/ai_service.py`, đúng vai trò mà TV5 đã ghi trong chú thích: *"API Interface để Backend (TV3)
hoặc Service khác gọi vào"*. Nhờ vậy chỉ có một nguồn sự thật: TV5 đổi thứ tự đặc trưng, đổi ngưỡng
phân loại xu hướng hay đổi tầm nhìn dự báo thì hệ thống chạy theo ngay, Backend không sửa gì.

Hai file được tích hợp là `tv5/train_ai.py` và `tv5/ai_service.py`. File `generate_data.py` không
đưa vào hệ thống vì nó chỉ sinh dữ liệu mô phỏng; mô hình sẽ được train trên dữ liệu thật lấy từ
TimescaleDB (mục 5.3).

Mô hình chạy trong **container riêng** (`ai`), không nhét vào Backend. Ba lý do:

scikit-learn cùng pandas và numpy nặng khoảng 400 MB. Backend giữ nhẹ thì mỗi lần sửa REST API
build lại chỉ mất vài giây.

File `.pkl` của joblib phụ thuộc phiên bản scikit-learn lúc train. Tách ra thì TV5 nâng phiên bản
cũng không làm hỏng Backend.

Mô hình lỗi hoặc thiếu file thì chỉ mất tính năng dự báo. Luồng giám sát, điều khiển quạt và cảnh
báo khẩn cấp vẫn chạy. Đây là yêu cầu bắt buộc: dự báo là tính năng phụ trợ, không được phép kéo
sập chức năng an toàn.

---

## 2. Ba chỗ phải xử lý khi ghép vào hệ thống thật

### 2.1 Lệch chu kỳ lấy mẫu: 10 giây so với 2 giây

Dữ liệu huấn luyện gốc của TV5 có nhịp **10 giây/mẫu**, nên cửa sổ 20 mẫu tương ứng 200 giây quá khứ.
Nhưng Bản thống nhất quy định ESP32 gửi telemetry **2 giây/lần**.

Nếu đưa thẳng 20 bản tin thô vào mô hình thì cửa sổ chỉ còn 40 giây thay vì 200 giây. Mô hình vẫn
chạy, vẫn trả về số, không báo lỗi gì cả, nhưng kết quả vô nghĩa vì nó được học trên dữ liệu có
nhịp khác hẳn.

Cách xử lý: Backend gộp dữ liệu bằng `time_bucket('10 seconds')` của TimescaleDB ngay trong câu
truy vấn, lấy trung bình mỗi khung 10 giây. Đây cũng là một lợi ích cụ thể của việc chọn CSDL chuỗi
thời gian thay vì PostgreSQL thường, nên đưa vào báo cáo.

### 2.2 Đơn vị của `dP_dt`

Trong dữ liệu huấn luyện, `dP_dt` là `pollution_percent.diff()`, tức chênh lệch giữa hai mẫu liền
nhau của chuỗi 10 giây. **Đơn vị là %/mẫu, không phải %/giây.**

Backend tính đúng như vậy: lấy hiệu nồng độ giữa hai khung 10 giây liên tiếp. Nếu ai đó tính đạo
hàm theo giây thì đặc trưng sẽ nhỏ đi 10 lần, mô hình lệch mà không ai phát hiện.

Vì cần một khung trước để tính hiệu, Backend lấy 21 khung rồi bỏ khung đầu.

### 2.3 Mốc thời gian trong `ai_service.py`

```python
datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
```

Dòng này lấy **giờ máy** rồi gắn thêm chữ `Z`, mà `Z` nghĩa là giờ UTC. Chạy ở Việt Nam sẽ ghi sai
7 tiếng, và mốc thời gian cũng không có mili-giây như hợp đồng dữ liệu quy định.

Dịch vụ AI bỏ trường `timestamp` mà `predict_future()` trả về, Backend tự sinh mốc thời gian đúng
chuẩn UTC kèm mili-giây khi publish. Phần dự báo và xu hướng thì lấy nguyên từ hàm của TV5.
TV5 nên sửa lại dòng này nếu còn dùng đoạn code đó ở chỗ khác.

### 2.4 `ai_service.py` gọi `exit()` khi thiếu file mô hình

Dòng `exit()` ở đầu file rất hợp lý khi chạy như một script độc lập, nhưng khi import vào dịch vụ
thì nó tắt luôn cả tiến trình, khiến `/health` mất khả năng báo lý do. Dịch vụ AI bắt `SystemExit`
để vẫn sống và trả về `status: no_model` kèm đường dẫn file đang thiếu.

---

## 3. Những gì đã thêm vào hệ thống

| Thành phần | Nội dung |
|---|---|
| Container `ai` | FastAPI gọi `predict_future()` của TV5, cổng 9100, có `/health`, `/predict`, `/reload` |
| `prediction_service.py` | Vòng lặp 10 giây trong Backend: dựng cửa sổ, gọi AI, publish, lưu CSDL |
| Bảng `ai_predictions` | Hypertable lưu từng lần dự báo, giữ 90 ngày |
| `GET /prediction` | Dự báo mới nhất, kèm lý do nếu chưa dự báo được |
| `GET /prediction/history` | Danh sách dự báo, để vẽ chồng lên đường thực đo |
| `GET /prediction/accuracy` | **MAE/RMSE đo trên dữ liệu thật** |
| Topic `.../ai_prediction` | QoS 0, retain false, đúng Bản thống nhất mục 2.2 |

Backend tự hỏi `/health` của dịch vụ AI để biết mô hình cần cửa sổ bao nhiêu mẫu, thay vì ghi cứng
số 20. TV5 đổi sang 30 mẫu thì chỉ cần thay file `.pkl`, Backend tự lấy đúng số khung.

Backend bỏ qua lượt dự báo khi dữ liệu chưa đủ 210 giây, khi có khung thiếu số đo vì cảm biến lỗi,
hoặc khi dữ liệu đứt quãng làm cửa sổ không còn liên tục. Lý do bỏ qua hiện trong trường `detail`
của `GET /prediction`, không im lặng.

### Payload publish lên MQTT

```json
{
  "device_id": "esp32_kitchen_01",
  "timestamp": "2026-09-16T06:52:07.157Z",
  "target_time": "2026-09-16T07:07:07.157Z",
  "prediction_window_minutes": 15,
  "predicted_pollution_percent": 32.2,
  "current_pollution_percent": 36.0,
  "trend": "FALLING",
  "model_version": "RandomForestRegressor-5d1fce38"
}
```

Bốn trường bắt buộc theo Bản thống nhất đều có. `device_id`, `target_time`,
`current_pollution_percent` và `model_version` là phần bổ sung để Dashboard vẽ được điểm dự báo lên
trục thời gian và để đối chiếu số liệu theo từng phiên bản mô hình.

---

## 4. Kết quả kiểm thử

Chạy thật với mô hình do `train_ai.py` sinh ra:

| Hạng mục | Kết quả |
|---|---|
| Nạp mô hình | Qua `tv5/ai_service.py`, tự nhận ra cửa sổ 20 mẫu x 4 đặc trưng |
| Thiếu file `.pkl` | Dịch vụ vẫn sống, `/health` trả `no_model` kèm đường dẫn thiếu, `/predict` trả 503 |
| Nạp nóng | Chép file `.pkl` vào rồi gọi `/reload`, dùng được ngay, không khởi động lại container |
| Thời gian suy luận | 4,9 đến 9,7 ms |
| Publish MQTT | Đúng chu kỳ, payload đúng hợp đồng |
| Lưu CSDL | Đủ cả `target_time` và `model_version` |
| Thiếu dữ liệu | Báo rõ "mới có 0/21 khung dữ liệu", không sập |
| Tính MAE/RMSE | Nạp 40 dự báo quá khứ có sai số biết trước, API trả MAE 3,83 và RMSE 4,15, **khớp chính xác với giá trị tính tay** |
| Tắt AI | `AI_ENABLED=false` thì `/health` báo `ai: disabled`, phần còn lại chạy bình thường |

---

## 5. Ba điểm TV5 cần biết về mô hình

Đây là nhận xét kỹ thuật, không phải chê bai. Random Forest hoạt động đúng như bản chất của nó,
vấn đề nằm ở dữ liệu huấn luyện.

### 5.1 Mô hình không ngoại suy được

Random Forest dự đoán bằng cách lấy trung bình các giá trị đã thấy ở lá cây, nên **không bao giờ
trả về giá trị nằm ngoài khoảng đã học**. Mô hình hiện tại chỉ xuất ra được khoảng 15,9% đến 84,6%.

Thử một số cửa sổ giả định:

| Tình huống đưa vào | Mô hình dự báo |
|---|---|
| Ổn định 20% (bếp sạch, không nấu) | 39,8% |
| Ổn định 60% | 70,0% |
| Đang tăng nhanh, 45% và +2%/mẫu | 48,4% |
| Cực đoan 95% (khói dày, nguy hiểm) | **60,2%** |

Dòng cuối là chỗ đáng lo. Khi bếp đang ở mức nguy hiểm 95%, mô hình dự báo 15 phút nữa còn 60%,
tức là báo xu hướng **GIẢM** đúng lúc cần cảnh báo nhất. Ngược lại khi bếp sạch ở 20%, nó dự báo
39,8% và báo **TĂNG** trong khi không có gì xảy ra.

Nguyên nhân: dữ liệu huấn luyện là hình sin dao động đều quanh 50, biên độ 30. Mô hình
chưa bao giờ thấy trạng thái phẳng kéo dài, cũng chưa thấy mức trên 85% hay dưới 16%.

### 5.2 Chỉ số MAE 7,64% chưa dùng được cho báo cáo

Con số đó đo trên chính dữ liệu hình sin nhân tạo. Nó cho biết mô hình học thuộc bài tốt đến đâu,
không cho biết nó dự báo bếp thật tốt đến đâu.

Con số nên đưa vào báo cáo lấy từ `GET /prediction/accuracy?hours=24`, đo trên dữ liệu thật của hệ
thống. Nên chạy sau khi đã thu ít nhất vài giờ dữ liệu có cả lúc nấu lẫn lúc nghỉ.

### 5.3 Cách khắc phục: train lại trên dữ liệu thật

Hệ thống đã lưu sẵn dữ liệu thật trong TimescaleDB. Xuất ra file CSV đúng định dạng mà
`train_ai.py` đang đọc, không phải sửa một dòng code nào:

```powershell
Get-Content tools\export_training_data.sql | `
  docker compose exec -T timescaledb psql -U iot_admin -d iot_kitchen -t -A -F"," `
  > ai\data\real_kitchen_data.csv
```

File sinh ra có đúng 5 cột `timestamp, temperature, humidity, pollution_percent, dP_dt` và đã gộp
về 10 giây/mẫu, tức đúng định dạng mà `train_ai.py` đang đọc. TV5 chỉ cần sửa một dòng:

```python
data_path = 'data/real_kitchen_data.csv'
```

rồi train ngay trong container:

```powershell
docker compose run --rm --entrypoint python ai tv5/train_ai.py
curl.exe -X POST http://localhost:9100/reload
```

Lưu ý về lượng dữ liệu: `train_ai.py` cần tối thiểu 110 mẫu (20 cửa sổ + 90 bước dự báo) mới tạo
được một mẫu huấn luyện, tức khoảng 18 phút dữ liệu. Muốn có tập đủ dùng thì nên chạy hệ thống ít
nhất 3 tiếng, trong đó có cả lúc nấu lẫn lúc bếp nghỉ.

Ba gợi ý khi chuẩn bị dữ liệu: cần có đoạn phẳng kéo dài (bếp không hoạt động), cần có đỉnh trên
85% (đốt khói mạnh), và nên trộn dữ liệu thật với dữ liệu mô phỏng nếu dữ liệu thật còn ít.

Một chi tiết nhỏ nữa: trong `train_test_split`, khi đã đặt `shuffle=False` thì `random_state=42`
không có tác dụng gì, để lại cũng không sai nhưng dễ gây hiểu nhầm khi bảo vệ.

---

## 6. Vận hành

```powershell
# Chạy cả hệ thống
docker compose up -d --build

# Kiểm tra
curl.exe http://localhost:9100/health
curl.exe http://localhost:8000/api/v1/kitchen/prediction
curl.exe "http://localhost:8000/api/v1/kitchen/prediction/accuracy?hours=24"

# Sau khi chép file .pkl mới vào ai/models/ thì nạp lại, không cần khởi động lại container
curl.exe -X POST http://localhost:9100/reload
```

Nếu TV5 gửi file `.pkl` train trên máy khác, phải hỏi phiên bản scikit-learn bên đó
(`pip show scikit-learn`) rồi sửa `ai/requirements.txt` cho khớp. File joblib train bằng phiên bản
này nạp bằng phiên bản khác có thể lỗi hoặc cho kết quả sai lệch. Cách chắc chắn nhất là train
ngay trong container như lệnh ở trên.

---

## 7. Gợi ý cho Dashboard (TV4)

Nhận dự báo qua MQTT topic `iot/kitchen/{device_id}/ai_prediction`, hoặc gọi `GET /prediction` lúc
mới mở trang vì bản tin này không retain.

Nên hiển thị ba thứ: giá trị dự báo kèm nhãn xu hướng, một điểm hoặc đường nét đứt vẽ ở vị trí
`target_time` trên biểu đồ, và cảnh báo sớm khi `predicted_pollution_percent` vượt 50% trong khi
giá trị hiện tại còn thấp. Cảnh báo sớm chính là lý do tồn tại của phần AI.
