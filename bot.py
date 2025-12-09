#!/usr/bin/env python3
import os
import time
import asyncio
import logging
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

# مكتبات تيليجرام (Pyrogram - الأسرع)
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# مكتبة التحميل
import yt_dlp

# ================= 1. الإعدادات والتوكن =================
# يجب الحصول على API_ID و API_HASH من https://my.telegram.org
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# مجلد التحميلات
DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2GB حد تيليجرام
COMPRESSION_THRESHOLD = 50 * 1024 * 1024  # 50MB (أي ملف أكبر سيتم محاولة ضغطه)

# إعداد السجل (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء العميل
app = Client("super_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# تخزين الروابط مؤقتاً لربطها باختيار المستخدم
user_pending_links = {}

# executor لتشغيل المهام الثقيلة في الخلفية
executor = ThreadPoolExecutor(max_workers=4)

# ================= 2. التعامل مع الكوكيز (من الكود الأول) =================
COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("COOKIES_CONTENT")
if cookies_content:
    try:
        with open(COOKIES_FILE, "w") as f:
            f.write(cookies_content)
        logger.info("✅ Cookies file created successfully.")
    except Exception as e:
        logger.error(f"⚠️ Error creating cookies: {e}")

# ================= 3. دوال مساعدة (ضغط وفورمات) =================

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

# دالة ضغط الفيديو (من الكود الأول) لتقليل الحجم
def compress_video(input_path):
    # إذا الملف أصغر من 50 ميجا، لا تضغطه
    if os.path.getsize(input_path) <= COMPRESSION_THRESHOLD:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg_path = shutil.which("ffmpeg")
    
    if not ffmpeg_path:
        return input_path # FFmpeg غير موجود

    # إعدادات ضغط سريعة ومتوازنة
    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", "-preset", "superfast", 
        "-crf", "30", # جودة متوسطة لتقليل الحجم
        "-acodec", "aac", "-b:a", "128k",
        output_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        # التحقق هل الضغط نجح وكان الملف الناتج أصغر
        if os.path.exists(output_path) and os.path.getsize(output_path) < os.path.getsize(input_path):
            os.remove(input_path) # حذف الأصلي
            return output_path
    except Exception as e:
        logger.error(f"Compression failed: {e}")
    
    return input_path

# شريط التقدم للرفع (ميزة Pyrogram)
async def progress_bar(current, total, message: Message, start_time):
    now = time.time()
    # تحديث الرسالة كل 5 ثواني فقط لتجنب الحظر
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
            f"📦 الحجم: {format_bytes(current)} / {format_bytes(total)}\n"
            f"🚀 السرعة: {format_bytes(speed)}/s"
        )
    except:
        pass

# ================= 4. منطق التحميل (Core Logic) =================

def download_worker(url, quality, is_audio):
    """هذه الدالة تعمل في Thread منفصل لأنها متزامنة (Blocking)"""
    
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "restrictfilenames": True, # لتجنب الأسماء الغريبة
    }

    # إضافة الكوكيز إذا وجدت
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
        # منطق اختيار الجودة (من الكود الأول)
        if quality == "best":
            ydl_opts["format"] = "bestvideo+bestaudio/best"
        else:
            # محاولة جلب الجودة المطلوبة أو أقل، مع دمج الصوت
            ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        ydl_opts["merge_output_format"] = "mp4"

    final_path = None
    file_title = "Unknown"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_title = info.get('title', 'Video')
            
            # تحديد مسار الملف الناتج
            if 'requested_downloads' in info:
                final_path = info['requested_downloads'][0]['filepath']
            else:
                final_path = ydl.prepare_filename(info)
                if is_audio and not final_path.endswith(".mp3"):
                    final_path = final_path.rsplit(".", 1)[0] + ".mp3"

        # مرحلة الضغط (فقط للفيديو)
        if not is_audio and final_path and os.path.exists(final_path):
            final_path = compress_video(final_path)

        return final_path, file_title, None

    except Exception as e:
        return None, None, str(e)

