#!/usr/bin/env python3
import os
import time
import asyncio
import logging
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

# مكتبات تيليجرام
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# مكتبة التحميل
import yt_dlp

# ================= 1. الإعدادات =================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# حدود Railway
DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 900 * 1024 * 1024  # 900MB (حد صارم)
COMPRESSION_THRESHOLD = 200 * 1024 * 1024  # 200MB (حد الضغط)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# إدارة المهام
user_pending_data = {} 
executor = ThreadPoolExecutor(max_workers=2)
cancel_flags = {} 

# ================= 2. الكوكيز =================
COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("COOKIES_CONTENT")
if cookies_content:
    try:
        with open(COOKIES_FILE, "w") as f:
            f.write(cookies_content)
    except: pass

# ================= 3. الدوال المساعدة =================

def format_bytes(size):
    if not size or size == 0: return "غير معروف"
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

class FileTooBigError(Exception): pass
class UserCancelledError(Exception): pass

def download_hook(d, chat_id):
    """حارس التحميل: يراقب الحجم لحظة بلحظة"""
    if d['status'] == 'downloading':
        if cancel_flags.get(chat_id):
            raise UserCancelledError("Cancelled")
        
        # قطع التحميل فوراً إذا تجاوز 900MB
        if d.get('downloaded_bytes', 0) > MAX_FILE_SIZE:
            raise FileTooBigError(f"تجاوز الحد: {format_bytes(d['downloaded_bytes'])}")

def compress_video(input_path, chat_id):
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD: return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg: return input_path 

    cmd = [
        ffmpeg, "-i", input_path,
        "-vcodec", "libx264", "-preset", "ultrafast", "-crf", "35",
        "-pix_fmt", "yuv420p", "-acodec", "aac", "-b:a", "64k",
        "-movflags", "+faststart", "-y", output_path
    ]
    
    process = None
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while process.poll() is None:
            if cancel_flags.get(chat_id):
                process.kill()
                raise UserCancelledError("Cancelled compression")
            time.sleep(1)
            
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            os.remove(input_path)
            return output_path
    except UserCancelledError:
        raise
    except:
        if process: process.kill()
    
    return input_path

async def progress_bar(current, total, message, start_time, chat_id):
    if cancel_flags.get(chat_id):
        app.stop_transmission()
        return

    now = time.time()
    if (now - start_time[0]) < 5: return
    start_time[0] = now
    
    percent = current * 100 / total
    filled = int(percent / 10)
    bar = '▓' * filled + '░' * (10 - filled)
    
    try:
        await message.edit_text(
            f"⬆️ **جاري الرفع...**\n{bar} {percent:.1f}%\n📦 {format_bytes(current)} / {format_bytes(total)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
        )
    except: pass

# ================= 4. العمال (Workers) =================

def info_worker(url):
    """
    🕵️ الجاسوس: يجلب المعلومات فقط بدون تحميل
    """
    ydl_opts = {
        "quiet": True, 
        "nocheckcertificate": True, 
        "skip_download": True, # ⚠️ هذا هو الأهم: لا تحمل شيئاً!
        "noplaylist": True,
        "format": "best",
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # استخراج البيانات الوصفية فقط
            info = ydl.extract_info(url, download=False)
            
            # التعامل مع القوائم (أخذ أول فيديو)
            if 'entries' in info:
                info = info['entries'][0]
                
            title = info.get('title', 'Video')
            # محاولة قراءة الحجم من البيانات الوصفية
            size = info.get('filesize_approx') or info.get('filesize') or 0
            return title, size
    except Exception as e:
        return None, 0

def download_worker(client, chat_id, message_id, url, quality, is_audio):
    cancel_flags[chat_id] = False
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True, "nocheckcertificate": True, "restrictfilenames": True,
        "progress_hooks": [lambda d: download_hook(d, chat_id)], # تفعيل الحارس
    }
    if os.path.exists(COOKIES_FILE): ydl_opts["cookiefile"] = COOKIES_FILE

    if is_audio:
        ydl_opts.update({"format": "bestaudio/best", "postprocessors": [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]})
    else:
        if quality == "best": ydl_opts["format"] = "bestvideo+bestaudio/best"
        else: ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        ydl_opts["merge_output_format"] = "mp4"

    final_path = None
    title = "Video"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', title)
            if 'requested_downloads' in info: final_path = info['requested_downloads'][0]['filepath']
            else: 
                final_path = ydl.prepare_filename(info)
                if is_audio: final_path = final_path.rsplit(".", 1)[0] + ".mp3"

        if not is_audio and final_path and os.path.exists(final_path):
            f_size = os.path.getsize(final_path)
            if f_size > MAX_FILE_SIZE:
                os.remove(final_path)
                return None, None, f"الملف ({format_bytes(f_size)}) أكبر من الحد المسموح."

            if f_size > COMPRESSION_THRESHOLD:
                client.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    client.edit_message_text(
                        chat_id, message_id, 
                        f"🔨 **الحجم {format_bytes(f_size)}**\nجاري الضغط...",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
                    )
                )
                final_path = compress_video(final_path, chat_id)

        return final_path, title, None

    except UserCancelledError:
        return None, None, "🛑 تم الإلغاء."
    except FileTooBigError as e:
        return None, None, f"⛔ توقف التحميل: الملف تجاوز 900MB."
    except Exception as e:
        return None, None, str(e)
    finally:
        if chat_id in cancel_flags: del cancel_flags[chat_id]

