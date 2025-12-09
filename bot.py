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

# حدود Railway المعدلة حسب طلبك
DOWNLOAD_DIR = "downloads"

# 🚨 التعديل الأساسي: الحد الأقصى أصبح 300 ميجابايت
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300MB

# 🚨 تعديل عتبة الضغط: نضغط الملفات التي بين 150 و 300 ميجا
COMPRESSION_THRESHOLD = 150 * 1024 * 1024  # 150MB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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
    if d['status'] == 'downloading':
        if cancel_flags.get(chat_id): raise UserCancelledError("Cancelled")
        if d.get('downloaded_bytes', 0) > MAX_FILE_SIZE:
            raise FileTooBigError(f"تجاوز الحد: {format_bytes(d['downloaded_bytes'])}")

def compress_video(input_path, chat_id):
    """دالة ضغط الفيديو باستخدام FFmpeg"""
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD: return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg: return input_path 

    # إعدادات ضغط سريعة (Ultrafast) لتقليل استهلاك المعالج
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
            if os.path.getsize(output_path) < size:
                os.remove(input_path)
                return output_path
    except UserCancelledError: raise
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
    """جلب المعلومات فقط"""
    ydl_opts = {
        "quiet": True, "nocheckcertificate": True, "skip_download": True, "noplaylist": True,
        "format": "best", "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info: info = info['entries'][0]
            title = info.get('title', 'Video')
            size = info.get('filesize_approx') or info.get('filesize') or 0
            return title, size
    except: return None, 0

def get_stream_link_worker(url):
    """جلب رابط مباشر للبث"""
    ydl_opts = {
        "quiet": True, "nocheckcertificate": True, "skip_download": True,
        "format": "best", "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('url'), info.get('title')
    except Exception as e: return None, str(e)

def download_worker(client, chat_id, message_id, url, quality, is_audio):
    cancel_flags[chat_id] = False
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        "outtmpl": output_template, "quiet": True, "nocheckcertificate": True, "restrictfilenames": True,
        "progress_hooks": [lambda d: download_hook(d, chat_id)],
    }
    if os.path.exists(COOKIES_FILE): ydl_opts["cookiefile"] = COOKIES_FILE

    if is_audio: 
        ydl_opts.update({"format": "bestaudio/best", "postprocessors": [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]})
    else:
        if quality == "best": ydl_opts["format"] = "bestvideo+bestaudio/best"
        else: ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        ydl_opts["merge_output_format"] = "mp4"

    final_path, title = None, "Video"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', title)
            if 'requested_downloads' in info: final_path = info['requested_downloads'][0]['filepath']
            else: final_path = ydl.prepare_filename(info)
            if is_audio: final_path = final_path.rsplit(".", 1)[0] + ".mp3"

        if not is_audio and final_path and os.path.exists(final_path):
            f_size = os.path.getsize(final_path)
            # فحص أخير للحجم بعد التحميل
            if f_size > MAX_FILE_SIZE:
                os.remove(final_path)
                return None, None, f"الملف ({format_bytes(f_size)}) أكبر من الحد المسموح ({format_bytes(MAX_FILE_SIZE)})."
            
            # ضغط الفيديو إذا كان حجمه أكبر من عتبة الضغط وأقل من الحد الأقصى
            if f_size > COMPRESSION_THRESHOLD:
                client.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    client.edit_message_text(
                        chat_id, message_id, f"🔨 **الحجم {format_bytes(f_size)}**\nجاري الضغط لتقليل الحجم...",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
                    )
                )
                final_path = compress_video(final_path, chat_id)
        return final_path, title, None

    except UserCancelledError: return None, None, "🛑 تم الإلغاء."
    except FileTooBigError as e: return None, None, f"⛔ {str(e)}"
    except Exception as e: return None, None, str(e)
    finally:
        if chat_id in cancel_flags: del cancel_flags[chat_id]

# ================= 5. المعالجات =================

@app.on_message(filters.command(["start"]))
async def start(client, message):
    await message.reply_text("👋 أهلاً! أرسل رابط فيديو (يوتيوب، فيسبوك، انستقرام...)")

