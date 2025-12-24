# 1. البدء بالصورة الأساسية (يجب أن يكون أول سطر) 
FROM python:3.10-slim-bookworm

# 2. إعدادات البيئة لضمان استقرار البوت
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# 3. تحديد مجلد العمل
WORKDIR /app

# 4. تثبيت FFmpeg (ضروري جداً لدمج الصوت والفيديو) 
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 5. نسخ ملف المتطلبات أولاً (للاستفادة من الكاش في حال لم تتغير المكتبات) [cite: 2, 3]
COPY requirements.txt .

# 6. تثبيت المكتبات مع ضمان عدم استخدام الكاش القديم [cite: 3]
RUN pip install --upgrade pip --no-cache-dir && \
    pip install --no-cache-dir -r requirements.txt

# 7. نسخ باقي ملفات المشروع (بما في ذلك bot.py) [cite: 3]
COPY . .

# 8. تشغيل البوت
CMD ["python", "bot.py"]




