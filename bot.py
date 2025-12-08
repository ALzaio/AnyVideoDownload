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

# ================= إعدادات البوت =================
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN is missing!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# إعداد التسجيل (Logging)
logging.basicConfig(
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# استعادة الكوكيز (للمواقع المحجوبة)
cookies_content = os.getenv("COOKIES_CONTENT")
if cookies_content:
    try:
        with open("cookies.txt", "w") as f:
            f.write(cookies_content)
        logger.info("✅ Cookies loaded.")
    except Exception as e:
        logger.error(f"⚠️ Cookies error: {e}")

# إعداد المجلدات
TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# إدارة المهام
executor = ThreadPoolExecutor(max_workers=2)
current_tasks = {}  # chat_id -> abort_flag
pending_links = {}  # chat_id -> {"url": ..., "message_id": ...}
download_queue = []  # قائمة انتظار: [(chat_id, message_id, url, is_audio)]
user_stats = defaultdict(lambda: {"downloads": 0, "total_size": 0})  # إحصائيات المستخدمين

MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
COMPRESSION_THRESHOLD = 50 * 1024 * 1024  # ضغط فوق 50MB
MAX_RETRIES = 3  # عدد محاولات التحميل

# =================== أدوات مساعدة ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)
    logger.info("🗑️ Temporary files cleared.")

def compress_video(input_path, output_path):
    """ضغط الفيديو باستخدام ffmpeg لحجب أصغر"""
    try:
        command = [
            "ffmpeg", "-i", input_path,
            "-c:v", "libx264", "-crf", "28",  # ضغط جيد
            "-c:a", "aac", "-b:a", "128k",
            "-preset", "fast",
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True)
        logger.info(f"✅ Video compressed: {input_path} -> {output_path}")
        return True
    except Exception as e:
        logger.error(f"❌ Compression failed: {e}")
        return False

def progress_hook(d, chat_id, message_id, abort_flag, last_update):
    if abort_flag["abort"]:
        raise yt_dlp.utils.DownloadError("Cancelled")

    if d["status"] == "downloading":
        now = time.time()
        if now - last_update[0] < 5:  # تحديث كل 5 ثواني
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
    """محاولة التحميل مع إعادة محاولة تلقائية و backoff exponential"""
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info, ydl.prepare_filename(info)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)[:100]}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(2 ** attempt)  # انتظار 1, 2, 4 ثواني بين المحاولات

# =================== منطق التحميل ===================

def process_download(chat_id, message_id, url, is_audio, abort_flag):
    output_path = get_output_path("mp3" if is_audio else "mp4")
    final_file = output_path
    
    # إعدادات التحميل
    ydl_opts = {
        "outtmpl": output_path.replace(".mp3", "") if is_audio else output_path,
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 60,
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None
    }
    
    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }]
    else:
        ydl_opts["format"] = "bestvideo+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"

    last_update = [0]
    ydl_opts["progress_hooks"] = [lambda d: progress_hook(d, chat_id, message_id, abort_flag, last_update)]

    try:
        # محاولة التحميل مع إعادة محاولة
        info, final_file = process_with_retry(ydl_opts, url)

        if abort_flag["abort"]:
            bot.edit_message_text("❌ تم إلغاء العملية.", chat_id, message_id)
            logger.info(f"Download aborted by user: {chat_id}")
            return

        # تصحيح اسم الملف للصوتيات
        if is_audio and not final_file.endswith(".mp3"):
            new_path = final_file.rsplit(".", 1)[0] + ".mp3"
            if os.path.exists(final_file):
                shutil.move(final_file, new_path)
                final_file = new_path

        # التحقق من الحجم
        file_size = os.path.getsize(final_file)
        if file_size > MAX_TELEGRAM_SIZE:
            bot.edit_message_text(f"❌ الملف كبير جداً ({file_size//1024//1024}MB).", chat_id, message_id)
            logger.warning(f"File too large: {file_size} bytes")
            return

        # ضغط الفيديو إذا كان كبيراً
        if not is_audio and file_size > COMPRESSION_THRESHOLD:
            bot.edit_message_text("🔄 الملف كبير، جاري ضغطه...", chat_id, message_id)
            compressed_path = final_file.replace(".mp4", "_compressed.mp4")
            if compress_video(final_file, compressed_path):
                os.remove(final_file)
                final_file = compressed_path
                file_size = os.path.getsize(final_file)
                logger.info(f"Video compressed. New size: {file_size} bytes")
            else:
                bot.edit_message_text("⚠️ فشل الضغط، سيتم الرفع بدون ضغط...", chat_id, message_id)

        # الرفع
        bot.edit_message_text("⬆️ جاري الرفع...", chat_id, message_id)
        
        with open(final_file, "rb") as f:
            caption_text = f"🎬 {info.get('title', 'Media')}"
            if is_audio:
                bot.send_audio(chat_id, f, caption=caption_text)
            else:
                bot.send_video(chat_id, f, caption=caption_text, supports_streaming=True)

        # تحديث الإحصائيات
        user_stats[chat_id]["downloads"] += 1
        user_stats[chat_id]["total_size"] += file_size
        
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, "✅ تم!")
        logger.info(f"✅ Download completed for user {chat_id}: {info.get('title', 'Unknown')}")

    except Exception as e:
        logger.error(f"Download failed: {traceback.format_exc()}")
        bot.edit_message_text("❌ فشل التحميل. قد يكون الرابط منتهي الصلاحية أو غير مدعوم.", chat_id, message_id)

    finally:
        # تنظيف الملفات
        try:
            if os.path.exists(final_file): os.remove(final_file)
            if os.path.exists(output_path) and output_path != final_file: os.remove(output_path)
        except Exception as e:
            logger.error(f"Failed to cleanup files: {e}")
        
        # إزالة من المهام الحالية
        if chat_id in current_tasks: 
            del current_tasks[chat_id]
        
        # معالجة قائمة الانتظار
        process_queue()

