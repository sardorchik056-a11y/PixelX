import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = "8545314102:AAGMDpkutDPoqPuIMcjMawQMCKHQgnXWPho"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─────────────────────────────────────────
#  Главное меню
# ─────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    buttons = [
        ("👤 Профиль",     "profile"),
        ("👥 Рефералы",    "referrals"),
        ("🎮 Игры",        "games"),
        ("🏆 Лидеры",      "leaders"),
        ("💹 Биржа",       "exchange"),
        ("🎁 Бонус",       "bonus"),
        ("🎟 Промокоды",   "promocodes"),
        ("📌 О проекте",   "about"),
        ("📖 Инструкция",  "instruction"),
        ("⛏ Шахта",       "mine"),
    ]

    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)

    # Расположение: 2 кнопки в ряд, последняя — по центру
    builder.adjust(2)
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад в меню", callback_data="main_menu")
    return builder.as_markup()


# ─────────────────────────────────────────
#  /start
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\nВыберите раздел:",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────
#  Возврат в главное меню
# ─────────────────────────────────────────
@dp.callback_query(F.data == "main_menu")
async def back_to_menu(call: CallbackQuery):
    await call.message.edit_text(
        "👋 <b>Главное меню</b>\n\nВыберите раздел:",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────
#  Обработчики разделов
# ─────────────────────────────────────────
SECTIONS = {
    "profile": (
        "👤 <b>Профиль</b>",
        "Здесь отображается информация о вашем аккаунте: баланс, статистика и настройки.",
    ),
    "referrals": (
        "👥 <b>Рефералы</b>",
        "Приглашайте друзей и получайте бонусы за каждого реферала!\n\n"
        "Ваша реферальная ссылка: <code>https://t.me/YourBot?start=ref_ID</code>",
    ),
    "games": (
        "🎮 <b>Игры</b>",
        "Выбирайте игру и испытайте удачу!\n\n• 🎲 Кости\n• 🃏 Карты\n• 🎰 Слоты",
    ),
    "leaders": (
        "🏆 <b>Таблица лидеров</b>",
        "Топ игроков по балансу и активности.\n\n🥇 Player1 — 10 000 монет\n🥈 Player2 — 8 500 монет\n🥉 Player3 — 7 200 монет",
    ),
    "exchange": (
        "💹 <b>Биржа</b>",
        "Обменивайте внутреннюю валюту, торгуйте активами и следите за курсами.",
    ),
    "bonus": (
        "🎁 <b>Бонус</b>",
        "Ежедневный бонус доступен раз в 24 часа.\n\nНажмите кнопку ниже, чтобы забрать награду!",
    ),
    "promocodes": (
        "🎟 <b>Промокоды</b>",
        "Введите промокод и получите вознаграждение.\n\n<i>Пример: PROMO2025</i>",
    ),
    "about": (
        "📌 <b>О проекте</b>",
        "Мы — команда энтузиастов, создавших уникальную платформу.\n\n"
        "🌐 Сайт: example.com\n📢 Канал: @YourChannel",
    ),
    "instruction": (
        "📖 <b>Инструкция</b>",
        "1️⃣ Зарегистрируйтесь через /start\n"
        "2️⃣ Пополните баланс на бирже\n"
        "3️⃣ Участвуйте в играх и зарабатывайте\n"
        "4️⃣ Приглашайте друзей за бонусы\n"
        "5️⃣ Следите за таблицей лидеров",
    ),
    "mine": (
        "⛏ <b>Шахта</b>",
        "Добывайте ресурсы и прокачивайте шахту!\n\n"
        "⚡ Мощность: 100 ед/ч\n💰 Баланс шахты: 0 монет\n\n"
        "<i>Следующий сбор через: 12:00:00</i>",
    ),
}


@dp.callback_query(F.data.in_(SECTIONS.keys()))
async def section_handler(call: CallbackQuery):
    section = call.data
    title, text = SECTIONS[section]
    await call.message.edit_text(
        f"{title}\n\n{text}",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
