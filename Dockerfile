# استخدام نسخة بايثون خفيفة (Slim) لتقليل حجم الصورة وسرعة البناء
FROM python:3.10-slim

# إعداد متغيرات البيئة لمنع بايثون من إنشاء ملفات pyc وتفعيل الإخراج المباشر
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# تحديد مجلد العمل داخل الحاوية
WORKDIR /app

# تحديث النظام وتثبيت FFmpeg (الخطوة الأهم لدمج الصوت والفيديو)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# نسخ ملف المتطلبات أولاً (للاستفادة من كاش Docker)
COPY requirements.txt .

# تثبيت مكتبات بايثون
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع (الكود)
COPY . .

# التأكد من وجود مجلد التحميلات (اختياري لأن الكود ينشئه، لكن جيد للتنظيم)
RUN mkdir -p downloads

# نسخ باقي ملفات المشروع
COPY . .

# أمر تشغيل البوت (تم التعديل إلى bot.py)
CMD ["python", "bot.py"]
