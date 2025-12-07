import os
import asyncio
import logging
import time
import math
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- 1. الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}
# قاموس لحفظ وقت آخر تحديث للرسالة (لتجنب Flood Wait)
last_update_time = {}

# --- 2. دوال المساعدة (شريط التقدم) ---

def humanbytes(size):
    """تحويل الحجم إلى صيغة مقروءة (MB, GB)"""
    if not size:
        return ""
    power = 2**10
    n = 0
    dic_powerN = {0: ' ', 1: 'KiB', 2: 'MiB', 3: 'GiB', 4: 'TiB'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + dic_powerN.get(n, 'B')

def get_progress_bar_string(current, total):
    """رسم شريط التقدم [■■■□□]"""
    completed = int(current * 10 / total)
    return "■" * completed + "□" * (10 - completed)

async def progress_bar(current, total, status_msg, start_time, operation_name):
    """دالة تحديث الرسالة في تيليجرام"""
    now = time.time()
    # تحديث الرسالة كل 4 ثواني فقط لتجنب الحظر
    if last_update_time.get(status_msg.id) and (now - last_update_time[status_msg.id]) < 4:
        return

    last_update_time[status_msg.id] = now
    
    percentage = current * 100 / total
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    
    # تنسيق الوقت المتبقي
    eta_str = time.strftime("%M:%S", time.gmtime(eta)) if eta < 3600 else "Wait.."

    text = (
        f"**{operation_name}** 🔄\n"
        f"[{get_progress_bar_string(current, total)}] {round(percentage, 2)}%\n"
        f"📊 **H:** {humanbytes(current)} / {humanbytes(total)}\n"
        f"🚀 **S:** {humanbytes(speed)}/s\n"
        f"⏳ **ETA:** {eta_str}"
    )
    
    try:
        await status_msg.edit_text(text)
    except Exception:
        pass

# --- 3. هوك التحميل (yt-dlp Hook) ---
# هذه الدالة تعمل داخل Thread خاص بـ yt-dlp
def ytdlp_progress_hook(d, client, status_msg, start_time, loop):
    if d['status'] == 'downloading':
        try:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            current = d.get('downloaded_bytes', 0)
            
            if total > 0:
                # استدعاء دالة التحديث داخل الـ Loop الرئيسي لتيليجرام
                future = asyncio.run_coroutine_threadsafe(
                    progress_bar(current, total, status_msg, start_time, "جاري التحميل من المصدر"),
                    loop
                )
        except Exception as e:
            pass

# --- 4. أوامر البوت ---

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    await message.reply_text(
        "👋 أهلاً بك! \n\n"
        "🔗 أرسل رابط الفيديو وسأقوم بتحميله.\n"
        "🚀 **يدعم الملفات الكبيرة مع شريط تقدم!** 📊"
    )

@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    try:
        await message.reply_text("تم المسح.")
        # (يمكنك إضافة منطق المسح هنا كما في الكود السابق)
    except:
        pass

@app.on_message(filters.text & ~filters.command(["start", "help", "clear"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    user_urls[chat_id] = message.text.strip()
    
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 Video", callback_data="type_video"),
            InlineKeyboardButton("🎵 Audio", callback_data="type_audio")
        ]
    ])
    await message.reply_text("⬇️ اختر الصيغة:", reply_markup=buttons, quote=True)

@app.on_callback_query()
async def callback_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    url = user_urls.get(chat_id)
    
    if not url:
        await callback_query.answer("❌ الرابط قديم", show_alert=True)
        return

    is_audio = (data == "type_audio")
    
    # رسالة الحالة الأولية
    status_msg = await callback_query.edit_message_text("⏳ جاري تهيئة التحميل...")
    start_time = time.time()
    
    # تشغيل العملية في الخلفية
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_and_upload, client, chat_id, url, is_audio, status_msg, start_time, loop)

def download_and_upload(client, chat_id, url, is_audio, status_msg, start_time, loop):
    file_path = None
    try:
        # إعدادات yt-dlp مع إضافة الـ Hook
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s",
            "restrictfilenames": True,
            # ربط دالة التقدم
            "progress_hooks": [lambda d: ytdlp_progress_hook(d, client, status_msg, start_time, loop)],
        }

        if is_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            })
        else:
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        # --- مرحلة 1: التحميل ---
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media")
            
            if 'requested_downloads' in info:
                file_path = info['requested_downloads'][0]['filepath']
            else:
                filename = ydl.prepare_filename(info)
                if is_audio: filename = os.path.splitext(filename)[0] + ".mp3"
                file_path = filename

        if not os.path.exists(file_path):
            raise Exception("الملف غير موجود")

        # --- مرحلة 2: الرفع ---
        caption = f"✅ **{title}**"
        
        # نقوم بتحديث الرسالة لبدء الرفع
        # نستخدم run_coroutine_threadsafe لأننا داخل thread
        asyncio.run_coroutine_threadsafe(
            status_msg.edit_text("🚀 جاري الرفع إلى تيليجرام..."), loop
        ).result()
        
        # إعادة تعيين وقت البدء لحساب سرعة الرفع بدقة
        upload_start_time = time.time()

        # دالة الرفع في Pyrogram تقبل progress
        # ملاحظة: نستدعي الدالة مباشرة هنا (client methods are sync-friendly inside async context usually, but better await inside async func)
        # لكن لأننا في executor، يجب استدعاء دالة الرفع بشكل متزامن أو استخدام خدعة.
        # الأسهل: استخدام app.send_video الخاص بـ Pyrogram هو async، لذا يجب استخدام run_coroutine_threadsafe
        
        async def upload_async():
            client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
            if is_audio:
                await client.send_audio(
                    chat_id, 
                    file_path, 
                    caption=caption, 
                    title=title,
                    progress=progress_bar, 
                    progress_args=(status_msg, upload_start_time, "جاري الرفع")
                )
            else:
                await client.send_video(
                    chat_id, 
                    file_path, 
                    caption=caption, 
                    supports_streaming=True,
                    progress=progress_bar, 
                    progress_args=(status_msg, upload_start_time, "جاري الرفع")
                )
        
        # تنفيذ الرفع
        asyncio.run_coroutine_threadsafe(upload_async(), loop).result()

        # تنظيف
        asyncio.run_coroutine_threadsafe(status_msg.delete(), loop)
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        asyncio.run_coroutine_threadsafe(
            client.send_message(chat_id, f"❌ خطأ: {str(e)[:50]}"), loop
        )
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    app.run()
