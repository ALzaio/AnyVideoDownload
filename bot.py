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

# ================= إعدادات البوت =================
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN is missing!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# استعادة الكوكيز (مهم للمواقع المحجوبة)
cookies_content = os.getenv("COOKIES_CONTENT")
if cookies_content:
    try:
        with open("cookies.txt", "w") as f:
            f.write(cookies_content)
        print("✅ Cookies loaded.")
    except Exception as e:
        print(f"⚠️ Cookies error: {e}")

# إعداد المجلدات
TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

# إدارة المهام
executor = ThreadPoolExecutor(max_workers=2)
current_tasks = {}
pending_links = {}

MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
COMPRESSION_THRESHOLD = 50 * 1024 * 1024  # 50MB

# =================== أدوات مساعدة ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

def compress_video(input_path):
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", "-preset", "ultrafast",
        "-crf", "32", "-acodec", "aac", "-b:a", "128k",
        output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=400)
    except subprocess.TimeoutExpired:
        return input_path

    if os.path.exists(output_path) and os.path.getsize(output_path) < size:
        return output_path
    return input_path

def make_bar(percent):
    filled = int(percent / 5)
    empty = 20 - filled
    return f"[{'█'*filled}{'░'*empty}] {percent:.1f}%"

def progress_hook(d, chat_id, message_id, abort_flag, last_update):
    if abort_flag["abort"]:
        raise yt_dlp.utils.DownloadError("Cancelled")

    if d["status"] == "downloading":
        now = time.time()
        if now - last_update[0] < 4:
            return
        last_update[0] = now
        
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
        downloaded = d.get("downloaded_bytes", 0)
        percent = (downloaded / total) * 100
        bar = make_bar(percent)
        
        try:
            bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id,
                text=f"⏳ جاري التحميل...\n{bar}\n{downloaded//1024} KB / {total//1024} KB"
            )
        except: pass

# =================== منطق التحميل الذكي ===================

def run_download(ydl_opts, url):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info), info

def process_download(chat_id, message_id, url, quality, is_audio, abort_flag):
    output_path = get_output_path("mp3" if is_audio else "mp4")
    final_file = output_path
    
    # إعدادات عامة (مهلة طويلة لدعم SharePoint)
    base_opts = {
        "outtmpl": output_path.replace(".mp3", "") if is_audio else output_path,
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 60, 
        "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None
    }
    
    last_update = [0]
    base_opts["progress_hooks"] = [lambda d: progress_hook(d, chat_id, message_id, abort_flag, last_update)]

    try:
        info = None
        
        # --- المحاولة الأولى: حسب طلب المستخدم ---
        try:
            ydl_opts = base_opts.copy()
            if is_audio:
                ydl_opts["format"] = "bestaudio/best"
                ydl_opts["postprocessors"] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
            else:
                # محاولة تحديد الدقة
                if quality == "best":
                    ydl_opts["format"] = "bestvideo+bestaudio/best"
                else:
                    ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
                ydl_opts["merge_output_format"] = "mp4"

            final_file, info = run_download(ydl_opts, url)

        except Exception as e:
            # --- المحاولة الثانية: الوضع المباشر (Fallback) ---
            if abort_flag["abort"]: raise e # إذا المستخدم ألغى، لا نكمل
            
            print(f"Fallback triggered: {e}")
            bot.edit_message_text(f"⚠️ الرابط يتطلب تعامل خاص، جاري التحميل المباشر...", chat_id, message_id)
            
            # إعدادات "حمل أي شيء موجود"
            ydl_opts = base_opts.copy()
            ydl_opts["format"] = "best" # أفضل ملف متاح وخلاص
            if not is_audio and "merge_output_format" in ydl_opts:
                 del ydl_opts["merge_output_format"]

            final_file, info = run_download(ydl_opts, url)

        if abort_flag["abort"]:
            bot.edit_message_text("❌ تم إلغاء العملية.", chat_id, message_id)
            return

        # --- المعالجة بعد التحميل ---
        if is_audio:
            if not final_file.endswith(".mp3"):
                new_path = final_file.rsplit(".", 1)[0] + ".mp3"
                if os.path.exists(final_file):
                    shutil.move(final_file, new_path)
                    final_file = new_path
        elif os.path.exists(final_file):
            size = os.path.getsize(final_file)
            if size > COMPRESSION_THRESHOLD:
                bot.edit_message_text(f"⚡ ضغط الملف ({size//1024//1024}MB)...", chat_id, message_id)
                final_file = compress_video(final_file)

        # --- الرفع ---
        file_size = os.path.getsize(final_file)
        if file_size > MAX_TELEGRAM_SIZE:
            bot.edit_message_text(f"❌ الملف كبير جداً ({file_size//1024//1024}MB).", chat_id, message_id)
            return

        bot.edit_message_text("⬆️ جاري الرفع...", chat_id, message_id)
        
        with open(final_file, "rb") as f:
            caption_text = f"🎬 {info.get('title', 'Media')}\nvia AnyVideoBot"
            if is_audio:
                bot.send_audio(chat_id, f, caption=caption_text)
            else:
                bot.send_video(chat_id, f, caption=caption_text)

        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, "✅ تم التحميل!")

    except Exception as e:
        print(traceback.format_exc())
        bot.edit_message_text("❌ فشل التحميل. الرابط محمي أو غير صالح.", chat_id, message_id)

    finally:
        try:
            if os.path.exists(final_file): os.remove(final_file)
            if os.path.exists(output_path) and output_path != final_file: os.remove(output_path)
        except: pass
        if chat_id in current_tasks: del current_tasks[chat_id]


