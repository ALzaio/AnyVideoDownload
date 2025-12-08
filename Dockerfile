# استخدام نسخة بايثون خفيفة وحديثة
FROM python:3.11-slim

# تحديث النظام وتثبيت FFmpeg (ضروري جداً لعمل yt-dlp وضغط الفيديو)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# تحديد مجلد العمل داخل السيرفر
WORKDIR /app

# نسخ ملف المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات البوت (bot.py)
COPY . .

# إنشاء مجلد التنزيلات لضمان وجوده
RUN mkdir -p downloads

# أمر تشغيل البوت
CMD ["python", "bot.py"]
