import os
import time
import requests
import asyncio
import aiohttp
import subprocess
from pyrogram import Client, filters, types
from dotenv import load_dotenv

# טעינת משתני סביבה
load_dotenv()
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# מילונים לניהול נתונים, הודעות למחיקה, נעילות ומשימות פעולות למשתמש
messages_to_delete = {}
user_data = {}         # מידע זמני למשתמש (קישור, שם, בחירות וכו')
user_thumbnails = {}   # שמירת נתיב תמונת הממוזערת של כל משתמש
user_locks = {}        # נעילה למניעת פעולות מקבילות לאותו משתמש
user_tasks = {}        # שמירת משימה פעילה לכל משתמש

MAX_FILE_SIZE = 9 * 1024 * 1024 * 1024  # 9GB - הוגדל למקסימום 9 ג'יגה

# נתיב לתמונת ממוזערת ברירת מחדל
DEFAULT_THUMB_URL = "https://envs.sh/dPt.jpg"
DEFAULT_THUMB_PATH = "default_thumb.jpg"
if not os.path.exists(DEFAULT_THUMB_PATH):
    r = requests.get(DEFAULT_THUMB_URL)
    with open(DEFAULT_THUMB_PATH, "wb") as f:
        f.write(r.content)

# פונקציה לקבלת משך הווידאו באמצעות ffprobe
def get_video_duration(file_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        duration_str = result.stdout.decode().strip()
        if duration_str:
            return int(float(duration_str))
        else:
            return 0
    except Exception as e:
        return 0

####################################
# פקודות כלליות למשתמש
####################################

@app.on_message(filters.command("start"))
async def start_command(client, message):
    user_id = message.from_user.id
    # מחיקת הודעות ישנות
    if user_id in messages_to_delete:
        for msg_id in messages_to_delete[user_id]:
            try:
                await client.delete_messages(message.chat.id, msg_id)
            except Exception:
                pass
    messages_to_delete[user_id] = []
    user_data.pop(user_id, None)
    # אם יש למשתמש משימה פעילה - בטל אותה ונקה קבצים זמניים
    if user_id in user_tasks:
        task = user_tasks[user_id]
        if not task.done():
            task.cancel()
        user_tasks.pop(user_id, None)
    # ניקוי קבצים שהורדו עבור המשתמש
    if os.path.exists("downloads"):
        for f in os.listdir("downloads"):
            if f.startswith(f"{user_id}_"):
                try:
                    os.remove(os.path.join("downloads", f))
                except Exception:
                    pass
    msg = await message.reply("שלח לי קישור להורדה")
    messages_to_delete.setdefault(user_id, []).append(msg.id)
    messages_to_delete[user_id].append(message.id)

@app.on_message(filters.command("view_thumb"))
async def view_thumb(client, message):
    user_id = message.from_user.id
    thumb_path = user_thumbnails.get(user_id)
    if thumb_path and os.path.exists(thumb_path):
        await client.send_photo(message.chat.id, thumb_path, caption="זו תמונת הממוזערת שלך")
    else:
        await client.send_photo(message.chat.id, DEFAULT_THUMB_PATH, caption="אין תמונת ממוזערת מוגדרת, הנה ברירת מחדל")

@app.on_message(filters.command("del_thumb"))
async def del_thumb(client, message):
    user_id = message.from_user.id
    thumb_path = user_thumbnails.get(user_id)
    if thumb_path and os.path.exists(thumb_path):
        os.remove(thumb_path)
        user_thumbnails.pop(user_id, None)
        await message.reply("תמונת הממוזערת נמחקה")
    else:
        await message.reply("אין תמונת ממוזערת מוגדרת")

# קבלת תמונה לעדכון תמונת ממוזערת למשתמש
@app.on_message(filters.photo)
async def set_thumbnail(client, message):
    user_id = message.from_user.id
    if not os.path.exists("thumbs"):
        os.makedirs("thumbs")
    thumb_path = f"thumbs/{user_id}.jpg"
    await message.download(file_name=thumb_path)
    user_thumbnails[user_id] = thumb_path
    await message.reply("תמונת הממוזערת עודכנה!")

####################################
# תהליך ההורדה והבחירות
####################################

# שליחת קישור להורדה – בדיקת גודל הקובץ וקבלת שם הקובץ המקורי
@app.on_message(filters.text & filters.regex(r'https?://'))
async def handle_download_link(client, message):
    user_id = message.from_user.id

    # בדיקה אם למשתמש כבר יש פעולה מתבצעת
    if user_id in user_locks and user_locks[user_id].locked():
        await message.reply("יש לך פעולה מתבצעת כבר, אנא המתן לסיומה.")
        return

    download_link = message.text.strip()
    user_data[user_id] = {"download_link": download_link}

    try:
        response = requests.head(download_link)
        file_size = int(response.headers.get("content-length", 0))
        if file_size > MAX_FILE_SIZE:
            error_msg = await message.reply(
                f"הקובץ גדול מדי ({file_size/(1024*1024):.1f}MB). הגודל המקסימלי הוא {MAX_FILE_SIZE/(1024*1024)}MB"
            )
            messages_to_delete.setdefault(user_id, []).append(error_msg.id)
            return

        original_filename = os.path.basename(download_link.split("?")[0]) # Use os.path.basename
        file_extension = os.path.splitext(original_filename)[1]
        if not original_filename or not file_extension:
            msg = await message.reply(
                "לא נמצא שם קובץ בקישור.\nאנא שלחו שם קובץ כולל פורמט (למשל: video.mp4)"
            )
            messages_to_delete.setdefault(user_id, []).append(msg.id)
            user_data[user_id]["waiting_for_filename"] = True
            return

        user_data[user_id]["original_filename"] = original_filename

        # בשלב זה מוצגת רק בחירת שינוי שם (דלג או שנה)
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton("דלג על שינוי", callback_data="skip_name"),
                    types.InlineKeyboardButton("שנה שם", callback_data="change_name")
                ]
            ]
        )
        msg = await message.reply(
            f"שם הקובץ המקורי הוא: `{original_filename}`\nהאם תרצו לשנות את השם?",
            reply_markup=keyboard
        )
        messages_to_delete.setdefault(user_id, []).append(msg.id)

    except Exception as e:
        error_msg = await message.reply("שגיאה בבדיקת גודל הקובץ")
        messages_to_delete.setdefault(user_id, []).append(error_msg.id)
        return

