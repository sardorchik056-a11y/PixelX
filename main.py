import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "8586332532:AAHX758cf6iOUpPNpY2sqseGBYsKJo9js4U"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─────────────────────────────────────────
#  Имитация БД пользователей
#  В реальном боте замените на настоящую БД
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
            "balance": 0,
            "px": 0,
            "registered_at": datetime.now(),
        }
    else:
        # Обновляем актуальные данные из TG
        USERS_DB[uid]["first_name"] = user.first_name or ""
        USERS_DB[uid]["last_name"] = user.last_name or ""
        USERS_DB[uid]["username"] = user.username or ""
    return USERS_DB[uid]

def days_in_project(registered_at: datetime) -> int:
    return (datetime.now() - registered_at).days


# ─────────────────────────────────────────
#  Custom Emoji ID для кнопок
#  Замените на свои ID из вашего набора стикеров
# ─────────────────────────────────────────
# Формат в тексте кнопки: <tg-emoji emoji-id="ID">🔹</tg-emoji>
# (fallback-символ внутри тега — на случай если эмодзи не загрузится)

def ce(emoji_id: str, fallback: str) -> str:
    """Хелпер для custom emoji в тексте кнопки."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

# ── Custom Emoji IDs (замените на свои) ──
CE = {
    "profile":     ("5381990043642502553", "👤"),
    "referrals":   ("5381990043642502553", "👥"),  # замените ID
    "games":       ("5381990043642502553", "🎮"),  # замените ID
    "leaders":     ("5381990043642502553", "🏆"),  # замените ID
    "exchange":    ("5381990043642502553", "💹"),  # замените ID
    "bonus":       ("5381990043642502553", "🎁"),  # замените ID
    "promocodes":  ("5381990043642502553", "🎟"),  # замените ID
    "mine":        ("5381990043642502553", "⛏"),  # замените ID
    "about":       ("5381990043642502553", "📌"),  # замените ID
    "instruction": ("5381990043642502553", "📖"),  # замените ID
    "back":        ("5381990043642502553", "◀️"),  # замените ID
}


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Ряд 1 — 3 кнопки
    builder.row(
        InlineKeyboardButton(text=f"{ce(*CE['profile'])} Профиль",      callback_data="profile"),
        InlineKeyboardButton(text=f"{ce(*CE['referrals'])} Рефералы",   callback_data="referrals"),
        InlineKeyboardButton(text=f"{ce(*CE['games'])} Игры",           callback_data="games"),
    )
    # Ряд 2 — 2 кнопки
    builder.row(
        InlineKeyboardButton(text=f"{ce(*CE['leaders'])} Лидеры",       callback_data="leaders"),
        InlineKeyboardButton(text=f"{ce(*CE['exchange'])} Биржа",       callback_data="exchange"),
    )
    # Ряд 3 — 3 кнопки
    builder.row(
        InlineKeyboardButton(text=f"{ce(*CE['bonus'])} Бонус",          callback_data="bonus"),
        InlineKeyboardButton(text=f"{ce(*CE['promocodes'])} Промокоды", callback_data="promocodes"),
        InlineKeyboardButton(text=f"{ce(*CE['mine'])} Шахта",           callback_data="mine"),
    )
    # Ряд 4 — 2 кнопки
    builder.row(
        InlineKeyboardButton(text=f"{ce(*CE['about'])} О проекте",      callback_data="about"),
        InlineKeyboardButton(text=f"{ce(*CE['instruction'])} Инструкция", callback_data="instruction"),
    )

    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"{ce(*CE['back'])} Назад в меню",
        callback_data="main_menu"
    )
    return builder.as_markup()


# ─────────────────────────────────────────
#  Тексты разделов
# ─────────────────────────────────────────
def build_profile_text(user: dict) -> str:
    days = days_in_project(user["registered_at"])
    full_name = f"{user['first_name']} {user['last_name']}".strip()
    username = f"@{user['username']}" if user["username"] else "—"
    reg_date = user["registered_at"].strftime("%d.%m.%Y")

    return (
        f"👤 <b>Профиль</b>\n\n"
        f"┌─────────────────────\n"
        f"│ 🪪  <b>Имя:</b> {full_name}\n"
        f"│ 📎  <b>Username:</b> {username}\n"
        f"│ 🆔  <b>ID:</b> <code>{user['id']}</code>\n"
        f"├─────────────────────\n"
        f"│ 💰  <b>Баланс:</b> <b>{user['balance']:,} монет</b>\n"
        f"│ ⚡  <b>Px:</b> <b>{user['px']} Px</b>\n"
        f"├─────────────────────\n"
        f"│ 📅  <b>В проекте с:</b> {reg_date}\n"
        f"│ 🗓  <b>Дней в проекте:</b> <b>{days}</b>\n"
        f"└─────────────────────"
    )


SECTIONS: dict[str, tuple[str, str]] = {
    "referrals": (
        "👥 <b>Рефералы</b>",
        "┌─────────────────────\n"
        "│ 👫  Приглашено: <b>0 чел.</b>\n"
        "│ 💎  Заработано: <b>0 монет</b>\n"
        "├─────────────────────\n"
        "│ 🔗  Ваша ссылка:\n"
        "│ <code>https://t.me/YourBot?start=ref_123</code>\n"
        "└─────────────────────"
    ),
    "games": (
        "🎮 <b>Игры</b>",
        "┌─────────────────────\n"
        "│ 🎲  Кости — угадай число\n"
        "│ 🃏  Карты — 21 очко\n"
        "│ 🎰  Слоты — сорви джекпот\n"
        "└─────────────────────"
    ),
    "leaders": (
        "🏆 <b>Таблица лидеров</b>",
        "┌─────────────────────\n"
        "│ 🥇  Player1 — <b>10 000 монет</b>\n"
        "│ 🥈  Player2 — <b>8 500 монет</b>\n"
        "│ 🥉  Player3 — <b>7 200 монет</b>\n"
        "│ 4️⃣   Player4 — <b>5 100 монет</b>\n"
        "│ 5️⃣   Player5 — <b>3 800 монет</b>\n"
        "└─────────────────────"
    ),
    "exchange": (
        "💹 <b>Биржа</b>",
        "┌─────────────────────\n"
        "│ 📈  Курс: <b>1 USD = 100 монет</b>\n"
        "│ 📊  Объём за 24ч: <b>125 000 монет</b>\n"
        "└─────────────────────"
    ),
    "bonus": (
        "🎁 <b>Ежедневный бонус</b>",
        "┌─────────────────────\n"
        "│ 🎁  Награда: <b>50 монет</b>\n"
        "│ ⏳  Следующий бонус через: <b>24:00:00</b>\n"
        "└─────────────────────"
    ),
    "promocodes": (
        "🎟 <b>Промокоды</b>",
        "┌─────────────────────\n"
        "│ ✏️  Введите промокод в чат:\n"
        "│ <code>PROMO2026</code>\n"
        "└─────────────────────"
    ),
    "mine": (
        "⛏ <b>Шахта</b>",
        "┌─────────────────────\n"
        "│ ⚡  Мощность: <b>100 ед/ч</b>\n"
        "│ 💰  Накоплено: <b>0 монет</b>\n"
        "│ 🔧  Уровень шахты: <b>1</b>\n"
        "│ ⏱   Следующий сбор: <b>через 12:00:00</b>\n"
        "└─────────────────────"
    ),
    "about": (
        "📌 <b>О проекте</b>",
        "┌─────────────────────\n"
        "│ 🌐  Сайт: <b>example.com</b>\n"
        "│ 📢  Канал: <b>@YourChannel</b>\n"
        "│ 💬  Поддержка: <b>@YourSupport</b>\n"
        "└─────────────────────"
    ),
    "instruction": (
        "📖 <b>Инструкция</b>",
        "┌─────────────────────\n"
        "│ 1️⃣  Запустите бота через /start\n"
        "│ 2️⃣  Пополните баланс на бирже\n"
        "│ 3️⃣  Участвуйте в играх\n"
        "│ 4️⃣  Добывайте ресурсы в шахте\n"
        "│ 5️⃣  Приглашайте друзей\n"
        "│ 6️⃣  Следите за таблицей лидеров\n"
        "└─────────────────────"
    ),
}

MAIN_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "🚀 Выберите раздел в меню ниже:"
)


# ─────────────────────────────────────────
#  /start
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_or_create_user(message.from_user)
    await message.answer(
        MAIN_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────
#  Назад в главное меню
# ─────────────────────────────────────────
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery):
    await call.message.edit_text(
        MAIN_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )
    await call.answer()


# ─────────────────────────────────────────
#  Профиль — динамический
# ─────────────────────────────────────────
@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    user = get_or_create_user(call.from_user)
    text = build_profile_text(user)
    await call.message.edit_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )
    await call.answer()


# ─────────────────────────────────────────
#  Остальные разделы
# ─────────────────────────────────────────
@dp.callback_query(F.data.in_(SECTIONS.keys()))
async def cb_section(call: CallbackQuery):
    title, text = SECTIONS[call.data]
    await call.message.edit_text(
        f"{title}\n\n{text}",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )
    await call.answer()


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
