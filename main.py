import asyncio
import os
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
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
    db_try_spend_px,
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
#  Админы
# ─────────────────────────────────────────
ADMIN_IDS: list[int] = [
    8476835256, 8118184388,
]

# ─────────────────────────────────────────
#  Лимиты перевода
# ─────────────────────────────────────────
TRANSFER_MIN      = 1
TRANSFER_MAX      = 100_000_000
TRANSFER_COOLDOWN = 10   # секунд между переводами

# ─────────────────────────────────────────
#  Rate limiting промокодов
#  Не более PROMO_MAX_ATTEMPTS попыток за PROMO_WINDOW секунд
#  После превышения — бан на PROMO_BAN_TIME секунд
# ─────────────────────────────────────────
PROMO_MAX_ATTEMPTS = 5     # попыток
PROMO_WINDOW       = 60    # за 60 секунд
PROMO_BAN_TIME     = 300   # бан 5 минут после превышения

# uid -> список timestamp'ов попыток
_promo_attempts: dict[int, list[float]] = {}
# uid -> timestamp окончания бана
_promo_banned:   dict[int, float]       = {}

# ─────────────────────────────────────────
#  Словари кулдаунов (очищаются фоновой задачей)
# ─────────────────────────────────────────
# uid -> timestamp последнего перевода
_transfer_cooldowns: dict[int, float] = {}

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

# ── Роутеры других модулей подключаются ПЕРВЫМИ ──
dp.include_router(mine_router)
dp.include_router(referral_router)
dp.include_router(bonus_router)
dp.include_router(game_router)
dp.include_router(tower_router)
dp.include_router(mines_router)
dp.include_router(gold_router)

# ── Роутер с низким приоритетом (баланс) — подключится ПОСЛЕДНИМ в конце файла ──
low_priority_router = Router()


# ─────────────────────────────────────────
#  Фоновая задача: очистка устаревших записей
#  Запускается раз в 5 минут
# ─────────────────────────────────────────
async def _cleanup_task():
    while True:
        await asyncio.sleep(300)  # каждые 5 минут
        now = time.monotonic()

        # Очищаем кулдауны переводов старше 60 сек (давно истекли)
        expired_transfers = [
            uid for uid, ts in _transfer_cooldowns.items()
            if now - ts > 60
        ]
        for uid in expired_transfers:
            del _transfer_cooldowns[uid]

        # Очищаем истёкшие баны промокодов
        expired_bans = [
            uid for uid, ts in _promo_banned.items()
            if now > ts
        ]
        for uid in expired_bans:
            del _promo_banned[uid]

        # Очищаем устаревшие окна попыток промокодов
        expired_attempts = [
            uid for uid, attempts in _promo_attempts.items()
            if not attempts or now - attempts[-1] > PROMO_WINDOW
        ]
        for uid in expired_attempts:
            del _promo_attempts[uid]


# ─────────────────────────────────────────
#  Хэлпер: проверка rate limit промокодов
#  Возвращает None если всё ок,
#  или строку с текстом ошибки если заблокирован
# ─────────────────────────────────────────
def _check_promo_rate_limit(uid: int) -> str | None:
    now = time.monotonic()

    # Проверяем бан
    ban_until = _promo_banned.get(uid, 0)
    if now < ban_until:
        wait = int(ban_until - now)
        minutes = wait // 60
        seconds = wait % 60
        if minutes > 0:
            time_str = f"{minutes} мин. {seconds} сек."
        else:
            time_str = f"{seconds} сек."
        return (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Слишком много попыток!</b>\n\n'
            f'<blockquote>Попробуйте через <b>{time_str}</b></blockquote>'
        )

    # Обновляем окно попыток — оставляем только те что в пределах PROMO_WINDOW
    attempts = _promo_attempts.get(uid, [])
    attempts = [ts for ts in attempts if now - ts < PROMO_WINDOW]

    if len(attempts) >= PROMO_MAX_ATTEMPTS:
        # Превышен лимит — баним
        _promo_banned[uid]   = now + PROMO_BAN_TIME
        _promo_attempts[uid] = []
        minutes = PROMO_BAN_TIME // 60
        return (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Слишком много попыток!</b>\n\n'
            f'<blockquote>Вы ввели слишком много неверных промокодов.\n'
            f'Попробуйте через <b>{minutes} мин.</b></blockquote>'
        )

    # Фиксируем попытку
    attempts.append(now)
    _promo_attempts[uid] = attempts
    return None


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
    f'<tg-emoji emoji-id="5197269100878907942">🎟</tg-emoji>Введите промокод в чат регистр не важен!'
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
#  Хэлпер: активация промокода (с rate limit)
# ─────────────────────────────────────────
async def _activate_promo(uid: int, code: str) -> str:
    # Проверяем rate limit ДО обращения к БД
    rate_error = _check_promo_rate_limit(uid)
    if rate_error:
        return rate_error

    result = db_use_promo(uid, code)

    if result["ok"]:
        # Успешная активация — сбрасываем счётчик попыток
        _promo_attempts.pop(uid, None)
        _promo_banned.pop(uid, None)
        reward = result["reward"]
        return (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокод активирован!</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5206607081334906820">🎟</tg-emoji> Промокод <code>{code.upper()}</code> успешно активирован!\n'
            f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  Начислено: <b>{reward:,.2f} Px</b>'
            f'</blockquote>'
        )
    else:
        reason = result["reason"]
        if reason == "not_found":
            detail = "Такой промокод не существует."
        elif reason == "expired":
            detail = "Промокод уже использован максимальное количество раз."
        elif reason == "already_used":
            # Уже активированный — не считаем за попытку брутфорса
            _promo_attempts[uid].pop() if _promo_attempts.get(uid) else None
            detail = "Вы уже активировали этот промокод."
        else:
            detail = "Неизвестная ошибка."

        return (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокоды</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5210952531676504517">🎟</tg-emoji> <b>Не удалось активировать промокод!</b>\n'
            f'{detail}'
            f'</blockquote>'
        )


