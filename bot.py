import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo
import yt_dlp
import os
import tempfile
import logging

# جلب التوكن
TOKEN = os.environ.get("BOT_TOKEN")

# للتجربة المحلية فقط (احذف هذا السطر عند الرفع على Railway)
if not TOKEN:
    TOKEN = "ضع_التوكن_هنا_للتجربة" 

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)

@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome! Send me any link (YouTube, Facebook, TikTok, Instagram) and I will download it."
    )

@bot.message_handler(commands=['info'])
def info(message):
    bot.send_message(
        message.chat.id,
        "Downloader Bot 2025\nOwner: @Ziad"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith("http"))
def handler(message):
    url = message.text.strip()
    status_msg = bot.reply_to(message, "⏳ Processing link...")

    try:
        # --- التعديل هنا لتحسين جودة التحميل وضمان صيغة MP4 ---
        ydl_options = {
            "format": "bestvideo+bestaudio/best", # محاولة جلب أفضل جودة
            "merge_output_format": "mp4",          # إجبار التحويل إلى MP4
            "quiet": True,
            "no_warnings": True,
            "outtmpl": "%(id)s.%(ext)s",
            # تقييد حجم الملف لتجنب مشاكل تيليجرام (اختياري، مثلاً 50 ميجا)
            # "max_filesize": 50 * 1024 * 1024 
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_options["outtmpl"] = os.path.join(tmpdir, "%(id)s.%(ext)s")

            with yt_dlp.YoutubeDL(ydl_options) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title", "Media")[:50]
                extractor = info.get("extractor", "Platform").replace(":", " ").title()

            # البحث عن الملفات
            files = [
                os.path.join(tmpdir, f) 
                for f in os.listdir(tmpdir) 
                if f.lower().endswith((".mp4", ".jpg", ".jpeg", ".png", ".webp"))
            ]
            files.sort()

            if not files:
                raise Exception("No MP4 or Image files found (Format might be MKV/WebM).")
            
            # --- إرسال الملفات ---
            # إذا كان ملف واحد
            if len(files) == 1:
                file_path = files[0]
                caption = f"✅ Success from {extractor}\nTitle: {title}"
                
                with open(file_path, "rb") as f:
                    if file_path.endswith(".mp4"):
                        bot.send_video(message.chat.id, f, caption=caption)
                    else:
                        bot.send_photo(message.chat.id, f, caption=caption)
            
            # إذا كانت مجموعة (ألبوم)
            else:
                media_group = []
                open_files = [] # قائمة لحفظ الملفات المفتوحة لإغلاقها لاحقاً
                
                for index, f_path in enumerate(files):
                    # وضع التعليق (Caption) على أول ملف فقط
                    caption = f"✅ Success from {extractor}" if index == 0 else None
                    
                    f = open(f_path, "rb")
                    open_files.append(f)
                    
                    if f_path.endswith(".mp4"):
                        media_group.append(InputMediaVideo(f, caption=caption))
                    else:
                        media_group.append(InputMediaPhoto(f, caption=caption))
                
                # إرسال المجموعة
                if media_group:
                    bot.send_media_group(message.chat.id, media_group)
                
                # إغلاق الملفات يدوياً
                for f in open_files:
                    f.close()

            bot.delete_message(message.chat.id, status_msg.message_id)
            bot.send_message(message.chat.id, "✨ Done!")

    except Exception as e:
        error_msg = f"❌ Error: {str(e)[:100]}"
        # محاولة تعديل الرسالة، إذا فشل نرسل رسالة جديدة
        try:
            bot.edit_message_text(error_msg, message.chat.id, status_msg.message_id)
        except:
            bot.send_message(message.chat.id, error_msg)
            
        logging.error(f"Download Error: {e}")

@bot.message_handler(func=lambda m: True)
def default_response(message):
    bot.reply_to(message, "Please send a valid link starting with http.")

if __name__ == "__main__":
    bot.infinity_polling()
