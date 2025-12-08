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

# جلب التوكن
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    print("Error: BOT_TOKEN is missing!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ================= إضافة الكوكيز =================
cookies_content = os.getenv("COOKIES_CONTENT")
if cookies_content:
    try:
        with open("cookies.txt", "w") as f:
            f.write(cookies_content)
        print("✅ تم استعادة ملف الكوكيز.")
    except Exception as e:
        print(f"⚠️ خطأ في الكوكيز: {e}")
# ===============================================

TEMP_DIR = "downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=2)
current_tasks = {}
pending_links = {} # لتخزين الرابط مؤقتاً في كلا الوضعين
user_mode = {} # لتخزين وضع التشغيل (new / old)

MAX_TELEGRAM_SIZE = 2000 * 1024 * 1024  # 2GB
COMPRESSION_THRESHOLD = 50 * 1024 * 1024  # 50MB

# =================== Utilities and Core Logic (بدون تغييرات جوهرية) ===================

def get_output_path(extension="mp4"):
    return os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{extension}")

def clear_temp_files():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

def compress_video(input_path):
    # ... (دالة ضغط الفيديو كما هي)
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

def process_download(chat_id, message_id, url, quality, is_audio, abort_flag):
    # ... (دالة التحميل الرئيسية كما هي)
    output_path = get_output_path("mp3" if is_audio else "mp4")
    
    ydl_opts = {
        "outtmpl": output_path.replace(".mp3", "") if is_audio else output_path,
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 15
    }

    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        if quality == "best":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
        else:
            ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        ydl_opts["merge_output_format"] = "mp4"

    if os.path.exists("cookies.txt"):
        ydl_opts["cookiefile"] = "cookies.txt"

    last_update = [0]
    ydl_opts["progress_hooks"] = [lambda d: progress_hook(d, chat_id, message_id, abort_flag, last_update)]

    final_file = output_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if is_audio:
                final_file = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"
            else:
                final_file = ydl.prepare_filename(info)

        if abort_flag["abort"]:
            bot.edit_message_text("❌ تم إلغاء العملية.", chat_id, message_id)
            return

        if not is_audio and os.path.exists(final_file):
            size = os.path.getsize(final_file)
            if size > COMPRESSION_THRESHOLD:
                bot.edit_message_text(f"⚡ الحجم ({size//1024//1024}MB) كبير، جاري الضغط...", chat_id, message_id)
                final_file = compress_video(final_file)

        file_size = os.path.getsize(final_file)
        if file_size > MAX_TELEGRAM_SIZE:
            bot.edit_message_text(f"❌ الملف كبير جداً ({file_size//1024//1024}MB).", chat_id, message_id)
            return

        bot.edit_message_text("⬆️ جاري الرفع إلى تيليجرام...", chat_id, message_id)
        
        with open(final_file, "rb") as f:
            if is_audio:
                bot.send_audio(chat_id, f, caption=f"🎵 {info.get('title', 'Audio')}\n👤 {info.get('uploader', 'Unknown')}")
            else:
                caption_text = f"🎥 {info.get('title', 'Video')}\n"
                caption_text += f"⚙️ Quality: {quality}p" if quality not in ["best", "720"] else "⚙️ Quality: Best/Default"
                
                bot.send_video(chat_id, f, caption=caption_text)

        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, "✅ تم!")

    except Exception as e:
        print(traceback.format_exc())
        bot.edit_message_text("❌ فشل التحميل. تأكد أن الرابط صالح.", chat_id, message_id)

    finally:
        try:
            if os.path.exists(final_file): os.remove(final_file)
            if os.path.exists(output_path) and output_path != final_file: os.remove(output_path)
        except: pass
        if chat_id in current_tasks: del current_tasks[chat_id]


# =================== Handlers for Mode Selection ===================

