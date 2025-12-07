import os
import asyncio
import logging
from pyrogram import Client, filters, enums # ✅ تم إضافة enums هنا
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import yt_dlp

# --- 1. الإعدادات والمتغيرات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}

# --- 2. أوامر البداية ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    await message.reply_text(
        "👋 أهلاً بك! \n\n"
        "🔗 أرسل أي رابط فيديو (يوتيوب، تيك توك، انستقرام..).\n"
        "🚀 **أدعم رفع ملفات حتى 2 جيجابايت!**\n"
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

# --- 5. المعالجة ---
@app.on_callback_query()
async def callback_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    url = user_urls.get(chat_id)
    if not url:
        await callback_query.answer("❌ الرابط قديم، أرسله مرة أخرى.", show_alert=True)
        return

    is_audio = (data == "type_audio")
    await callback_query.edit_message_text(
        f"⏳ جاري التحميل والمعالجة... \nالرجاء الانتظار (قد يستغرق وقتاً للملفات الكبيرة)."
    )
    
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_and_upload, client, chat_id, url, is_audio, callback_query.message.id)

def download_and_upload(client, chat_id, url, is_audio, message_id_to_edit):
    file_path = None # تعريف المتغير لتجنب أخطاء النطاق
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s",
            "restrictfilenames": True,
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

        # هنا كان يحدث الخطأ سابقاً، الآن سيعمل بوجود enums
        client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
        
        if is_audio:
            client.send_audio(chat_id, file_path, caption=caption, title=title)
        else:
            client.send_video(chat_id, file_path, caption=caption, supports_streaming=True)

        client.delete_messages(chat_id, message_id_to_edit)
        
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            client.send_message(chat_id, f"❌ حدث خطأ أثناء التحميل: {str(e)[:100]}")
        except:
            pass
        
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    print("Bot is running...")
    app.run()

