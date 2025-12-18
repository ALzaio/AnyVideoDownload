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
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# مكتبة التحميل ومعالجة الأخطاء
import yt_dlp
from yt_dlp.utils import DownloadError, GeoRestrictedError

# مكتبة السيرفر (لابقاء البوت يعمل على Railway)
from aiohttp import web

# ================= 1. الإعدادات =================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# إعداد المنفذ (Port)
PORT = int(os.environ.get("PORT", 8080))

DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300MB
COMPRESSION_THRESHOLD = 200 * 1024 * 1024  # 200MB

# إعدادات التمويه (User Agent) لتجنب حظر تويتر
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

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
        # كتابة الكوكيز عند بدء التشغيل
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(cookies_content)
        logger.info("✅ Cookies file created successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to create cookies file: {e}")

# ================= 3. دوال السيرفر (Web Server) =================

async def health_check_handler(request):
    """صفحة الويب التي يزورها موقع المراقبة"""
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    """تشغيل سيرفر الويب في الخلفية"""
    server = web.Application()
    server.router.add_get("/", health_check_handler)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌍 Web server started on port {PORT}")

# ================= 4. دوال مساعدة وتنظيف =================

def format_bytes(size):
    if not size or size == 0: return "N/A"
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.1f}{power_labels[n]}B"

class FileTooBigError(Exception): pass
class UserCancelledError(Exception): pass

async def scheduled_cleanup():
    """مهمة خلفية لتنظيف الملفات القديمة كل ساعة"""
    while True:
        await asyncio.sleep(3600)
        logger.info("🧹 Starting scheduled cleanup...")
        try:
            now = time.time()
            if os.path.exists(DOWNLOAD_DIR):
                for filename in os.listdir(DOWNLOAD_DIR):
                    filepath = os.path.join(DOWNLOAD_DIR, filename)
                    if os.path.getmtime(filepath) < now - 3600:
                        try:
                            if os.path.isfile(filepath): os.remove(filepath)
                            elif os.path.isdir(filepath): shutil.rmtree(filepath)
                        except Exception as e:
                            logger.error(f"Error deleting {filename}: {e}")
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")

def download_hook(d, chat_id):
    if d['status'] == 'downloading':
        if cancel_flags.get(chat_id): raise UserCancelledError("Cancelled")
        if d.get('downloaded_bytes', 0) > MAX_FILE_SIZE:
            raise FileTooBigError(f"Limit exceeded: {format_bytes(d['downloaded_bytes'])}")

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
            if os.path.getsize(output_path) < size:
                os.remove(input_path)
                return output_path
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

# ================= 5. العمال (Workers) =================

