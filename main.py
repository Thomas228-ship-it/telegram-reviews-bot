import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8324522332:AAGy6qDs8j-uILme5ReWJXvmUdyUXHBONJY")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "6555503209")
ADMIN_IDS: List[int] = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect("reviews.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    rating INTEGER,
    text TEXT,
    attachments TEXT,
    status TEXT,
    admin_id INTEGER,
    moderation_date TEXT,
    created_at TEXT
)
""")
conn.commit()

REVIEW_SESSIONS: Dict[int, Dict] = {}
PENDING_EDITS: Dict[int, tuple] = {}

LAST_BOT_MESSAGE_BY_CHAT: Dict[int, int] = {}

STATUS_EMOJI = {
    "pending": "⏳",
    "approved": "✅",
    "rejected": "❌"
}

# ---- helper keyboards ----
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список отзывов", callback_data="list_reviews")],
        [InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data="leave_review")]
    ])
    return kb

def rating_kb() -> InlineKeyboardMarkup:
    stars = [InlineKeyboardButton(text=f"{i}⭐", callback_data=f"rate_{i}") for i in range(1, 6)]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        stars,
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="main_menu")]
    ])
    return kb

def admin_keyboard(review_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve_{review_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{review_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{review_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{review_id}")
        ]
    ])
    return kb

def _attachments_to_str(lst: Optional[List[Tuple[str, str]]]) -> Optional[str]:
    """
    lst: list of tuples (type, file_id)
    returns "type:fileid,type2:fileid2" or None
    """
    if not lst:
        return None
    parts = []
    for t, fid in lst:
        if not t or not fid:
            continue
        parts.append(f"{t}:{fid}")
    return ",".join(parts) if parts else None

def _parse_attachments_from_db(s: Optional[str]) -> List[Tuple[str, str]]:
    """
    parse "type:fileid,type2:fileid2" -> [("type","fileid"), ...]
    """
    if not s:
        return []
    res = []
    for p in s.split(","):
        if not p:
            continue
        if ":" not in p:
            continue
        t, fid = p.split(":", 1)
        res.append((t, fid))
    return res

async def _delete_last_bot_message_in_chat(chat_id: int):
    last_id = LAST_BOT_MESSAGE_BY_CHAT.get(chat_id)
    if not last_id:
        return
    try:
        await bot.delete_message(chat_id, last_id)
    except Exception:
        logger.debug("Could not delete last bot message %s in chat %s", last_id, chat_id)
    finally:
        LAST_BOT_MESSAGE_BY_CHAT.pop(chat_id, None)

async def _store_last_bot_message(chat_id: int, message_obj: types.Message):
    try:
        if message_obj and getattr(message_obj, "message_id", None):
            LAST_BOT_MESSAGE_BY_CHAT[chat_id] = message_obj.message_id
    except Exception:
        logger.exception("Failed to store last bot message for chat %s", chat_id)

async def add_review_to_db(user_id: int, username: str, rating: int, text_body: str, attachments_list: Optional[List[Tuple[str, str]]] = None) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM reviews WHERE user_id = ?",
        (user_id,)
    )
    count = cursor.fetchone()[0]
    
    if count >= 2:
        raise ValueError("Превышен лимит отзывов (максимум 2 на пользователя)")
    
    created_at = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    attachments_str = _attachments_to_str(attachments_list)
    cursor.execute(
        "INSERT INTO reviews (user_id, username, rating, text, attachments, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (user_id, username, rating, text_body, attachments_str, created_at)
    )
    conn.commit()
    rid = cursor.lastrowid
    await notify_admins_new_review(rid)
    return rid

async def _send_text_with_attachments_and_kb(chat_id: int, text: str, attachments: Optional[List[str]], kb: Optional[InlineKeyboardMarkup] = None):
    """
    attachments: list of strings "type:fileid" (as stored in DB) or None
    First (main) attachment is sent with caption/text (if possible),
    extra attachments are sent afterwards without caption.
    Special handling for video_note: since it can't have caption, we send it first, then the text message.
    The function also replaces last bot message in the chat.
    """
    attachments = attachments or []
    try:
        try:
            await _delete_last_bot_message_in_chat(chat_id)
        except Exception:
            pass

        parsed = [tuple(x.split(":", 1)) for x in attachments if ":" in x]
        
        if not parsed:
            sent_msg = await bot.send_message(chat_id, text, reply_markup=kb)
            await _store_last_bot_message(chat_id, sent_msg)
            return

        first_type, first_fid = parsed[0]
        sent_msg = None

        try:
            if first_type == "video_note":
                try:
                    await bot.send_video_note(chat_id, first_fid)
                except Exception:
                    logger.exception("Failed to send video_note %s to %s", first_fid, chat_id)
                sent_msg = await bot.send_message(chat_id, text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
            elif first_type == "photo":
                sent_msg = await bot.send_photo(chat_id, first_fid, caption=text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
            elif first_type == "video":
                sent_msg = await bot.send_video(chat_id, first_fid, caption=text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
            elif first_type == "document":
                sent_msg = await bot.send_document(chat_id, first_fid, caption=text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
            elif first_type == "audio":
                sent_msg = await bot.send_audio(chat_id, first_fid, caption=text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
            elif first_type == "voice":
                if text:
                    sent_msg = await bot.send_message(chat_id, text, reply_markup=kb)
                    await _store_last_bot_message(chat_id, sent_msg)
                    try:
                        await bot.send_voice(chat_id, first_fid)
                    except Exception:
                        logger.exception("Failed to send voice %s to %s", first_fid, chat_id)
                else:
                    sent_msg = await bot.send_voice(chat_id, first_fid, reply_markup=kb)
                    await _store_last_bot_message(chat_id, sent_msg)
            else:
                sent_msg = await bot.send_message(chat_id, text, reply_markup=kb)
                await _store_last_bot_message(chat_id, sent_msg)
        except Exception:
            sent_msg = await bot.send_message(chat_id, text, reply_markup=kb)
            await _store_last_bot_message(chat_id, sent_msg)

        if len(parsed) > 1:
            for t, fid in parsed[1:]:
                try:
                    if t == "photo":
                        await bot.send_photo(chat_id, fid)
                    elif t == "video":
                        await bot.send_video(chat_id, fid)
                    elif t == "document":
                        await bot.send_document(chat_id, fid)
                    elif t == "audio":
                        await bot.send_audio(chat_id, fid)
                    elif t == "voice":
                        await bot.send_voice(chat_id, fid)
                    elif t == "video_note":
                        await bot.send_video_note(chat_id, fid)
                    else:
                        await bot.send_document(chat_id, fid)
                except Exception:
                    logger.exception("Failed to send extra attachment %s (%s) to %s", t, fid, chat_id)
    except Exception:
        logger.exception("Error while sending text+attachments to %s", chat_id)

async def notify_admins_new_review(rid: int):
    try:
        cursor.execute("SELECT id, user_id, username, rating, text, attachments, created_at FROM reviews WHERE id = ?", (rid,))
        row = cursor.fetchone()
        if not row:
            return
        _id, user_id, username, rating, text_body, attachments, created_at = row
        author = username or "Аноним"
        stars = "⭐" * int(rating)
        text = f"🆕 Новый отзыв #{rid} — {stars}\nОт: @{author}\nДата: {created_at}\n\n{text_body}"
        kb = admin_keyboard(rid)

        for a in ADMIN_IDS:
            try:
                at_list = (attachments.split(',') if attachments else [])
                await _send_text_with_attachments_and_kb(a, text, at_list, kb)
            except Exception:
                logger.exception("Failed to notify admin %s about review %s", a, rid)
    except Exception:
        logger.exception("Error in notify_admins_new_review for id=%s", rid)

async def _send_step_message(uid: int, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await _delete_last_bot_message_in_chat(uid)
    except Exception:
        pass

    try:
        msg = await bot.send_message(uid, text, reply_markup=reply_markup)
    except Exception:
        try:
            msg = await bot.send_message(uid, text, reply_markup=reply_markup)
        except Exception:
            logger.exception("Failed to send step message to %s", uid)
            return None

    await _store_last_bot_message(uid, msg)
    session = REVIEW_SESSIONS.get(uid)
    if session is not None:
        session["last_bot_message_id"] = msg.message_id
    return msg

async def _delete_last_step_message_for_user(uid: int):
    try:
        await _delete_last_bot_message_in_chat(uid)
    except Exception:
        pass
    session = REVIEW_SESSIONS.get(uid)
    if session:
        session["last_bot_message_id"] = None

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Привет! Я бот для приёма отзывов. Выбери действие:", reply_markup=main_menu_kb())

@dp.message(Command("admin"))
async def cmd_admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("Только для администраторов.")
        return

    cursor.execute("SELECT id, username, rating, status FROM reviews ORDER BY created_at DESC LIMIT 50")
    rows = cursor.fetchall()
    if not rows:
        await message.reply("Нет отзывов для модерации.")
        return

    kb_rows = []
    for rid, username, rating, status in rows:
        author = username or "Аноним"
        status_icon = STATUS_EMOJI.get(status, status)
        btn_text = f"Модерировать отзыв от {author} ({rating}⭐) [{status_icon}]"
        kb_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"admin_review_{rid}")])

    kb_rows.append([InlineKeyboardButton(text="Закрыть", callback_data="admin_close")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    try:
        await _delete_last_bot_message_in_chat(message.chat.id)
    except Exception:
        pass

    sent = await bot.send_message(message.chat.id, "Админ-панель — выберите отзыв:", reply_markup=kb)
    await _store_last_bot_message(message.chat.id, sent)

@dp.callback_query(F.data == "admin_close")
async def cb_admin_close(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.")
        return
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.answer()

@dp.callback_query(F.data == "list_reviews")
async def cb_list_reviews(query: CallbackQuery):
    cursor.execute("SELECT id, username, rating, text, attachments, created_at FROM reviews WHERE status = 'approved' ORDER BY created_at DESC LIMIT 50")
    rows = cursor.fetchall()
    if not rows:
        await query.message.answer("Пока нет одобренных отзывов.")
        await query.answer()
        return

    total = len(rows)
    review_buttons = []

    for idx, row in enumerate(rows):
        rid, username, rating, _text, _attachments, created_at = row
        author = username or "Аноним"
        seq = total - idx
        btn_text = f"Отзыв {seq} ({rating}⭐ от {author})"
        review_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"review_{rid}_{seq}")])

    review_buttons.append([InlineKeyboardButton(text="Назад", callback_data="main_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=review_buttons)
    try:
        await query.message.answer("Выберите отзыв:", reply_markup=kb)
    except Exception:
        await query.message.answer("Выберите отзыв:", reply_markup=kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin_review_"))
async def cb_admin_review_open(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    try:
        review_id = int(query.data.split("_")[2])
    except Exception:
        await query.answer("Некорректный ID отзыва.")
        return

    cursor.execute("SELECT username, rating, text, attachments, created_at, status FROM reviews WHERE id = ?", (review_id,))
    row = cursor.fetchone()
    if not row:
        await query.answer("Отзыв не найден.")
        return

    username, rating, text_body, attachments, created_at, status = row
    author = username or "Аноним"
    stars = "⭐" * int(rating)
    status_icon = STATUS_EMOJI.get(status, status)
    review_text = (
        f"Отзыв #{review_id}\n\n"
        f"От: {author}\n"
        f"Оценка: {stars}\n"
        f"Статус: {status_icon}\n"
        f"Дата: {created_at}\n\n"
        f"{text_body or ''}"
    )
    kb = admin_keyboard(review_id)
    at_list = (attachments.split(',') if attachments else [])
    await _send_text_with_attachments_and_kb(query.from_user.id, review_text, at_list, kb)
    await query.answer()

@dp.callback_query(F.data.startswith("review_"))
async def cb_show_review(query: CallbackQuery):
    try:
        parts = query.data.split("_")
        review_id = int(parts[1])
        seq_number = int(parts[2]) if len(parts) > 2 else None
    except Exception:
        await query.answer("Некорректный ID", show_alert=True)
        return
    cursor.execute("SELECT username, rating, text, attachments, created_at FROM reviews WHERE id = ? AND status = 'approved'", (review_id,))
    row = cursor.fetchone()
    if not row:
        await query.message.answer("Отзыв не найден или ещё не одобрен.")
        await query.answer()
        return
    username, rating, text_body, attachments, created_at = row
    author = username or "Аноним"
    stars = "⭐" * int(rating)

    display_number = seq_number if seq_number is not None else review_id
    header = f"Отзыв #{display_number}\n\nОт: {author}\nОценка: {stars}\nДата: {created_at}\n\n"
    full_text = header + (text_body or "")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К списку отзывов", callback_data="list_reviews")],
        [InlineKeyboardButton(text="↩️ В главное меню", callback_data="main_menu")]
    ])
    at_list = (attachments.split(',') if attachments else [])
    await _send_text_with_attachments_and_kb(query.message.chat.id, full_text, at_list, kb)
    await query.answer()

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(query: CallbackQuery):
    try:
        await query.message.edit_text("Привет! Я бот для приёма отзывов. Выбери действие:", reply_markup=main_menu_kb())
    except Exception:
        await query.message.answer("Привет! Я бот для приёма отзывов. Выбери действие:", reply_markup=main_menu_kb())
    await query.answer()

@dp.callback_query(F.data == "leave_review")
async def cb_leave_review(query: CallbackQuery):
    uid = query.from_user.id
    
    cursor.execute(
        "SELECT COUNT(*) FROM reviews WHERE user_id = ?",
        (uid,)
    )
    count = cursor.fetchone()[0]
    
    if count >= 2:
        await query.answer("Вы уже оставили максимальное количество отзывов (2).", show_alert=True)
        return
    
    REVIEW_SESSIONS[uid] = {"step": "rating", "rating": None, "text": None, "attachments": [], "last_bot_message_id": None}
    await _send_step_message(uid, "Для начала оцените по шкале от 1 до 5 звёзд:", reply_markup=rating_kb())
    try:
        await query.answer()
    except Exception:
        pass

@dp.callback_query(F.data.startswith("rate_"))
async def cb_rating_selected(query: CallbackQuery):
    uid = query.from_user.id
    if uid not in REVIEW_SESSIONS:
        await query.answer("Сессия не найдена. Нажмите 'Оставить отзыв' снова.", show_alert=True)
        return
    try:
        rating = int(query.data.split("_")[1])
    except Exception:
        await query.answer("Неверный рейтинг", show_alert=True)
        return
    REVIEW_SESSIONS[uid]["rating"] = rating
    REVIEW_SESSIONS[uid]["step"] = "text"
    await _send_step_message(uid, "Ваша оценка сохранена!\nТеперь пришлите ваш отзыв (это может быть скрин/видео/кружок):")
    await query.answer()

def _get_message_text(message: Message) -> Optional[str]:
    return message.text if message.text is not None else getattr(message, "caption", None)

def _gather_attachments_from_message(message: Message) -> List[Tuple[str, str]]:
    """
    Собирает вложения из message и возвращает список tuples (type, file_id)
    types: photo, document, video, voice, audio, video_note
    """
    res: List[Tuple[str, str]] = []
    try:
        if message.photo:
            res.append(("photo", message.photo[-1].file_id))
        if message.video:
            res.append(("video", message.video.file_id))
        if getattr(message, "video_note", None):
            res.append(("video_note", message.video_note.file_id))
        if message.voice:
            res.append(("voice", message.voice.file_id))
        if message.audio:
            res.append(("audio", message.audio.file_id))
        if message.document:
            res.append(("document", message.document.file_id))
    except Exception:
        logger.exception("Failed to gather attachments from message")
    return res

@dp.message()
async def handle_messages(message: Message):
    uid = message.from_user.id

    if uid in PENDING_EDITS:
        try:
            rid, field = PENDING_EDITS.pop(uid)
            value = (_get_message_text(message) or "").strip()
            now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
            if field == 'text':
                if len(value) < 10 or len(value) > 2000:
                    await message.reply("Неверная длина текста. Отправьте текст 10–2000 символов.")
                    return
                cursor.execute("UPDATE reviews SET text = ?, admin_id = ?, moderation_date = ? WHERE id = ?", (value, uid, now, rid))
                conn.commit()
                await message.reply(f"Текст отзыва #{rid} обновлён.")
            elif field == 'rating':
                try:
                    rt = int(value)
                    if rt < 1 or rt > 5:
                        raise ValueError
                except Exception:
                    await message.reply("Неверный рейтинг. Отправьте число от 1 до 5.")
                    return
                cursor.execute("UPDATE reviews SET rating = ?, admin_id = ?, moderation_date = ? WHERE id = ?", (rt, uid, now, rid))
                conn.commit()
                await message.reply(f"Рейтинг отзыва #{rid} обновлён на {rt}⭐.")
        except Exception:
            logger.exception("Error while processing admin edit input")
        return

    if uid not in REVIEW_SESSIONS:
        return

    session = REVIEW_SESSIONS[uid]
    step = session.get("step")

    raw_text = _get_message_text(message)
    attachments_here = _gather_attachments_from_message(message)

    if step == "text":
        if not raw_text and not attachments_here:
            await _send_step_message(uid, "Пожалуйста, отправьте текст отзыва (10–2000 символов) или вложение (фото/видео/документ/голос/кружок).")
            return

        if raw_text:
            text_body = raw_text.strip()
            if len(text_body) < 10:
                await _send_step_message(uid, "Текст слишком короткий — минимум 10 символов.")
                return
            if len(text_body) > 2000:
                await _send_step_message(uid, "Текст слишком длинный — максимум 2000 символов.")
                return

            session["text"] = text_body

            if attachments_here:
                session["attachments"] = attachments_here[:3]
                try:
                    rid = await add_review_to_db(uid, message.from_user.username or '', session["rating"], text_body, session["attachments"])
                    await _delete_last_step_message_for_user(uid)
                    await message.answer("Ваш отзыв отправлен на модерацию. Администратор проверит его и опубликует или отклонит.")
                except ValueError as e:
                    await _delete_last_step_message_for_user(uid)
                    await message.answer(str(e))
                except Exception:
                    logger.exception("Failed to save review with attachments")
                    await message.answer("Произошла ошибка при сохранении отзыва. Попробуйте ещё раз.")
                REVIEW_SESSIONS.pop(uid, None)
                return

            session["attachments"] = []
            session["step"] = "attachments"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Да — прикреплю", callback_data="attach_yes")],
                [InlineKeyboardButton(text="Нет — отправить", callback_data="confirm_review")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
            ])
            await _send_step_message(uid, "Хотите прикрепить скрин/видео/кружок?", reply_markup=kb)
            return

        if attachments_here and not raw_text:
            session["attachments"] = attachments_here[:3]
            session["step"] = "maybe_add_text_for_attachments"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Да — напишу текст", callback_data="write_text")],
                [InlineKeyboardButton(text="Нет — отправить", callback_data="confirm_review")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
            ])
            await _send_step_message(uid, "Хотите написать текст для отзыва? (от 10 символов)", reply_markup=kb)
            return

    if step == "attachments":
        if not attachments_here and (not raw_text or raw_text.lower() != "готово"):
            await _send_step_message(uid, "Пришлите до 3 файлов (скрин/видео/кружок) или нажмите 'Готово — подтвердить отправку'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Готово — подтвердить отправку", callback_data="confirm_review")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
            ]))
            return

        if attachments_here:
            current = session.get("attachments", []) or []
            if len(current) + len(attachments_here) > 3:
                await _send_step_message(uid, "Нельзя прикрепить больше 3 файлов.")
                return
            to_add = attachments_here[:(3 - len(current))]
            current.extend(to_add)
            session["attachments"] = current

            if any(t == "voice" for t, _ in to_add):
                session["step"] = "voice_caption"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Пропустить подпись", callback_data="skip_voice_caption")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
                ])
                await _send_step_message(uid, f"Голос принято. Хотите добавить подпись к голосовому сообщению? Отправьте текст или нажмите 'Пропустить подпись'.", reply_markup=kb)
                return
            else:
                await _send_step_message(uid, f"Вложение принято. Сейчас прикреплено {len(current)}/3. Когда закончите — нажмите 'Готово — подтвердить отправку'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Готово — подтвердить отправку", callback_data="confirm_review")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
                ]))
                return

        if raw_text and raw_text.lower() == "готово":
            try:
                rid = await add_review_to_db(uid, message.from_user.username or '', session["rating"], session.get("text", ""), session.get("attachments", []))
                await _delete_last_step_message_for_user(uid)
                await message.answer("Ваш отзыв отправлен на модерацию. Администратор проверит его и опубликует или отклонит.")
                REVIEW_SESSIONS.pop(uid, None)
            except ValueError as e:
                await _delete_last_step_message_for_user(uid)
                await message.answer(str(e))
                REVIEW_SESSIONS.pop(uid, None)
            except Exception:
                logger.exception("Failed to save review")
                await message.answer("Произошла ошибка при сохранении отзыва. Попробуйте ещё раз.")
                REVIEW_SESSIONS.pop(uid, None)
            return

    if step == "maybe_add_text_for_attachments":
        if raw_text:
            text_body = raw_text.strip()
            if len(text_body) < 10:
                await _send_step_message(uid, "Текст слишком короткий — минимум 10 символов. Отправьте ещё раз или нажмите 'Нет — отправить'.")
                return
            if len(text_body) > 2000:
                await _send_step_message(uid, "Текст слишком длинный — максимум 2000 символов.")
                return
            session["text"] = text_body
            try:
                rid = await add_review_to_db(uid, message.from_user.username or '', session["rating"], text_body, session.get("attachments", []))
                await _delete_last_step_message_for_user(uid)
                await message.answer("Ваш отзыв отправлен на модерацию. Администратор проверит его и опубликует или отклонит.")
            except ValueError as e:
                await _delete_last_step_message_for_user(uid)
                await message.answer(str(e))
            except Exception:
                logger.exception("Failed to save review with attachments+text")
                await message.answer("Произошла ошибка при сохранении отзыва. Попробуйте ещё раз.")
            REVIEW_SESSIONS.pop(uid, None)
            return

        if attachments_here:
            current = session.get("attachments", []) or []
            if len(current) + len(attachments_here) > 3:
                await _send_step_message(uid, "Нельзя прикрепить больше 3 файлов.")
                return
            to_add = attachments_here[:(3 - len(current))]
            current.extend(to_add)
            session["attachments"] = current
            await _send_step_message(uid, f"Вложение принято. Сейчас прикреплено {len(current)}/3. Если хотите — отправьте текст или нажмите 'Нет — отправить' (кнопка).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Нет — отправить", callback_data="confirm_review")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
            ]))
            return

    if step == "voice_caption":
        caption_text = (_get_message_text(message) or "").strip()
        if caption_text and len(caption_text) > 500:
            await _send_step_message(uid, "Слишком длинная подпись к голосу — максимум 500 символов. Отправьте короткую подпись или нажмите 'Пропустить подпись'.")
            return


        text_body = caption_text
        attachments = session.get("attachments", [])
        try:
            rid = await add_review_to_db(uid, message.from_user.username or '', session["rating"], text_body, attachments)
            await _delete_last_step_message_for_user(uid)
            await message.answer("Ваш отзыв с голосовым сообщением отправлен на модерацию.")
        except ValueError as e:
            await _delete_last_step_message_for_user(uid)
            await message.answer(str(e))
        except Exception:
            logger.exception("Failed to save voice+caption review")
            await message.answer("Произошла ошибка при сохранении отзыва. Попробуйте ещё раз.")
        REVIEW_SESSIONS.pop(uid, None)
        return

@dp.callback_query(F.data == "confirm_review")
async def cb_confirm_review(query: CallbackQuery):
    uid = query.from_user.id
    if uid not in REVIEW_SESSIONS:
        await query.answer("Сессия не найдена.", show_alert=True)
        return
    sess = REVIEW_SESSIONS.pop(uid)
    try:
        last = sess.get("last_bot_message_id")
        if last:
            await bot.delete_message(uid, last)
    except Exception:
        pass

    rating = sess.get("rating")
    text_body = sess.get("text") or ""
    attachments = sess.get("attachments", [])
    if not rating:
        await query.answer("Неполные данные. Отзыв не отправлен.", show_alert=True)
        return
    
    try:
        rid = await add_review_to_db(uid, query.from_user.username or '', rating, text_body, attachments)
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to save review for user %s", uid)
        await query.answer("Произошла ошибка при сохранении отзыва.", show_alert=True)
        return
    
    try:
        await query.message.answer("Ваш отзыв отправлен на модерацию. Администратор проверит его и опубликует или отклонит.")
    except Exception:
        logger.exception("Failed to send final confirmation to user %s", uid)
    await query.answer("Отзыв отправлен")

@dp.callback_query(F.data == "cancel_review")
async def cb_cancel_review(query: CallbackQuery):
    uid = query.from_user.id
    if uid in REVIEW_SESSIONS:
        try:
            await _delete_last_step_message_for_user(uid)
        except Exception:
            pass
        REVIEW_SESSIONS.pop(uid)
    await query.message.answer("Процесс отправки отзыва отменён.")
    await query.answer()

@dp.callback_query(F.data == "skip_voice_caption")
async def cb_skip_voice_caption(query: CallbackQuery):
    uid = query.from_user.id
    if uid not in REVIEW_SESSIONS:
        await query.answer("Сессия не найдена.", show_alert=True)
        return
    session = REVIEW_SESSIONS.pop(uid)
    rating = session.get("rating")
    attachments = session.get("attachments", [])
    if not rating:
        await query.answer("Неполные данные. Отзыв не отправлен.", show_alert=True)
        return
    try:
        rid = await add_review_to_db(uid, query.from_user.username or '', rating, "", attachments)
        try:
            await query.message.answer("Ваш отзыв отправлен на модерацию (без подписи к голосу).")
        except Exception:
            pass
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        return
    except Exception:
        logger.exception("Failed to save voice-only review (skip caption)")
        await query.answer("Ошибка при сохранении.", show_alert=True)
        return
    await query.answer("Отзыв отправлен")

@dp.callback_query(F.data == "attach_yes")
async def cb_attach_yes(query: CallbackQuery):
    uid = query.from_user.id
    if uid not in REVIEW_SESSIONS:
        await query.answer("Сессия не найдена.", show_alert=True)
        return
    session = REVIEW_SESSIONS[uid]
    session["step"] = "attachments"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово — подтвердить отправку", callback_data="confirm_review")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel_review")]
    ])
    await _send_step_message(uid, "Пришлите до 3 файлов (скрин/видео/кружок). Когда закончите — нажмите 'Готово — подтвердить отправку'.", reply_markup=kb)
    try:
        await query.answer()
    except Exception:
        pass

@dp.callback_query(F.data == "write_text")
async def cb_write_text(query: CallbackQuery):
    uid = query.from_user.id
    if uid not in REVIEW_SESSIONS:
        await query.answer("Сессия не найдена.", show_alert=True)
        return
    session = REVIEW_SESSIONS[uid]
    session["step"] = "maybe_add_text_for_attachments"
    await _send_step_message(uid, "Отправьте текст для отзыва (10–2000 символов).")
    await query.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def cb_admin_approve(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    try:
        rid = int(query.data.split("_")[1])
    except Exception:
        await query.answer("Некорректный ID", show_alert=True)
        return
    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    cursor.execute("UPDATE reviews SET status = 'approved', admin_id = ?, moderation_date = ? WHERE id = ?", (query.from_user.id, now, rid))
    conn.commit()
    cursor.execute("SELECT user_id FROM reviews WHERE id = ?", (rid,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            await bot.send_message(row[0], "Ваш отзыв опубликован. Спасибо!")
        except Exception:
            logger.exception("Failed to notify author for review %s", rid)
    try:
        await query.message.edit_text(f"Отзыв #{rid} — принят ✅")
    except Exception:
        pass
    await query.answer("Отзыв одобрен и опубликован")

@dp.callback_query(F.data.startswith("reject_"))
async def cb_admin_reject(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    try:
        rid = int(query.data.split("_")[1])
    except Exception:
        await query.answer("Некорректный ID", show_alert=True)
        return

    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    cursor.execute("UPDATE reviews SET status = 'rejected', admin_id = ?, moderation_date = ? WHERE id = ?", (query.from_user.id, now, rid))
    conn.commit()
    cursor.execute("SELECT user_id FROM reviews WHERE id = ?", (rid,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            await bot.send_message(row[0], "Ваш отзыв отклонён.")
        except Exception:
            logger.exception("Failed to notify author for rejection %s", rid)
    try:
        await query.message.edit_text(f"Отзыв #{rid} — отклонён ❌")
    except Exception:
        pass
    await query.answer("Отзыв отклонён")

@dp.callback_query(F.data.startswith("delete_"))
async def cb_admin_delete(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    try:
        rid = int(query.data.split("_")[1])
    except Exception:
        await query.answer("Некорректный ID", show_alert=True)
        return

    cursor.execute("SELECT user_id FROM reviews WHERE id = ?", (rid,))
    row = cursor.fetchone()
    user_to_notify = row[0] if row and row[0] else None

    try:
        cursor.execute("DELETE FROM reviews WHERE id = ?", (rid,))
        conn.commit()
    except Exception:
        logger.exception("Failed to DELETE review %s", rid)
        await query.answer("Ошибка при удалении.", show_alert=True)
        return

    try:
        await query.message.delete()
    except Exception:
        logger.debug("Не удалось удалить admin message for review %s", rid)

    try:
        await _delete_last_bot_message_in_chat(query.message.chat.id)
    except Exception:
        pass

    if user_to_notify:
        try:
            await bot.send_message(user_to_notify, "Ваш отзыв был полностью удалён модератором.")
        except Exception:
            logger.exception("Failed to notify author for deletion %s", rid)

    try:
        await query.answer("Отзыв полностью удалён из БД.")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("edit_") & ~F.data.startswith("edit_field_"))
async def cb_admin_edit(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    rid = int(query.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Редактировать текст", callback_data=f"edit_field_{rid}_text")],
        [InlineKeyboardButton(text="Редактировать рейтинг", callback_data=f"edit_field_{rid}_rating")],
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"approve_{rid}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{rid}")]
    ])
    await query.message.answer("Выберите что редактировать:", reply_markup=kb)
    await query.answer()

@dp.callback_query(F.data.startswith("edit_field_"))
async def cb_admin_edit_field(query: CallbackQuery):
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только для администраторов.", show_alert=True)
        return
    parts = query.data.split("_")
    rid = int(parts[2])
    field = parts[3]
    PENDING_EDITS[query.from_user.id] = (rid, field)
    if field == 'text':
        await query.message.answer("Отправьте новый текст отзыва (10–2000 символов).")
    elif field == 'rating':
        await query.message.answer("Отправьте новый рейтинг (число 1–5).")
    await query.answer()

async def main():
    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")

    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
