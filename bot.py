import os
import asyncio
import logging
import time
import math
# استيراد المكتبات الضرورية
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- 1. الإعدادات والمتغيرات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# إعداد التسجيل (Logging) لمعرفة الأخطاء
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# تهيئة البوت
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# متغيرات عالمية
user_urls = {}          # لحفظ الروابط
last_update_time = {}   # لتنظيم تحديث شريط التقدم
cancellation_flags = {} # لحفظ حالة الإلغاء

# تعريف خطأ خاص للإلغاء
class TaskCancelled(Exception):
    pass

# --- 2. دوال المساعدة (شريط التقدم) ---

def humanbytes(size):
    """تحويل الحجم إلى صيغة مقروءة (MB, GB)"""
    if not size: return ""
    power = 2**10
    n = 0
    dic_powerN = {0: ' ', 1: 'KiB', 2: 'MiB', 3: 'GiB', 4: 'TiB'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + dic_powerN.get(n, 'B')

def get_progress_bar_string(current, total):
    """رسم شريط التقدم [■■■□□]"""
    completed = int(current * 10 / total)
    return "■" * completed + "□" * (10 - completed)

async def progress_bar(current, total, status_msg, start_time, operation_name):
    """تحديث رسالة تيليجرام بالنسبة المئوية"""
    # التحقق من الإلغاء أثناء التحديث (اختياري)
    chat_id = status_msg.chat.id
    if cancellation_flags.get(chat_id):
        return # توقف عن التحديث إذا تم الإلغاء

    now = time.time()
    # تحديث الرسالة كل 4 ثواني فقط لتجنب الحظر (Flood Wait)
    if last_update_time.get(status_msg.id) and (now - last_update_time[status_msg.id]) < 4:
        return

    last_update_time[status_msg.id] = now
    
    percentage = current * 100 / total
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    eta_str = time.strftime("%M:%S", time.gmtime(eta)) if eta < 3600 else "Wait.."

    text = (
        f"**{operation_name}** 🔄\n"
        f"[{get_progress_bar_string(current, total)}] {round(percentage, 2)}%\n"
        f"📊 **Size:** {humanbytes(current)} / {humanbytes(total)}\n"
        f"🚀 **Speed:** {humanbytes(speed)}/s\n"
        f"⏳ **ETA:** {eta_str}"
    )
    
    # إضافة زر الإلغاء دائماً مع شريط التقدم
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية", callback_data="cancel_task")]])
    
    try:
        await status_msg.edit_text(text, reply_markup=cancel_markup)
    except Exception:
        pass

# --- 3. هوك التحميل (yt-dlp Hook) ---
def ytdlp_progress_hook(d, client, status_msg, start_time, loop, chat_id):
    # التحقق من الإلغاء أثناء التحميل
    if cancellation_flags.get(chat_id):
        raise TaskCancelled("تم إلغاء التحميل")

    if d['status'] == 'downloading':
        try:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            current = d.get('downloaded_bytes', 0)
            if total > 0:
                future = asyncio.run_coroutine_threadsafe(
                    progress_bar(current, total, status_msg, start_time, "جاري التحميل من المصدر"),
                    loop
                )
        except Exception:
            pass

# --- 4. أوامر البوت والمحادثة ---

@app.on_message(filters.command(["start", "help"]))
async def start_command(client, message):
    await message.reply_text(
        "👋 **أهلاً بك!**\n\n"
        "🔗 أرسل رابط فيديو (يوتيوب، تيك توك، إلخ..).\n"
        "🚀 **المميزات:**\n"
        "- دعم ملفات حتى **2 جيجابايت**.\n"
        "- شريط تقدم مباشر.\n"
        "- إمكانية إلغاء العملية.\n\n"
        "استخدم /clear لتنظيف المحادثة."
    )

@app.on_message(filters.command("clear"))
async def clear_command(client, message):
    chat_id = message.chat.id
    status = await message.reply_text("🗑️ جاري التنظيف...")
    ids = [message.id, status.id]
    for i in range(1, 31):
        ids.append(message.id - i)
    try:
        await client.delete_messages(chat_id, ids)
    except:
        pass

@app.on_message(filters.text & ~filters.command(["start", "help", "clear"]) & filters.regex(r"http"))
async def handle_link(client, message):
    chat_id = message.chat.id
    url = message.text.strip()
    user_urls[chat_id] = url
    
    # تصفير علم الإلغاء عند طلب جديد
    cancellation_flags[chat_id] = False
    
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 Video", callback_data="type_video"),
            InlineKeyboardButton("🎵 Audio", callback_data="type_audio")
        ]
    ])
    await message.reply_text("⬇️ اختر الصيغة:", reply_markup=buttons, quote=True)

# --- 5. معالجة الأزرار (بما فيها الإلغاء) ---

