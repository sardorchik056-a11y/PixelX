import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = "8586332532:AAHX758cf6iOUpPNpY2sqseGBYsKJo9js4U"

# ─────────────────────────────────────────
#  Custom Emoji IDs — только из main.py
# ─────────────────────────────────────────
EMOJI_PROFILE     = "5906581476639513176"
EMOJI_PARTNERS    = "5906986955911993888"
EMOJI_GAMES       = "5424972470023104089"
EMOJI_LEADERS     = "5440539497383087970"
EMOJI_ABOUT       = "5251203410396458957"
EMOJI_PROMO       = "5444856076954520455"
EMOJI_INSTRUCT    = "5334544901428229844"
EMOJI_BACK        = "5906771962734057347"
EMOJI_WALLET      = "5443127283898405358"
EMOJI_MINES       = "5307996024738395492"
EMOJI_GOLD        = "5278467510604160626"
EMOJI_STATS       = "5197288647275071607"
EMOJI_DEVELOPMENT = "5445355530111437729"
EMOJI_WELCOME     = "5199885118214255386"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ─────────────────────────────────────────
#  In-memory БД
# ─────────────────────────────────────────
USERS_DB: dict[int, dict] = {}

def get_or_create_user(user) -> dict:
    uid = user.id
    if uid not in USERS_DB:
        USERS_DB[uid] = {
            "id":            uid,
            "first_name":    user.first_name or "",
            "last_name":     user.last_name  or "",
            "username":      user.username   or "",
            "px":            0,
            "games_played":  0,
            "total_won":     0.0,
            "total_lost":    0.0,
            "registered_at": datetime.now(),
        }
    else:
        USERS_DB[uid]["first_name"] = user.first_name or ""
        USERS_DB[uid]["last_name"]  = user.last_name  or ""
        USERS_DB[uid]["username"]   = user.username   or ""
    return USERS_DB[uid]

def days_in_project(registered_at: datetime) -> int:
    return (datetime.now() - registered_at).days

def days_label(n: int) -> str:
    if 11 <= n % 100 <= 19: return "дней"
    r = n % 10
    if r == 1:         return "день"
    if r in (2, 3, 4): return "дня"
    return "дней"


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Профиль",   callback_data="profile",    icon_custom_emoji_id=EMOJI_PROFILE),
            InlineKeyboardButton(text="Рефералы",  callback_data="referrals",  icon_custom_emoji_id=EMOJI_PARTNERS),
            InlineKeyboardButton(text="Игры",      callback_data="games",      icon_custom_emoji_id=EMOJI_GAMES),
        ],
        [
            InlineKeyboardButton(text="Лидеры",    callback_data="leaders",    icon_custom_emoji_id=EMOJI_LEADERS),
            InlineKeyboardButton(text="Биржа",     callback_data="exchange",   icon_custom_emoji_id=EMOJI_WALLET),
        ],
        [
            InlineKeyboardButton(text="Бонус",     callback_data="bonus",      icon_custom_emoji_id=EMOJI_WALLET),
            InlineKeyboardButton(text="Промокоды", callback_data="promocodes", icon_custom_emoji_id=EMOJI_PROMO),
            InlineKeyboardButton(text="Шахта",     callback_data="mine",       icon_custom_emoji_id=EMOJI_MINES),
        ],
        [
            InlineKeyboardButton(text="О проекте", callback_data="about",      icon_custom_emoji_id=EMOJI_ABOUT),
            InlineKeyboardButton(text="Инструкция",callback_data="instruction",icon_custom_emoji_id=EMOJI_INSTRUCT),
        ],
    ])

def back_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
    ]])

def back_profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="profile", icon_custom_emoji_id=EMOJI_BACK)
    ]])

def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статистика", callback_data="stats",  icon_custom_emoji_id=EMOJI_STATS),
            InlineKeyboardButton(text="Купить Px",  callback_data="buy_px", icon_custom_emoji_id=EMOJI_GOLD),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
        ],
    ])


