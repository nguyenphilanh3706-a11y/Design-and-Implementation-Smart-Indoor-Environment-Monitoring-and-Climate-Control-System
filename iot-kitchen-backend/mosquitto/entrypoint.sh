#!/bin/ash
# Sinh file mật khẩu + ACL từ biến môi trường rồi chạy Broker.
# Dùng khi triển khai lên Railway hoặc nền tảng khác không chạy được docker compose
# (phần command trong docker-compose.yml chỉ có compose mới hiểu).
set -e

: "${DEVICE_ID:=esp32_kitchen_01}"
: "${MQTT_BACKEND_PASSWORD:?Thieu MQTT_BACKEND_PASSWORD}"
: "${MQTT_DEVICE_PASSWORD:?Thieu MQTT_DEVICE_PASSWORD}"
: "${MQTT_WEB_PASSWORD:?Thieu MQTT_WEB_PASSWORD}"

mkdir -p /mosquitto/runtime /mosquitto/data
# mosquitto_passwd -c khong ghi de file da ton tai -> phai xoa truoc,
# neu khong container se chet o lan khoi dong lai thu hai.
rm -f /mosquitto/runtime/passwd /mosquitto/runtime/acl

mosquitto_passwd -c -b /mosquitto/runtime/passwd backend_svc   "$MQTT_BACKEND_PASSWORD"
mosquitto_passwd -b    /mosquitto/runtime/passwd esp32_device  "$MQTT_DEVICE_PASSWORD"
mosquitto_passwd -b    /mosquitto/runtime/passwd web_dashboard "$MQTT_WEB_PASSWORD"
sed "s/__DEVICE_ID__/$DEVICE_ID/g" /mosquitto/config/acl.template > /mosquitto/runtime/acl

chown -R mosquitto:mosquitto /mosquitto/runtime /mosquitto/data
chmod 700 /mosquitto/runtime
chmod 600 /mosquitto/runtime/passwd /mosquitto/runtime/acl

echo "[init] Da tao 3 tai khoan MQTT va ACL cho thiet bi $DEVICE_ID"
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
