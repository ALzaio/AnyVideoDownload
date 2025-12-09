#!/usr/bin/env python3
import os
import time
import asyncio
import logging
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

# مكتبات تيليجرام (Pyrogram)
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# مكتبة التحميل
import yt_dlp

# ================= 1. الإعدادات والتوكن =================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# إعدادات المجلدات والحدود (مخصصة لسيرفر Railway الضعيف)
DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 900 * 1024 * 1024  # 900MB (الحد الأقصى المطلق لحماية القرص)
COMPRESSION_THRESHOLD = 200 * 1024 * 1024  # 200MB (أي ملف أكبر من هذا سيتم ضغطه)

# إعداد السجل (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء العميل
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# تخزين الروابط مؤقتاً
user_pending_links = {}

# executor محدود بـ 2 فقط لحماية الرامات والمعالج
executor = ThreadPoolExecutor(max_workers=2)

# ================= 2. التعامل مع الكوكيز =================
COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("COOKIES_CONTENT")
if cookies_content:
    try:
        with open(COOKIES_FILE, "w") as f:
            f.write(cookies_content)
        logger.info("✅ Cookies file created successfully.")
    except Exception as e:
        logger.error(f"⚠️ Error creating cookies: {e}")

# ================= 3. دوال مساعدة =================

def format_bytes(size):
    """تحويل الحجم إلى صيغة مقروءة (MB, GB)"""
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def compress_video(input_path):
    """ضغط الفيديو باستخدام FFmpeg بأقصى ضغط لتقليل الحمل على CPU"""
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg_path = shutil.which("ffmpeg")
    
    if not ffmpeg_path:
        return input_path 

    # إعدادات ضغط قوية (CRF 35) وسريعة (superfast) ومتوافقة (yuv420p)
    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", 
        "-preset", "superfast", 
        "-crf", "35",  
        "-pix_fmt", "yuv420p", 
        "-acodec", "aac", 
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]
    
    try:
        # مهلة 5 دقائق
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        
        # التحقق من نجاح الضغط وحجم الملف الجديد
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            # التحقق هل الحجم الجديد أصغر فعلاً؟
            if os.path.getsize(output_path) < size:
                os.remove(input_path)
                return output_path
            else:
                os.remove(output_path) # حذف الملف المضغوط لأنه أكبر أو يساوي الأصلي
    except subprocess.TimeoutExpired:
        logger.error("Compression timed out (300s).")
    except Exception as e:
        logger.error(f"Compression failed with exception: {e}")
    
    return input_path

async def progress_bar(current, total, message, start_time):
    """شريط تقدم للرفع فقط (Upload Progress)"""
    now = time.time()
    if (now - start_time[0]) < 5: 
        return
    
    start_time[0] = now
    percent = current * 100 / total
    filled = int(percent / 10)
    bar = '▓' * filled + '░' * (10 - filled)
    speed = current / (now - start_time[1]) if (now - start_time[1]) > 0 else 0
    
    try:
        await message.edit_text(
            f"⬆️ **جاري الرفع...**\n"
            f"{bar} {percent:.1f}%\n"
            f"📦 {format_bytes(current)} / {format_bytes(total)}\n"
            f"🚀 {format_bytes(speed)}/s"
        )
    except:
        pass

# ================= 4. العمال (Workers) =================

def check_file_size_worker(url):
    """التحقق المسبق من حجم الفيديو قبل التحميل"""
    ydl_opts = {
        "quiet": True,
        "nocheckcertificate": True,
        "skip_download": True,
        "format": "best",
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            expected_size = info.get("filesize_approx") or info.get("filesize") or 0
            return True, expected_size
    except Exception as e:
        logger.error(f"Pre-check failed: {e}")
        return None, 0

def download_worker(client, chat_id, message_id, url, quality, is_audio):
    """دالة التحميل والضغط"""
    
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "restrictfilenames": True,
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    if is_audio:
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        if quality == "best":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
        else:
            ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        ydl_opts["merge_output_format"] = "mp4"

    final_path = None
    file_title = "Unknown"

    try:
        # 1. مرحلة التحميل
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_title = info.get('title', 'Video')
            
            if 'requested_downloads' in info:
                final_path = info['requested_downloads'][0]['filepath']
            else:
                final_path = ydl.prepare_filename(info)
                if is_audio and not final_path.endswith(".mp3"):
                    final_path = final_path.rsplit(".", 1)[0] + ".mp3"

        # 2. مرحلة التحقق والضغط
        if not is_audio and final_path and os.path.exists(final_path):
            file_size = os.path.getsize(final_path)
            
            # حماية القرص (طبقة أمان ثانية)
            if file_size > MAX_FILE_SIZE:
                os.remove(final_path)
                return None, None, f"عذراً، الملف ({format_bytes(file_size)}) أكبر من حد السيرفر (900MB)."

            # الضغط إذا تجاوز الحد
            if file_size > COMPRESSION_THRESHOLD:
                msg_text = (
                    f"⚙️ **جاري المعالجة...**\n"
                    f"📁 الحجم الأصلي: {format_bytes(file_size)}\n"
                    f"🔨 يتم ضغط الفيديو لتوفير المساحة...\n"
                    f"⚠️ قد تستغرق العملية بضع دقائق."
                )
                
                client.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=msg_text
                    )
                )
                
                final_path = compress_video(final_path)

        return final_path, file_title, None

    except Exception as e:
        return None, None, str(e)

