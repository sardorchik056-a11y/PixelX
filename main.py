import asyncio
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from dotenv import load_dotenv  # Добавьте этот импорт

import mine as _mine_module
from mine import mine_router, mine_watchdog

# Загружаем переменные из .env файла
load_dotenv()

# Получаем токен из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Проверяем, загрузился ли токен
if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN не найден! Проверьте:\n"
        "1. Создан ли файл .env в папке проекта\n"
        "2. Есть ли в нем строка BOT_TOKEN=ваш_токен\n"
        "3. Нет ли пробелов или кавычек вокруг токена"
    )

# Для отладки (можно удалить после проверки)
print(f"Токен загружен: {'Да' if BOT_TOKEN else 'Нет'}")
print(f"Длина токена: {len(BOT_TOKEN) if BOT_TOKEN else 0}")

# ─────────────────────────────────────────
#  Emoji IDs
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
EMOJI_STATS       = "5231200819986047254"
EMOJI_DEVELOPMENT = "5445355530111437729"
EMOJI_WELCOME     = "5199885118214255386"

# ─────────────────────────────────────────
#  БД пользователей
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

def get_px(uid: int) -> float:
    return USERS_DB.get(uid, {}).get("px", 0)

def add_px(uid: int, amount: float):
    if uid in USERS_DB:
        USERS_DB[uid]["px"] = round(USERS_DB[uid].get("px", 0) + amount, 2)

def spend_px(uid: int, amount: float):
    if uid in USERS_DB:
        USERS_DB[uid]["px"] = max(0, round(USERS_DB[uid].get("px", 0) - amount, 2))

def days_in_project(registered_at: datetime) -> int:
    return (datetime.now() - registered_at).days

def days_label(n: int) -> str:
    if 11 <= n % 100 <= 19: return "дней"
    r = n % 10
    if r == 1:         return "день"
    if r in (2, 3, 4): return "дня"
    return "дней"

# ─────────────────────────────────────────
#  Owner guard
# ─────────────────────────────────────────
_MSG_OWNERS_MAX = 10_000  # FIX: ограничение размера словаря, чтобы не росло вечно
_msg_owners: dict[int, int] = {}

def set_owner(message_id: int, user_id: int):
    # FIX: если словарь слишком большой — чистим старые записи
    if len(_msg_owners) >= _MSG_OWNERS_MAX:
        # удаляем первые 20% записей (самые старые по порядку вставки)
        keys_to_delete = list(_msg_owners.keys())[:_MSG_OWNERS_MAX // 5]
        for k in keys_to_delete:
            del _msg_owners[k]
    _msg_owners[message_id] = user_id

def is_owner(message_id: int, user_id: int) -> bool:
    owner = _msg_owners.get(message_id)
    return owner is None or owner == user_id

def inject_to_modules(bot: Bot):
    # FIX: передаём bot ref в mine для watchdog'а
    _mine_module.set_bot_ref(bot)
    _mine_module.set_owner_fn = set_owner
    _mine_module.is_owner_fn  = is_owner
    _mine_module.get_px_fn    = get_px
    _mine_module.add_px_fn    = add_px
    _mine_module.spend_px_fn  = spend_px


# ─────────────────────────────────────────
#  Bot + Dispatcher
# ─────────────────────────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

dp.include_router(mine_router)


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
#  Тексты
# ─────────────────────────────────────────
MAIN_TEXT = (
    f'<blockquote>'
    f'<tg-emoji emoji-id="{EMOJI_WELCOME}">👋</tg-emoji> <b>Добро пожаловать!</b>\n\n'
    f'🚀 Выберите раздел в меню ниже'
    f'</blockquote>'
)

def dev_text(section: str) -> str:
    return (
        f'<tg-emoji emoji-id="{EMOJI_DEVELOPMENT}">🔧</tg-emoji> <b>{section}</b>\n\n'
        f'<blockquote>'
        f'⚙️  Раздел находится в разработке.\n'
        f'🚀  Скоро будет доступен!'
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
        f'<tg-emoji emoji-id="5201691993775818138">⚡</tg-emoji>  <b>Имя:</b> {name}\n'
        f'<tg-emoji emoji-id="5445353829304387411">⚡</tg-emoji>  <b>Username:</b> {uname}\n'
        f'🆔  <b>ID:</b> <code>{user["id"]}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  <b>Баланс:</b> <code>{user["px"]} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5906909964328245730">⚡</tg-emoji>  <b>В проекте с:</b> {reg}\n'
        f'<tg-emoji emoji-id="5274055917766202507">⚡</tg-emoji>  <b>Дней в проекте:</b> <code>{days} {label}</code>'
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
        f'<tg-emoji emoji-id="5400362079783770689">⚡</tg-emoji>  <b>Сыграно игр:</b> <code>{user["games_played"]}</code>\n'
        f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  <b>Выиграно всего:</b> <code>{user["total_won"]:,.2f}</code>\n'
        f'<tg-emoji emoji-id="5429518319243775957">⚡</tg-emoji>  <b>Проиграно всего:</b> <code>{user["total_lost"]:,.2f}</code>\n'
        f'<tg-emoji emoji-id="5274055917766202507">⚡</tg-emoji>  <b>Дней в проекте:</b> <code>{days} {label}</code>'
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
    "about":       "О проекте",
    "instruction": "Инструкция",
}

# ─────────────────────────────────────────
#  Хэндлеры
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_or_create_user(message.from_user)
    sent = await message.answer(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(sent.message_id, message.from_user.id)

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    user = get_or_create_user(call.from_user)
    await call.message.edit_text(build_profile_text(user), reply_markup=profile_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    user = get_or_create_user(call.from_user)
    await call.message.edit_text(build_stats_text(user), reply_markup=back_profile_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data == "buy_px")
async def cb_buy_px(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await call.message.edit_text(dev_text("Купить Px"), reply_markup=back_profile_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data.in_(DEV_SECTIONS.keys()))
async def cb_dev_section(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await call.message.edit_text(dev_text(DEV_SECTIONS[call.data]), reply_markup=back_main_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()

# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    inject_to_modules(bot)
    asyncio.create_task(mine_watchdog())
    print("✅ Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