# ================= 5. المعالجات =================

@app.on_message(filters.command(["start"]))
async def start(client, message):
    await message.reply_text("👋 أهلاً! أرسل رابطاً وسأفحصه أولاً.")

@app.on_message(filters.command("clear"))
async def clear(client, message):
    if os.path.exists(DOWNLOAD_DIR): shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await message.reply_text("✅ تم")

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    # 1. إشعار الفحص
    status = await message.reply_text("🔎 **جاري فحص الرابط والحجم (بدون تحميل)...**")
    
    loop = asyncio.get_event_loop()
    # 2. تشغيل الجاسوس
    title, size = await loop.run_in_executor(executor, info_worker, url)

    await status.delete()

    if not title: return await message.reply_text("❌ رابط غير صالح")

    user_pending_data[message.chat.id] = {"url": url}
    size_txt = format_bytes(size)

    # 3. اتخاذ القرار قبل عرض الأزرار
    if size > MAX_FILE_SIZE:
        await message.reply_text(f"⛔ **توقف!** الملف حجمه ({size_txt}) وهو أكبر من حد السيرفر (900MB).\nلن يتم التحميل حفاظاً على الموارد.")
        return

    msg_text = f"📺 **{title}**\n💾 الحجم المتوقع: {size_txt}\n⬇️ اختر الجودة:"
    if size == 0: msg_text += "\n⚠️ (الحجم غير معروف، سيتم مراقبته أثناء التحميل)"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 MP3", callback_data="audio"), InlineKeyboardButton("🎥 Best", callback_data="vid_best")],
        [InlineKeyboardButton("🎥 720p", callback_data="vid_720"), InlineKeyboardButton("🎥 360p", callback_data="vid_360")]
    ])
    
    await message.reply_text(msg_text, reply_markup=kb)

@app.on_callback_query()
async def callback(client, call):
    if call.data == "cancel_dl":
        cancel_flags[call.message.chat.id] = True
        await call.answer("جاري الإلغاء...")
        return

    data = user_pending_data.get(call.message.chat.id)
    if not data: return await call.answer("قديم", show_alert=True)

    url = data["url"]
    is_audio = (call.data == "audio")
    quality = call.data.split("_")[1] if "vid" in call.data else "720"

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
    await call.message.edit_text("⏳ جاري التحميل...", reply_markup=cancel_btn)

    loop = asyncio.get_event_loop()
    path, title, err = await loop.run_in_executor(
        executor, download_worker, client, call.message.chat.id, call.message.id, url, quality, is_audio
    )

    if err:
        if path and os.path.exists(path): os.remove(path)
        return await call.message.edit_text(f"❌ {err}")
    
    if not path: return await call.message.edit_text("❌ فشل غير معروف")

    try:
        await call.message.edit_text("⬆️ جاري الرفع...", reply_markup=cancel_btn)
        args = (call.message, [time.time(), time.time()], call.message.chat.id)
        
        if is_audio: await client.send_audio(call.message.chat.id, path, caption=title, progress=progress_bar, progress_args=args)
        else: await client.send_video(call.message.chat.id, path, caption=title, supports_streaming=True, progress=progress_bar, progress_args=args)
        
        await call.message.delete()
    except Exception as e:
        if not cancel_flags.get(call.message.chat.id):
            await call.message.edit_text(f"❌ فشل الرفع: {e}")
    finally:
        if os.path.exists(path): os.remove(path)

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    app.run()
