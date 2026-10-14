FROM python:3.11-slim

WORKDIR /app

# نصب FFmpeg، فونت‌های فارسی و کتابخانه‌های پردازش متن راست‌به‌چپ
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto-core \
        fonts-noto-extra \
        fontconfig \
        libfreetype6 \
        libharfbuzz0b \
        libfribidi0 \
        libraqm-dev \
        libfreetype6-dev \
        libharfbuzz-dev \
        libfribidi-dev \
        build-essential \
    && fc-cache -f -v \
    && rm -rf /var/lib/apt/lists/*

# نصب وابستگی‌های پایتون
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# کپی فایل‌های پروژه
COPY . .

# اجرای ربات
CMD ["python", "-u", "main.py"]
