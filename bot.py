#!/usr/bin/env python3
import os
import uuid
import telebot
import yt_dlp
import traceback
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import time

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=3)
current_tasks = {}

MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
COMPRESSION_THRESHOLD = 45 * 1024 * 1024  # 45MB

# =================== Utilities ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    for f in os.listdir(TEMP_DIR):
        try: os.remove(os.path.join(TEMP_DIR, f))
        except: pass

def compress_video(input_path):
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD:
        return input_path  # لا حاجة للضغط

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg_path = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"

    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", "-preset", "veryfast",
        "-crf", "28", "-acodec", "aac", "-b:a", "128k",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(output_path) and os.path.getsize(output_path) < size:
        return output_path
    return input_path

# =================== Progress ===================

def make_bar(percent):
    filled = int(percent / 5)
    empty = 20 - filled
    return f"[{'█'*filled}{'░'*empty}] {percent:.1f}%"

def progress_hook(d, progress_msg, abort_flag):
    if abort_flag["abort"]:
        raise yt_dlp.utils.DownloadError("تم إلغاء التحميل من قبل المستخدم")

    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        downloaded = d.get("downloaded_bytes", 0)
        percent = downloaded * 100 / total
        bar = make_bar(percent)
        try:
            bot.edit_message_text(progress_msg.chat.id, progress_msg.message_id,
                                  f"⏳ جاري التحميل...\n{bar}\n{downloaded//1024} KB / {total//1024} KB")
        except: pass

# =================== Processing ===================

def process_message(message, abort_flag):
    user_id = message.chat.id
    url = message.text.strip()

    progress_msg = bot.send_message(user_id, "⏳ جاري التحضير للتحميل...")
    output_path = get_output_path("mp4")
    cookie_file = "cookies.txt"

    ydl_opts = {
        "outtmpl": output_path,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 3,
        "progress_hooks": [lambda d: progress_hook(d, progress_msg, abort_flag)]
    }

    if os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    file_name = output_path
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_name = ydl.prepare_filename(info)
            if not os.path.exists(file_name):
                file_name = output_path

        if abort_flag["abort"]:
            bot.send_message(user_id, "❌ تم إلغاء التحميل.")
            return

        # ضغط الفيديو إذا كان كبير
        if os.path.getsize(file_name) > COMPRESSION_THRESHOLD:
            bot.edit_message_text(user_id, progress_msg.message_id, "⚡ جاري ضغط الفيديو لتقليل الحجم...")
            file_name = compress_video(file_name)

        if abort_flag["abort"]:
            bot.send_message(user_id, "❌ تم إلغاء العملية أثناء الضغط.")
            return

        # فحص حجم الفيديو للرفع على Telegram
        file_size = os.path.getsize(file_name)
        if file_size > MAX_TELEGRAM_SIZE:
            bot.send_message(user_id, f"❌ حجم الملف كبير جدًا ({file_size//1024//1024}MB) ولا يمكن رفعه.")
            return

        with open(file_name, "rb") as f:
            bot.send_video(user_id, f)

        bot.send_message(user_id, "✅ تم رفع الفيديو بنجاح!")

    except Exception:
        print(traceback.format_exc())
        bot.send_message(user_id, "❌ حدث خطأ أثناء التحميل. الرجاء تجربة رابط آخر.")
    finally:
        for f in [output_path, file_name]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass
        # إزالة المهمة من القائمة
        if user_id in current_tasks:
            del current_tasks[user_id]

# =================== Bot Handlers ===================

@bot.message_handler(commands=["clear"])
def handle_clear(message):
    clear_temp_files()
    bot.send_message(message.chat.id, "🗑️ تم مسح جميع الملفات المؤقتة.")

@bot.message_handler(commands=["abort"])
def handle_abort(message):
    user_id = message.chat.id
    if user_id in current_tasks:
        current_tasks[user_id]["abort"] = True
        bot.send_message(user_id, "⛔ جاري إلغاء التحميل الحالي...")
    else:
        bot.send_message(user_id, "⚠️ لا توجد عملية تحميل حالية للإلغاء.")

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    abort_flag = {"abort": False}
    current_tasks[message.chat.id] = abort_flag
    executor.submit(process_message, message, abort_flag)

print("🚀 البوت يعمل الآن...")
bot.infinity_polling()






