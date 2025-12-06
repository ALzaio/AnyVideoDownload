# استخدام نسخة بايثون خفيفة
FROM python:3.10-slim

# هذا هو السطر السحري: تثبيت FFmpeg إجبارياً
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# إعداد مجلد العمل
WORKDIR /app

# نسخ جميع ملفاتك إلى السيرفر
COPY . .

# تثبيت مكتبات بايثون
RUN pip install --no-cache-dir -r requirements.txt

# تشغيل البوت
CMD ["python", "bot.py"]