# ==============================
# Dockerfile للبوت AnyVideoDownload
# ==============================

# استخدام صورة Python 3.11 خفيفة
FROM python:3.11-slim

# تثبيت الأدوات الأساسية و ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        && rm -rf /var/lib/apt/lists/*

# إنشاء مجلد العمل
WORKDIR /app

# نسخ ملفات البوت إلى الحاوية
COPY . /app

# تثبيت مكتبات Python المطلوبة
RUN pip install --no-cache-dir \
        pyTelegramBotAPI \
        yt-dlp

# إنشاء مجلد التنزيلات
RUN mkdir -p downloads

# البيئة المطلوبة
ENV BOT_TOKEN="ضع_توكن_البوت_هنا"

# بدء تشغيل البوت
CMD ["python3", "bot.py"]

