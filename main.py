import asyncio
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from dotenv import load_dotenv

import mine as _mine_module
import referrals as _referral_module
import bonus as _bonus_module
import game as _game_module
import tower as _tower_module
import mines as _mines_module
import gold as _gold_module

from mine import mine_router, mine_watchdog
from referrals import referral_router
from bonus import bonus_router
from game import game_router, init_game
from tower import tower_router
from mines import mines_router
from gold import gold_router

from database import (
    init_db,
    db_get_or_create_user,
    db_get_user,
    db_get_px,
    db_add_px,
    db_spend_px,
    db_register_referral,
    db_try_reward_referral,
    db_is_already_referred,
    REFERRAL_REWARD_PX,
    db_create_promo,
    db_use_promo,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN не найден! Проверьте:\n"
        "1. Создан ли файл .env в папке проекта\n"
        "2. Есть ли в нем строка BOT_TOKEN=ваш_токен\n"
        "3. Нет ли пробелов или кавычек вокруг токена"
    )

# ─────────────────────────────────────────
#  Админы — впишите сюда свои Telegram ID
# ─────────────────────────────────────────
ADMIN_IDS: list[int] = [
    123456789,  # ← замените на ваш Telegram ID
]

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
EMOJI_BIRJ        = "5402186569006210455"
EMOJI_MINE        = "5197371802136892976"
EMOJI_BONUS       = "5305699699204837855"
EMOJI_CHAT        = "5303138782004924588"
EMOJI_NEWS        = "5201691993775818138"
EMOJI_SUPPORT     = "5907025791006283345"

# ─────────────────────────────────────────
#  Owner guard
# ─────────────────────────────────────────
_MSG_OWNERS_MAX = 10_000
_msg_owners: dict[int, int] = {}


def set_owner(message_id: int, user_id: int):
    if len(_msg_owners) >= _MSG_OWNERS_MAX:
        keys = list(_msg_owners.keys())[:_MSG_OWNERS_MAX // 5]
        for k in keys:
            del _msg_owners[k]
    _msg_owners[message_id] = user_id


def is_owner(message_id: int, user_id: int) -> bool:
    owner = _msg_owners.get(message_id)
    return owner is None or owner == user_id


def inject_to_modules(bot: Bot):
    _mine_module.set_bot_ref(bot)
    _mine_module.set_owner_fn = set_owner
    _mine_module.is_owner_fn  = is_owner
    _mine_module.get_px_fn    = db_get_px
    _mine_module.add_px_fn    = db_add_px
    _mine_module.spend_px_fn  = db_spend_px
    _referral_module.is_owner_fn  = is_owner
    _referral_module.set_owner_fn = set_owner
    _bonus_module.is_owner_fn  = is_owner
    _bonus_module.set_owner_fn = set_owner
    _game_module.is_owner_fn  = is_owner
    _game_module.set_owner_fn = set_owner
    init_game(bot)
    _tower_module.is_owner_fn  = is_owner
    _tower_module.set_owner_fn = set_owner
    _mines_module.is_owner_fn  = is_owner
    _mines_module.set_owner_fn = set_owner
    _gold_module.is_owner_fn  = is_owner
    _gold_module.set_owner_fn = set_owner


# ─────────────────────────────────────────
#  FSM состояния
# ─────────────────────────────────────────
class PromoStates(StatesGroup):
    waiting_for_code = State()


# ─────────────────────────────────────────
#  Bot + Dispatcher
# ─────────────────────────────────────────
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())

dp.include_router(mine_router)
dp.include_router(referral_router)
dp.include_router(bonus_router)
dp.include_router(game_router)
dp.include_router(tower_router)
dp.include_router(mines_router)
dp.include_router(gold_router)


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Профиль",   callback_data="profile",    icon_custom_emoji_id=EMOJI_PROFILE),
            InlineKeyboardButton(text="Рефералы",  callback_data="referrals",  icon_custom_emoji_id=EMOJI_PARTNERS),
        ],
        [
            InlineKeyboardButton(text="Игры",      callback_data="games",      icon_custom_emoji_id=EMOJI_GAMES),
            InlineKeyboardButton(text="Лидеры",    callback_data="leaders",    icon_custom_emoji_id=EMOJI_LEADERS),
            InlineKeyboardButton(text="Бонус",     callback_data="bonus",      icon_custom_emoji_id=EMOJI_BONUS),
        ],
        [
            InlineKeyboardButton(text="Биржа",     callback_data="exchange",   icon_custom_emoji_id=EMOJI_BIRJ),
        ],
        [
            InlineKeyboardButton(text="Промокоды", callback_data="promocodes", icon_custom_emoji_id=EMOJI_PROMO),
            InlineKeyboardButton(text="О проекте", callback_data="about",      icon_custom_emoji_id=EMOJI_ABOUT),
            InlineKeyboardButton(text="Инструкция",url="https://t.me/REPLACE_INSTRUCTION_LINK", icon_custom_emoji_id=EMOJI_INSTRUCT),
        ],
        [
            InlineKeyboardButton(text="Шахта",     callback_data="mine",       icon_custom_emoji_id=EMOJI_MINE),
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


def cancel_promo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="cancel_promo", icon_custom_emoji_id=EMOJI_BACK)
    ]])


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статистика", callback_data="stats",  icon_custom_emoji_id=EMOJI_STATS),
            InlineKeyboardButton(text="Купить Px",  callback_data="buy_px", icon_custom_emoji_id=EMOJI_GOLD),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


