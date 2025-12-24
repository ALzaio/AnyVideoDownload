# استبدل السطر القديم في ملف Dockerfile بهذا السطر:
RUN pip install --no-cache-dir --force-reinstall -r requirements.txt [cite: 3]
# صورة Python خفيفة وسريعة
FROM python:3.10-slim-bookworm

# منع كتابة الـ pyc وضبط stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ضبط locale لمنع مشاكل Pyrogram على بعض السيرفرات
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# تحديد مجلد العمل
WORKDIR /app

# تثبيت FFmpeg فقط (بدون أدوات غير ضرورية)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# تثبيت pip أسرع
RUN pip install --upgrade pip setuptools wheel --no-cache-dir

# نسخ ملف المتطلبات
COPY requirements.txt .

# تثبيت مكتبات المشروع
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الكود
COPY . .

# تشغيل البوت
CMD ["python", "bot.py"]