def process_queue():
    """معالجة العناصر في قائمة الانتظار"""
    if download_queue and len(current_tasks) < 2:
        chat_id, message_id, url, is_audio = download_queue.pop(0)
        
        # التحقق مما إذا كان المستخدم لديه مهمة جارية
        if chat_id in current_tasks:
            # إعادة إضافة إلى قائمة الانتظار
            download_queue.insert(0, (chat_id, message_id, url, is_audio))
            return
        
        bot.edit_message_text("⏳ جاري البدء...", chat_id, message_id)
        
        abort_flag = {"abort": False}
        current_tasks[chat_id] = abort_flag
        
        executor.submit(process_download, chat_id, message_id, url, is_audio, abort_flag)


# =================== الأوامر ===================

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    welcome = """
👋 **أهلاً بك!**
هذا البوت يحمل من يوتيوب، تيك توك، فيسبوك، والروابط المباشرة.

📌 **الأوامر:**
/start - البداية
/clear - تنظيف الملفات
/abort - إلغاء التحميل
/stats - إحصائيات التحميل
    """
    bot.send_message(message.chat.id, welcome, parse_mode="Markdown")
    logger.info(f"User {message.chat.id} started the bot.")

@bot.message_handler(commands=["clear"])
def handle_clear(message):
    clear_temp_files()
    bot.reply_to(message, "🗑️ تم التنظيف.")
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

    # إرسال رسالة الاختيار
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_video = types.InlineKeyboardButton("🎥 فيديو (أفضل جودة)", callback_data="video")
    btn_audio = types.InlineKeyboardButton("🎵 صوت (MP3)", callback_data="audio")
    markup.add(btn_video, btn_audio)

    msg = bot.send_message(chat_id, "⬇️ اختر طريقة التحميل:", reply_markup=markup)
    
    # تخزين الرابط والرسالة
    pending_links[chat_id] = {
        "url": text.strip(),
        "message_id": msg.message_id
    }
    logger.info(f"User {chat_id} added a link: {text[:50]}...")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    
    if chat_id not in pending_links:
        bot.answer_callback_query(call.id, "⚠️ الرابط انتهى صلاحيته.")
        return

    data = pending_links[chat_id]
    url = data["url"]
    original_msg_id = data["message_id"]
    del pending_links[chat_id]

    # إزالة الأزرار
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)

    is_audio = (call.data == "audio")

    # التحقق من عدد المهام الجارية
    if chat_id in current_tasks:
        # إضافة إلى قائمة الانتظار
        download_queue.append((chat_id, original_msg_id, url, is_audio))
        queue_position = len(download_queue)
        bot.edit_message_text(
            f"⏳ تمت الإضافة إلى قائمة الانتظار. موقعك: #{queue_position}",
            chat_id,
            call.message.message_id
        )
        logger.info(f"Added to queue for user {chat_id}, position: {queue_position}")
    else:
        # بدء التحميل مباشرة
        bot.edit_message_text("⏳ جاري البدء...", chat_id, call.message.message_id)
        
        abort_flag = {"abort": False}
        current_tasks[chat_id] = abort_flag
        
        executor.submit(process_download, chat_id, original_msg_id, url, is_audio, abort_flag)
        logger.info(f"Started download for user {chat_id}, audio: {is_audio}")

if __name__ == "__main__":
    print("🚀 Bot Started (Enhanced Version with Queue & Stats)...")
    logger.info("Bot started successfully")
    bot.infinity_polling()








