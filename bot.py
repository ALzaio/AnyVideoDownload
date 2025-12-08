#!/usr/bin/env python3
import os
import uuid
import telebot
import yt_dlp
import traceback
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

# جلب التوكن من متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN is missing!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ================= إضافة الكوكيز من Railway =================
# هذا الجزء يأخذ النص من المتغيرات ويحوله لملف cookies.txt
cookies_content = os.getenv("COOKIES_CONTENT")
if cookies_content:
    try:
        with open("cookies.txt", "w") as f:
            f.write(cookies_content)
        print("✅ تم استعادة ملف الكوكيز من المتغيرات بنجاح.")
    except Exception as e:
        print(f"⚠️ خطأ في كتابة ملف الكوكيز: {e}")
# ==========================================================

TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# تقليل عدد العمال لتناسب السيرفر المجاني
executor = ThreadPoolExecutor(max_workers=2)
current_tasks = {}

MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
COMPRESSION_THRESHOLD = 50 * 1024 * 1024  # 50MB

# =================== Utilities ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    # دالة تنظيف قوية
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR) # يحذف المجلد بما فيه
        os.makedirs(TEMP_DIR, exist_ok=True) # يعيد إنشاء المجلد فارغاً

def compress_video(input_path):
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    # محاولة العثور على ffmpeg
    ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

    # إعدادات ضغط متوسطة لتناسب السيرفرات الضعيفة
    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", "-preset", "ultrafast", # ultrafast لتقليل استهلاك المعالج
        "-crf", "30", "-acodec", "aac", "-b:a", "128k",
        output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300) # مهلة 5 دقائق
    except subprocess.TimeoutExpired:
        return input_path # إذا تأخر الضغط، أعد الملف الأصلي

    if os.path.exists(output_path) and os.path.getsize(output_path) < size:
        return output_path
    return input_path

# =================== Progress Bar ===================

def make_bar(percent):
    filled = int(percent / 5)
    empty = 20 - filled
    return f"[{'█'*filled}{'░'*empty}] {percent:.1f}%"

def progress_hook(d, progress_msg, abort_flag, last_update):
    if abort_flag["abort"]:
        raise yt_dlp.utils.DownloadError("تم إلغاء التحميل من قبل المستخدم")

    if d["status"] == "downloading":
        now = time.time()
        # تحديث الرسالة كل 3 ثواني لتجنب الحظر من تيليجرام (Flood Wait)
        if now - last_update[0] < 3:
            return
        last_update[0] = now
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        downloaded = d.get("downloaded_bytes", 0)
        percent = (downloaded / total) * 100
        bar = make_bar(percent)
        
        try:
            bot.edit_message_text(
                chat_id=progress_msg.chat.id, 
                message_id=progress_msg.message_id,
                text=f"⏳ جاري التحميل...\n{bar}\n{downloaded//1024} KB / {total//1024} KB"
            )
        except Exception: 
            pass

# =================== Core Processing ===================

