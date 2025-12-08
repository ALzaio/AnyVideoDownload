import os
import asyncio
import logging
import time
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# === الإعدادات ===
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

download_count = 0  # للتنظيف الدوري

# === شريط التقدم ===
def progress_bar(current, total):
    if total == 0:
        return "[░░░░░░░░░░] 0.0%"
    percentage = min(current / total, 1.0)
    filled = int(percentage * 10)
    return f"[{'▓' * filled}{'░' * (10 - filled)}] {percentage*100:.1f}%"

def format_size(size):
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"

# === الأوامر ===
@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    await message.reply_text(
        "👋 مرحباً بك في بوت التحميل الأقوى!\n\n"
        "🔥 يدعم يوتيوب، تيك توك، إنستغرام، تويتر، فيسبوك، soundcloud...\n"
        "⚡ حتى 2 جيجابايت + تقدم تحميل + رفع + صورة مصغرة\n"
        "🧹 /clear → مسح الشات\n\n"
        "أرسل أي رابط واستمتع!",
        quote=True
    )

@app.on_message(filters.command("clear") & filters.private)
async def clear(client, message):
    deleted = 0
    async for msg in client.get_chat_history(message.chat.id, limit=60):
        if msg.from_user.is_self:
            try:
                await msg.delete()
                deleted += 1
            except:
                pass
    await message.reply_text(f"🧹 تم حذف {deleted} رسالة بنجاح")

# === استقبال الرابط ===
@app.on_message(filters.text & filters.regex(r"https?://") & ~filters.command(["start", "help", "clear"]))
async def get_link(client, message):
    url = message.text.strip()
    user_urls[message.chat.id] = url

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 فيديو", callback_data="video"),
         InlineKeyboardButton("🎵 صوت فقط", callback_data="audio")]
    ])
    await message.reply_text("⬇️ اختر نوع التحميل:", reply_markup=keyboard, quote=True)

# === Callback ===
@app.on_callback_query()
async def callback(client, cb):
    url = user_urls.get(cb.message.chat.id)
    if not url:
        return await cb.answer("الرابط انتهى، أرسله من جديد", show_alert=True)

    await cb.edit_message_text("⏳ جاري تحليل الرابط...")
    await cb.answer()

    asyncio.create_task(download_and_upload(
        client=client,
        chat_id=cb.message.chat.id,
        url=url,
        is_audio=(cb.data == "audio"),
        status_msg=cb.message
    ))

# === دالة الرفع مع التقدم (آمنة 100%) ===
async def upload_progress(current, total, client, status_msg):
    try:
        text = f"⬆️ جاري الرفع...\n{progress_bar(current, total)}\n{format_size(current)} / {format_size(total)}"
        await status_msg.edit_text(text)
    except:
        pass
    await asyncio.sleep(3)  # تحديث كل 3 ثواني تقريبًا

# === التحميل والرفع (النسخة المثالية) ===
async def download_and_upload(client, chat_id, url, is_audio, status_msg):
    global download_count
    file_path = None
    thumb_path = None

    try:
        loop = asyncio.get_running_loop()

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'bestaudio/best' if is_audio else 'best[height<=1080]/best',
            'merge_output_format': 'mp4',
            'writethumbnail': True,
            'noplaylist': True,
            'progress_hooks': [],
        }

        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        # استخراج المعلومات أولاً (بدون تحميل)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, ydl.extract_info, url, False)

        if not info:
            raise Exception("فشل في تحليل الرابط")

        title = info.get('title', 'ملف وسائط') or 'Media'
        video_id = info['id']
        ext = 'mp3' if is_audio else 'mp4'
        file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.{ext}")
        thumb_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.jpg")

        # إعداد الـ hook باستخدام threadsafe
        last_update = 0
        def hook(d):
            nonlocal last_update
            if d['status'] == 'downloading':
                if time.time() - last_update > 3:
                    last_update = time.time()
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                    downloaded = d.get('downloaded_bytes') or 0
                    text = f"⬇️ جاري التحميل...\n{progress_bar(downloaded, total)}\n{format_size(downloaded)} / {format_size(total)}"
                    asyncio.run_coroutine_threadsafe(status_msg.edit_text(text), loop)

        ydl_opts['progress_hooks'] = [hook]

        await status_msg.edit_text("⬇️ بدء التحميل من المصدر...")

        # التحميل الفعلي
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, ydl.download, [url])

        if not os.path.exists(file_path):
            raise Exception("فشل في التحميل")

        await status_msg.edit_text("⬆️ جاري الرفع إلى تيليجرام...")

        caption = f"**{title}**\n\nVia @YourBotUsername"

        common_kwargs = {
            "caption": caption,
            "progress": upload_progress,
            "progress_args": (client, status_msg),
            "supports_streaming": True if not is_audio else False,
            "thumb": thumb_path if os.path.exists(thumb_path) else None,
        }

        if is_audio:
            await client.send_audio(chat_id, file_path, **common_kwargs)
        else:
            await client.send_video(chat_id, file_path, **common_kwargs)

        await status_msg.delete()

        # تنظيف دوري للمجلد
        download_count += 1
        if download_count % 50 == 0:
            for filename in os.listdir(DOWNLOAD_DIR):
                os.remove(os.path.join(DOWNLOAD_DIR, filename))

    except Exception as e:
        error_msg = str(e) if len(str(e)) <= 100 else "خطأ غير معروف"
        if "private" in error_msg.lower() or "unavailable" in error_msg.lower():
            error_msg = "الفيديو خاص أو محذوف أو مقيد بالعمر"
        await status_msg.edit_text(f"❌ فشل التحميل:\n{error_msg}")
        logger.error(f"Error: {e}")
    finally:
        for path in [file_path, thumb_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

# === تشغيل البوت ===
print("🤖 البوت شغال ومستعد للتحميلات!")
app.run()