# טיפול בלחיצות על כפתורי ה־Inline
@app.on_callback_query()
async def callback_query_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    if user_id not in user_data:
        await callback_query.answer("נא להתחיל מחדש עם /start", show_alert=True)
        return

    if data == "skip_name":
        original_filename = user_data[user_id].get("original_filename")
        user_data[user_id]["final_filename"] = original_filename
        await callback_query.answer("השם נשאר כמו המקורי")
        # כעת מוצגת בחירת אופן ההעלאה (כתוביות בלבד)
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton("כתוביות", callback_data="download_subtitles")
                ]
            ]
        )
        await callback_query.message.edit_text("איך תרצו להעלות את הקובץ?", reply_markup=keyboard)

    elif data == "change_name":
        user_data[user_id]["waiting_for_new_name"] = True
        await callback_query.answer("שלח את השם החדש (בלי סיומת)")
        await callback_query.message.edit_text("שלח שם חדש לקובץ (הסיומת תישאר כפי שהיא)")

    elif data == "download_subtitles":
        user_data[user_id]["upload_type"] = data
        await callback_query.answer()
        download_link = user_data[user_id].get("download_link")
        final_filename = user_data[user_id].get("final_filename", user_data[user_id].get("original_filename"))
        if not download_link or not final_filename:
            await callback_query.answer("שגיאה: אין קישור או שם קובץ", show_alert=True)
            return
        status_msg = await callback_query.message.edit_text("מתחיל בהורדת הווידאו להפקת כתוביות...")

        if user_id not in user_locks:
            user_locks[user_id] = asyncio.Lock()
        task = asyncio.create_task(handle_subtitles_download(client, callback_query.message, final_filename, download_link, status_msg))
        user_tasks[user_id] = task
        await task
        user_data.pop(user_id, None)
        user_tasks.pop(user_id, None)
    else:
        await callback_query.answer("פעולה לא מוכרת", show_alert=True)

