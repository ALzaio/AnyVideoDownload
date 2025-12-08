import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# ====================== الإعدادات ======================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ====================== شريط التقدم ======================
def progress_bar(current, total):
    if total == 0:
        return "[░░░░░░░░░░] 0.0%"
    percentage = min(current / total, 1.0)
    filled = int(percentage * 10)
    return f"[{'▓' * filled}{'░' * (10 - filled)}] {percentage*100:.1f}%"

def format_size(size):
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024**2:.2f} MB"

# ====================== الأوامر ======================
@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    await message.reply_text(
        "مرحباً بك في بوت التحميل!\n\n"
        "يدعم يوتيوب • تيك توك • إنستغرام • فيسبوك وأكثر\n"
        "حتى 2 جيجا + تقدم تحميل\n\n"
        "أرسل أي رابط!",
        quote=True
    )

@app.on_message(filters.command("clear") & filters.private)
async def clear(client, message):
    deleted = 0
    async for msg in client.get_chat_history(message.chat.id, limit=100):
        if msg.from_user.is_self:
            try:
                await msg.delete()
                deleted += 1
            except:
                pass
    await message.reply_text(f"تم حذف {deleted} رسالة")

# ====================== استقبال الرابط ======================
@app.on_message(filters.text & filters.regex(r"https?://") & ~filters.command(["start", "help", "clear"]))
async def get_link(client, message):
    url = message.text.strip()
    user_urls[message.chat.id] = url

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("فيديو", callback_data="video"),
         InlineKeyboardButton("صوت", callback_data="audio")]
    ])
    await message.reply_text("اختر نوع التحميل:", reply_markup=keyboard, quote=True)

# ====================== Callback ======================
@app.on_callback_query()
async def callback(client, cb):
    url = user_urls.get(cb.message.chat.id)
    if not url:
        return await cb.answer("الرابط انتهى، أرسل جديد", show_alert=True)

    status_msg = await cb.message.reply_text("جاري تحليل الرابط...")
    await cb.answer()

    asyncio.create_task(download_and_upload(
        client=client,
        chat_id=cb.message.chat.id,
        url=url,
        is_audio=(cb.data == "audio"),
        status_msg=status_msg
    ))

# ====================== التحميل والرفع (الحل النهائي) ======================
async def download_and_upload(client, chat_id, url, is_audio, status_msg):
    file_path = None
    thumb_path = None
    video_id = None

    try:
        # حماية من روابط SharePoint
        if any(d in url.lower() for d in ["sharepoint.com", "1drv.ms", "onedrive.live.com"]) and "/personal/" in url.lower():
            await status_msg.edit_text("هذا رابط OneDrive شخصي - لا يمكن تحميله")
            return

        # إعدادات yt-dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'bestaudio/best' if is_audio else 'best[height<=1080]/best',
            'merge_output_format': 'mp4' if not is_audio else None,
            'writethumbnail': True,
            'noplaylist': True,
            'retries': 5,
        }

        if os.path.exists('youtube_cookies.txt'):
            ydl_opts['cookiefile'] = 'youtube_cookies.txt'

        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        # استخراج المعلومات
        await status_msg.edit_text("جاري استخراج المعلومات...")
        info = yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False)
        if not info:
            raise Exception("فشل استخراج المعلومات")

        title = info.get('title', 'ملف وسائط')
        video_id = info['id']

        # تحديث التقدم بـ reply بدل edit (الحل السحري)
        last_reply = status_msg

        def hook(d):
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    perc = int(downloaded / total * 100)
                    if perc % 10 == 0:  # كل 10%
                        text = f"جاري التحميل...\n{progress_bar(downloaded, total)}\n{format_size(downloaded)} / {format_size(total)}"
                        asyncio.create_task(last_reply.reply_text(text, quote=False))

        ydl_opts['progress_hooks'] = [hook]

        await status_msg.edit_text("بدء التحميل...")
        yt_dlp.YoutubeDL(ydl_opts).download([url])

        # البحث عن الملفات
        import glob
        pattern = os.path.join(DOWNLOAD_DIR, f"*{video_id}*")
        files = glob.glob(pattern)
        video_files = [f for f in files if f.endswith(('.mp4', '.mp3', '.mkv', '.webm', '.m4a'))]
        thumb_files = [f for f in files if f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]

        if not video_files:
            raise Exception("الملف لم يُحمل")

        file_path = video_files[0]
        thumb_path = thumb_files[0] if thumb_files else None

        await status_msg.edit_text("جاري الرفع إلى تيليجرام...")

        caption = f"**{title}**\n\n@YourBotUsername"

        if is_audio:
            await client.send_audio(
                chat_id, file_path,
                caption=caption,
                thumb=thumb_path,
                parse_mode="markdown"
            )
        else:
            await client.send_video(
                chat_id, file_path,
                caption=caption,
                thumb=thumb_path,
                supports_streaming=True,
                parse_mode="markdown"
            )

        await status_msg.edit_text("تم بنجاح!")

    except Exception as e:
        error_text = str(e)[:100]
        if "private" in error_text.lower() or "unavailable" in error_text.lower():
            error_text = "الفيديو خاص أو غير متاح"
        try:
            await status_msg.edit_text(f"فشل التحميل:\n{error_text}")
        except:
            await client.send_message(chat_id, f"فشل التحميل:\n{error_text}")
        logger.error(f"Error: {e}")

    finally:
        # تنظيف
        if video_id:
            for f in os.listdir(DOWNLOAD_DIR):
                if video_id in f:
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except:
                        pass

# ====================== تشغيل ======================
print("البوت شغال الآن - بدون أي coroutine errors!")
app.run()