# ================= 5. معالجات البوت =================

@app.on_message(filters.command(["start", "help"]))
async def start_handler(client, message):
    await message.reply_text(
        "👋 **أهلاً بك!**\n"
        "أرسل رابط فيديو للتحميل.\n"
        "🔹 أدعم: يوتيوب، تيك توك، فيسبوك، انستقرام.\n"
        "🔹 **الحد الأقصى للملفات:** 900MB\n"
        "🧹 أمر التنظيف: /clear"
    )

@app.on_message(filters.command("clear"))
async def clear_handler(client, message):
    try:
        await message.reply_text("🗑️ جاري تنظيف السيرفر...")
        if os.path.exists(DOWNLOAD_DIR):
            shutil.rmtree(DOWNLOAD_DIR)
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        await message.reply_text("✅ تم التنظيف!")
    except:
        pass

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    
    # رسالة الفحص
    status_msg = await message.reply_text("🔎 **جاري فحص الرابط والحجم...**")
    
    loop = asyncio.get_event_loop()
    
    # تشغيل الفحص المسبق
    is_valid, expected_size = await loop.run_in_executor(
        executor, check_file_size_worker, url
    )

    await status_msg.delete()

    if is_valid is None:
        await message.reply_text("❌ فشل الاتصال بالمصدر أو الرابط غير مدعوم.")
        return

    # التحقق من الحجم قبل عرض الأزرار
    if expected_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"⛔ **الملف كبير جداً!**\n"
            f"الحجم المتوقع: {format_bytes(expected_size)}\n"
            f"الحد الأقصى للسيرفر: {format_bytes(MAX_FILE_SIZE)}"
        )
        return

    # إذا الملف مناسب، نعرض الأزرار
    user_pending_links[message.chat.id] = url
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 MP3 (صوت)", callback_data="audio"),
            InlineKeyboardButton("🎥 Best Quality", callback_data="vid_best")
        ],
        [
            InlineKeyboardButton("🎥 1080p", callback_data="vid_1080"),
            InlineKeyboardButton("🎥 720p", callback_data="vid_720"),
            InlineKeyboardButton("🎥 360p", callback_data="vid_360")
        ]
    ])
    
    await message.reply_text(
        f"✅ **الرابط مقبول** ({format_bytes(expected_size) if expected_size else 'غير محدد'})\n"
        "⬇️ اختر الجودة المطلوبة:",
        reply_markup=keyboard,
        quote=True
    )

@app.on_callback_query()
async def callback_handler(client, callback):
    chat_id = callback.message.chat.id
    message_id = callback.message.id
    data = callback.data
    url = user_pending_links.get(chat_id)

    if not url:
        await callback.answer("❌ الرابط منتهي الصلاحية.", show_alert=True)
        return

    is_audio = (data == "audio")
    quality = data.split("_")[1] if data.startswith("vid_") else "720"

    await callback.message.edit_text(f"⏳ **جاري التحميل...**\n⚙️ النوع: {quality if not is_audio else 'MP3'}")
    
    loop = asyncio.get_event_loop()
    
    file_path, title, error = await loop.run_in_executor(
        executor, download_worker, client, chat_id, message_id, url, quality, is_audio
    )

    if error:
        await callback.message.edit_text(f"❌ خطأ: {error}")
        return
        
    if not file_path or not os.path.exists(file_path):
        await callback.message.edit_text("❌ لم يتم العثور على الملف.")
        return

    try:
        await callback.message.edit_text("⬆️ **جاري الرفع...**")
        start_time = [time.time(), time.time()]
        
        caption = f"🎬 **{title}**\n⚙️ Quality: {quality if not is_audio else 'MP3'}\n🤖 via Bot"
        
        await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)

        if is_audio:
            await client.send_audio(
                chat_id, 
                file_path, 
                caption=caption, 
                title=title,
                progress=progress_bar,
                progress_args=(callback.message, start_time)
            )
        else:
            await client.send_video(
                chat_id, 
                file_path, 
                caption=caption, 
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(callback.message, start_time)
            )
        
        await callback.message.delete()
        
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await callback.message.edit_text(f"❌ فشل الرفع: {e}")
    
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except: pass

# ================= 6. التشغيل =================

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    
    print("🚀 Bot is running on Railway...")
    app.run()
