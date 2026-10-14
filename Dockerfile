FROM python:3.11-slim

WORKDIR /app

# نصب FFmpeg برای ساخت و تبدیل GIF
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# نصب وابستگی‌های پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی فایل‌های پروژه
COPY . .

# اجرای ربات
CMD ["python", "-u", "main.py"]
