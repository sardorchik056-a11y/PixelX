import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from database import db_add_px, db_get_or_create_user

# ─────────────────────────────────────────
#  Router
# ─────────────────────────────────────────
bonus_router = Router()

# ─────────────────────────────────────────
#  Инжектируемые функции (из main.py)
# ─────────────────────────────────────────
is_owner_fn  = None
set_owner_fn = None

# ─────────────────────────────────────────
#  Emoji IDs
# ─────────────────────────────────────────
EMOJI_BONUS = "5305699699204837855"
EMOJI_GOLD  = "5278467510604160626"
EMOJI_BACK  = "5906771962734057347"

# ─────────────────────────────────────────
#  Награды за грани кубика
# ─────────────────────────────────────────
DICE_REWARDS = {
    1: 200,
    2: 350,
    3: 500,
    4: 750,
    5: 1000,
    6: 1500,
}

COOLDOWN_HOURS = 24

# ─────────────────────────────────────────
#  База данных — утилиты
# ─────────────────────────────────────────
def _get_last_bonus(uid: int) -> datetime | None:
    from database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_bonus_at FROM bonus WHERE uid = ?", (uid,)
        ).fetchone()
        if row and row["last_bonus_at"]:
            return datetime.fromisoformat(row["last_bonus_at"])
        return None


def _set_last_bonus(uid: int):
    from database import get_conn
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO bonus (uid, last_bonus_at)
            VALUES (?, ?)
            ON CONFLICT(uid) DO UPDATE SET last_bonus_at = excluded.last_bonus_at
        """, (uid, now))


def _next_bonus_time(last: datetime) -> str:
    return (last + timedelta(hours=COOLDOWN_HOURS)).strftime("%d.%m.%Y в %H:%M")


# ─────────────────────────────────────────
#  Клавиатура
# ─────────────────────────────────────────
def back_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
    ]])


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
def build_result_text(face: int, reward: int) -> str:
    face_emojis = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}
    return (
        f'<tg-emoji emoji-id="{EMOJI_BONUS}">🎁</tg-emoji> <b>Ежедневный бонус</b>\n\n'
        f'<blockquote>'
        f'🎲 Выпало: {face_emojis[face]} — <b>{face}</b>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> '
        f'Начислено: <code>{reward:,} Px</code>'
        f'</blockquote>'
    )


def build_cooldown_text(last: datetime) -> str:
    return (
        f'<tg-emoji emoji-id="{EMOJI_BONUS}">🎁</tg-emoji> <b>Ежедневный бонус</b>\n\n'
        f'<blockquote>'
        f'⏳ Вы уже получали бонус сегодня.\n\n'
        f'🕐 Следующий бросок доступен:\n'
        f'<code>{_next_bonus_time(last)}</code>'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Хэндлер
# ─────────────────────────────────────────
@bonus_router.callback_query(F.data == "bonus")
async def cb_bonus(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid  = call.from_user.id
    last = _get_last_bonus(uid)

    # Кулдаун — показываем когда следующий бросок
    if last is not None and datetime.now() - last < timedelta(hours=COOLDOWN_HOURS):
        await call.message.edit_text(build_cooldown_text(last), reply_markup=back_main_keyboard())
        if set_owner_fn:
            set_owner_fn(call.message.message_id, uid)
        await call.answer()
        return

    await call.answer()

    db_get_or_create_user(call.from_user)

    # Сразу кидаем кубик — Telegram сам анимирует выпавшую грань
    dice_msg = await call.message.answer_dice(emoji="🎲")
    face     = dice_msg.dice.value
    reward   = DICE_REWARDS[face]

    # Ждём пока анимация кубика завершится (~3 сек)
    await asyncio.sleep(3)

    # Начисляем и сохраняем
    db_add_px(uid, reward)
    _set_last_bonus(uid)

    # Отправляем результат отдельным сообщением
    result_msg = await call.message.answer(
        build_result_text(face, reward),
        reply_markup=back_main_keyboard(),
    )
    if set_owner_fn:
        set_owner_fn(result_msg.message_id, uid)
