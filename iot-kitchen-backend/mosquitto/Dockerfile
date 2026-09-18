# syntax=docker/dockerfile:1
# Image Broker tự chứa cấu hình - dùng khi triển khai lên Railway, Fly.io hoặc
# bất kỳ nền tảng nào không chạy được docker compose.
# Chạy ở nhà thì KHÔNG cần file này: docker-compose.yml đã làm việc tương đương
# bằng phần command của service mosquitto.
FROM eclipse-mosquitto:2

COPY config/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY config/acl.template   /mosquitto/config/acl.template
COPY entrypoint.sh         /entrypoint.sh

# Xoá ký tự xuống dòng kiểu Windows: file chép từ Windows sẽ có CRLF và
# làm hỏng dòng shebang, báo lỗi "bad interpreter".
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 1883 9001
ENTRYPOINT ["/entrypoint.sh"]
