import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "8545314102:AAGMDpkutDPoqPuIMcjMawQMCKHQgnXWPho"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─────────────────────────────────────────
#  Инлайн-клавиатура главного меню
# ─────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Ряд 1 — 3 кнопки
    builder.row(
        InlineKeyboardButton(text="👤 Профиль",   callback_data="profile"),
        InlineKeyboardButton(text="👥 Рефералы",  callback_data="referrals"),
        InlineKeyboardButton(text="🎮 Игры",      callback_data="games"),
    )
    # Ряд 2 — 2 кнопки
    builder.row(
        InlineKeyboardButton(text="🏆 Лидеры",   callback_data="leaders"),
        InlineKeyboardButton(text="💹 Биржа",     callback_data="exchange"),
    )
    # Ряд 3 — 3 кнопки
    builder.row(
        InlineKeyboardButton(text="🎁 Бонус",     callback_data="bonus"),
        InlineKeyboardButton(text="🎟 Промокоды", callback_data="promocodes"),
        InlineKeyboardButton(text="⛏ Шахта",      callback_data="mine"),
    )
    # Ряд 4 — 2 кнопки
    builder.row(
        InlineKeyboardButton(text="📌 О проекте", callback_data="about"),
        InlineKeyboardButton(text="📖 Инструкция",callback_data="instruction"),
    )

    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data="main_menu")
    return builder.as_markup()


# ─────────────────────────────────────────
#  Тексты разделов
# ─────────────────────────────────────────
SECTIONS = {
    "profile": (
        "👤 <b>Профиль</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🆔 ID: <code>123456789</code>\n"
        "💰 Баланс: <b>0 монет</b>\n"
        "⭐️ Уровень: <b>1</b>\n"
        "📅 Дата регистрации: <b>04.03.2026</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "referrals": (
        "👥 <b>Рефералы</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Приглашайте друзей и получайте бонусы!\n\n"
        "👫 Приглашено: <b>0 чел.</b>\n"
        "💎 Заработано: <b>0 монет</b>\n\n"
        "🔗 Ваша ссылка:\n"
        "<code>https://t.me/YourBot?start=ref_123</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "games": (
        "🎮 <b>Игры</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Выбирайте игру и испытайте удачу!\n\n"
        "🎲 Кости — угадай число\n"
        "🃏 Карты — 21 очко\n"
        "🎰 Слоты — сорви джекпот\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "leaders": (
        "🏆 <b>Таблица лидеров</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🥇 Player1 — <b>10 000 монет</b>\n"
        "🥈 Player2 — <b>8 500 монет</b>\n"
        "🥉 Player3 — <b>7 200 монет</b>\n"
        "4️⃣  Player4 — <b>5 100 монет</b>\n"
        "5️⃣  Player5 — <b>3 800 монет</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "exchange": (
        "💹 <b>Биржа</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Обменивайте валюту и торгуйте активами!\n\n"
        "📈 Курс: <b>1 USD = 100 монет</b>\n"
        "📊 Объём за 24ч: <b>125 000 монет</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "bonus": (
        "🎁 <b>Ежедневный бонус</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Забирайте награду раз в 24 часа!\n\n"
        "🎁 Награда: <b>50 монет</b>\n"
        "⏳ Следующий бонус через: <b>24:00:00</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "promocodes": (
        "🎟 <b>Промокоды</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Введите промокод и получите вознаграждение!\n\n"
        "✏️ Напишите код в чат, например:\n"
        "<code>PROMO2026</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "mine": (
        "⛏ <b>Шахта</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Добывайте ресурсы и прокачивайте шахту!\n\n"
        "⚡ Мощность: <b>100 ед/ч</b>\n"
        "💰 Накоплено: <b>0 монет</b>\n"
        "🔧 Уровень шахты: <b>1</b>\n"
        "⏱ Следующий сбор: <b>через 12:00:00</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "about": (
        "📌 <b>О проекте</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Мы — команда энтузиастов, создавших уникальную игровую платформу.\n\n"
        "🌐 Сайт: <b>example.com</b>\n"
        "📢 Канал: <b>@YourChannel</b>\n"
        "💬 Поддержка: <b>@YourSupport</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "instruction": (
        "📖 <b>Инструкция</b>",
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Запустите бота через /start\n"
        "2️⃣ Пополните баланс на бирже\n"
        "3️⃣ Участвуйте в играх и зарабатывайте\n"
        "4️⃣ Добывайте ресурсы в шахте\n"
        "5️⃣ Приглашайте друзей за рефбонусы\n"
        "6️⃣ Следите за таблицей лидеров\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
}

MAIN_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n\n"
    "🚀 Выберите раздел в меню ниже:"
)


# ─────────────────────────────────────────
#  /start — отправляет единственное сообщение
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        MAIN_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────
#  Главное меню — редактирует текущее сообщение
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
#  Разделы — редактируют то же сообщение
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
