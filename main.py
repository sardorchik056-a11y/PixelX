import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = "8586332532:AAHX758cf6iOUpPNpY2sqseGBYsKJo9js4U"

# ─────────────────────────────────────────
#  Custom Emoji IDs (из вашего main.py)
# ─────────────────────────────────────────
EMOJI_PROFILE    = "5906581476639513176"
EMOJI_PARTNERS   = "5906986955911993888"
EMOJI_GAMES      = "5424972470023104089"
EMOJI_LEADERS    = "5440539497383087970"
EMOJI_ABOUT      = "5251203410396458957"
EMOJI_PROMO      = "5444856076954520455"
EMOJI_INSTRUCT   = "5334544901428229844"
EMOJI_BACK       = "5906771962734057347"
EMOJI_WALLET     = "5443127283898405358"
EMOJI_WITHDRAWAL = "5445355530111437729"
EMOJI_BONUS      = "5443127283898405358"
EMOJI_MINES      = "5307996024738395492"
EMOJI_GOLD       = "5278467510604160626"

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ─────────────────────────────────────────
#  Простая in-memory БД
# ─────────────────────────────────────────
USERS_DB: dict[int, dict] = {}

def get_or_create_user(user) -> dict:
    uid = user.id
    if uid not in USERS_DB:
        USERS_DB[uid] = {
            "id": uid,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
            "balance": 0.0,
            "px": 0,
            "registered_at": datetime.now(),
        }
    else:
        USERS_DB[uid]["first_name"] = user.first_name or ""
        USERS_DB[uid]["last_name"]  = user.last_name or ""
        USERS_DB[uid]["username"]   = user.username or ""
    return USERS_DB[uid]

def days_in_project(registered_at: datetime) -> int:
    return (datetime.now() - registered_at).days

def days_label(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "дней"
    r = n % 10
    if r == 1:   return "день"
    if r in (2, 3, 4): return "дня"
    return "дней"


# ─────────────────────────────────────────
#  Клавиатуры — через icon_custom_emoji_id
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
            InlineKeyboardButton(text="Бонус",     callback_data="bonus",      icon_custom_emoji_id=EMOJI_BONUS),
            InlineKeyboardButton(text="Промокоды", callback_data="promocodes", icon_custom_emoji_id=EMOJI_PROMO),
            InlineKeyboardButton(text="Шахта",     callback_data="mine",       icon_custom_emoji_id=EMOJI_MINES),
        ],
        [
            InlineKeyboardButton(text="О проекте", callback_data="about",      icon_custom_emoji_id=EMOJI_ABOUT),
            InlineKeyboardButton(text="Инструкция",callback_data="instruction",icon_custom_emoji_id=EMOJI_INSTRUCT),
        ],
    ])

def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
    ]])

def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Пополнить", callback_data="deposit",  icon_custom_emoji_id=EMOJI_WALLET),
            InlineKeyboardButton(text="Вывести",   callback_data="withdraw", icon_custom_emoji_id=EMOJI_WITHDRAWAL),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
        ],
    ])


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
MAIN_TEXT = (
    f'<blockquote><b>👋 Добро пожаловать!</b></blockquote>\n\n'
    f'<blockquote>🚀 Выберите раздел в меню ниже</blockquote>'
)

def build_profile_text(user: dict) -> str:
    days     = days_in_project(user["registered_at"])
    label    = days_label(days)
    name     = f"{user['first_name']} {user['last_name']}".strip() or "—"
    username = f"@{user['username']}" if user["username"] else "—"
    reg_date = user["registered_at"].strftime("%d.%m.%Y")
    balance  = user["balance"]
    px       = user["px"]

    return (
        f'<tg-emoji emoji-id="{EMOJI_PROFILE}">👤</tg-emoji> <b>Профиль</b>\n\n'
        f'<blockquote>'
        f'🪪  <b>Имя:</b> {name}\n'
        f'📎  <b>Username:</b> {username}\n'
        f'🆔  <b>ID:</b> <code>{user["id"]}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_WALLET}">💰</tg-emoji>  <b>Баланс:</b> <code>{balance:,.2f}</code>\n'
        f'⚡  <b>Px:</b> <code>{px} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📅  <b>В проекте с:</b> {reg_date}\n'
        f'🗓  <b>Дней в проекте:</b> <code>{days} {label}</code>'
        f'</blockquote>'
    )


