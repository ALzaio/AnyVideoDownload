import telebot
from telebot import types
import yt_dlp
import os
import tempfile
import logging

# 1. إعداد التوكن
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    TOKEN = "TOKEN_PLACEHOLDER"

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)

# ذاكرة مؤقتة لحفظ الرابط (User State)
user_urls = {}

# --- أمر البداية ---
@bot.message_handler(commands=['start', 'help'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 أهلاً بك! \n\n"
        "🔗 أرسل أي رابط فيديو (يوتيوب، تيك توك، انستقرام، فيسبوك..).\n"
        "🤔 سأعطيك الخيار لتحميله كـ **فيديو** 🎥 أو **صوت** 🎵.\n\n"
        "🧹 استخدم الأمر /clear لمسح الرسائل."
    )

# --- أمر مسح الرسائل (الذي طلبته) ---
@bot.message_handler(commands=['clear'])
def clear_chat(message):
    chat_id = message.chat.id
    current_msg_id = message.message_id
    
    status_msg = bot.send_message(chat_id, "🗑️ جاري التنظيف...")
    
    # يحاول مسح آخر 30 رسالة
    for i in range(1, 31): 
        try:
            bot.delete_message(chat_id, current_msg_id - i)
        except Exception:
            continue # تجاهل الرسائل القديمة جداً أو المحذوفة

    try:
        bot.delete_message(chat_id, current_msg_id)
        bot.delete_message(chat_id, status_msg.message_id)
    except:
        pass

# --- 1. استقبال أي رابط وعرض الأزرار ---
@bot.message_handler(func=lambda m: m.text and m.text.startswith(("http", "www")))
def handle_link(message):
    try:
        url = message.text.strip()
        chat_id = message.chat.id
        
        # حفظ الرابط في الذاكرة
        user_urls[chat_id] = url
        
        # تصميم الأزرار
        markup = types.InlineKeyboardMarkup()
        btn_video = types.InlineKeyboardButton("🎥 Video (فيديو)", callback_data="type_video")
        btn_audio = types.InlineKeyboardButton("🎵 Audio (صوت)", callback_data="type_audio")
        markup.add(btn_video, btn_audio)
        
        bot.reply_to(message, "⬇️ كيف تريد تحميل هذا الرابط؟", reply_markup=markup)
        
    except Exception as e:
        bot.reply_to(message, "حدث خطأ بسيط، حاول مرة أخرى.")

# --- 2. معالجة ضغط الزر والتحميل ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    url = user_urls.get(chat_id)
    
    if not url:
        bot.answer_callback_query(call.id, "❌ الرابط قديم، أرسله مرة أخرى.")
        return

    is_audio = (call.data == "type_audio")
    
    # رسالة الانتظار
    bot.edit_message_text(
        f"⏳ جاري معالجة {'الصوت 🎵' if is_audio else 'الفيديو 🎥'}...\nالرجاء الانتظار.",
        chat_id,
        call.message.message_id
    )

    try:
        # إعدادات عامة لـ yt-dlp تعمل مع كل المواقع
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": "%(id)s.%(ext)s",
            "restrictfilenames": True, # لضمان عدم وجود رموز غريبة في اسم الملف
        }

        if is_audio:
            # تحويل إلى MP3
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
            })
        else:
            # دمج الفيديو والصوت بأفضل جودة MP4
            ydl_opts.update({
                "format": "bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        # بدء التحميل
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts["outtmpl"] = os.path.join(tmpdir, "%(id)s.%(ext)s")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # محاولة جلب العنوان أو استخدام اسم افتراضي
                title = info.get("title", "Media Clip")[:50]
                extractor = info.get("extractor", "Web").replace(":", " ").title()

            files = os.listdir(tmpdir)
            if not files:
                raise Exception("فشل التحميل، لم يتم العثور على الملف.")
            
            file_path = os.path.join(tmpdir, files[0])
            caption = f"✅ {title}\nSource: {extractor}"

            # الإرسال
            with open(file_path, "rb") as f:
                if is_audio:
                    bot.send_audio(chat_id, f, caption=caption, title=title)
                else:
                    bot.send_video(chat_id, f, caption=caption)
            
            # تنظيف
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_message(chat_id, "✨ تم التحميل!")

    except Exception as e:
        # رسالة خطأ لطيفة للمستخدم
        bot.send_message(chat_id, f"❌ عذراً، لم أستطع تحميل هذا الرابط.\nالسبب: {str(e)[:50]}")
        logging.error(e)

if __name__ == "__main__":
    bot.infinity_polling()