def process_message(message, abort_flag):
    user_id = message.chat.id
    url = message.text.strip()
    
    # رسالة أولية
    try:
        progress_msg = bot.send_message(user_id, "🔍 جاري فحص الرابط...")
    except:
        return # إذا لم يستطع البوت الإرسال (حظر مثلاً)

    output_path = get_output_path("mp4")
    
    # إعدادات yt-dlp
    ydl_opts = {
        "outtmpl": output_path,
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]", # تحديد الدقة بـ 720 لضمان السرعة
        "merge_output_format": "mp4",
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 15
    }
    
    # استخدام ملف الكوكيز إذا وجد
    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    last_update = [0]
    ydl_opts["progress_hooks"] = [lambda d: progress_hook(d, progress_msg, abort_flag, last_update)]

    final_file = output_path
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                final_file = ydl.prepare_filename(info)
            except Exception as e:
                bot.edit_message_text("❌ الرابط غير صالح أو الموقع غير مدعوم.", user_id, progress_msg.message_id)
                return

        if abort_flag["abort"]:
            bot.send_message(user_id, "❌ تم إلغاء التحميل.")
            return

        # التحقق من الحجم والضغط
        if os.path.exists(final_file):
            file_size = os.path.getsize(final_file)
            
            if file_size > COMPRESSION_THRESHOLD:
                bot.edit_message_text("⚡ الحجم كبير، جاري الضغط...", user_id, progress_msg.message_id)
                final_file = compress_video(final_file)
            
            file_size = os.path.getsize(final_file)
            if file_size > MAX_TELEGRAM_SIZE:
                bot.send_message(user_id, f"❌ الملف كبير جداً ({file_size//1024//1024}MB) ولا يمكن رفعه.")
                return

            bot.edit_message_text("⬆️ جاري الرفع...", user_id, progress_msg.message_id)
            
            with open(final_file, "rb") as f:
                bot.send_video(user_id, f, caption=f"🎥 {info.get('title', 'Video')}")
            
            # حذف رسالة الانتظار
            try: bot.delete_message(user_id, progress_msg.message_id)
            except: pass
            
            bot.send_message(user_id, "✅ تم التحميل بنجاح!")

    except Exception as e:
        print(traceback.format_exc())
        bot.send_message(user_id, "❌ حدث خطأ غير متوقع.")
    
    finally:
        # تنظيف الملف الحالي
        if os.path.exists(output_path): 
            try: os.remove(output_path) 
            except: pass
        if os.path.exists(final_file) and final_file != output_path:
            try: os.remove(final_file)
            except: pass
            
        if user_id in current_tasks:
            del current_tasks[user_id]

# =================== Bot Handlers (الأوامر) ===================

@bot.message_handler(commands=["start", "help"])
def handle_help(message):
    help_text = """
👋 **مرحباً بك في بوت التحميل!**

📌 **الأوامر المتاحة:**
/start  - إظهار هذه القائمة
/info   - معلومات المستخدم والمطور
/clear  - تنظيف الملفات المؤقتة من السيرفر
/abort  - إلغاء عملية التحميل الحالية

🔗 **كيفية الاستخدام:**
فقط أرسل رابط الفيديو (يوتيوب، تيك توك، فيسبوك...) وسيبدأ التحميل فوراً.
    """
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=["info"])
def handle_info(message):
    # تم إضافة اسمك ziad كما طلبت
    info_text = f"""
👤 **معلومات المستخدم:**
الاسم: ziad
ID: {message.from_user.id}

🛠 **معلومات البوت:**
النسخة: 2.0 (Railway Edition)
المطور: ALzaio
    """
    bot.send_message(message.chat.id, info_text)

@bot.message_handler(commands=["clear"])
def handle_clear(message):
    # التأكد أن المستخدم هو المشرف (اختياري، هنا متاح للجميع للتجربة)
    clear_temp_files()
    bot.send_message(message.chat.id, "🗑️ **تم مسح جميع الملفات المؤقتة من السيرفر بنجاح.**", parse_mode="Markdown")

@bot.message_handler(commands=["abort"])
def handle_abort(message):
    user_id = message.chat.id
    if user_id in current_tasks:
        current_tasks[user_id]["abort"] = True
        bot.send_message(user_id, "⛔ تم إرسال أمر الإيقاف، يرجى الانتظار...")
    else:
        bot.send_message(user_id, "⚠️ لا يوجد تحميل جاري حالياً.")

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    # التحقق من أن الرسالة تحتوي على رابط
    if not message.text.startswith("http"):
        bot.reply_to(message, "⚠️ الرجاء إرسال رابط صحيح يبدأ بـ http")
        return

    if message.chat.id in current_tasks:
        bot.reply_to(message, "⚠️ لديك عملية تحميل جارية بالفعل. انتظر انتهاءها أو استخدم /abort")
        return

    abort_flag = {"abort": False}
    current_tasks[message.chat.id] = abort_flag
    
    # بدء المعالجة في خيط منفصل
    executor.submit(process_message, message, abort_flag)

# تشغيل البوت
if __name__ == "__main__":
    print("🚀 البوت يعمل الآن (Ziad Edition)...")
    bot.infinity_polling()






