#!/usr/bin/env python3
import os
import uuid
import telebot
from telebot import types
import yt_dlp
import traceback
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
import logging
from collections import defaultdict
import json

# ================= إعدادات البوت =================
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN is missing!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# إعداد التسجيل
logging.basicConfig(
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# مسار ملف الكوكيز الكامل
COOKIE_PATH = os.path.abspath("cookies.txt") if os.path.exists("cookies.txt") else None

# إعداد المجلدات
TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# إدارة المهام
executor = ThreadPoolExecutor(max_workers=2)
current_tasks = {}
pending_links = {}
download_queue = []

# تحميل الإحصائيات من الملف
STATS_FILE = "stats.json"
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return defaultdict(lambda: {"downloads": 0, "total_size": 0}, json.load(f))
    return defaultdict(lambda: {"downloads": 0, "total_size": 0})

def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump(dict(user_stats), f)

user_stats = load_stats()

# الحدود
MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
MAX_RAILWAY_SIZE = 800 * 1024 * 1024   # 800MB لـ Railway المجاني
COMPRESSION_THRESHOLD = 50 * 1024 * 1024
MAX_RETRIES = 3

# =================== أدوات مساعدة ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_old_temp_files():
    """حذف الملفات القديمة فقط (أكثر من ساعة)"""
    now = time.time()
    deleted = 0
    for f in os.listdir(TEMP_DIR):
        path = os.path.join(TEMP_DIR, f)
        if os.path.isfile(path) and now - os.path.getctime(path) > 3600:
            os.remove(path)
            deleted += 1
    logger.info(f"🗑️ Cleared {deleted} old temp files.")

def compress_video(input_path, output_path):
    """ضغط الفيديو باستخدام ffmpeg"""
    try:
        command = [
            "ffmpeg", "-i", input_path,
            "-c:v", "libx264", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
            "-preset", "fast",
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True)
        logger.info(f"✅ Video compressed: {input_path} -> {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ FFmpeg compression failed: {e.stderr.decode()}")
        return False
    except Exception as e:
        logger.error(f"❌ Compression error: {e}")
        return False

def progress_hook(d, chat_id, message_id, abort_flag, last_update):
    if abort_flag["abort"]:
        raise yt_dlp.utils.DownloadError("Cancelled")

    if d["status"] == "downloading":
        now = time.time()
        if now - last_update[0] < 5:
            return
        last_update[0] = now
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        downloaded = d.get("downloaded_bytes", 0)
        percent = (downloaded / total) * 100
        
        try:
            bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id,
                text=f"⏳ جاري التحميل... {percent:.1f}%"
            )
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")
    
    elif d["status"] == "finished":
        logger.info(f"✅ Download finished for chat_id: {chat_id}")

def process_with_retry(ydl_opts, url, max_retries=MAX_RETRIES):
    """محاولة التحميل مع إعادة محاولة تلقائية"""
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info, ydl.prepare_filename(info)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)[:100]}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(2 ** attempt)

# =================== منطق التحميل ===================

