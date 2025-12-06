import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo
import yt_dlp
import os
import tempfile
import logging

# --- إعدادات البوت ---
TOKEN = os.environ.get("BOT_TOKEN")

# فحص للتأكد من وجود التوكن
if not TOKEN:
    print("خطأ: لم يتم وضع التوكن في إعدادات الموقع!")
    # هذا توكن مؤقت فقط لكي لا ينهار الكود عند التجربة المحلية، لكنه لن يعمل على السيرفر بدونه
    TOKEN = "TOKEN_PLACEHOLDER"

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(level=logging.INFO)
user_states = {}

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("Download from TikTok"),
        KeyboardButton("Download from Instagram"),
        KeyboardButton("Bot Info")
    )
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "Welcome to the 2025 Downloader Bot!\nTikTok + Instagram (HD, No Watermark)",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: True)
def handler(message):
    text = message.text
    user_id = message.from_user.id

    if text == "Download from TikTok":
        user_states[user_id] = "tiktok"
        bot.reply_to(message, "Send the TikTok link.", reply_markup=main_menu())
        return

    if text == "Download from Instagram":
        user_states[user_id] = "instagram"
        bot.reply_to(message, "Send the Instagram link.", reply_markup=main_menu())
        return

    if text == "Bot Info":
        bot.reply_to(
            message,
            "Downloader Bot 2025\nOwner: @Ziad",
            reply_markup=main_menu()
        )
        return

    if user_id in user_states and text.startswith("http"):
        url = text.strip()
        status_msg = bot.reply_to(message, "Processing...")

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
                    bot.edit_message_text("Failed to download.", message.chat.id, status_msg.message_id)
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
            bot.send_message(message.chat.id, "Done!", reply_markup=main_menu())
            user_states.pop(user_id, None)

        except Exception as e:
            bot.edit_message_text(f"Error: {e}", message.chat.id, status_msg.message_id)
            user_states.pop(user_id, None)
    else:
        bot.reply_to(message, "Choose from menu first.", reply_markup=main_menu())

if __name__ == "__main__":
    bot.infinity_polling()