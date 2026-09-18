#!/bin/bash
# =====================================================================
#  SAO LƯU CSDL - chạy tay hoặc đặt lịch tự động
#
#  Chạy tay:       ./deploy/backup.sh
#  Đặt lịch 2h sáng mỗi ngày:
#      crontab -e
#      0 2 * * * cd /home/<user>/iot-kitchen-backend && ./deploy/backup.sh >> backups/cron.log 2>&1
#
#  Giữ lại 7 bản gần nhất, tự xoá bản cũ hơn.
# =====================================================================
set -e
cd "$(dirname "$0")/.."

# Lấy tên CSDL và user từ .env
set -a; . ./.env; set +a
DB="${POSTGRES_DB:-iot_kitchen}"
USER_DB="${POSTGRES_USER:-iot_admin}"

mkdir -p backups
FILE="backups/${DB}-$(date +%F-%H%M).sql.gz"

docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml \
    exec -T timescaledb pg_dump -U "$USER_DB" "$DB" | gzip > "$FILE"

# Xoá bản cũ, chỉ giữ 7 bản mới nhất
ls -1t backups/*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm --

echo "Đã lưu $FILE ($(du -h "$FILE" | cut -f1))"
