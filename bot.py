import os
import asyncio
import logging
import time
import math
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- 1. الإعدادات والمتغيرات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}

# --- دالة مساعدة لتنسيق شريط التقدم ---
def get_progress_text(current, total, type_desc):
    percentage = current * 100 / total
    finished_length = int(percentage / 10)
    progress_bar = "▓" * finished_length + "░" * (10 - finished_length)
    
    # تحويل الحجم إلى ميجابايت
    current_mb = round(current / 1024 / 1024, 2)
    total_mb = round(total / 1024 / 1024, 2)
    
    return (
        f"⏳ **{type_desc}**\n"
        f"[{progress_bar}] {round(percentage, 1)}%\n"
        f"📦 {current_mb} MB / {total_mb} MB"
    )

# --- 2. أوامر البداية ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    await message.reply_text(
        "👋 أهلاً بك! \n\n"
        "🔗 أرسل أي رابط فيديو (يوتيوب، تيك توك، انستقرام..).\n"
        "🚀 **أدعم رفع ملفات حتى 2 جيجابايت!**\n"
        "📊 سأعرض لك نسبة التقدم أثناء التحميل.\n"
        "🧹 استخدم الأمر /clear لمسح الرسائل."
    )

# --- 3. أمر مسح الرسائل ---
@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    chat_id = message.chat.id
    status_msg = await message.reply_text("🗑️ جاري التنظيف...")
    message_ids_to_delete = [message.id, status_msg.id]
    start_id = message.id
    for i in range(1, 31):
        message_ids_to_delete.append(start_id - i)
    try:
        await client.delete_messages(chat_id, message_ids_to_delete)
    except Exception:
        pass

# --- 4. استقبال الرابط ---
@app.on_message(filters.text & ~filters.command(["start", "help", "clear"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    url = message.text.strip()
    user_urls[chat_id] = url
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 Video (فيديو)", callback_data="type_video"),
            InlineKeyboardButton("🎵 Audio (صوت)", callback_data="type_audio")
        ]
    ])
    await message.reply_text(
        "⬇️ كيف تريد تحميل هذا الرابط؟",
        reply_markup=buttons,
        quote=True
    )

# --- 5. المعالجة والتحميل ---
@app.on_callback_query()
async def callback_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    url = user_urls.get(chat_id)
    
    if not url:
        await callback_query.answer("❌ الرابط قديم، أرسله مرة أخرى.", show_alert=True)
        return

    is_audio = (data == "type_audio")
    await callback_query.edit_message_text(f"🚀 جاري تهيئة التحميل...")
    
    # الحصول على الـ Loop الحالي لتمريره للدالة المتزامنة
    loop = asyncio.get_running_loop()
    
    # تشغيل التحميل في خيط منفصل (Thread) لعدم تجميد البوت
    await loop.run_in_executor(
        None, 
        download_and_upload, 
        client, 
        chat_id, 
        url, 
        is_audio, 
        callback_query.message.id,
        loop
    )

def download_and_upload(client, chat_id, url, is_audio, message_id_to_edit, loop):
    file_path = None
    last_update_time = 0

    # --- دالة تتبع التحميل (من المصدر) ---
    def download_hook(d):
        nonlocal last_update_time
        if d['status'] == 'downloading':
            current_time = time.time()
            if current_time - last_update_time > 4: # التحديث كل 4 ثواني
                last_update_time = current_time
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    text = get_progress_text(downloaded, total, "جاري التحميل من المصدر...")
                    try:
                        asyncio.run_coroutine_threadsafe(
                            client.edit_message_text(chat_id, message_id_to_edit, text),
                            loop
                        )
                    except Exception:
                        pass

    # --- دالة تتبع الرفع (إلى تيليجرام) ---
    async def upload_progress(current, total):
        nonlocal last_update_time
        current_time = time.time()
        if current_time - last_update_time > 4:
            last_update_time = current_time
            text = get_progress_text(current, total, "جاري الرفع إلى تيليجرام...")
            try:
                await client.edit_message_text(chat_id, message_id_to_edit, text)
            except Exception:
                pass

    try:
        # إعدادات yt-dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s",
            "restrictfilenames": True,
            "progress_hooks": [download_hook], # إضافة Hook التحميل
        }

        if is_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        # بدء التحميل
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media Clip")
            extractor = info.get("extractor", "Web")
            
            if 'requested_downloads' in info:
                file_path = info['requested_downloads'][0]['filepath']
            else:
                filename = ydl.prepare_filename(info)
                if is_audio:
                    filename = os.path.splitext(filename)[0] + ".mp3"
                file_path = filename

        if not os.path.exists(file_path):
            raise Exception("الملف غير موجود بعد التحميل.")

        caption = f"✅ **{title}**\nSource: {extractor}\nvia @YourBotName"

        # إرسال إشعار بدء الرفع
        asyncio.run_coroutine_threadsafe(
            client.edit_message_text(chat_id, message_id_to_edit, "⬆️ جاري بدء الرفع..."),
            loop
        )

        client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
        
        # الرفع باستعمال دالة الـ Wrapper للتعامل مع الـ Async داخل الـ Executor
        # ملاحظة: لأننا داخل executor، نحتاج لتشغيل دالة الرفع بشكل متزامن وانتظارها
        
        # الطريقة الأفضل هنا: استدعاء دالة الرفع عبر run_coroutine_threadsafe وانتظار النتيجة
        # لكن للتبسيط ولضمان عمل الـ Progress Callback الخاص بـ Pyrogram:
        
        async def perform_upload():
            if is_audio:
                await client.send_audio(
                    chat_id, 
                    file_path, 
                    caption=caption, 
                    title=title, 
                    progress=upload_progress
                )
            else:
                await client.send_video(
                    chat_id, 
                    file_path, 
                    caption=caption, 
                    supports_streaming=True, 
                    progress=upload_progress
                )
        
        future = asyncio.run_coroutine_threadsafe(perform_upload(), loop)
        future.result() # انتظار انتهاء الرفع

        # حذف رسالة التقدم بعد الانتهاء
        asyncio.run_coroutine_threadsafe(
            client.delete_messages(chat_id, message_id_to_edit),
            loop
        )
        
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            error_text = f"❌ حدث خطأ: {str(e)[:100]}"
            asyncio.run_coroutine_threadsafe(
                client.edit_message_text(chat_id, message_id_to_edit, error_text),
                loop
            )
        except:
            pass
        
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    print("Bot is running...")
    app.run()