SECTIONS: dict[str, tuple[str, str]] = {
    "referrals": (
        f'<tg-emoji emoji-id="{EMOJI_PARTNERS}">👥</tg-emoji> <b>Рефералы</b>',
        '<blockquote>'
        '👫  Приглашено: <b>0 чел.</b>\n'
        '💎  Заработано: <b>0 монет</b>'
        '</blockquote>\n\n'
        '<blockquote>'
        '🔗  Ваша ссылка:\n'
        '<code>https://t.me/YourBot?start=ref_123</code>'
        '</blockquote>'
    ),
    "games": (
        f'<tg-emoji emoji-id="{EMOJI_GAMES}">🎮</tg-emoji> <b>Игры</b>',
        '<blockquote>'
        '🎲  Кости — угадай число\n'
        '🃏  Карты — 21 очко\n'
        '🎰  Слоты — сорви джекпот\n'
        f'<tg-emoji emoji-id="{EMOJI_MINES}">💣</tg-emoji>  Мины\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">🥇</tg-emoji>  Золото'
        '</blockquote>'
    ),
    "leaders": (
        f'<tg-emoji emoji-id="{EMOJI_LEADERS}">🏆</tg-emoji> <b>Таблица лидеров</b>',
        '<blockquote>'
        '🥇  Player1 — <b>10 000</b>\n'
        '🥈  Player2 — <b>8 500</b>\n'
        '🥉  Player3 — <b>7 200</b>\n'
        '4️⃣   Player4 — <b>5 100</b>\n'
        '5️⃣   Player5 — <b>3 800</b>'
        '</blockquote>'
    ),
    "exchange": (
        f'<tg-emoji emoji-id="{EMOJI_WALLET}">💹</tg-emoji> <b>Биржа</b>',
        '<blockquote>'
        '📈  Курс: <b>1 USD = 100 монет</b>\n'
        '📊  Объём за 24ч: <b>125 000 монет</b>'
        '</blockquote>'
    ),
    "bonus": (
        f'<tg-emoji emoji-id="{EMOJI_BONUS}">🎁</tg-emoji> <b>Ежедневный бонус</b>',
        '<blockquote>'
        '🎁  Награда: <b>50 монет</b>\n'
        '⏳  Следующий бонус через: <b>24:00:00</b>'
        '</blockquote>'
    ),
    "promocodes": (
        f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокоды</b>',
        '<blockquote>'
        '✏️  Введите промокод в чат:\n'
        '<code>PROMO2026</code>'
        '</blockquote>'
    ),
    "mine": (
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Шахта</b>',
        '<blockquote>'
        '⚡  Мощность: <b>100 ед/ч</b>\n'
        '💰  Накоплено: <b>0 монет</b>\n'
        '🔧  Уровень шахты: <b>1</b>\n'
        '⏱   Следующий сбор: <b>через 12:00:00</b>'
        '</blockquote>'
    ),
    "about": (
        f'<tg-emoji emoji-id="{EMOJI_ABOUT}">📌</tg-emoji> <b>О проекте</b>',
        '<blockquote>'
        '🌐  Сайт: <b>example.com</b>\n'
        '📢  Канал: <b>@YourChannel</b>\n'
        '💬  Поддержка: <b>@YourSupport</b>'
        '</blockquote>'
    ),
    "instruction": (
        f'<tg-emoji emoji-id="{EMOJI_INSTRUCT}">📖</tg-emoji> <b>Инструкция</b>',
        '<blockquote>'
        '1️⃣  Запустите бота через /start\n'
        '2️⃣  Пополните баланс на бирже\n'
        '3️⃣  Участвуйте в играх\n'
        '4️⃣  Добывайте ресурсы в шахте\n'
        '5️⃣  Приглашайте друзей\n'
        '6️⃣  Следите за таблицей лидеров'
        '</blockquote>'
    ),
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


@dp.callback_query(F.data == "deposit")
async def cb_deposit(call: CallbackQuery):
    await call.answer("💳 Раздел пополнения — подключите вашу платёжную систему", show_alert=True)


@dp.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery):
    await call.answer("💸 Раздел вывода — подключите вашу платёжную систему", show_alert=True)


@dp.callback_query(F.data.in_(SECTIONS.keys()))
async def cb_section(call: CallbackQuery):
    title, text = SECTIONS[call.data]
    await call.message.edit_text(f"{title}\n\n{text}", reply_markup=back_keyboard())
    await call.answer()


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