# טיפול בהקלדת שם חדש במקרה שנבחר לשנות שם
@app.on_message(filters.text)
async def handle_new_name(client, message):
    user_id = message.from_user.id
    if user_id not in user_data:
        return
    if user_data[user_id].get("waiting_for_new_name"):
        new_name = message.text.strip()
        original_filename = user_data[user_id].get("original_filename")
        file_extension = os.path.splitext(original_filename)[1]
        final_filename = f"{new_name}{file_extension}"
        user_data[user_id]["final_filename"] = final_filename
        user_data[user_id].pop("waiting_for_new_name", None)
        try:
            await message.delete()
        except Exception:
            pass
        # כעת מוצגת בחירת אופן ההעלאה לאחר שינוי שם
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                     types.InlineKeyboardButton("כתוביות", callback_data="download_subtitles")
                ]
            ]
        )
        sent_msg = await message.reply("איך תרצו להעלות את הקובץ?", reply_markup=keyboard)
        messages_to_delete.setdefault(user_id, []).append(sent_msg.id)
    elif user_data[user_id].get("waiting_for_filename"): # Handle missing filename
        final_filename = message.text.strip()
        user_data[user_id]["final_filename"] = final_filename
        user_data[user_id].pop("waiting_for_filename", None)
        try:
            await message.delete()
        except Exception:
            pass
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton("כתוביות", callback_data="download_subtitles")
                ]
            ]
        )
        sent_msg = await message.reply("איך תרצו להעלות את הקובץ?", reply_markup=keyboard)
        messages_to_delete.setdefault(user_id, []).append(sent_msg.id)

####################################
# פונקציות להורדה, העלאה והפקת כתוביות
####################################

async def handle_subtitles_download(client, message, filename, download_link, status_msg):
    user_id = message.from_user.id
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    # שמירת הקובץ להורדה
    file_path = f"downloads/{user_id}_{filename}" # Removed user_id prefix
    #srt_path = f"downloads/{user_id}_{os.path.splitext(filename)[0]}.srt"
    base_filename = os.path.splitext(filename)[0]
    srt_files = [] # List to store paths of all srt files.

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_link) as response:
                total_size = int(response.headers.get("content-length", 0))
                block_size = 8192
                downloaded = 0
                start_time = time.time()
                last_update_time = start_time
                last_percent = 0

                with open(file_path, "wb") as file:
                    async for chunk in response.content.iter_chunked(block_size):
                        file.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            current_time = time.time()
                            if percent - last_percent >= 5 or current_time - last_update_time >= 5:
                                speed = (downloaded / (current_time - start_time)) / (1024 * 1024)
                                filled = int(percent / 10)
                                empty = 10 - filled
                                progress_bar = "█" * filled + "▒" * empty
                                try:
                                    await status_msg.edit_text(
                                        f"הורדה בתהליך:\n{progress_bar} {percent:.1f}%\n"
                                        f"מהירות: {speed:.2f} MB/s\n"
                                        f"הורד: {downloaded/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB"
                                    )
                                except Exception:
                                    pass
                                last_update_time = current_time
                                last_percent = percent

        await status_msg.edit_text("הורדה הושלמה, מפיק כתוביות...")

        # הפעלת ffmpeg להפקת כתוביות (לולאה להפקת כל סוגי הכתוביות)
        for i in range(10):  # מקסימום 10 ניסיונות, ניתן לשנות
            srt_path = f"downloads/{base_filename}_{i}.srt" # Removed user_id
            ffmpeg_cmd = ["ffmpeg", "-i", file_path, "-map", f"0:s:{i}", srt_path, "-y"]
            proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(srt_path):
                srt_files.append(srt_path)  # Add path to the list
            # Break if no more subtitles are found
            if "Stream map '0:s:{i}'" in proc.stderr.decode():
                break

        if not srt_files:
            await status_msg.edit_text("לא נמצאו כתוביות בוידאו או שהפקת הכתוביות נכשלה.")
        else:
            # Send all subtitle files
            for srt_file in srt_files:
                await client.send_document(message.chat.id, srt_file, caption="כתוביות (SRT)")
            await status_msg.edit_text("הכתוביות נשלחו בהצלחה.")

    except Exception as e:
        await status_msg.edit_text(f"שגיאה: {str(e)}")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        for srt_file in srt_files:
            if os.path.exists(srt_file):
                os.remove(srt_file)
        messages_to_delete[user_id] = []
        await client.send_message(message.chat.id, "שלחו /start להורדה נוספת")

app.run()

