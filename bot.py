import os
import asyncio
import logging
import time
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# ------------------- إعدادات البوت -------------------
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "YOUR_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN")

# إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# متغيرات التشغيل
user_urls = {}
last_update_time = {}
cancellation_flags = {}

class TaskCancelled(Exception):
    pass

# ------------------- دوال مساعدة -------------------
def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    units = {0: ' ', 1: 'KiB', 2: 'MiB', 3: 'GiB', 4: 'TiB'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {units.get(n, 'B')}"

def get_progress_bar_string(current, total):
    completed = int(current * 10 / total)
    return "■" * completed + "□" * (10 - completed)

async def progress_bar(current, total, status_msg, start_time, operation_name):
    chat_id = status_msg.chat.id
    if cancellation_flags.get(chat_id): return

    now = time.time()
    if last_update_time.get(status_msg.id) and (now - last_update_time[status_msg.id]) < 4:
        return

    last_update_time[status_msg.id] = now
    percentage = current * 100 / total
    speed = current / (now - start_time) if now > start_time else 0
    eta = (total - current) / speed if speed > 0 else 0
    eta_str = time.strftime("%M:%S", time.gmtime(eta)) if eta < 3600 else "Wait.."

    text = (
        f"**{operation_name}** 🔄\n"
        f"[{get_progress_bar_string(current, total)}] {round(percentage, 2)}%\n"
        f"📊 **Size:** {humanbytes(current)} / {humanbytes(total)}\n"
        f"🚀 **Speed:** {humanbytes(speed)}/s\n"
        f"⏳ **ETA:** {eta_str}"
    )
    try:
        await status_msg.edit_text(
            text, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])
        )
    except:
        pass

# ------------------- دالة الرفع (Async Fix) -------------------
async def upload_file_async(client, chat_id, file_path, title, is_audio, status_msg, start_time):
    """دالة الرفع المنفصلة لتجنب مشاكل Coroutine"""
    await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
    
    try:
        if is_audio:
            await client.send_audio(
                chat_id, file_path, caption=title,
                progress=progress_bar, progress_args=(status_msg, start_time, "Uploading")
            )
        else:
            await client.send_video(
                chat_id, file_path, caption=title, supports_streaming=True,
                progress=progress_bar, progress_args=(status_msg, start_time, "Uploading")
            )
    except Exception as e:
        raise e

# ------------------- YT-DLP Hook -------------------
def ytdlp_progress_hook(d, client, status_msg, start_time, loop, chat_id):
    if cancellation_flags.get(chat_id): raise TaskCancelled()

    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        current = d.get('downloaded_bytes', 0)
        if total > 0:
            # هنا يتم إنشاء كائن Coroutine وتمريره للحلقة
            coro = progress_bar(current, total, status_msg, start_time, "Downloading")
            asyncio.run_coroutine_threadsafe(coro, loop)

# ------------------- الأوامر (Commands) -------------------

# 1. أمر البدء والمساعدة
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    txt = (
        "👋 **Hello! Welcome to Media Downloader**\n\n"
        "Send me any link (YouTube, TikTok, Instagram...) to download.\n\n"
        "**🤖 Available Commands:**\n"
        "• /start - Show this menu\n"
        "• /help - Help guide\n"
        "• /clear - Clean bot messages\n"
        "• /info - About the developer\n"
    )
    await message.reply_text(txt)

# 2. أمر المعلومات (تمت إعادته)
@app.on_message(filters.command("info"))
async def info_command(client, message):
    await message.reply_text(
        "👤 **Developer:** Ziad\n"
        "🔧 **Platform:** Railway\n"
        "🤖 **Bot Version:** 3.0"
    )

# 3. أمر التنظيف (تم إصلاحه)
@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    chat_id = message.chat.id
    status = await message.reply_text("🧹 **Cleaning chat...**")
    
    # حذف آخر 50 رسالة (تجنباً لحذف رسائل قديمة جداً)
    messages_to_delete = [status.id, message.id]
    
    # نقوم بتوليد معرفات الرسائل السابقة بشكل تقريبي للحذف
    # هذه الطريقة أسرع من جلب الرسائل (History)
    for i in range(1, 51):
        messages_to_delete.append(message.id - i)
        
    try:
        await client.delete_messages(chat_id, messages_to_delete)
    except Exception as e:
        # قد تفشل إذا كانت الرسائل قديمة جداً أو غير موجودة
        logger.warning(f"Clear cleanup warning: {e}")
        await status.edit_text("⚠️ Could not delete old messages.")

