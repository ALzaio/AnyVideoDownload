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

DOWNLOAD_DIR = "downloads"
MAX_FILE_SIZE = 900 * 1024 * 1024  # 900MB
COMPRESSION_THRESHOLD = 200 * 1024 * 1024  # 200MB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# تخزين البيانات
user_pending_data = {} 
executor = ThreadPoolExecutor(max_workers=2)

# 🛑 قاموس التحكم في الإلغاء (New: Cancellation Control)
# الهيكل: {chat_id: True/False} (True means cancel immediately)
cancel_flags = {}

# ================= 2. الكوكيز =================
COOKIES_FILE = "cookies.txt"
cookies_content = os.environ.get("COOKIES_CONTENT")
if cookies_content:
    try:
        with open(COOKIES_FILE, "w") as f:
            f.write(cookies_content)
    except Exception as e:
        logger.error(f"Cookie Error: {e}")

# ================= 3. دوال مساعدة والاستثناءات =================

def format_bytes(size):
    if not size or size == 0: return "Unknown"
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

class FileTooBigError(Exception):
    pass

class UserCancelledError(Exception):
    """خطأ مخصص عند طلب المستخدم للإلغاء"""
    pass

def download_hook(d, chat_id):
    """
    مراقب التحميل: يفحص الحجم + طلب الإلغاء
    """
    if d['status'] == 'downloading':
        # 1. فحص طلب الإلغاء (New)
        if cancel_flags.get(chat_id, False):
            raise UserCancelledError("تم الإلغاء بواسطة المستخدم.")

        # 2. فحص الحدود
        if d.get('total_bytes') and d['total_bytes'] > MAX_FILE_SIZE:
            raise FileTooBigError("Total size exceeds limit.")
        
        if d.get('downloaded_bytes') and d['downloaded_bytes'] > MAX_FILE_SIZE:
            raise FileTooBigError("Downloaded bytes exceeded limit.")

def compress_video(input_path, chat_id):
    """ضغط الفيديو مع دعم الإلغاء"""
    size = os.path.getsize(input_path)
    if size <= COMPRESSION_THRESHOLD:
        return input_path

    output_path = input_path.rsplit(".", 1)[0] + "_compressed.mp4"
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path: return input_path 

    cmd = [
        ffmpeg_path, "-i", input_path,
        "-vcodec", "libx264", "-preset", "superfast", "-crf", "35",
        "-pix_fmt", "yuv420p", "-acodec", "aac", "-b:a", "128k",
        "-movflags", "+faststart", output_path
    ]
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # حلقة انتظار لتمكين الإلغاء أثناء الضغط
        while process.poll() is None:
            if cancel_flags.get(chat_id, False):
                process.kill()
                raise UserCancelledError("تم إلغاء الضغط.")
            time.sleep(1) # فحص كل ثانية
            
        if os.path.exists(output_path) and os.path.getsize(output_path) < size:
            os.remove(input_path)
            return output_path
    except UserCancelledError:
        raise # إعادة رفع الخطأ ليتم التقاطه في الخارج
    except:
        pass
    
    return input_path

async def progress_bar(current, total, message, start_time, chat_id):
    # فحص الإلغاء أثناء الرفع
    if cancel_flags.get(chat_id, False):
        app.stop_transmission() # أمر خاص بـ Pyrogram لإيقاف الرفع
        
    now = time.time()
    if (now - start_time[0]) < 5: return
    start_time[0] = now
    percent = current * 100 / total
    filled = int(percent / 10)
    bar = '▓' * filled + '░' * (10 - filled)
    try:
        await message.edit_text(
            f"⬆️ **جاري الرفع...**\n{bar} {percent:.1f}%\n📦 {format_bytes(current)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
        )
    except: pass

# ================= 4. العمال (Workers) =================

