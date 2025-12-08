import os
import glob
import asyncio
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ANY_VIDEO_DL")

app = Client("AnyVideoDownload", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

user_urls = {}


# ========================= PROGRESS BAR =========================

def make_bar(percent):
    filled = int(percent / 5)
    empty = 20 - filled
    return f"[{'█' * filled}{'░' * empty}] {percent:.1f}%"


async def update_progress(d, msg):
    if d["status"] != "downloading":
        return

    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    if not total:
        return

    downloaded = d.get("downloaded_bytes", 0)
    percent = (downloaded / total) * 100
    bar = make_bar(percent)

    try:
        await msg.edit_text(f"Downloading...\n{bar}")
    except:
        pass


# ========================= VIDEO COMPRESSION =========================

async def compress_video(input_path):
    size = os.path.getsize(input_path)

    # If <= 45MB → no need to compress
    if size <= 45 * 1024 * 1024:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"

    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-vcodec", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-acodec", "aac",
        "-b:a", "128k",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    await process.communicate()

    if os.path.exists(output_path) and os.path.getsize(output_path) < size:
        return output_path

    return input_path


# ========================= COMMANDS =========================

@app.on_message(filters.command(["start", "help"]))
async def start(_, m):
    await m.reply("Send video URL and choose format.")


@app.on_message(filters.text & filters.regex(r"https?://"))
async def handle_link(_, m):
    user_urls[m.chat.id] = m.text.strip()
    await m.reply(
        "Choose format:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Video", "video"), InlineKeyboardButton("Audio", "audio")]
        ])
    )


@app.on_callback_query()
async def callback(c, cb):
    url = user_urls.get(cb.message.chat.id)
    if not url:
        return await cb.answer("URL expired.", show_alert=True)

    await cb.answer()
    msg = await cb.message.reply("Analyzing...")

    asyncio.create_task(download_and_send(c, cb.message.chat.id, url, cb.data == "audio", msg))


# ========================= DOWNLOAD AND SEND =========================

async def download_and_send(client, chat_id, url, is_audio, status_msg):
    loop = asyncio.get_running_loop()
    video_id = None

    try:
        opts = {
            "format": "bestaudio/best" if is_audio else "best[height<=1080]+bestaudio/best",
            "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [lambda d: asyncio.run_coroutine_threadsafe(update_progress(d, status_msg), loop)],
        }

        await status_msg.edit("Starting download...")

        info = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=True)
        )

        video_id = info["id"]
        title = info.get("title", "Media file")

        # Detect files
        files = glob.glob(f"{DOWNLOAD_DIR}/{video_id}*")
        media = next((f for f in files if f.endswith((".mp4", ".mp3", ".webm", ".m4a", ".mkv")) and ".part" not in f), None)
        thumb = next((f for f in files if f.endswith((".jpg", ".png", ".webp"))), None)

        if not media:
            return await status_msg.edit("Failed: media file missing.")

        if not is_audio:
            await status_msg.edit("Compressing video if needed...")
            media = await compress_video(media)

        await status_msg.edit("Uploading...")

        caption = f"{title}\n\n@AnyVideoDownload"

        if is_audio:
            await client.send_audio(chat_id, media, caption=caption, thumb=thumb)
        else:
            await client.send_video(chat_id, media, caption=caption, thumb=thumb, supports_streaming=True)

        await status_msg.delete()
        await client.send_message(chat_id, "Done!")

    except Exception as e:
        await status_msg.edit(f"Error: {str(e)[:200]}")
        logger.error(e)

    finally:
        if video_id:
            for f in glob.glob(f"{DOWNLOAD_DIR}/{video_id}*"):
                try:
                    os.remove(f)
                except:
                    pass


print("Bot is running...")
app.run()




