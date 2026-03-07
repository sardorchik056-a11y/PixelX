import asyncio
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database import db_add_px, db_get_or_create_user

bonus_router = Router()

is_owner_fn  = None
set_owner_fn = None

EMOJI_BONUS = "5305699699204837855"
EMOJI_GOLD  = "5278467510604160626"

DICE_REWARDS = {
    1: 200,
    2: 350,
    3: 500,
    4: 750,
    5: 1000,
    6: 1500,
}

COOLDOWN_HOURS = 24

# Кастомные emoji для каждой грани кубика — замени ID на свои
FACE_EMOJI = {
    1: "5382322671679708881",
    2: "5381990043642502553",
    3: "5381879959335738545",
    4: "5382054253403577563",
    5: "5391197405553107640",
    6: "5390966190283694453",
}

def face_tg_emoji(face: int) -> str:
    return f'<tg-emoji emoji-id="{FACE_EMOJI[face]}">🎲</tg-emoji>'

# Множество uid кто прямо сейчас в процессе броска — защита от двойного нажатия
_rolling: set[int] = set()


# ─────────────────────────────────────────
#  БД
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


def _try_claim_bonus(uid: int) -> bool:
    from database import get_conn
    now = datetime.now()
    cooldown_boundary = (now - timedelta(hours=COOLDOWN_HOURS)).isoformat()
    now_iso = now.isoformat()

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO bonus (uid, last_bonus_at)
            VALUES (?, ?)
            ON CONFLICT(uid) DO UPDATE SET last_bonus_at = excluded.last_bonus_at
            WHERE last_bonus_at IS NULL OR last_bonus_at <= ?
        """, (uid, now_iso, cooldown_boundary))
        return cur.rowcount > 0


def _time_until_next(last: datetime) -> str:
    remaining = (last + timedelta(hours=COOLDOWN_HOURS)) - datetime.now()
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return "0мин"
    hours   = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes:02d}мин"
    return f"{minutes}мин"


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
def build_result_text(face: int, reward: int) -> str:
    return (
        f'<tg-emoji emoji-id="{EMOJI_BONUS}">🎁</tg-emoji> <b>Ежедневный бонус</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5310278924616356636">💰</tg-emoji> Выпало: {face_tg_emoji(face)}\n'
        f'<tg-emoji emoji-id="5429651785352501917">💰</tg-emoji> Начислено: <code>+{reward:,} Px</code>'
        f'</blockquote>'
    )


def build_cooldown_text(last: datetime) -> str:
    return (
        f'<blockquote>'
        f'<tg-emoji emoji-id="5382194935057372936">💰</tg-emoji>Бонус доступен через: <code>{_time_until_next(last)}</code>'
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

    uid = call.from_user.id

    if uid in _rolling:
        await call.answer("🎲 Подождите, кубик уже брошен!", show_alert=True)
        return

    claimed = _try_claim_bonus(uid)

    if not claimed:
        last = _get_last_bonus(uid)
        await call.message.answer(build_cooldown_text(last))
        await call.answer()
        return

    _rolling.add(uid)
    await call.answer()

    try:
        db_get_or_create_user(call.from_user)

        dice_msg = await call.message.answer_dice(emoji="🎲")
        face     = dice_msg.dice.value
        reward   = DICE_REWARDS[face]

        await asyncio.sleep(3)

        db_add_px(uid, reward)

        await call.message.answer(build_result_text(face, reward))

    finally:
        _rolling.discard(uid)
