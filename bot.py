import os
import asyncio
import logging
import time
import math
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- Settings ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_urls = {}
last_update_time = {}
cancellation_flags = {}

class TaskCancelled(Exception):
    pass

# --- Helper Functions ---
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
    if cancellation_flags.get(chat_id):
        return

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

    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])

    try:
        await status_msg.edit_text(text, reply_markup=cancel_markup)
    except:
        pass

# --- yt-dlp Hook ---
def ytdlp_progress_hook(d, client, status_msg, start_time, loop, chat_id):
    if cancellation_flags.get(chat_id):
        raise TaskCancelled()

    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        current = d.get('downloaded_bytes', 0)
        if total > 0:
            asyncio.run_coroutine_threadsafe(
                progress_bar(current, total, status_msg, start_time, "Downloading"),
                loop
            )

# --- Commands ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    txt = (
        "👋 Welcome!\n\n"
        "Send any video link (YouTube, TikTok...).\n"
        "Available commands:\n"
        "• /start - Show menu\n"
        "• /help - How to use bot\n"
        "• /clear - Clean chat\n"
        "• /info - Your info\n"
    )
    await message.reply_text(txt)

@app.on_message(filters.command("info"))
async def info_command(client, message):
    await message.reply_text("👤 Your Name: zeyad al-haiqi")

@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    chat_id = message.chat.id
    status = await message.reply_text("Cleaning...")
    ids = [message.id, status.id] + [message.id - i for i in range(1, 31)]
    try:
        await client.delete_messages(chat_id, ids)
    except:
        pass

@app.on_message(filters.text & ~filters.command(["start", "help", "clear", "info"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    url = message.text.strip()
    user_urls[chat_id] = url
    cancellation_flags[chat_id] = False

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Video", callback_data="type_video"),
         InlineKeyboardButton("🎵 Audio", callback_data="type_audio")]
    ])
    await message.reply_text("Choose format:", reply_markup=buttons)

# --- Cancel Button ---
@app.on_callback_query(filters.regex("^cancel_task"))
async def cancel_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    cancellation_flags[chat_id] = True
    await callback_query.edit_message_text("🛑 Task canceled.")

# --- Main Callback ---
@app.on_callback_query()
async def callback_handler(client, callback_query):
    data = callback_query.data
    if data == "cancel_task": return

    chat_id = callback_query.message.chat.id
    url = user_urls.get(chat_id)
    if not url:
        return await callback_query.answer("Expired link", show_alert=True)

    is_audio = (data == "type_audio")
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])

    status_msg = await callback_query.edit_message_text("Preparing...", reply_markup=cancel_markup)

    start_time = time.time()
    loop = asyncio.get_event_loop()

    await loop.run_in_executor(
        None,
        download_and_upload,
        client, chat_id, url, is_audio, status_msg, start_time, loop
    )

# --- Download & Upload Core ---
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
            ydl_opts.update({"format": "bestaudio/best",
                             "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]})
        else:
            ydl_opts.update({"format": "best[ext=mp4]/bestvideo+bestaudio/best",
                             "merge_output_format": "mp4"})

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media Clip")
            file_path = ydl.prepare_filename(info)
            if is_audio:
                file_path = os.path.splitext(file_path)[0] + ".mp3"

        if cancellation_flags.get(chat_id): raise TaskCancelled()
        if not os.path.exists(file_path): raise Exception("File missing after download")

        upload_start = time.time()

        asyncio.run_coroutine_threadsafe(
            status_msg.edit_text("Uploading...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel", callback_data="cancel_task")]])),
            loop
        ).result()

        async def upload_task():
            await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
            if is_audio:
                await client.send_audio(chat_id, file_path, caption=title,
                    progress=progress_bar, progress_args=(status_msg, upload_start, "Uploading"))
            else:
                await client.send_video(chat_id, file_path,caption=title,supports_streaming=True,
                    progress=progress_bar, progress_args=(status_msg, upload_start, "Uploading"))

        asyncio.run_coroutine_threadsafe(upload_task(), loop).result()

        asyncio.run_coroutine_threadsafe(status_msg.delete(), loop).result()

    except TaskCancelled:
       }}

