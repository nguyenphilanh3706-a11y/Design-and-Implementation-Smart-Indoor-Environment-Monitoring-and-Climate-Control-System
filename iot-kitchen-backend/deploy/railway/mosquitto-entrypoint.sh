#!/bin/ash
# Sinh file mật khẩu và ACL lúc container khởi động, giống hệt phần command của
# service mosquitto trong docker-compose.yml. Railway không mount được file từ
# repo nên phải làm bằng script nằm sẵn trong image.
set -e

: "${MQTT_BACKEND_PASSWORD:?Thiếu biến MQTT_BACKEND_PASSWORD}"
: "${MQTT_DEVICE_PASSWORD:?Thiếu biến MQTT_DEVICE_PASSWORD}"
: "${MQTT_WEB_PASSWORD:?Thiếu biến MQTT_WEB_PASSWORD}"
DEVICE_ID="${DEVICE_ID:-esp32_kitchen_01}"

mkdir -p /mosquitto/runtime /mosquitto/data
# mosquitto_passwd -c không ghi đè file cũ mà báo lỗi, phải xoá trước
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
