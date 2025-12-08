# ==============================
# Dockerfile للبوت AnyVideoDownload
# ==============================

FROM python:3.11-slim

# تثبيت الأدوات الأساسية و ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ الملفات
COPY . /app

# تثبيت المكتبات
RUN pip install --no-cache-dir -r requirements.txt

# إنشاء المجلدات
RUN mkdir -p downloads

CMD ["python3", "bot.py"]