# ─────────────────────────────────────────
#  Хэндлеры — основные (регистрируются в dp)
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
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
#  Промокоды — кнопка в боте (FSM)
# ─────────────────────────────────────────
@dp.callback_query(F.data == "promocodes")
async def cb_promocodes(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    await state.set_state(PromoStates.waiting_for_code)
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

    try:
        await message.delete()
    except Exception:
        pass

    data         = await state.get_data()
    promo_msg_id = data.get("promo_msg_id")

    if not code:
        return

    text = await _activate_promo(uid, code)
    await state.clear()

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

    sent = await message.answer(text, reply_markup=back_main_keyboard())
    set_owner(sent.message_id, uid)


# ─────────────────────────────────────────
#  Промокоды — команды для чата (/promo, promo, промо)
# ─────────────────────────────────────────
@dp.message(Command("promo"))
async def cmd_promo_slash(message: Message, command: CommandObject):
    uid  = message.from_user.id
    code = (command.args or "").strip()

    if not code:
        await message.reply(
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Укажите промокод!</b>\n\n'
            f'<blockquote>Пример: <code>/promo 8MART</code></blockquote>'
        )
        return

    db_get_or_create_user(message.from_user)
    text = await _activate_promo(uid, code)
    await message.reply(text)


@dp.message(F.text.regexp(r"(?i)^(promo|промо)\s+\S+"))
async def cmd_promo_text(message: Message):
    uid   = message.from_user.id
    parts = message.text.split(maxsplit=1)
    code  = parts[1].strip() if len(parts) > 1 else ""

    if not code:
        return

    db_get_or_create_user(message.from_user)
    text = await _activate_promo(uid, code)
    await message.reply(text)


# ─────────────────────────────────────────
#  Перевод Px — /pay /gift /дать
#  Мин: 1 Px  |  Макс: 100 000 000 Px
#  Кулдаун: 10 секунд между переводами
# ─────────────────────────────────────────
async def _handle_transfer(message: Message, amount_str: str):
    sender = message.from_user
    uid    = sender.id

    if not message.reply_to_message:
        await message.reply(
            f'<tg-emoji emoji-id="5334544901428229844">⚡</tg-emoji> <b>Как сделать перевод?</b>\n\n'
            f'<blockquote>Ответьте на сообщение получателя и напишите:\n'
            f'<code>/gift, /pay, /дать (сумма)</code></blockquote>'
        )
        return

    target_user = message.reply_to_message.from_user

    if target_user.id == uid:
        return
    if target_user.is_bot:
        return

    amount_str = amount_str.strip().replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        return

    if amount < TRANSFER_MIN:
        await message.reply(
            f'<tg-emoji emoji-id="5287231198098117669">⚡</tg-emoji> '
            f'<b>Минимальная сумма перевода — {TRANSFER_MIN:,} Px!</b>'
        )
        return

    if amount > TRANSFER_MAX:
        await message.reply(
            f'<tg-emoji emoji-id="5287231198098117669">⚡</tg-emoji> '
            f'<b>Максимальная сумма перевода — {TRANSFER_MAX:,} Px!</b>'
        )
        return

    # Проверка кулдауна
    now       = time.monotonic()
    last_time = _transfer_cooldowns.get(uid, 0)
    elapsed   = now - last_time

    if elapsed < TRANSFER_COOLDOWN:
        wait = int(TRANSFER_COOLDOWN - elapsed) + 1
        await message.reply(
            f'<tg-emoji emoji-id="5287231198098117669">⚡</tg-emoji> '
            f'<b>Подождите ещё {wait} сек. перед следующим переводом!</b>'
        )
        return

    db_get_or_create_user(sender)
    db_get_or_create_user(target_user)

    success = db_try_spend_px(uid, amount)
    if not success:
        return  # недостаточно средств — тихо игнорируем

    # Обновляем кулдаун только после успешного списания
    _transfer_cooldowns[uid] = now

    db_add_px(target_user.id, amount)

    sender_name = f"<a href='tg://user?id={uid}'>{sender.first_name}</a>"
    target_name = f"<a href='tg://user?id={target_user.id}'>{target_user.first_name}</a>"

    await message.reply(
        f'<tg-emoji emoji-id="5206607081334906820">⚡</tg-emoji> <b>Перевод выполнен!</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5195033767969839232">⚡</tg-emoji>  Отправитель: {sender_name}\n'
        f'<tg-emoji emoji-id="5197288647275071607">⚡</tg-emoji>  Получатель: {target_name}\n'
        f'<tg-emoji emoji-id="5287231198098117669">⚡</tg-emoji>  Сумма: <b>{amount:,.2f} Px</b>'
        f'</blockquote>'
    )


@dp.message(Command("pay"))
async def cmd_pay(message: Message, command: CommandObject):
    await _handle_transfer(message, command.args or "")


@dp.message(Command("gift"))
async def cmd_gift(message: Message, command: CommandObject):
    await _handle_transfer(message, command.args or "")


@dp.message(Command("дать"))
async def cmd_dat(message: Message, command: CommandObject):
    await _handle_transfer(message, command.args or "")


# ─────────────────────────────────────────
#  Промокоды — команда админа /addpromo
# ─────────────────────────────────────────
@dp.message(Command("addpromo"))
async def cmd_addpromo(message: Message):
    uid = message.from_user.id

    if uid not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к этой команде!")
        return

    args = message.text.split(maxsplit=3)[1:]

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
        await message.answer("❌ Сумма должна быть положительным числом!")
        return

    try:
        max_uses = int(uses_raw)
        if max_uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Количество активаций должно быть целым положительным числом!")
        return

    created = db_create_promo(code, reward, max_uses)

    if created:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Промокод создан!</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5271604874419647061">🎟</tg-emoji>  Код: <code>{code}</code>\n'
            f'<tg-emoji emoji-id="5427168083074628963">⚡</tg-emoji>  Награда: <b>{reward:,.2f} Px</b>\n'
            f'<tg-emoji emoji-id="5201691993775818138">🎟</tg-emoji>  Активаций: <b>{max_uses}</b>'
            f'</blockquote>'
        )
    else:
        await message.answer(
            f'❌ Промокод <code>{code}</code> уже существует!\n'
            f'Выберите другой код!'
        )