@bot.message_handler(commands=["start"])
def handle_start_mode(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_new = types.InlineKeyboardButton("✨ النسخة الحديثة (خيارات الجودة/الصوت)", callback_data="mode_new")
    btn_old = types.InlineKeyboardButton("⚙️ النسخة القديمة (فيديو/صوت مباشر)", callback_data="mode_old")
    markup.add(btn_new, btn_old)
    
    bot.send_message(message.chat.id, 
                     "👋 **مرحباً بك!**\nالرجاء اختيار وضع التشغيل الذي تفضله:", 
                     reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data in ['mode_new', 'mode_old'])
def select_mode_query(call):
    chat_id = call.message.chat.id
    mode = call.data.split('_')[1]
    user_mode[chat_id] = mode
    
    mode_text = "الحديثة (مع خيارات)" if mode == 'new' else "القديمة (تحميل مباشر)"
    
    bot.edit_message_text(f"✅ تم اختيار **النسخة {mode_text}**.\nالآن أرسل رابط الفيديو للبدء.",
                          chat_id, call.message.message_id, parse_mode="Markdown")

# =================== Handlers for New Mode (Quality Selection) ===================

@bot.callback_query_handler(func=lambda call: call.data in ['audio', 'video_360', 'video_720', 'video_1080'])
def handle_new_mode_query(call):
    chat_id = call.message.chat.id
    
    if chat_id not in pending_links:
        bot.answer_callback_query(call.id, "⚠️ الرابط انتهت صلاحيته، أرسله مجدداً.")
        return

    url = pending_links[chat_id]
    del pending_links[chat_id]
    
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    bot.edit_message_text(f"⏳ تم اختيار {call.data.replace('video_', '')}. جاري البدء...", chat_id, call.message.message_id)

    is_audio = (call.data == "audio")
    quality = "720"
    if call.data.startswith("video_"):
        quality = call.data.split("_")[1]

    abort_flag = {"abort": False}
    current_tasks[chat_id] = abort_flag
    
    executor.submit(process_download, chat_id, call.message.message_id, url, quality, is_audio, abort_flag)

# =================== Handlers for Old Mode (Simple Video/Audio Selection) ===================

@bot.callback_query_handler(func=lambda call: call.data in ['type_video', 'type_audio'])
def handle_old_mode_query(call):
    chat_id = call.message.chat.id
    
    if chat_id not in pending_links:
        bot.answer_callback_query(call.id, "❌ الرابط قديم، أرسله مرة أخرى.", show_alert=True)
        return

    url = pending_links[chat_id]
    del pending_links[chat_id]
    
    is_audio = (call.data == "type_audio")
    quality = "best" # في الوضع القديم، نختار أفضل جودة للفيديو
    
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    bot.edit_message_text(f"⏳ جاري التحميل والمعالجة... \nالرجاء الانتظار.", chat_id, call.message.message_id)

    abort_flag = {"abort": False}
    current_tasks[chat_id] = abort_flag
    
    executor.submit(process_download, chat_id, call.message.message_id, url, quality, is_audio, abort_flag)


# =================== Main Message Handler (Traffic Controller) ===================

@bot.message_handler(func=lambda msg: True)
def handle_message(message):
    chat_id = message.chat.id
    
    if not message.text or not message.text.startswith("http"):
        # السماح بالرسائل العادية إذا لم تكن رابط
        return
    
    # 1. تحقق من وضع التشغيل
    if chat_id not in user_mode:
        bot.reply_to(message, "⚠️ يرجى **اختيار وضع التشغيل** أولاً باستخدام أمر /start.")
        return

    # 2. منع التحميل المتعدد
    if chat_id in current_tasks:
        bot.reply_to(message, "⚠️ لديك عملية تحميل جارية بالفعل. انتظر انتهاءها أو استخدم /abort.")
        return

    url = message.text.strip()
    
    if user_mode[chat_id] == 'new':
        # الوضع الحديث: إظهار خيارات الجودة
        pending_links[chat_id] = url
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_audio = types.InlineKeyboardButton("🎵 MP3 (صوت)", callback_data="audio")
        btn_360 = types.InlineKeyboardButton("🎥 360p", callback_data="video_360")
        btn_720 = types.InlineKeyboardButton("🎥 720p", callback_data="video_720")
        btn_1080 = types.InlineKeyboardButton("🎥 1080p", callback_data="video_1080")
        
        markup.add(btn_audio)
        markup.add(btn_360, btn_720, btn_1080)

        bot.send_message(chat_id, "⬇️ اختر الجودة المطلوبة:", reply_markup=markup)
        
    elif user_mode[chat_id] == 'old':
        # الوضع القديم: إظهار خيارات فيديو/صوت بسيطة
        pending_links[chat_id] = url
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_video = types.InlineKeyboardButton("🎥 Video (فيديو)", callback_data="type_video")
        btn_audio = types.InlineKeyboardButton("🎵 Audio (صوت)", callback_data="type_audio")
        
        markup.add(btn_video, btn_audio)

        bot.send_message(chat_id, "⬇️ كيف تريد تحميل هذا الرابط؟", reply_markup=markup)


# =================== Handlers الباقية ===================

@bot.message_handler(commands=["help"])
def handle_help(message):
    current_mode_display = user_mode.get(message.chat.id, "لم يتم الاختيار")
    help_text = f"""
👋 **مرحباً بك في بوت التحميل!**

*وضع التشغيل الحالي: {current_mode_display}*

📌 **الأوامر المتاحة:**
/start  - تغيير وضع التشغيل (النسخة الحديثة/القديمة)
/info   - معلومات المستخدم والمطور
/clear  - تنظيف الملفات المؤقتة من السيرفر
/abort  - إلغاء عملية التحميل الحالية
"""
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=["info"])
def handle_info(message):
    current_mode_display = user_mode.get(message.chat.id, "لم يتم الاختيار")
    info_text = f"""
👤 **معلومات المستخدم:**
الاسم: ziad
ID: {message.from_user.id}
الوضع المختار: {current_mode_display}

🛠 **معلومات البوت:**
النسخة: 3.1 (Unified Edition)
المطور: ALzaio
    """
    bot.send_message(message.chat.id, info_text, parse_mode="Markdown")

@bot.message_handler(commands=["clear"])
def handle_clear(message):
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


# تشغيل البوت
if __name__ == "__main__":
    print("🚀 Bot Started (Unified Edition)...")
    bot.infinity_polling()







