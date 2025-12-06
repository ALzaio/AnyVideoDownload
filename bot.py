import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
import yt_dlp
import os
import tempfile
import logging

# جلب التوكن من Railway
TOKEN = os.environ.get("BOT_TOKEN")

# حماية في حال كنت تجربه على جهازك بدون توكن
if not TOKEN:
    TOKEN = "TOKEN_PLACEHOLDER"

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)
user_states = {}

def main_menu():
    """إنشاء أزرار شفافة تظهر تحت الرسالة"""
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("TikTok 🎵", callback_data="tiktok"),
        InlineKeyboardButton("Instagram 📸", callback_data="instagram"),
        InlineKeyboardButton("Bot Info ℹ️", callback_data="info")
    )
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 Welcome! Choose a service:",
        reply_markup=main_menu()
    )

# هذا الجزء الجديد للتعامل مع ضغطات الأزرار
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    user_id = call.from_user.id
    
    if call.data == "tiktok":
        user_states[user_id] = "tiktok"
        bot.answer_callback_query(call.id, "TikTok Selected")
        bot.send_message(call.message.chat.id, "Send the TikTok link now 🎵")
        
    elif call.data == "instagram":
        user_states[user_id] = "instagram"
        bot.answer_callback_query(call.id, "Instagram Selected")
        bot.send_message(call.message.chat.id, "Send the Instagram link now 📸")
        
    elif call.data == "info":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Downloader Bot 2025\nOwner: @Ziad")

@bot.message_handler(func=lambda m: True)
def handler(message):
    text = message.text
    user_id = message.from_user.id

    # التأكد أن المستخدم اختار خدمة وأن النص رابط
    if user_id in user_states and text.startswith("http"):
        url = text.strip()
        status_msg = bot.reply_to(message, "⏳ Processing...")

        try:
            ydl_options = {
                "format": "best",
                "quiet": True,
                "outtmpl": "%(id)s.%(ext)s"
            }

            with tempfile.TemporaryDirectory() as tmpdir:
                ydl_options["outtmpl"] = os.path.join(tmpdir, "%(id)s.%(ext)s")

                with yt_dlp.YoutubeDL(ydl_options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get("title", "Media")[:50]

                files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
                files.sort()

                if not files:
                    bot.edit_message_text("❌ Failed to find media.", message.chat.id, status_msg.message_id)
                    return

                if len(files) == 1:
                    with open(files[0], "rb") as f:
                        if files[0].endswith(".mp4"):
                            bot.send_video(message.chat.id, f, caption=title)
                        else:
                            bot.send_photo(message.chat.id, f, caption=title)
                else:
                    media = []
                    open_files = []
                    for f_path in files:
                        f = open(f_path, "rb")
                        open_files.append(f)
                        if f_path.endswith(".mp4"):
                            media.append(InputMediaVideo(f))
                        else:
                            media.append(InputMediaPhoto(f))
                    bot.send_media_group(message.chat.id, media)
                    for f in open_files: f.close()

            bot.delete_message(message.chat.id, status_msg.message_id)
            bot.send_message(message.chat.id, "✅ Done! Choose again:", reply_markup=main_menu())
            user_states.pop(user_id, None)

        except Exception as e:
            bot.edit_message_text(f"❌ Error: {str(e)[:100]}", message.chat.id, status_msg.message_id)
            user_states.pop(user_id, None)
    else:
        bot.reply_to(message, "⚠️ Please choose a service from the buttons first.", reply_markup=main_menu())

if __name__ == "__main__":
    bot.infinity_polling()
