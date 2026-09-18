# TimescaleDB cho Railway, có kèm sẵn file tạo bảng.
# Railway không mount được thư mục database/init nên phải nhúng vào image.
# Các file trong /docker-entrypoint-initdb.d chỉ chạy lần đầu, khi volume còn trống.
#
# Trong Railway đặt:  Dockerfile Path = deploy/railway/timescaledb.Dockerfile
#                     Root Directory  = / (gốc repo)
#                     Volume mount    = /var/lib/postgresql/data
FROM timescale/timescaledb:latest-pg17

COPY database/init/*.sql /docker-entrypoint-initdb.d/
