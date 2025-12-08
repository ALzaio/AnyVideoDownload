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

# ====================== الأوامر الأساسية ======================
@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    await message.reply_text(
        "مرحباً بك في بوت التحميل الأقوى!\n\n"
        "يدعم كل المواقع + يوتيوب بدون أي فيديو حتى المقيد بالعمر والخاص\n"
        "حتى 2 جيجا + تقدم + صورة مصغرة\n\n"
        "/clear → مسح الشات\n\n"
        "أرسل أي رابط واستمتع!",
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
         InlineKeyboardButton("صوت فقط", callback_data="audio")]
    ])
    await message.reply_text("اختر نوع التحميل:", reply_markup=keyboard, quote=True)

# ====================== Callback ======================
@app.on_callback_query()
async def callback(client, cb):
    url = user_urls.get(cb.message.chat.id)
    if not url:
        return await cb.answer("الرابط انتهى، أرسله من جديد", show_alert=True)

    await cb.edit_message_text("جاري تحليل الرابط...")
    await cb.answer()

    asyncio.create_task(download_and_upload(
        client=client,
        chat_id=cb.message.chat.id,
        url=url,
        is_audio=(cb.data == "audio"),
        status_msg=cb.message
    ))

# ====================== تقدم الرفع ======================
async def upload_progress(current, total, client, status_msg):
    try:
        text = f"جاري الرفع إلى تيليجرام...\n{progress_bar(current, total)}\n{format_size(current)} / {format_size(total)}"
        await status_msg.edit_text(text)
    except:
        pass

# ====================== التحميل والرفع (النسخة النهائية مع كوكيز يوتيوب) ======================
async def download_and_upload(client, chat_id, url, is_audio, status_msg):
    file_path = None
    thumb_path = None
    video_id = None

    try:
        # منع روابط SharePoint/OneDrive الشخصية
        if any(domain in url.lower() for domain in ["sharepoint.com", "1drv.ms", "onedrive.live.com"]) and "/personal/" in url.lower():
            await status_msg.edit_text(
                "هذا الرابط من OneDrive شخصي أو SharePoint\n"
                "البوت لا يستطيع تحميله لأنه يحتاج تسجيل دخول\n"
                "حمّله على جهازك ثم أرسله هنا"
            )
            return

        loop = asyncio.get_running_loop()

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'bestaudio/best' if is_audio else 'best[height<=1080]/bestvideo[height<=1080]+bestaudio/best',
            'merge_output_format': 'mp4' if not is_audio else None,
            'writethumbnail': True,
            'noplaylist': True,
            'cookiefile': 'youtube_cookies.txt',           # الكوكيز السحرية
            'extractor_retries': 5,
            'fragment_retries': 20,
            'retries': 10,
            'sleep_interval': 2,
            'max_sleep_interval': 10,
            'age_limit': 25,  # للفيديوهات +18
        }

        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        # استخراج المعلومات
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))

        if not info:
            raise Exception("تعذر استخراج معلومات الفيديو")

        title = info.get('title', 'ملف وسائط') or 'Media'
        video_id = info['id']

        # Hook آمن تمامًا (تحديث كل 5%)
        last_percentage = 0
        def safe_hook(d):
            nonlocal last_percentage
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes') or 0
                if total > 0:
                    percentage = int(downloaded / total * 100)
                    if percentage >= last_percentage + 5 or percentage in [0, 100]:
                        last_percentage = percentage
                        text = f"جاري التحميل...\n{progress_bar(downloaded, total)}\n{format_size(downloaded)} / {format_size(total)}"
                        asyncio.run_coroutine_threadsafe(status_msg.edit_text(text), loop)

        ydl_opts['progress_hooks'] = [safe_hook]

        await status_msg.edit_text("بدء التحميل من المصدر...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, ydl.download, [url])

        # البحث عن الملف
        files = [f for f in os.listdir(DOWNLOAD_DIR) if video_id in f and f.endswith(('.mp4', '.mp3', '.mkv', '.webm', '.m4a'))]
        if not files:
            raise Exception("فشل في العثور على الملف")
        file_path = os.path.join(DOWNLOAD_DIR, files[0])

        # البحث عن الصورة المصغرة
        thumbs = [f for f in os.listdir(DOWNLOAD_DIR) if video_id in f and f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]
        thumb_path = os.path.join(DOWNLOAD_DIR, thumbs[0]) if thumbs else None

        await status_msg.edit_text("جاري الرفع إلى تيليجرام...")

        caption = f"**{title}**\n\n@YourBotUsername"  # غيّر اليوزر

        kwargs = {
            "caption": caption,
            "progress": upload_progress,
            "progress_args": (client, status_msg),
            "thumb": thumb_path,
            "supports_streaming": not is_audio,
            "parse_mode": "markdown",
        }

        if is_audio:
            await client.send_audio(chat_id, file_path, **kwargs)
        else:
            await client.send_video(chat_id, file_path, **kwargs)

        await status_msg.delete()

    except Exception as e:
        err = str(e)[:150]
        if any(x in err.lower() for x in ["private", "unavailable", "age", "sign in", "login", "429"]):
            err = "الفيديو خاص أو مقيد بالعمر أو محظور في بلدك"
        try:
            await status_msg.edit_text(f"فشل التحميل:\n{err}")
        except:
            pass
        logger.error(f"Error: {e}")

    finally:
        if video_id:
            for f in os.listdir(DOWNLOAD_DIR):
                if video_id in f:
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except:
                        pass

# ====================== تشغيل البوت ======================
print("البوت شغال الآن مع كوكيز يوتيوب - كل حاجة هتشتغل!")
app.run()