def process_download(chat_id, message_id, url, quality, abort_flag):
    """جودة: 'audio' أو '240' أو '480' أو '720' أو '1080'"""
    is_audio = (quality == "audio")
    output_path = get_output_path("mp3" if is_audio else "mp4")
    final_file = output_path
    
    # إعداد التحميل
    ydl_opts = {
        "outtmpl": output_path.replace(".mp3", "") if is_audio else output_path,
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 60,
        "cookiefile": COOKIE_PATH
    }
    
    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }]
    else:
        # اختيار الجودة
        height = int(quality)
        ydl_opts["format"] = f"bestvideo[height<={height}]+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"

    last_update = [0]
    ydl_opts["progress_hooks"] = [lambda d: progress_hook(d, chat_id, message_id, abort_flag, last_update)]

    try:
        # فحص حجم الملف أولاً
        bot.edit_message_text("🔍 جاري التحقق من حجم الفيديو...", chat_id, message_id)
        with yt_dlp.YoutubeDL(dict(ydl_opts, **{"skip_download": True})) as ydl:
            info = ydl.extract_info(url, download=False)
            filesize = info.get("filesize", 0) or info.get("filesize_approx", 0)
            if filesize > MAX_RAILWAY_SIZE:
                bot.edit_message_text(
                    f"❌ الفيديو أكبر من 800MB ({filesize//1024//1024}MB). لا يمكن التحميل في الباقة المجانية.",
                    chat_id,
                    message_id
                )
                return

        # التحميل
        bot.edit_message_text("⬇️ جاري التحميل...", chat_id, message_id)
        info, final_file = process_with_retry(ydl_opts, url)

        if abort_flag["abort"]:
            bot.edit_message_text("❌ تم إلغاء العملية.", chat_id, message_id)
            logger.info(f"Download aborted by user: {chat_id}")
            return

        # تصحيح اسم الملف للصوت
        if is_audio and not final_file.endswith(".mp3"):
            new_path = final_file.rsplit(".", 1)[0] + ".mp3"
            if os.path.exists(final_file):
                shutil.move(final_file, new_path)
                final_file = new_path

        # التحقق من الحجم النهائي
        file_size = os.path.getsize(final_file)
        if file_size > MAX_TELEGRAM_SIZE:
            bot.edit_message_text(f"❌ الملف كبير جداً ({file_size//1024//1024}MB).", chat_id, message_id)
            return

        # ضغط الفيديو إذا كان كبيراً
        if not is_audio and file_size > COMPRESSION_THRESHOLD:
            bot.edit_message_text("🔄 الملف كبير، جاري ضغطه...", chat_id, message_id)
            compressed_path = final_file.replace(".mp4", "_compressed.mp4")
            if compress_video(final_file, compressed_path):
                os.remove(final_file)
                final_file = compressed_path
                file_size = os.path.getsize(final_file)
            else:
                bot.edit_message_text("⚠️ فشل الضغط، سيتم الرفع بدون ضغط...", chat_id, message_id)

        # الرفع
        bot.edit_message_text("⬆️ جاري الرفع...", chat_id, message_id)
        
        with open(final_file, "rb") as f:
            title = info.get('title', 'Media')
            if is_audio:
                bot.send_audio(chat_id, f, caption=f"🎵 {title}")
            else:
                bot.send_video(chat_id, f, caption=f"🎬 {title} ({quality}p)", supports_streaming=True)

        # تحديث الإحصائيات
        user_stats[chat_id]["downloads"] += 1
        user_stats[chat_id]["total_size"] += file_size
        save_stats()
        
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, "✅ تم!")
        logger.info(f"✅ Download completed for user {chat_id}: {title}")

    except Exception as e:
        logger.error(f"Download failed: {traceback.format_exc()}")
        bot.edit_message_text("❌ فشل التحميل. قد يكون الرابط منتهي أو غير مدعوم.", chat_id, message_id)

    finally:
        # تنظيف الملفات
        try:
            if os.path.exists(final_file): os.remove(final_file)
            if os.path.exists(output_path) and output_path != final_file: os.remove(output_path)
        except Exception as e:
            logger.error(f"Failed to cleanup files: {e}")
        
        if chat_id in current_tasks: 
            del current_tasks[chat_id]
        
        process_queue()

def process_queue():
    """معالجة قائمة الانتظار"""
    if download_queue and len(current_tasks) < 2:
        chat_id, message_id, url, quality = download_queue.pop(0)
        
        if chat_id in current_tasks:
            download_queue.insert(0, (chat_id, message_id, url, quality))
            return
        
        bot.edit_message_text("⏳ جاري البدء...", chat_id, message_id)
        
        abort_flag = {"abort": False}
        current_tasks[chat_id] = abort_flag
        
        executor.submit(process_download, chat_id, message_id, url, quality, abort_flag)

# =================== الأوامر ===================

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    welcome = """
👋 **أهلاً بك!**
هذا البوت يحمل من يوتيوب، تيك توك، فيسبوك، والروابط المباشرة.

📌 **الأوامر:**
/start - البداية
/clear - تنظيف الملفات القديمة
/abort - إلغاء التحميل
/stats - إحصائيات التحميل

📤 **كيفية الاستخدام:**
أرسل رابط الفيديو، ثم اختر الجودة المناسبة.
    """
    bot.send_message(message.chat.id, welcome, parse_mode="Markdown")
    logger.info(f"User {message.chat.id} started the bot.")

