import os
import asyncio
import logging
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# === الإعدادات ===
# تأكد من وضع المتغيرات في Railway Variables
API_ID = int(os.environ.get("API_ID", 12345)) 
API_HASH = os.environ.get("API_HASH", "YOUR_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}
DOWNLOAD_DIR = "downloads"
if os.path.exists(DOWNLOAD_DIR):
    shutil.rmtree(DOWNLOAD_DIR) # تنظيف عند التشغيل
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

download_count = 0 

# === أدوات التنسيق ===
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

# === الأوامر (Commands) ===

@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    text = (
        "👋 **مرحباً بك في بوت التحميل الخاص بـ Ziad!**\n\n"
        "🔥 **المميزات:**\n"
        "• تحميل من يوتيوب، تيك توك، إنستغرام، فيسبوك، والمزيد.\n"
        "• يدعم الفيديو والصوت بجودة عالية.\n\n"
        "📜 **قائمة الأوامر:**\n"
        "1️⃣ /start - تشغيل البوت وعرض القائمة\n"
        "2️⃣ /info - عرض معلومات المستخدم والمطور\n"
        "3️⃣ /clear - تنظيف محادثة البوت\n\n"
        "⬇️ **فقط أرسل الرابط لتبدأ التحميل!**"
    )
    await message.reply_text(text, quote=True)

@app.on_message(filters.command("info"))
async def info_command(client, message):
    await message.reply_text(
        "👤 **معلومات المستخدم:**\n\n"
        "👑 **الاسم:** Ziad\n"
        "🚀 **الاستضافة:** Railway Cloud\n"
        "🤖 **حالة البوت:** يعمل بكفاءة 🟢\n"
        "📅 **السنة:** 2025",
        quote=True
    )

@app.on_message(filters.command("clear") & filters.private)
async def clear(client, message):
    status = await message.reply_text("⏳ جاري التنظيف...")
    deleted = 0
    # مسح آخر 100 رسالة (الخاصة بالبوت فقط لأن تيليجرام يمنع مسح رسائل المستخدم في الخاص)
    async for msg in client.get_chat_history(message.chat.id, limit=100):
        if msg.from_user and msg.from_user.is_self:
            try:
                if msg.id != status.id: # لا تحذف رسالة الحالة الحالية
                    await msg.delete()
                    deleted += 1
            except:
                pass
    
    await status.edit_text(f"🧹 تم تنظيف {deleted} رسالة بنجاح!")
    # حذف رسالة التأكيد بعد 3 ثواني
    await asyncio.sleep(3)
    try:
        await status.delete()
    except:
        pass

# === استقبال الروابط ===
@app.on_message(filters.text & filters.regex(r"https?://") & ~filters.command(["start", "help", "clear", "info"]))
async def get_link(client, message):
    url = message.text.strip()
    user_urls[message.chat.id] = url

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 فيديو (Video)", callback_data="video"),
         InlineKeyboardButton("🎵 صوت (Audio)", callback_data="audio")]
    ])
    await message.reply_text("⬇️ **اختر نوع التحميل:**", reply_markup=keyboard, quote=True)

# === معالجة الأزرار (Callback) ===
@app.on_callback_query()
async def callback(client, cb):
    url = user_urls.get(cb.message.chat.id)
    if not url:
        return await cb.answer("❌ انتهت صلاحية الرابط، أرسله مجدداً.", show_alert=True)

    await cb.answer()
    await cb.edit_message_text("⏳ **جاري المعالجة... يرجى الانتظار**")

    # تشغيل عملية التحميل في الخلفية
    asyncio.create_task(download_and_upload(
        client=client,
        chat_id=cb.message.chat.id,
        url=url,
        is_audio=(cb.data == "audio"),
        status_msg=cb.message
    ))

