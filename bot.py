#!/usr/bin/env python3
import os
import uuid
import telebot
import yt_dlp
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# ThreadPool لإدارة التحميلات المتعددة
executor = ThreadPoolExecutor(max_workers=3)

# قائمة لتخزين المهام الجارية
current_tasks = {}

def get_output_path(extension="mp4"):
    """Generate safe unique output file path."""
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    for f in os.listdir(TEMP_DIR):
        try:
            os.remove(os.path.join(TEMP_DIR, f))
        except:
            pass

def process_message(message, abort_flag):
    user_id = message.chat.id
    url = message.text.strip()

    bot.send_message(user_id, "⏳ جاري التحميل...")

    output_path = get_output_path("mp4")
    cookie_file = "cookies.txt"
    ydl_opts = {
        "outtmpl": output_path,
        "ffmpeg_location": "/usr/local/bin/ffmpeg",  # Docker
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 3
    }

    if os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    file_name = output_path  # fallback
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_name = ydl.prepare_filename(info)
            if not os.path.exists(file_name):
                file_name = output_path

        if abort_flag["abort"]:
            bot.send_message(user_id, "❌ تم إلغاء التحميل.")
            return

        file_size = os.path.getsize(file_name)
        if file_size > 2000 * 1024 * 1024:
            bot.send_message(user_id, "❌ حجم الملف أكبر من 2GB ولا يمكن رفعه.")
            return

        with open(file_name, "rb") as f:
            bot.send_video(user_id, f)

        bot.send_message(user_id, "✅ تم الإرسال بنجاح!")

    except Exception:
        print(traceback.format_exc())
        bot.send_message(user_id, "❌ حدث خطأ أثناء التحميل. الرجاء تجربة رابط آخر.")
    finally:
        for f in [output_path, file_name]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

# أوامر التحكم
@bot.message_handler(commands=["clear"])
def handle_clear(message):
    clear_temp_files()
    bot.send_message(message.chat.id, "🗑️ تم مسح جميع الملفات المؤقتة.")

@bot.message_handler(commands=["abort"])
def handle_abort(message):
    user_id = message.chat.id
    if user_id in current_tasks:
        current_tasks[user_id]["abort"] = True
        bot.send_message(user_id, "⛔ جاري إلغاء التحميل...")
    else:
        bot.send_message(user_id, "⚠️ لا توجد عملية تحميل حالية لإلغائها.")

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    abort_flag = {"abort": False}
    current_tasks[message.chat.id] = abort_flag
    executor.submit(process_message, message, abort_flag)

print("🚀 البوت يعمل الآن...")
bot.infinity_polling()