@app.on_message(filters.command("clear"))
async def clear(client, message):
    if os.path.exists(DOWNLOAD_DIR): shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await message.reply_text("✅ تم تنظيف المخلفات")

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    status = await message.reply_text("🔎 **جاري فحص الرابط...**")
    
    loop = asyncio.get_event_loop()
    title, size = await loop.run_in_executor(executor, info_worker, url)
    await status.delete()

    if not title: return await message.reply_text("❌ رابط غير صالح أو محتوى خاص")

    user_pending_data[message.chat.id] = {"url": url}
    size_txt = format_bytes(size)

    # 🚨 منطق الشرط الجديد: 300MB 🚨
    if size > MAX_FILE_SIZE:
        # الحالة الأولى: الملف ضخم جداً -> عرض خيار المشاهدة فقط
        warning = f"\n⚠️ **تنبيه:** الحجم ({size_txt}) أكبر من 300MB.\n⚠️ لا يمكن تحميل هذا الملف لسيرفرات تيليجرام مباشرة.\n✅ **الحل:** استخدم الرابط المباشر أدناه."
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ مشاهدة مباشرة (Direct Link)", callback_data="method_stream")]
        ])
    else:
        # الحالة الثانية: الملف ضمن الحدود المسموحة -> عرض كل الخيارات
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📥 تحميل للمحادثة", callback_data="method_download"),
                InlineKeyboardButton("▶️ رابط مباشر", callback_data="method_stream")
            ]
        ])
        warning = ""

    await message.reply_text(
        f"📺 **{title}**\n💾 الحجم المتوقع: {size_txt}{warning}",
        reply_markup=kb
    )

@app.on_callback_query()
async def callback(client, call):
    if call.data == "cancel_dl":
        cancel_flags[call.message.chat.id] = True
        await call.answer("جاري الإلغاء...")
        return

    data = user_pending_data.get(call.message.chat.id)
    if not data: return await call.answer("انتهت جلسة هذا الرابط، أرسله مجدداً", show_alert=True)
    url = data["url"]

    # --- مسار 1: المشاهدة المباشرة ---
    if call.data == "method_stream":
        await call.message.edit_text("⏳ **جاري استخراج رابط البث...**")
        loop = asyncio.get_event_loop()
        stream_url, title = await loop.run_in_executor(executor, get_stream_link_worker, url)
        
        if stream_url:
            await call.message.edit_text(
                f"✅ **تم جلب الرابط!**\n🎬 {title}\n\n🔗 [اضغط هنا للمشاهدة]({stream_url})\n\n⚠️ الرابط يعمل لفترة مؤقتة ويدعم السرعات العالية.",
                disable_web_page_preview=True
            )
        else:
            await call.message.edit_text("❌ فشل جلب رابط البث.")
        return

    # --- مسار 2: التحميل (عرض الجودات) ---
    if call.data == "method_download":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 صوت (MP3)", callback_data="audio"), InlineKeyboardButton("🎥 فيديو (Best)", callback_data="vid_best")],
            [InlineKeyboardButton("🎥 720p", callback_data="vid_720"), InlineKeyboardButton("🎥 360p", callback_data="vid_360")]
        ])
        await call.message.edit_text("⬇️ اختر الجودة:", reply_markup=kb)
        return

    # --- مسار 3: التنفيذ ---
    is_audio = (call.data == "audio")
    quality = call.data.split("_")[1] if "vid" in call.data else "720"

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
    await call.message.edit_text("⏳ جاري التحميل إلى السيرفر...", reply_markup=cancel_btn)

    loop = asyncio.get_event_loop()
    path, title, err = await loop.run_in_executor(
        executor, download_worker, client, call.message.chat.id, call.message.id, url, quality, is_audio
    )

    if err:
        if path and os.path.exists(path): os.remove(path)
        return await call.message.edit_text(f"❌ خطأ: {err}")
    
    if not path: return await call.message.edit_text("❌ فشل غير معروف")

    try:
        await call.message.edit_text("⬆️ جاري الرفع إلى تيليجرام...", reply_markup=cancel_btn)
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