def info_worker(url):
    ydl_opts = {
        "quiet": True, "nocheckcertificate": True, "skip_download": True,
        "format": "best",
        "cookiefile": COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown Title')
            size = info.get('filesize_approx') or info.get('filesize') or 0
            return title, size
    except:
        return None, 0

def download_worker(client, chat_id, message_id, url, quality, is_audio):
    # تصفير علم الإلغاء عند البدء
    cancel_flags[chat_id] = False
    
    unique_id = uuid.uuid4().hex[:8]
    output_template = f"{DOWNLOAD_DIR}/{unique_id}_%(title)s.%(ext)s"
    
    # تمرير chat_id للخطاف
    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True, "nocheckcertificate": True, "restrictfilenames": True,
        "progress_hooks": [lambda d: download_hook(d, chat_id)], 
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

        # مرحلة الضغط
        if not is_audio and final_path and os.path.exists(final_path):
            f_size = os.path.getsize(final_path)
            if f_size > MAX_FILE_SIZE:
                os.remove(final_path)
                return None, None, "File too big!"
            
            if f_size > COMPRESSION_THRESHOLD:
                # تحديث الرسالة مع زر الإلغاء
                client.loop.call_soon_threadsafe(
                    asyncio.create_task, 
                    client.edit_message_text(
                        chat_id, message_id, "🔨 جاري الضغط...",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
                    )
                )
                final_path = compress_video(final_path, chat_id)

        return final_path, title, None

    except UserCancelledError:
        return None, None, "🛑 تم الإلغاء."
    except FileTooBigError:
        return None, None, "⛔ توقف: الملف تجاوز 900MB."
    except Exception as e:
        return None, None, str(e)
    finally:
        # تنظيف علم الإلغاء
        if chat_id in cancel_flags:
            del cancel_flags[chat_id]

# ================= 5. المعالجات =================

@app.on_message(filters.command(["start"]))
async def start(client, message):
    await message.reply_text("👋 أهلاً! أرسل رابطاً وسأفحصه أولاً.")

@app.on_message(filters.command("clear"))
async def clear(client, message):
    if os.path.exists(DOWNLOAD_DIR): shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await message.reply_text("✅ تم التنظيف")

@app.on_message(filters.text & filters.regex(r"http"))
async def link_handler(client, message):
    url = message.text.strip()
    status = await message.reply_text("🔎 جاري فحص الرابط...")

    loop = asyncio.get_event_loop()
    title, size = await loop.run_in_executor(executor, info_worker, url)

    await status.delete()

    if not title:
        await message.reply_text("❌ الرابط غير صالح.")
        return

    user_pending_data[message.chat.id] = {"url": url}
    size_txt = format_bytes(size) if size > 0 else "غير معروف"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 MP3", callback_data="audio"), InlineKeyboardButton("🎥 Best", callback_data="vid_best")],
        [InlineKeyboardButton("🎥 720p", callback_data="vid_720"), InlineKeyboardButton("🎥 360p", callback_data="vid_360")]
    ])

    await message.reply_text(
        f"📺 **العنوان:** {title}\n💾 **الحجم:** {size_txt}\n\n⬇️ اختر الجودة:",
        reply_markup=keyboard
    )

@app.on_callback_query()
async def callback(client, call):
    # 1. معالجة زر الإلغاء
    if call.data == "cancel_dl":
        cancel_flags[call.message.chat.id] = True
        await call.answer("🛑 جاري الإلغاء...")
        await call.message.edit_text("🛑 تم إلغاء العملية.")
        return

    # 2. معالجة بدء التحميل
    data = user_pending_data.get(call.message.chat.id)
    if not data: return await call.answer("أرسل الرابط مجدداً", show_alert=True)
    
    url = data["url"]
    is_audio = (call.data == "audio")
    quality = call.data.split("_")[1] if "vid" in call.data else "720"

    # إضافة زر الإلغاء أثناء التحميل
    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_dl")]])
    await call.message.edit_text("⏳ جاري التحميل...", reply_markup=cancel_btn)
    
    loop = asyncio.get_event_loop()
    path, title, err = await loop.run_in_executor(executor, download_worker, client, call.message.chat.id, call.message.id, url, quality, is_audio)

    if err: 
        if "تم الإلغاء" in str(err):
            # تم الإلغاء بالفعل، لا داعي لعمل شيء إضافي
            pass
        else:
            await call.message.edit_text(f"❌ خطأ: {err}")
        # تنظيف الملف الجزئي إذا وجد
        if path and os.path.exists(path): os.remove(path)
        return
        
    if not path: return await call.message.edit_text("❌ فشل التحميل")

    try:
        await call.message.edit_text("⬆️ جاري الرفع...", reply_markup=cancel_btn)
        capt = f"🎬 {title}\n🤖 @YourBot"
        action = enums.ChatAction.UPLOAD_DOCUMENT
        await client.send_chat_action(call.message.chat.id, action)
        
        # تمرير chat_id لدالة البروجرس للتحقق من الإلغاء
        args = (call.message, [time.time(), time.time()], call.message.chat.id)
        
        if is_audio: await client.send_audio(call.message.chat.id, path, caption=capt, title=title, progress=progress_bar, progress_args=args)
        else: await client.send_video(call.message.chat.id, path, caption=capt, supports_streaming=True, progress=progress_bar, progress_args=args)
        
        await call.message.delete()
    except Exception as e:
        if cancel_flags.get(call.message.chat.id):
             await call.message.edit_text("🛑 تم الإلغاء أثناء الرفع.")
        else:
             await call.message.edit_text(f"❌ فشل الرفع: {e}")
    finally:
        if os.path.exists(path): os.remove(path)

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    print("🚀 Bot Started with Cancel Feature...")
    app.run()
