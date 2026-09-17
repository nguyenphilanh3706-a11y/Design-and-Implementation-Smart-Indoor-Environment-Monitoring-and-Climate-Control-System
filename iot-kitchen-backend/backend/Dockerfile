# syntax=docker/dockerfile:1
FROM python:3.12-slim

# PYTHONUNBUFFERED: in log ra ngay, không giữ trong bộ đệm -> docker compose logs hiện tức thì
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Chép requirements trước rồi mới chép mã nguồn: sửa code không phải cài lại thư viện
# (Docker dùng lại lớp cache của bước pip install).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Không chạy bằng root - thói quen bắt buộc khi triển khai thật
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Docker Desktop hiển thị "healthy" khi endpoint /health trả về 200.
# Dùng thư viện chuẩn của Python để khỏi cài thêm curl vào image.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