@bot.message_handler(commands=["clear"])
def handle_clear(message):
    clear_old_temp_files()
    bot.reply_to(message, "🗑️ تم تنظيف الملفات القديمة.")
    logger.info(f"User {message.chat.id} cleared temp files.")

@bot.message_handler(commands=["abort"])
def handle_abort(message):
    chat_id = message.chat.id
    if chat_id in current_tasks:
        current_tasks[chat_id]["abort"] = True
        bot.reply_to(message, "🛑 جاري الإيقاف...")
        logger.info(f"User {chat_id} aborted a download.")
    else:
        bot.reply_to(message, "⚠️ لا يوجد تحميل جاري.")

@bot.message_handler(commands=["stats"])
def handle_stats(message):
    chat_id = message.chat.id
    stats = user_stats[chat_id]
    total_mb = stats["total_size"] // (1024 * 1024)
    
    stats_text = f"""
📊 **إحصائيات التحميل:**

عدد الملفات: {stats['downloads']}
إجمالي الحجم: {total_mb} MB
    """
    bot.send_message(chat_id, stats_text, parse_mode="Markdown")
    logger.info(f"User {chat_id} requested stats: {stats}")

# =================== معالجة الروابط ===================

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text

    if not text or not text.startswith("http"):
        return

    # إرسال قائمة اختيار الجودة
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # أزرار الجودات
    btn_1080 = types.InlineKeyboardButton("🎥 1080p", callback_data="quality_1080")
    btn_720 = types.InlineKeyboardButton("🎥 720p", callback_data="quality_720")
    btn_480 = types.InlineKeyboardButton("🎥 480p", callback_data="quality_480")
    btn_240 = types.InlineKeyboardButton("🎥 240p", callback_data="quality_240")
    btn_audio = types.InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="quality_audio")
    
    markup.add(btn_1080, btn_720, btn_480, btn_240, btn_audio)
    
    msg = bot.send_message(chat_id, "⬇️ اختر جودة التحميل:", reply_markup=markup)
    
    pending_links[chat_id] = {
        "url": text.strip(),
        "message_id": msg.message_id
    }
    logger.info(f"User {chat_id} added a link: {text[:50]}...")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    
    if call.data.startswith("quality_"):
        quality = call.data.split("_")[1]
    else:
        bot.answer_callback_query(call.id, "⚠️ اختيار غير صالح.")
        return

    if chat_id not in pending_links:
        bot.answer_callback_query(call.id, "⚠️ الرابط انتهت صلاحيته.")
        return

    data = pending_links[chat_id]
    url = data["url"]
    original_msg_id = data["message_id"]
    del pending_links[chat_id]

    # إزالة الأزرار
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)

    # التحقق من عدد المهام الجارية
    if chat_id in current_tasks:
        download_queue.append((chat_id, original_msg_id, url, quality))
        queue_position = len(download_queue)
        bot.edit_message_text(
            f"⏳ تمت الإضافة إلى قائمة الانتظار. موقعك: #{queue_position}",
            chat_id,
            call.message.message_id
        )
        logger.info(f"Added to queue for user {chat_id}, position: {queue_position}")
    else:
        bot.edit_message_text("⏳ جاري البدء...", chat_id, call.message.message_id)
        
        abort_flag = {"abort": False}
        current_tasks[chat_id] = abort_flag
        
        executor.submit(process_download, chat_id, original_msg_id, url, quality, abort_flag)
        logger.info(f"Started download for user {chat_id}, quality: {quality}")

# =================== تشغيل البوت ===================

if __name__ == "__main__":
    print("🚀 Bot Started (Enhanced Version with Quality Selection)...")
    logger.info("Bot started successfully")
    
    # استخدام Webhook (موصى به لـ Railway)
    from flask import Flask, request
    app = Flask(__name__)
    
    @app.route('/' + BOT_TOKEN, methods=['POST'])
    def get_message():
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "!", 200
    
    @app.route("/")
    def webhook():
        bot.remove_webhook()
        bot.set_webhook(url=f'https://{os.getenv("RAILWAY_APP_NAME")}.railway.app/{BOT_TOKEN}')
        return "!", 200
    
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
    
    # إذا أردت استخدام Polling بدلاً من Webhook، استبدل السطرين الأخيرين بـ:
    # bot.infinity_polling()