# ─────────────────────────────────────────
#  Баланс — регистрируется в low_priority_router ПОСЛЕДНИМ
# ─────────────────────────────────────────
_BALANCE_WORDS = {
    "б", "b",
    "bal", "balance",
    "баланс", "бал", "балик",
}


@low_priority_router.message(F.text)
async def cmd_balance_text(message: Message):
    text = (message.text or "").strip()

    if " " in text or "\n" in text:
        return

    if text.lower() not in _BALANCE_WORDS:
        return

    uid = message.from_user.id
    db_get_or_create_user(message.from_user)
    user = db_get_user(uid)

    if not user:
        return

    await message.reply(
        f'<blockquote><tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji><b>Баланс:</b> <code>{user["px"]:,.2f} Px</code></blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  <b>Выиграно всего:</b> <code>{user["total_won"]:,.2f} Px</code>\n'
        f'<tg-emoji emoji-id="5429518319243775957">⚡</tg-emoji>  <b>Проиграно всего:</b> <code>{user["total_lost"]:,.2f} Px</code>'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  low_priority_router подключается ПОСЛЕДНИМ
# ─────────────────────────────────────────
dp.include_router(low_priority_router)


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    init_db()
    inject_to_modules(bot)
    asyncio.create_task(mine_watchdog())
    asyncio.create_task(_cleanup_task())   # фоновая очистка словарей
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
