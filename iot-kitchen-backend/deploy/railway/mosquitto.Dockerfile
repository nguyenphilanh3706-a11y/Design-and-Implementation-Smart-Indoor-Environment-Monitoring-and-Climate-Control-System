# Image Mosquitto cho Railway. Railway không chạy docker-compose và không mount
# được file từ repo, nên cấu hình phải nằm sẵn trong image.
#
# Trong Railway đặt:  Dockerfile Path = deploy/railway/mosquitto.Dockerfile
#                     Root Directory  = / (gốc repo)
FROM eclipse-mosquitto:2

COPY mosquitto/config/mosquitto.conf   /mosquitto/config/mosquitto.conf
COPY mosquitto/config/acl.template     /mosquitto/config/acl.template
COPY deploy/railway/mosquitto-entrypoint.sh /entrypoint.sh

# sed xoá ký tự CR: repo checkout trên Windows sẽ có dấu xuống dòng kiểu CRLF,
# khiến script báo "bad interpreter" khi chạy trên Linux.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 1883 9001
ENTRYPOINT ["/bin/ash", "/entrypoint.sh"]