# ================= 5. معالجات البوت (Handlers) =================

@app.on_message(filters.command(["start", "help"]))
async def start_handler(client, message):
    await message.reply_text(
        "👋 **أهلاً بك في البوت المتكامل!**\n\n"
        "أرسل رابط فيديو (يوتيوب، فيسبوك، انستا، تيك توك...) وسأقوم بتحميله.\n"
        "🔹 أدعم اختيار الجودة (1080p, 720p, 360p).\n"
        "🔹 أدعم تحويل الصوت (MP3).\n"
        "🔹 أقوم بضغط الفيديوهات الكبيرة تلقائياً.\n"
        "🧹 للأوامر: /clear لتنظيف المحادثة."
    )

@app.on_message(filters.command("clear"))
async def clear_handler(client, message):
    try:
        await message.reply_text("🗑️ جاري التنظيف...")
        # حذف المجلد المؤقت
        if os.path.exists(DOWNLOAD_DIR):
            shutil.rmtree(DOWNLOAD_DIR)
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        # حذف الرسائل (اختياري)
        msg_ids = [message.id + i for i in range(-20, 2)]
        await client.delete_messages(message.chat.id, msg_ids)
    except:
        pass

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    user_pending_links[message.chat.id] = url
    
    # لوحة أزرار اختيار الجودة (من الكود الأول)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 MP3 (صوت)", callback_data="audio"),
            InlineKeyboardButton("🎥 Best", callback_data="vid_best")
        ],
        [
            InlineKeyboardButton("🎥 1080p", callback_data="vid_1080"),
            InlineKeyboardButton("🎥 720p", callback_data="vid_720"),
            InlineKeyboardButton("🎥 360p", callback_data="vid_360")
        ]
    ])
    
    await message.reply_text(
        "⬇️ **تم استلام الرابط!**\nاختر الجودة المطلوبة:",
        reply_markup=keyboard,
        quote=True
    )

@app.on_callback_query()
async def callback_handler(client, callback):
    chat_id = callback.message.chat.id
    data = callback.data
    url = user_pending_links.get(chat_id)

    if not url:
        await callback.answer("❌ الرابط انتهى، أرسله مجدداً.", show_alert=True)
        return

    # تحديد الإعدادات بناءً على الزر
    is_audio = False
    quality = "720"
    
    if data == "audio":
        is_audio = True
    elif data.startswith("vid_"):
        quality = data.split("_")[1]

    # حذف الأزرار وتحديث الرسالة
    await callback.message.edit_text(f"⏳ **جاري التحميل والمعالجة...**\n⚙️ الجودة: {quality if not is_audio else 'MP3'}")
    
    # بدء التحميل في الخلفية (Thread)
    loop = asyncio.get_event_loop()
    # نستخدم executor لتجنب تجميد البوت
    file_path, title, error = await loop.run_in_executor(
        executor, download_worker, url, quality, is_audio
    )

    if error or not file_path or not os.path.exists(file_path):
        await callback.message.edit_text(f"❌ فشل التحميل: {error or 'Unknown Error'}")
        return

    # الرفع إلى تيليجرام
    try:
        await callback.message.edit_text("⬆️ **جاري الرفع...**")
        start_time = [time.time(), time.time()] # للتحكم في تحديث البروجرس
        
        caption = f"🎬 **{title}**\n⚙️ Quality: {quality if not is_audio else 'MP3'}\n🤖 via @YourBot"
        
        # إرسال Action (جاري رفع ملف...)
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
        
        await callback.message.delete() # حذف رسالة الانتظار
        
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await callback.message.edit_text(f"❌ خطأ أثناء الرفع: {e}")
    
    finally:
        # تنظيف الملف
        if os.path.exists(file_path):
            os.remove(file_path)

# ================= 6. التشغيل =================

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    
    # التحقق من وجود FFmpeg
    if not shutil.which("ffmpeg"):
        logger.warning("⚠️ FFmpeg not found! Compression and MP3 conversion might fail.")

    print("🚀 Super Bot is Running...")
    app.run()