# ─────────────────────────────────────────
#  Текст "В разработке"
# ─────────────────────────────────────────
def dev_text(section: str) -> str:
    return (
        f'<tg-emoji emoji-id="{EMOJI_DEVELOPMENT}">🔧</tg-emoji> <b>{section}</b>\n\n'
        f'<blockquote>'
        f'⚙️  Раздел находится в разработке.\n'
        f'🚀  Скоро будет доступен!'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
MAIN_TEXT = (
    f'<blockquote>'
    f'<tg-emoji emoji-id="{EMOJI_WELCOME}">👋</tg-emoji> <b>Добро пожаловать!</b>\n\n'
    f'🚀 Выберите раздел в меню ниже'
    f'</blockquote>'
)

def build_profile_text(user: dict) -> str:
    days  = days_in_project(user["registered_at"])
    label = days_label(days)
    name  = f"{user['first_name']} {user['last_name']}".strip() or "—"
    uname = f"@{user['username']}" if user["username"] else "—"
    reg   = user["registered_at"].strftime("%d.%m.%Y")

    return (
        f'<tg-emoji emoji-id="{EMOJI_PROFILE}">👤</tg-emoji> <b>Профиль</b>\n\n'
        f'<blockquote>'
        f'🪪  <b>Имя:</b> {name}\n'
        f'📎  <b>Username:</b> {uname}\n'
        f'🆔  <b>ID:</b> <code>{user["id"]}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  <b>Баланс:</b> <code>{user["px"]} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📅  <b>В проекте с:</b> {reg}\n'
        f'🗓  <b>Дней в проекте:</b> <code>{days} {label}</code>'
        f'</blockquote>'
    )

def build_stats_text(user: dict) -> str:
    days  = days_in_project(user["registered_at"])
    label = days_label(days)

    return (
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Статистика</b>\n\n'
        f'<blockquote>'
        f'🆔  <b>ID:</b> <code>{user["id"]}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  <b>Баланс:</b> <code>{user["px"]} Px</code>\n'
        f'🎮  <b>Сыграно игр:</b> <code>{user["games_played"]}</code>\n'
        f'✅  <b>Выиграно всего:</b> <code>{user["total_won"]:,.2f}</code>\n'
        f'❌  <b>Проиграно всего:</b> <code>{user["total_lost"]:,.2f}</code>\n'
        f'🗓  <b>Дней в проекте:</b> <code>{days} {label}</code>'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Разделы в разработке
# ─────────────────────────────────────────
DEV_SECTIONS = {
    "referrals":   "Рефералы",
    "games":       "Игры",
    "leaders":     "Лидеры",
    "exchange":    "Биржа",
    "bonus":       "Бонус",
    "promocodes":  "Промокоды",
    "mine":        "Шахта",
    "about":       "О проекте",
    "instruction": "Инструкция",
}


# ─────────────────────────────────────────
#  Хэндлеры
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_or_create_user(message.from_user)
    await message.answer(MAIN_TEXT, reply_markup=main_menu_keyboard())


@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery):
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_menu_keyboard())
    await call.answer()


@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    user = get_or_create_user(call.from_user)
    await call.message.edit_text(build_profile_text(user), reply_markup=profile_keyboard())
    await call.answer()


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    user = get_or_create_user(call.from_user)
    await call.message.edit_text(build_stats_text(user), reply_markup=back_profile_keyboard())
    await call.answer()


@dp.callback_query(F.data == "buy_px")
async def cb_buy_px(call: CallbackQuery):
    await call.message.edit_text(dev_text("Купить Px"), reply_markup=back_profile_keyboard())
    await call.answer()


@dp.callback_query(F.data.in_(DEV_SECTIONS.keys()))
async def cb_dev_section(call: CallbackQuery):
    await call.message.edit_text(dev_text(DEV_SECTIONS[call.data]), reply_markup=back_main_keyboard())
    await call.answer()


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