# 4. معالجة الروابط
@app.on_message(filters.text & ~filters.command(["start", "help", "clear", "info"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    user_urls[chat_id] = message.text.strip()
    cancellation_flags[chat_id] = False

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Video", callback_data="type_video"),
         InlineKeyboardButton("🎵 Audio", callback_data="type_audio")]
    ])
    await message.reply_text(
        "⬇️ **Link Received!**\nChoose your preferred format:", 
        reply_markup=buttons
    )

# ------------------- الكزر والعمليات -------------------
@app.on_callback_query(filters.regex("^cancel_task"))
async def cancel_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    cancellation_flags[chat_id] = True
    await callback_query.answer("Canceling...", show_alert=False)
    try:
        await callback_query.edit_message_text("🛑 **Task Canceled by Ziad's Bot.**")
    except:
        pass

@app.on_callback_query()
async def callback_handler(client, callback_query):
    if callback_query.data == "cancel_task": return

    chat_id = callback_query.message.chat.id
    url = user_urls.get(chat_id)
    if not url: return await callback_query.answer("Link expired. Send again.", show_alert=True)

    is_audio = (callback_query.data == "type_audio")
    
    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])
    status_msg = await callback_query.edit_message_text("⚙️ **Preparing...**", reply_markup=cancel_btn)

    start_time = time.time()
    loop = asyncio.get_event_loop()

    # تشغيل المهمة الثقيلة في Thread منفصل
    await loop.run_in_executor(
        None, 
        download_and_upload, 
        client, chat_id, url, is_audio, status_msg, start_time, loop
    )

# ------------------- المحرك الرئيسي (Core) -------------------
def download_and_upload(client, chat_id, url, is_audio, status_msg, start_time, loop):
    file_path = None
    try:
        if cancellation_flags.get(chat_id): raise TaskCancelled()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s",
            "restrictfilenames": True,
            "progress_hooks": [lambda d: ytdlp_progress_hook(d, client, status_msg, start_time, loop, chat_id)],
        }

        if is_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
            })
        else:
            ydl_opts.update({
                "format": "best[ext=mp4]/bestvideo+bestaudio/best",
                "merge_output_format": "mp4"
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media Content")
            file_path = ydl.prepare_filename(info)
            if is_audio: file_path = os.path.splitext(file_path)[0] + ".mp3"

        if cancellation_flags.get(chat_id): raise TaskCancelled()
        if not file_path or not os.path.exists(file_path): raise Exception("File not found.")

        upload_start = time.time()
        
        # تبليغ المستخدم ببدء الرفع
        asyncio.run_coroutine_threadsafe(
            status_msg.edit_text(
                "🚀 **Uploading...**", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])
            ),
            loop
        ).result()

        # --- الحل الجذري لمشكلة Coroutine ---
        # نقوم بإنشاء الكوروتين عبر استدعاء الدالة
        upload_coro = upload_file_async(client, chat_id, file_path, title, is_audio, status_msg, upload_start)
        # نمرر الكوروتين (وليس الدالة) إلى run_coroutine_threadsafe
        future = asyncio.run_coroutine_threadsafe(upload_coro, loop)
        future.result() # ننتظر انتهاء الرفع

        asyncio.run_coroutine_threadsafe(status_msg.delete(), loop).result()

    except TaskCancelled:
        asyncio.run_coroutine_threadsafe(status_msg.edit_text("🛑 **Canceled.**"), loop)
    except Exception as e:
        logger.error(f"Error: {e}")
        asyncio.run_coroutine_threadsafe(status_msg.edit_text(f"❌ Error: {str(e)}"), loop)
    finally:
        # تنظيف مساحة Railway
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Deleted: {file_path}")
            except: pass

if __name__ == "__main__":
    if not os.path.exists("downloads"): os.makedirs("downloads")
    print("✅ Ziad Bot Started on Railway...")
    app.run()

