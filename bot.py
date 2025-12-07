import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
import yt_dlp

# --- 1. الإعدادات والمتغيرات ---
# الحصول على المتغيرات من Railway
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# إعداد السجل (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# إنشاء عميل Pyrogram
# سيقوم بإنشاء ملف جلسة باسم "my_bot.session" تلقائياً
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ذاكرة مؤقتة لحفظ الروابط (Dict)
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

# --- 3. أمر مسح الرسائل (/clear) ---
@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    chat_id = message.chat.id
    status_msg = await message.reply_text("🗑️ جاري التنظيف...")
    
    # تجميع أرقام الرسائل لحذفها دفعة واحدة (أسرع في Pyrogram)
    message_ids_to_delete = [message.id, status_msg.id]
    
    # نحاول تخمين آخر 30 رسالة (لأن Pyrogram لا يجلب التاريخ بسهولة دون صلاحيات إدارية)
    # الطريقة الأفضل هي حذف الرسالة الحالية والسابقة إن وجدت
    start_id = message.id
    for i in range(1, 31):
        message_ids_to_delete.append(start_id - i)
        
    try:
        await client.delete_messages(chat_id, message_ids_to_delete)
    except Exception as e:
        pass # تجاهل الأخطاء إذا كانت الرسائل قديمة جداً

# --- 4. استقبال الرابط وعرض الأزرار ---
# الفلتر: نص يحتوي على http ولا يبدأ بـ /
@app.on_message(filters.text & ~filters.command(["start", "help", "clear"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    # حفظ الرابط
    user_urls[chat_id] = url
    
    # تصميم الأزرار
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

# --- 5. معالجة ضغط الزر والتحميل ---
@app.on_callback_query()
async def callback_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    # التأكد من وجود رابط
    url = user_urls.get(chat_id)
    if not url:
        await callback_query.answer("❌ الرابط قديم، أرسله مرة أخرى.", show_alert=True)
        return

    is_audio = (data == "type_audio")
    
    # تعديل الرسالة لإظهار الانتظار
    await callback_query.edit_message_text(
        f"⏳ جاري التحميل والمعالجة... \nالرجاء الانتظار (قد يستغرق وقتاً للملفات الكبيرة)."
    )
    
    # تشغيل عملية التحميل في Thread منفصل لعدم تجميد البوت
    # لأن yt_dlp مكتبة متزامنة (Blocking)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_and_upload, client, chat_id, url, is_audio, callback_query.message.id)

# --- دالة التحميل والرفع (تعمل في الخلفية) ---
def download_and_upload(client, chat_id, url, is_audio, message_id_to_edit):
    try:
        # إعدادات yt-dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s", # مجلد فرعي للتنظيم
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
                "format": "bestvideo+bestaudio/best", # أفضل جودة ممكنة
                "merge_output_format": "mp4",
            })

        # تحميل الملف
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media Clip")
            extractor = info.get("extractor", "Web")
            
            # البحث عن الملف المحمل
            if 'requested_downloads' in info:
                file_path = info['requested_downloads'][0]['filepath']
            else:
                # طريقة بديلة لتخمين المسار إذا فشل السابق
                filename = ydl.prepare_filename(info)
                if is_audio:
                    filename = os.path.splitext(filename)[0] + ".mp3"
                file_path = filename

        # التأكد من وجود الملف
        if not os.path.exists(file_path):
            raise Exception("الملف غير موجود بعد التحميل.")

        caption = f"✅ **{title}**\nSource: {extractor}\nvia @YourBotName"

        # الرفع باستخدام Pyrogram (يدعم الملفات الكبيرة)
        client.send_chat_action(chat_id,  enums.ChatAction.UPLOAD_DOCUMENT) # إظهار "جاري الرفع.."
        
        if is_audio:
            client.send_audio(chat_id, file_path, caption=caption, title=title)
        else:
            # هنا السحر: Pyrogram يرفع الملفات الكبيرة تلقائياً
            client.send_video(chat_id, file_path, caption=caption, supports_streaming=True)

        # حذف رسالة الانتظار
        client.delete_messages(chat_id, message_id_to_edit)
        
        # حذف الملف من السيرفر
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            client.send_message(chat_id, f"❌ حدث خطأ أثناء التحميل: {str(e)[:100]}")
        except:
            pass
        
        # تنظيف في حالة الخطأ
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)

# تشغيل البوت
if __name__ == "__main__":
    # التأكد من وجود مجلد التحميلات
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    print("Bot is running...")
    app.run()