# === دالة الرفع (Upload) ===
async def upload_progress(current, total, client, status_msg):
    try:
        # تحديث كل 5 ثواني أو نسب معينة لتجنب الحظر (FloodWait)
        text = (
            "⬆️ **جاري الرفع إلى تيليجرام...**\n"
            f"{progress_bar(current, total)}\n"
            f"📦 {format_size(current)} / {format_size(total)}"
        )
        # نستخدم try/except لتجاهل الأخطاء إذا لم يتغير النص
        await status_msg.edit_text(text, disable_web_page_preview=True)
    except:
        pass

# === المحرك الرئيسي (Download Engine) ===
async def download_and_upload(client, chat_id, url, is_audio, status_msg):
    global download_count
    file_path = None
    thumb_path = None
    video_id = None
    
    loop = asyncio.get_running_loop()

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'format': 'bestaudio/best' if is_audio else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'writethumbnail': True,
            'noplaylist': True,
            'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None, # إذا كان لديك ملف كوكيز
        }

        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]

        # 1. استخراج المعلومات
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
        
        if not info:
            raise Exception("لم يتم العثور على معلومات الفيديو.")

        title = info.get('title', 'Media File')
        video_id = info['id']
        duration = info.get('duration', 0)

        # 2. إعداد شريط تقدم التحميل
        last_update_time = 0
        
        def download_hook(d):
            nonlocal last_update_time
            if d['status'] == 'downloading':
                now = time.time()
                # تحديث الرسالة كل 3 ثواني فقط
                if now - last_update_time > 3:
                    last_update_time = now
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes') or 0
                    if total > 0:
                        text = (
                            "⬇️ **جاري التحميل من المصدر...**\n"
                            f"{progress_bar(downloaded, total)}\n"
                            f"💾 {format_size(downloaded)} / {format_size(total)}"
                        )
                        asyncio.run_coroutine_threadsafe(
                            status_msg.edit_text(text, disable_web_page_preview=True), loop
                        )

        ydl_opts['progress_hooks'] = [download_hook]

        # 3. التحميل الفعلي
        await status_msg.edit_text("⬇️ **بدء التحميل...**")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, ydl.download, [url])

        # 4. تحديد الملفات الناتجة
        possible_files = [f for f in os.listdir(DOWNLOAD_DIR) if video_id in f]
        
        # تحديد ملف الميديا
        media_extensions = ('.mp3', '.m4a') if is_audio else ('.mp4', '.mkv', '.webm')
        file_path = next((os.path.join(DOWNLOAD_DIR, f) for f in possible_files if f.endswith(media_extensions)), None)
        
        # تحديد الصورة المصغرة
        thumb_extensions = ('.jpg', '.jpeg', '.png', '.webp')
        thumb_path = next((os.path.join(DOWNLOAD_DIR, f) for f in possible_files if f.endswith(thumb_extensions)), None)

        if not file_path:
            raise Exception("فشل العثور على الملف بعد التحميل.")

        # 5. الرفع
        caption = f"🎬 **{title}**\n👤 **ZiAD Downloader**"
        
        await status_msg.edit_text("⬆️ **جاري الرفع...**")
        
        common_args = {
            "chat_id": chat_id,
            "caption": caption,
            "progress": upload_progress,
            "progress_args": (client, status_msg),
            "thumb": thumb_path,
            "parse_mode": enums.ParseMode.MARKDOWN
        }

        if is_audio:
            await client.send_audio(audio=file_path, title=title, performer="Ziad Bot", **common_args)
        else:
            await client.send_video(video=file_path, duration=duration, supports_streaming=True, **common_args)

        # حذف رسالة الحالة بعد الانتهاء
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ **حدث خطأ:**\n`{str(e)[:100]}`")

    finally:
        # 6. التنظيف (Cleanup)
        if video_id:
            for f in os.listdir(DOWNLOAD_DIR):
                if video_id in f:
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except:
                        pass
        
        # تنظيف دوري شامل
        download_count += 1
        if download_count >= 10: # تنظيف كل 10 تحميلات للحفاظ على مساحة Railway
            for f in os.listdir(DOWNLOAD_DIR):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except:
                    pass
            download_count = 0

print("✅ البوت يعمل بنجاح (نسخة زياد المحسنة) ...")
app.run()
