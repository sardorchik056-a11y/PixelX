from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message, CopyTextButton

from database import (
    db_get_referral_stats,
    db_is_already_referred,
    REFERRAL_REWARD_PX,
)

# ─────────────────────────────────────────
#  Инжектируемые функции
# ─────────────────────────────────────────
is_owner_fn  = None
set_owner_fn = None
get_bot_fn: Bot | None = None

referral_router = Router()

# ─────────────────────────────────────────
#  Emoji IDs
# ─────────────────────────────────────────
EMOJI_BACK     = "5906771962734057347"
EMOJI_PARTNERS = "5906986955911993888"
EMOJI_GOLD     = "5278467510604160626"
EMOJI_COPY     = "5344794505584756273"
EMOJI_STATS    = "5231200819986047254"
EMOJI_SHARE    = "5456185955772579286"

# ─────────────────────────────────────────
#  Клавиатура
# ─────────────────────────────────────────
def referrals_keyboard(uid: int, bot_username: str) -> InlineKeyboardMarkup:
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скопировать ссылку",
            copy_text=CopyTextButton(text=ref_link),
            icon_custom_emoji_id=EMOJI_COPY,
        )],
        [InlineKeyboardButton(
            text="Поделиться",
            url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйся+к+проекту%21",
            icon_custom_emoji_id=EMOJI_SHARE,
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="main_menu",
            icon_custom_emoji_id=EMOJI_BACK,
        )],
    ])

# ─────────────────────────────────────────
#  Текст
# ─────────────────────────────────────────
def referrals_text(uid: int, bot_username: str) -> str:
    stats    = db_get_referral_stats(uid)
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    return (
        f'<tg-emoji emoji-id="{EMOJI_PARTNERS}">👥</tg-emoji> <b>Рефералы</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji>  <b>Награда за приглашение:</b> <code>{REFERRAL_REWARD_PX:,} Px</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji>  <b>Приглашено всего:</b> <code>{stats["total"]}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji>  <b>Активированных:</b> <code>{stats["rewarded"]}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji>  <b>Заработано с рефералов:</b> <code>{stats["earned"]:,} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'🔗  <b>Ваша реферальная ссылка:</b>\n'
        f'<code>{ref_link}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'ℹ️  <i>Reward зачисляется сразу после того как приглашённый напишет /start по вашей ссылке</i>'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Хэндлер — открытие раздела рефералов
# ─────────────────────────────────────────
@referral_router.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    bot_info = await call.bot.get_me()
    uid      = call.from_user.id

    await call.message.edit_text(
        referrals_text(uid, bot_info.username),
        reply_markup=referrals_keyboard(uid, bot_info.username),
        disable_web_page_preview=True,
    )
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer()
