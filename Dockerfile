# استخدام نسخة بايثون خفيفة
FROM python:3.10-slim

# إعداد متغيرات البيئة
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# تحديد مجلد العمل
WORKDIR /app

# تثبيت FFmpeg في طبقة واحدة لتقليل الحجم
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# نسخ ملف المتطلبات وتثبيت المكتبات أولاً
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي كود المشروع
COPY . .

# أمر التشغيل
CMD ["python", "bot.py"]
