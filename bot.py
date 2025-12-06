import telebot
from telebot import types
import yt_dlp
import os
import tempfile
import logging

# جلب التوكن
TOKEN = os.environ.get("BOT_TOKEN")

# للتجربة المحلية فقط
if not TOKEN:
    TOKEN = "TOKEN_PLACEHOLDER"

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)

# قاموس مؤقت لحفظ الرابط الخاص بكل مستخدم حتى يضغط على الزر
# Key: Chat ID, Value: The Link
user_links = {}

@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome! Send me a link, and I'll let you choose between Video 🎥 or Audio 🎵."
    )

# 1. استقبال الرابط وعرض الأزرار
@bot.message_handler(func=lambda m: m.text and m.text.startswith("http"))
def handle_link(message):
    url = message.text.strip()
    chat_id = message.chat.id
    
    # حفظ الرابط في الذاكرة المؤقتة
    user_links[chat_id] = url
    
    # إنشاء لوحة الأزرار
    markup = types.InlineKeyboardMarkup()
    btn_video = types.InlineKeyboardButton("🎥 Video", callback_data="dl_video")
    btn_audio = types.InlineKeyboardButton("🎵 Audio (MP3)", callback_data="dl_audio")
    markup.add(btn_video, btn_audio)
    
    bot.reply_to(message, "⬇️ Select the format you want:", reply_markup=markup)

# 2. استقبال ضغطة الزر وتنفيذ التحميل
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    url = user_links.get(chat_id)
    
    if not url:
        bot.answer_callback_query(call.id, "❌ Link expired, please send it again.")
        return

    # تحديد نوع التحميل بناءً على الزر
    is_audio = (call.data == "dl_audio")
    
    # تغيير رسالة الأزرار إلى "جاري التحميل"
    bot.edit_message_text(
        f"⏳ Processing {'Audio 🎵' if is_audio else 'Video 🎥'}...", 
        chat_id, 
        call.message.message_id
    )

    try:
        # إعدادات التحميل
        ydl_options = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": "%(id)s.%(ext)s",
        }

        if is_audio:
            # إعدادات خاصة للصوت (تحويل إلى MP3)
            ydl_options.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            # إعدادات الفيديو (أفضل جودة + MP4)
            ydl_options.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        # البدء في التحميل داخل مجلد مؤقت
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_options["outtmpl"] = os.path.join(tmpdir, "%(id)s.%(ext)s")
            
            with yt_dlp.YoutubeDL(ydl_options) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "Media")[:50]
                extractor = info.get("extractor", "Platform").replace(":", " ").title()

            # البحث عن الملف الناتج
            files = os.listdir(tmpdir)
            if not files:
                raise Exception("No file downloaded.")
            
            file_path = os.path.join(tmpdir, files[0])
            caption = f"✅ {title}\nSource: {extractor}"

            # الإرسال
            with open(file_path, "rb") as f:
                if is_audio:
                    bot.send_audio(chat_id, f, caption=caption, title=title)
                else:
                    bot.send_video(chat_id, f, caption=caption)

        # رسالة تأكيد نهائية وحذف رسالة الانتظار
        bot.delete_message(chat_id, call.message.message_id)
        bot.send_message(chat_id, "✨ Done! Send another link.")

    except Exception as e:
        error_msg = f"❌ Error: {str(e)[:100]}"
        bot.send_message(chat_id, error_msg)
        logging.error(e)

if __name__ == "__main__":
    bot.infinity_polling()

if __name__ == "__main__":
    bot.infinity_polling()