def about_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Чат",        url="https://t.me/REPLACE_CHAT_LINK",        icon_custom_emoji_id=EMOJI_CHAT),
            InlineKeyboardButton(text="Новости",    url="https://t.me/REPLACE_NEWS_LINK",        icon_custom_emoji_id=EMOJI_NEWS),
            InlineKeyboardButton(text="Поддержка",  url="https://t.me/REPLACE_SUPPORT_LINK",     icon_custom_emoji_id=EMOJI_SUPPORT),
        ],
        [
            InlineKeyboardButton(text="Инструкция", url="https://t.me/REPLACE_INSTRUCTION_LINK", icon_custom_emoji_id=EMOJI_INSTRUCT),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
MAIN_TEXT = (
    f'<tg-emoji emoji-id="{EMOJI_WELCOME}">👋</tg-emoji> <b>Добро пожаловать в PixelX!</b>\n\n'
    f'<blockquote>'
    f'<tg-emoji emoji-id="5197288647275071607">🛡</tg-emoji> <b>Честные игры — прозрачные правила и реальные шансы на победу.</b> '
    f'Без скрытых условий, всё открыто и по-настоящему честно.'
    f'</blockquote>\n\n'
    f'<blockquote>'
    f'<tg-emoji emoji-id="5262517101578443800">🏆</tg-emoji> <b>Испытай свои навыки в мини-играх, набирай очки, поднимайся в таблице лидеров</b> '
    f'и стань одним из лучших игроков PixelX.'
    f'</blockquote>\n\n'
)

ABOUT_TEXT = (
    f'<tg-emoji emoji-id="{EMOJI_ABOUT}">📋</tg-emoji> <b>О проекте</b>\n\n'
    f'<blockquote>'
    f'<tg-emoji emoji-id="5197288647275071607">🛡</tg-emoji><b>PixelX — честная игровая платформа в Telegram.</b>\n'
    f'<b>Прозрачные правила, реальные шансы на победу, без скрытых условий.</b>'
    f'</blockquote>\n\n'
    f'<blockquote>'
    f'<b><tg-emoji emoji-id="5397916757333654639">🛡</tg-emoji>Присоединяйся к сообществу, следи за новостями и обращайся в поддержку!</b>'
    f'</blockquote>'
)

PROMO_INPUT_TEXT = (
    f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокоды</b>\n\n'
    f'<blockquote>'
    f'Введите промокод в чат и получите награду в Px!\n\n'
    f'🔤 Регистр не важен — код принимается в любом виде.'
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


def days_in_project(registered_at: datetime) -> int:
    return (datetime.now() - registered_at).days


def days_label(n: int) -> str:
    if 11 <= n % 100 <= 19: return "дней"
    r = n % 10
    if r == 1:          return "день"
    if r in (2, 3, 4):  return "дня"
    return "дней"


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
        f'<tg-emoji emoji-id="5274055917766202507">⚡</tg-emoji>  <b>Дней в проекте:</b> <code>{days} {label}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5400362079783770689">⚡</tg-emoji>  <b>Сыграно игр:</b> <code>{user["games_played"]}</code>\n'
        f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  <b>Выиграно всего:</b> <code>{user["total_won"]:,.2f}</code>\n'
        f'<tg-emoji emoji-id="5429518319243775957">⚡</tg-emoji>  <b>Проиграно всего:</b> <code>{user["total_lost"]:,.2f}</code>\n'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Разделы в разработке
# ─────────────────────────────────────────
DEV_SECTIONS = {
    "leaders":  "Лидеры",
    "exchange": "Биржа",
}


# ─────────────────────────────────────────
#  Хэндлеры — основные
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    # Если пользователь был в FSM промокодов — сбрасываем
    await state.clear()

    uid    = message.from_user.id
    is_new = db_get_user(uid) is None

    db_get_or_create_user(message.from_user)

    if is_new and command.args:
        args = command.args.strip()
        if args.startswith("ref_"):
            inviter_part = args[4:]
            if inviter_part.isdigit():
                inviter_id = int(inviter_part)
                if inviter_id != uid and not db_is_already_referred(uid):
                    registered = db_register_referral(invitee_id=uid, inviter_id=inviter_id)
                    if registered:
                        rewarded_inviter = db_try_reward_referral(uid)
                        if rewarded_inviter:
                            try:
                                await bot.send_message(
                                    chat_id=inviter_id,
                                    text=(
                                        f'<tg-emoji emoji-id="5222079954421818267">👥</tg-emoji> '
                                        f'<b>Новый реферал!</b>\n\n'
                                        f'Вам начислено <b>{REFERRAL_REWARD_PX} Px</b>!'
                                    )
                                )
                            except Exception:
                                pass

    sent = await message.answer(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(sent.message_id, uid)


@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    user = db_get_or_create_user(call.from_user)
    await call.message.edit_text(build_profile_text(user), reply_markup=profile_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


@dp.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    user = db_get_or_create_user(call.from_user)
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


@dp.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await call.message.edit_text(ABOUT_TEXT, reply_markup=about_keyboard())
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
#  Промокоды — хэндлеры
# ─────────────────────────────────────────
@dp.callback_query(F.data == "promocodes")
async def cb_promocodes(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    await state.set_state(PromoStates.waiting_for_code)
    # Сохраняем message_id чтобы потом удалить или отредактировать
    await state.update_data(promo_msg_id=call.message.message_id)

    await call.message.edit_text(PROMO_INPUT_TEXT, reply_markup=cancel_promo_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


@dp.callback_query(F.data == "cancel_promo")
async def cb_cancel_promo(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


@dp.message(PromoStates.waiting_for_code)
async def handle_promo_input(message: Message, state: FSMContext):
    uid  = message.from_user.id
    code = message.text.strip() if message.text else ""

    # Удаляем сообщение пользователя сразу
    try:
        await message.delete()
    except Exception:
        pass

    data       = await state.get_data()
    promo_msg_id = data.get("promo_msg_id")

    if not code:
        return

    result = db_use_promo(uid, code)

    if result["ok"]:
        reward = result["reward"]
        text = (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокод активирован!</b>\n\n'
            f'<blockquote>'
            f'✅  Промокод <code>{code.upper()}</code> успешно применён.\n'
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  Начислено: <b>{reward:,.2f} Px</b>'
            f'</blockquote>'
        )
    else:
        reason = result["reason"]
        if reason == "not_found":
            detail = "Такой промокод не существует."
        elif reason == "expired":
            detail = "Промокод уже использован максимальное количество раз."
        elif reason == "already_used":
            detail = "Вы уже активировали этот промокод."
        else:
            detail = "Неизвестная ошибка."

        text = (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокоды</b>\n\n'
            f'<blockquote>'
            f'❌  <b>Не удалось активировать промокод.</b>\n'
            f'{detail}\n\n'
            f'🔤 Попробуйте ввести другой промокод.'
            f'</blockquote>'
        )

    await state.clear()

    # Редактируем исходное меню-сообщение с результатом
    if promo_msg_id:
        try:
            await bot.edit_message_text(
                chat_id=uid,
                message_id=promo_msg_id,
                text=text,
                reply_markup=back_main_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            set_owner(promo_msg_id, uid)
            return
        except Exception:
            pass

    # Fallback: отправить новым сообщением
    sent = await message.answer(text, reply_markup=back_main_keyboard())
    set_owner(sent.message_id, uid)


# ─────────────────────────────────────────
#  Промокоды — команда админа
# ─────────────────────────────────────────
@dp.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    uid = message.from_user.id

    if uid not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к этой команде.")
        return

    # Ожидаем: /addpromo КОД СУММА АКТИВАЦИИ
    args = message.text.split(maxsplit=3)[1:]  # убираем /addpromo

    if len(args) != 3:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Неверный формат.</b>\n\n'
            f'<blockquote>Использование:\n'
            f'<code>/addpromo КОД СУММА АКТИВАЦИИ</code>\n\n'
            f'Пример:\n'
            f'<code>/addpromo SUMMER2025 500 100</code></blockquote>'
        )
        return

    code_raw, amount_raw, uses_raw = args
    code = code_raw.strip().upper()

    try:
        reward = float(amount_raw.replace(",", "."))
        if reward <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Сумма должна быть положительным числом.")
        return

    try:
        max_uses = int(uses_raw)
        if max_uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Количество активаций должно быть целым положительным числом.")
        return

    created = db_create_promo(code, reward, max_uses)

    if created:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокод создан!</b>\n\n'
            f'<blockquote>'
            f'🔑  Код: <code>{code}</code>\n'
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  Награда: <b>{reward:,.2f} Px</b>\n'
            f'🔢  Активаций: <b>{max_uses}</b>'
            f'</blockquote>'
        )
    else:
        await message.answer(
            f'❌ Промокод <code>{code}</code> уже существует.\n'
            f'Выберите другой код.'
        )


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    init_db()
    inject_to_modules(bot)
    asyncio.create_task(mine_watchdog())
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