# =================== الأوامر (Handlers) ===================

@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    welcome_text = """
👋 **أهلاً بك في بوت التحميل الشامل!**

يمكنني تحميل الفيديو والصوت من معظم المواقع (YouTube, TikTok, Instagram, Facebook) وحتى الروابط المباشرة وروابط الجامعات.

📌 **الأوامر المتاحة:**
/start - عرض هذه الرسالة
/clear - تنظيف السيرفر (للمشرفين)
/abort - إلغاء التحميل الحالي
/info - معلومات عن البوت

🚀 **كيف أبدأ؟**
فقط أرسل الرابط وسأعطيك خيارات الجودة.
    """
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=["info"])
def handle_info(message):
    bot.reply_to(message, f"👤 UserID: {message.from_user.id}\n🤖 Version: 4.0 (Unified Smart Core)")

@bot.message_handler(commands=["clear"])
def handle_clear(message):
    clear_temp_files()
    bot.reply_to(message, "🗑️ تم تنظيف الملفات المؤقتة.")

@bot.message_handler(commands=["abort"])
def handle_abort(message):
    chat_id = message.chat.id
    if chat_id in current_tasks:
        current_tasks[chat_id]["abort"] = True
        bot.reply_to(message, "🛑 جاري إيقاف العملية...")
    else:
        bot.reply_to(message, "لا يوجد تحميل حالياً.")

# =================== معالجة الروابط والأزرار ===================

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text

    if not text or not text.startswith("http"):
        return

    if chat_id in current_tasks:
        bot.reply_to(message, "⚠️ لديك عملية جارية، انتظر انتهاءها.")
        return

    # حفظ الرابط وعرض الأزرار
    pending_links[chat_id] = text.strip()
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    # تعريف الأزرار بشكل منفصل لتجنب الأسطر الطويلة
    btn_audio = types.InlineKeyboardButton("🎵 MP3 (صوت)", callback_data="audio")
    btn_360 = types.InlineKeyboardButton("🎥 360p", callback_data="video_360")
    btn_720 = types.InlineKeyboardButton("🎥 720p", callback_data="video_720")
    btn_1080 = types.InlineKeyboardButton("🎥 1080p", callback_data="video_1080")
    
    markup.add(btn_audio)
    markup.add(btn_360, btn_720, btn_1080)

    bot.send_message(chat_id, "⬇️ اختر الجودة المطلوبة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    data = call.data

    if chat_id not in pending_links:
        bot.answer_callback_query(call.id, "⚠️ الرابط انتهى، أرسله مجدداً.")
        return

    url = pending_links[chat_id]
    del pending_links[chat_id]

    # إخفاء الأزرار وبدء التجهيز
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    bot.edit_message_text("⏳ جاري بدء التحميل...", chat_id, call.message.message_id)

    is_audio = (data == "audio")
    quality = "best" # الافتراضي
    
    if data.startswith("video_"):
        quality = data.split("_")[1]

    abort_flag = {"abort": False}
    current_tasks[chat_id] = abort_flag
    
    # بدء التحميل في الخلفية
    executor.submit(process_download, chat_id, call.message.message_id, url, quality, is_audio, abort_flag)

# تشغيل البوت
if __name__ == "__main__":
    print("🚀 Bot Started (Unified Edition)...")
    bot.infinity_polling()







