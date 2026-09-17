# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tv5 ./tv5

# tv5/ai_service.py tìm mô hình ở đường dẫn TƯƠNG ĐỐI 'models/pollution_model.pkl',
# nên thư mục làm việc phải là /app và ./ai/models được gắn vào /app/models.
RUN mkdir -p /app/models /app/data

EXPOSE 9100

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