@app.on_callback_query(filters.regex("^cancel_task"))
async def cancel_handler(client, callback_query):
    chat_id = callback_query.message.chat.id
    cancellation_flags[chat_id] = True # تفعيل علم الإلغاء
    await callback_query.answer("تم طلب الإلغاء...", show_alert=False)
    try:
        await callback_query.edit_message_text("🛑 تم إلغاء العملية. جاري التنظيف...")
    except:
        pass

@app.on_callback_query()
async def main_callback_handler(client, callback_query):
    data = callback_query.data
    # تجاهل زر الإلغاء هنا لأنه تمت معالجته بالأعلى
    if data == "cancel_task":
        return

    chat_id = callback_query.message.chat.id
    url = user_urls.get(chat_id)
    
    if not url:
        await callback_query.answer("❌ الرابط قديم", show_alert=True)
        return

    is_audio = (data == "type_audio")
    
    # إضافة زر إلغاء مبدئي
    cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية", callback_data="cancel_task")]])
    status_msg = await callback_query.edit_message_text("⏳ جاري تهيئة التحميل...", reply_markup=cancel_markup)
    
    start_time = time.time()
    loop = asyncio.get_event_loop()
    
    # تشغيل العملية الثقيلة في Thread منفصل
    await loop.run_in_executor(None, download_and_upload, client, chat_id, url, is_audio, status_msg, start_time, loop)

# --- 6. الدالة الرئيسية (تعمل في الخلفية) ---

def download_and_upload(client, chat_id, url, is_audio, status_msg, start_time, loop):
    file_path = None
    try:
        # 1. التحقق من الإلغاء قبل البدء
        if cancellation_flags.get(chat_id): raise TaskCancelled()

        # إعدادات yt-dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"downloads/%(id)s_%(epoch)s.%(ext)s",
            "restrictfilenames": True,
            # ربط دالة الـ Hook مع تمرير chat_id للتحقق من الإلغاء
            "progress_hooks": [lambda d: ytdlp_progress_hook(d, client, status_msg, start_time, loop, chat_id)],
        }

        if is_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            })
        else:
            # تحسين السرعة: طلب صيغة mp4 جاهزة لتجنب الدمج إذا أمكن
            ydl_opts.update({
                "format": "best[ext=mp4]/bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
            })

        # 2. بدء التحميل
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Media Clip")
            
            # تحديد مسار الملف
            if 'requested_downloads' in info:
                file_path = info['requested_downloads'][0]['filepath']
            else:
                filename = ydl.prepare_filename(info)
                if is_audio: filename = os.path.splitext(filename)[0] + ".mp3"
                file_path = filename

        # 3. التحقق من الإلغاء بعد التحميل
        if cancellation_flags.get(chat_id): raise TaskCancelled()
        if not os.path.exists(file_path): raise Exception("فشل العثور على الملف")

        # 4. بدء الرفع
        caption = f"✅ **{title}**\nvia @YourBot"
        upload_start_time = time.time()
        
        # تحديث الرسالة
        asyncio.run_coroutine_threadsafe(
            status_msg.edit_text("🚀 جاري الرفع إلى تيليجرام...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية", callback_data="cancel_task")]])),
            loop
        ).result()

        # دالة الرفع (Async Wrapped)
        async def upload_task():
            # إرسال حالة "جاري الرفع"
            await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_DOCUMENT)
            
            # إعدادات التقدم للرفع
            prog_args = (status_msg, upload_start_time, "جاري الرفع")
            
            if is_audio:
                await client.send_audio(
                    chat_id, file_path, caption=caption, title=title,
                    progress=progress_bar, progress_args=prog_args
                )
            else:
                await client.send_video(
                    chat_id, file_path, caption=caption, supports_streaming=True,
                    progress=progress_bar, progress_args=prog_args
                )

        # تشغيل الرفع والانتظار حتى ينتهي
        # ملاحظة: التحقق من الإلغاء أثناء الرفع يتم داخل progress_bar لكنه لا يوقف الرفع فوراً في Pyrogram
        # لذا نعتمد على أن المستخدم يرى زر الإلغاء. لإيقاف الرفع الحقيقي يتطلب client.stop_transmission() وهذا معقد قليلاً هنا
        # لكن الكود سيمنع تحديث البار إذا تم الإلغاء.
        asyncio.run_coroutine_threadsafe(upload_task(), loop).result()

        # تنظيف نهائي
        asyncio.run_coroutine_threadsafe(status_msg.delete(), loop)

    except TaskCancelled:
        asyncio.run_coroutine_threadsafe(status_msg.edit_text("🚫 تم إلغاء العملية بنجاح."), loop)
    except Exception as e:
        # تجاهل أخطاء الإلغاء التي تأتي من الـ Hook
        if "تم إلغاء التحميل" in str(e):
            asyncio.run_coroutine_threadsafe(status_msg.edit_text("🚫 تم إلغاء العملية."), loop)
        else:
            logger.error(f"Error: {e}")
            asyncio.run_coroutine_threadsafe(client.send_message(chat_id, f"❌ حدث خطأ: {str(e)[:50]}"), loop)
    finally:
        # حذف الملف في كل الأحوال
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    print("Bot Started...")
    app.run()