def analyze_video_worker(url):
    # إعدادات التحليل مع إضافة الهيدرز والكوكيز
    ydl_opts = {
        "quiet": True, "nocheckcertificate": True, "skip_download": True, "noplaylist": True,
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        "http_headers": HTTP_HEADERS  # 🆕 إضافة الهيدرز
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info: info = info['entries'][0]
            
            title = info.get('title', 'Video')
            formats_data = {}
            if 'formats' in info:
                for f in info['formats']:
                    if not f.get('height'): continue
                    height = f.get('height')
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    if filesize: filesize = int(filesize * 1.1)
                    if height not in formats_data or filesize > formats_data[height]:
                        formats_data[height] = filesize

            return {"title": title, "formats": formats_data, "error": None}
            
    except GeoRestrictedError: return {"error": "❌ هذا الفيديو محظور في دولة السيرفر."}
    except DownloadError as e:
        if "live event" in str(e): return {"error": "❌ لا يمكن تحميل البث المباشر."}
        return {"error": f"❌ خطأ في التحميل: {str(e)[:50]}"}
    except Exception as e: return {"error": f"❌ خطأ عام: {str(e)}"}

def get_stream_link_worker(url):
    ydl_opts = {
        "quiet": True, "nocheckcertificate": True, "skip_download": True,
        "format": "best",
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        "http_headers": HTTP_HEADERS  # 🆕 إضافة الهيدرز
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('url'), info.get('title')
    except Exception as e: return None, str(e)

def download_worker(client, chat_id, message_id, url, quality_setting, is_audio):
    cancel_flags[chat_id] = False
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    ydl_opts = {
        "outtmpl": output_template, "quiet": True, "nocheckcertificate": True,
        "restrictfilenames": True, "progress_hooks": [lambda d: download_hook(d, chat_id)],
        "http_headers": HTTP_HEADERS,  # 🆕 إضافة الهيدرز
    }
    
    if os.path.exists(COOKIES_FILE): ydl_opts["cookiefile"] = COOKIES_FILE

    if is_audio: 
        ydl_opts.update({"format": "bestaudio/best", "postprocessors": [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]})
    else:
        if quality_setting == "best":
            ydl_opts["format"] = f"bestvideo[filesize<{MAX_FILE_SIZE}]+bestaudio/best"
        else:
            target_h = int(quality_setting)
            ydl_opts["format"] = f"bestvideo[height<={target_h}]+bestaudio/best[height<={target_h}]/best"
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
            if f_size > MAX_FILE_SIZE:
                os.remove(final_path)
                return None, None, f"عذراً، الملف ({format_bytes(f_size)}) أكبر من الحد المسموح."
            
            if f_size > COMPRESSION_THRESHOLD:
                client.loop.call_soon_threadsafe(
                    asyncio.create_task,
                    client.edit_message_text(
                        chat_id, message_id, f"🔨 **الحجم {format_bytes(f_size)}**\nجاري الضغط...",
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

# ================= 6. المعالجات (Handlers) =================

@app.on_message(filters.command(["start"]))
async def start(client, message):
    await message.reply_text("👋 أهلاً! أرسل رابط فيديو لتحليله.")

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    
    # 🆕 إصلاح مهم: تحويل روابط X.com إلى twitter.com
    if "x.com" in url:
        url = url.replace("x.com", "twitter.com")
        
    status = await message.reply_text("🔎 **جاري تحليل الرابط...**")
    
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, analyze_video_worker, url)
    await status.delete()

    if result.get("error"):
        return await message.reply_text(result["error"])

    title = result["title"]
    formats = result["formats"]
    user_pending_data[message.chat.id] = {"url": url}
    
    buttons = []
    buttons.append([InlineKeyboardButton("🎵 تحويل صوت (MP3)", callback_data="start_audio")])
    
    valid_video_options = []
    priority_qualities = [1080, 720, 480, 360]
    has_downloadable_video = False
    
    for q in priority_qualities:
        closest_h = None
        for h_avail in formats.keys():
            if abs(h_avail - q) < 100:
                closest_h = h_avail
                break
        
        if closest_h:
            size = formats[closest_h]
            size_lbl = format_bytes(size) if size > 0 else "N/A"
            if size > 0 and size > MAX_FILE_SIZE: pass 
            else:
                btn_txt = f"🎥 {q}p ({size_lbl})"
                valid_video_options.append(InlineKeyboardButton(btn_txt, callback_data=f"ask_{q}"))
                has_downloadable_video = True

    buttons.append([InlineKeyboardButton("✨ أفضل جودة (<300MB)", callback_data="ask_best")])

    video_rows = [valid_video_options[i:i+2] for i in range(0, len(valid_video_options), 2)]
    for row in video_rows: buttons.append(row)

    buttons.append([InlineKeyboardButton("▶️ رابط مباشر (بدون تحميل)", callback_data="method_stream")])

    warning = "\n⚠️ **ملاحظة:** جميع جودات الفيديو تبدو أكبر من 300MB." if (not has_downloadable_video and len(formats) > 0) else ""

    await message.reply_text(
        f"📺 **{title}**{warning}\n⬇️ اختر الجودة المناسبة:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@app.on_callback_query()
async def callback(client, call):
    if call.data == "cancel_dl":
        cancel_flags[call.message.chat.id] = True
        await call.answer("جاري الإلغاء...")
        return

    data = user_pending_data.get(call.message.chat.id)
    if not data: return await call.answer("انتهت الجلسة", show_alert=True)
    url = data["url"]

    if call.data == "method_stream":
        await call.message.edit_text("⏳ **جاري استخراج الرابط...**")
        loop = asyncio.get_event_loop()
        stream_url, title = await loop.run_in_executor(executor, get_stream_link_worker, url)
        if stream_url:
            await call.message.edit_text(f"✅ **{title}**\n\n🔗 [اضغط للمشاهدة]({stream_url})", disable_web_page_preview=True)
        else:
            await call.message.edit_text("❌ فشل استخراج الرابط")
        return

    # سؤال عن نوع الملف (فيديو/ملف)
    if call.data.startswith("ask_"):
        quality = call.data.split("_")[1]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 فيديو للمشاهدة (Stream)", callback_data=f"start_vid_{quality}")],
            [InlineKeyboardButton("📁 ملف أصلي (File)", callback_data=f"start_doc_{quality}")]
        ])
        await call.message.edit_text("🛠 **كيف تريد استلام الملف؟**", reply_markup=kb)
        return

    # البدء الفعلي
    if not call.data.startswith("start_"): return

    action_parts = call.data.split("_")
    mode = action_parts[1]
    is_audio = (mode == "audio")
    quality = action_parts[2] if len(action_parts) > 2 else "best"

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
    await call.message.edit_text("⏳ جاري التحميل إلى السيرفر...", reply_markup=cancel_btn)

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
        
        if is_audio:
            await client.send_audio(call.message.chat.id, path, caption=title, progress=progress_bar, progress_args=args)
        elif mode == "doc":
            await client.send_document(
                call.message.chat.id, path, caption=title, force_document=True,
                progress=progress_bar, progress_args=args
            )
        else:
            await client.send_video(
                call.message.chat.id, path, caption=title, supports_streaming=True,
                progress=progress_bar, progress_args=args
            )
        
        await call.message.delete()
    except Exception as e:
        if not cancel_flags.get(call.message.chat.id):
            await call.message.edit_text(f"❌ خطأ الرفع: {e}")
    finally:
        if os.path.exists(path): os.remove(path)

# ================= 7. التشغيل =================

async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    
    asyncio.create_task(scheduled_cleanup())
    asyncio.create_task(start_web_server())
    
    logger.info("🤖 Bot started...")
    await app.start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
