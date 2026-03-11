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
import treyd as _treyd_module
import helper as _helper_module
import duels as _duels_module
import roulette as _roulette_module

from mine import mine_router, mine_watchdog
from referrals import referral_router
from bonus import bonus_router
from game import game_router, game_low_router, init_game
from tower import tower_router
from mines import mines_router
from gold import gold_router
from treyd import exchange_router, exchange_watchdog
from helper import helper_router
from duels import (
    duels_router,
    setup_duels,
    handle_duel_command,
    handle_mygames,
    handle_del,
    is_duel_command,
    is_mygames_command,
    is_del_command,
)
from roulette import roulette_router, handle_roulette_go, is_roulette_go, is_roulette_bet, handle_roulette_bet

from database import (
    init_db,
    db_roulette_save_result,
    db_roulette_get_last,
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
from treyd_db import init_exchange_db

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
    8476835256, 8118184388, 8115654734,
]

# ─────────────────────────────────────────
#  Лимиты перевода
# ─────────────────────────────────────────
TRANSFER_MIN      = 1
TRANSFER_MAX      = 100_000_000
TRANSFER_COOLDOWN = 10   # секунд между переводами

# ─────────────────────────────────────────
#  Rate limiting промокодов
# ─────────────────────────────────────────
PROMO_MAX_ATTEMPTS = 5
PROMO_WINDOW       = 60
PROMO_BAN_TIME     = 300

_promo_attempts: dict[int, list[float]] = {}
_promo_banned:   dict[int, float]       = {}

# ─────────────────────────────────────────
#  Кулдауны переводов
# ─────────────────────────────────────────
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
EMOJI_ACTIV       = "5271604874419647061"
EMOJI_NEET        = "5206607081334906820"

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
    # ── Биржа ──
    _treyd_module.is_owner_fn  = is_owner
    _treyd_module.set_owner_fn = set_owner
    _treyd_module.set_bot_ref(bot)
    _treyd_module.set_admin_ids(ADMIN_IDS)
    # ── Дуэли ──
    class _DuelsStorage:
        def get_balance(self, uid: int) -> float:
            return db_get_px(uid)

        def add_balance(self, uid: int, amount: float):
            if amount >= 0:
                db_add_px(uid, amount)
            else:
                db_spend_px(uid, abs(amount))

    setup_duels(bot, _DuelsStorage())
    _duels_module.set_owner_fn = is_owner
    _duels_module.set_owner_fn = set_owner
    # ── Рулетка ──
    _roulette_module.set_bot_ref(bot)
    _roulette_module.set_db_fns(
        db_get_px,
        db_add_px,
        db_try_spend_px,
        db_get_or_create_user,
    )
    _roulette_module.set_db_log_fns(
        db_roulette_save_result,
        db_roulette_get_last,
    )
    _roulette_module.is_owner_fn  = is_owner
    _roulette_module.set_owner_fn = set_owner


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

# ── Роутеры подключаются в порядке приоритета ──
dp.include_router(mine_router)
dp.include_router(referral_router)
dp.include_router(bonus_router)
dp.include_router(game_router)
dp.include_router(tower_router)
dp.include_router(mines_router)
dp.include_router(gold_router)
dp.include_router(exchange_router)
dp.include_router(duels_router)
dp.include_router(helper_router)
dp.include_router(roulette_router)

low_priority_router = Router()


# ─────────────────────────────────────────
#  Фоновая задача: очистка устаревших записей
# ─────────────────────────────────────────
async def _cleanup_task():
    while True:
        await asyncio.sleep(300)
        now = time.monotonic()

        expired_transfers = [
            uid for uid, ts in _transfer_cooldowns.items()
            if now - ts > 60
        ]
        for uid in expired_transfers:
            del _transfer_cooldowns[uid]

        expired_bans = [
            uid for uid, ts in _promo_banned.items()
            if now > ts
        ]
        for uid in expired_bans:
            del _promo_banned[uid]

        expired_attempts = [
            uid for uid, attempts in _promo_attempts.items()
            if not attempts or now - attempts[-1] > PROMO_WINDOW
        ]
        for uid in expired_attempts:
            del _promo_attempts[uid]


# ─────────────────────────────────────────
#  Rate limit промокодов
# ─────────────────────────────────────────
def _check_promo_rate_limit(uid: int) -> str | None:
    now = time.monotonic()

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

    attempts = _promo_attempts.get(uid, [])
    attempts = [ts for ts in attempts if now - ts < PROMO_WINDOW]

    if len(attempts) >= PROMO_MAX_ATTEMPTS:
        _promo_banned[uid]   = now + PROMO_BAN_TIME
        _promo_attempts[uid] = []
        minutes = PROMO_BAN_TIME // 60
        return (
            f'<tg-emoji emoji-id="{EMOJI_PROMO}">🎟</tg-emoji> <b>Слишком много попыток!</b>\n\n'
            f'<blockquote>Вы ввели слишком много неверных промокодов.\n'
            f'Попробуйте через <b>{minutes} мин.</b></blockquote>'
        )

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
            InlineKeyboardButton(text="Инструкция",url="https://t.me/instruuct1on", icon_custom_emoji_id=EMOJI_INSTRUCT),
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
            InlineKeyboardButton(text="Статистика", callback_data="stats", icon_custom_emoji_id=EMOJI_STATS),
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


def about_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Чат",        url="https://t.me/chatp1x",        icon_custom_emoji_id=EMOJI_CHAT),
            InlineKeyboardButton(text="Новости",    url="https://t.me/pixelxch",        icon_custom_emoji_id=EMOJI_NEWS),
            InlineKeyboardButton(text="Поддержка",  url="https://t.me/Xyloth_1337",     icon_custom_emoji_id=EMOJI_SUPPORT),
        ],
        [
            InlineKeyboardButton(text="Инструкция", url="https://t.me/instruuct1on", icon_custom_emoji_id=EMOJI_INSTRUCT),
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
DEV_SECTIONS: dict = {}


# ─────────────────────────────────────────
#  Топ-10 по балансу
# ─────────────────────────────────────────
EMOJI_LEADERS_PLACE = [
    "5440539497383087970",
    "5447203607294265305",
    "5453902265922376865",
    "5382054253403577563",
    "5391197405553107640",
    "5390966190283694453",
    "5382132232829804982",
    "5391038994274329680",
    "5391234698754138414",
    "5393480373944459905",
]


def db_get_top10_by_balance() -> list[dict]:
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, first_name, last_name, username, px FROM users ORDER BY px DESC LIMIT 10"
        ).fetchall()
    return [dict(r) for r in rows]


def build_leaders_text() -> str:
    top = db_get_top10_by_balance()
    lines = []
    for i, user in enumerate(top):
        emoji_id = EMOJI_LEADERS_PLACE[i]
        name  = f"{user['first_name']} {user.get('last_name') or ''}".strip() or "—"
        uname = f"@{user['username']}" if user.get("username") else name
        px    = user["px"]
        lines.append(
            f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>  '
            f'{uname} — <b>{px:,.2f} Px</b>'
        )
    body = "\n".join(lines) if lines else "Пока нет данных."
    return (
        f'<tg-emoji emoji-id="{EMOJI_LEADERS}">🏆</tg-emoji> <b>Таблица лидеров</b>\n\n'
        f'<blockquote>{body}</blockquote>'
    )


# ─────────────────────────────────────────
#  Активация промокода (с rate limit)
# ─────────────────────────────────────────
async def _activate_promo(uid: int, code: str) -> str:
    rate_error = _check_promo_rate_limit(uid)
    if rate_error:
        return rate_error

    result = db_use_promo(uid, code)

    if result["ok"]:
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
            if _promo_attempts.get(uid):
                _promo_attempts[uid].pop()
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
#  Чеки — хранилище в памяти
# ─────────────────────────────────────────
import secrets

_checks: dict[str, dict] = {}
_BOT_USERNAME: str = ""

_CHECK_IMAGES: dict[float, str] = {
    500.0:  "https://i.postimg.cc/G9Vh9XxC/Chat-GPT-Image-8-mar-2026-g-15-11-13.png",
    1000.0: "https://i.postimg.cc/7bdYbKn4/Chat-GPT-Image-8-mar-2026-g-15-10-51.png",
    1250.0: "https://i.postimg.cc/8sjz12Wx/Chat-GPT-Image-8-mar-2026-g-15-13-51.png",
    1500.0: "https://i.postimg.cc/t7wC7BNH/Chat-GPT-Image-8-mar-2026-g-15-13-03.png",
}


def _get_check_image(amount: float) -> str | None:
    return _CHECK_IMAGES.get(amount)


def _check_keyboard(check_id: str, exhausted: bool = False) -> InlineKeyboardMarkup:
    if exhausted:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Чек исчерпан",
                callback_data="check_exhausted_info",
                icon_custom_emoji_id=EMOJI_NEET,
            )]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Активировать",
            url=f"https://t.me/{_BOT_USERNAME}?start=check_{check_id}",
            icon_custom_emoji_id=EMOJI_ACTIV,
        )]
    ])


def _build_check_caption(check: dict) -> str:
    amt = check['amount']
    return (
        f'<tg-emoji emoji-id="5201691993775818138">💰</tg-emoji> <b>Чек на {amt:,.2f}Px </b>\n\n'
    )


@dp.message(Command("addcheck"))
async def cmd_addcheck(message: Message):
    uid = message.from_user.id

    if uid not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к этой команде!")
        return

    args = (message.text or "").split(maxsplit=2)[1:]

    if len(args) != 2:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji> <b>Неверный формат.</b>\n\n'
            f'<blockquote>Использование:\n'
            f'<code>/addcheck СУММА АКТИВАЦИИ</code>\n\n'
            f'Пример:\n'
            f'<code>/addcheck 1000 50</code></blockquote>'
        )
        return

    amount_raw, uses_raw = args

    try:
        amount = float(amount_raw.replace(',', '.'))
        if amount <= 0:
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

    check_id = secrets.token_urlsafe(12)
    _checks[check_id] = {
        'amount':     amount,
        'max_uses':   max_uses,
        'used_count': 0,
        'used_by':    set(),
        'created_by': uid,
    }

    check     = _checks[check_id]
    caption   = _build_check_caption(check)
    keyboard  = _check_keyboard(check_id)
    image_url = _get_check_image(amount)

    if image_url:
        from aiogram.types import URLInputFile
        photo = URLInputFile(image_url)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=keyboard)
    else:
        await message.answer(caption, reply_markup=keyboard)


async def _process_check_deeplink(message: Message, check_id: str):
    uid   = message.from_user.id
    check = _checks.get(check_id)

    db_get_or_create_user(message.from_user)

    if not check:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_NEET}">💰</tg-emoji> <b>Чек не найден!</b>\n\n'
            f'<blockquote>Возможно, чек устарел или введён неверно.</blockquote>'
        )
        return

    if uid in check['used_by']:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_NEET}">💰</tg-emoji> <b>Вы уже активировали этот чек!</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  Сумма чека: <b>{check["amount"]:,.2f} Px</b>\n'
            f'<tg-emoji emoji-id="5278467510604160626">⚡</tg-emoji>  Ваш баланс: <b>{db_get_px(uid):,.2f} Px</b>'
            f'</blockquote>'
        )
        return

    if check['used_count'] >= check['max_uses']:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_NEET}">💰</tg-emoji> <b>Чек уже исчерпан!</b>\n\n'
            f'<blockquote>Все активации этого чека уже использованы.</blockquote>'
        )
        return

    db_add_px(uid, check['amount'])
    check['used_by'].add(uid)
    check['used_count'] += 1

    new_balance = db_get_px(uid)

    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI_ACTIV}">💰</tg-emoji> <b>Чек активирован!</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5429651785352501917">⚡</tg-emoji>  Начислено: <b>+{check["amount"]:,.2f} Px</b>\n'
        f'<tg-emoji emoji-id="5278467510604160626">⚡</tg-emoji>  Баланс: <b>{new_balance:,.2f} Px</b>'
        f'</blockquote>'
    )


@dp.callback_query(F.data == "check_exhausted_info")
async def cb_check_exhausted_info(call: CallbackQuery):
    await call.answer("Все активации этого чека уже использованы.", show_alert=True)


# ─────────────────────────────────────────
#  /start
# ─────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()

    uid    = message.from_user.id
    is_new = db_get_user(uid) is None

    db_get_or_create_user(message.from_user)

    if command.args:
        args = command.args.strip()

        if args.startswith("check_"):
            check_id = args[6:]
            await _process_check_deeplink(message, check_id)
            return

        if is_new and args.startswith("ref_"):
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
                                    )
                                )
                            except Exception:
                                pass

    sent = await message.answer(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(sent.message_id, uid)


# ─────────────────────────────────────────
#  Главное меню
# ─────────────────────────────────────────
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await call.message.edit_text(MAIN_TEXT, reply_markup=main_menu_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


# ─────────────────────────────────────────
#  Профиль
# ─────────────────────────────────────────
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


# ─────────────────────────────────────────
#  О проекте
# ─────────────────────────────────────────
@dp.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await call.message.edit_text(ABOUT_TEXT, reply_markup=about_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


# ─────────────────────────────────────────
#  Лидеры — топ-10 по балансу
# ─────────────────────────────────────────
@dp.callback_query(F.data == "leaders")
async def cb_leaders(call: CallbackQuery):
    if not is_owner(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    text = build_leaders_text()
    await call.message.edit_text(text, reply_markup=back_main_keyboard())
    set_owner(call.message.message_id, call.from_user.id)
    await call.answer()


# ─────────────────────────────────────────
#  Промокоды — FSM
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
#  Промокоды — команды /promo, promo, промо
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
        return

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
#  /addpromo — команда админа
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
#  /add — команда админа: выдача Px
# ─────────────────────────────────────────
def _db_find_user_by_username(username: str) -> dict | None:
    from database import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (username.lstrip('@'),)
        ).fetchone()
    if not row:
        return None
    from database import _row_to_user
    return _row_to_user(dict(row))


def _db_find_user_by_id(uid: int) -> dict | None:
    return db_get_user(uid)


@dp.message(Command("add"))
async def cmd_add_px(message: Message):
    admin_uid = message.from_user.id

    if admin_uid not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к этой команде!")
        return

    args = (message.text or "").split(maxsplit=2)[1:]

    if len(args) != 2:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> <b>Неверный формат.</b>\n\n'
            f'<blockquote>Использование:\n'
            f'<code>/add @username 1000</code>\n'
            f'<code>/add 123456789 1000</code>\n\n'
            f'Сумма может быть отрицательной для списания.'
            f'</blockquote>'
        )
        return

    target_raw, amount_raw = args

    try:
        amount = float(amount_raw.replace(',', '.'))
    except ValueError:
        await message.answer("❌ Неверная сумма!")
        return

    if amount == 0:
        await message.answer("❌ Сумма не может быть нулём!")
        return

    target_raw = target_raw.strip()
    if target_raw.lstrip('-').isdigit():
        target = _db_find_user_by_id(int(target_raw))
        lookup = f'ID <code>{target_raw}</code>'
    else:
        uname  = target_raw.lstrip('@')
        target = _db_find_user_by_username(uname)
        lookup = f'@{uname}'

    if not target:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> '
            f'<b>Пользователь не найден:</b> {lookup}\n\n'
            f'<blockquote>Убедитесь что пользователь хотя бы раз запускал бота.</blockquote>'
        )
        return

    target_uid = target['id']

    if amount > 0:
        db_add_px(target_uid, amount)
        action = 'начислено'
        sign   = '+'
    else:
        abs_amount = abs(amount)
        success    = db_try_spend_px(target_uid, abs_amount)
        if not success:
            current_px = db_get_px(target_uid)
            await message.answer(
                f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> '
                f'<b>Недостаточно Px для списания!</b>\n\n'
                f'<blockquote>Баланс пользователя: <b>{current_px:,.2f} Px</b>\n'
                f'Запрошено к списанию: <b>{abs_amount:,.2f} Px</b></blockquote>'
            )
            return
        amount = abs_amount
        action = 'списано'
        sign   = '-'

    new_balance  = db_get_px(target_uid)
    display_name = f"@{target['username']}" if target['username'] else target['first_name']

    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> <b>Готово!</b>\n\n'
        f'<blockquote>'
        f'👤  Пользователь: <b>{display_name}</b>\n'
        f'🆔  ID: <code>{target_uid}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  {action.capitalize()}: <b>{sign}{amount:,.2f} Px</b>\n'
        f'💰  Новый баланс: <b>{new_balance:,.2f} Px</b>'
        f'</blockquote>'
    )

    notif_text = (
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji> '
        f'<b>Изменение баланса</b>\n\n'
        f'<blockquote>'
        f'{"📥" if sign == "+" else "📤"}  {action.capitalize()}: <b>{sign}{amount:,.2f} Px</b>\n'
        f'💰  Ваш баланс: <b>{new_balance:,.2f} Px</b>'
        f'</blockquote>'
    )
    try:
        await bot.send_message(target_uid, notif_text)
    except Exception:
        pass


# ─────────────────────────────────────────
#  /reck — рассылка всем пользователям (только админ)
# ─────────────────────────────────────────
def _db_get_all_user_ids() -> list[int]:
    from database import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM users").fetchall()
    return [row[0] for row in rows]


@dp.message(Command("reck"))
async def cmd_reck(message: Message, command: CommandObject):
    admin_uid = message.from_user.id

    if admin_uid not in ADMIN_IDS:
        await message.answer("🚫 У вас нет доступа к этой команде!")
        return

    text = (command.args or "").strip()

    if not text:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_NEWS}">📢</tg-emoji> <b>Неверный формат.</b>\n\n'
            f'<blockquote>Использование:\n'
            f'<code>/reck Текст вашего сообщения</code></blockquote>'
        )
        return

    user_ids = _db_get_all_user_ids()
    total    = len(user_ids)

    status_msg = await message.answer(
        f'<tg-emoji emoji-id="{EMOJI_NEWS}">📢</tg-emoji> <b>Рассылка запущена...</b>\n\n'
        f'<blockquote>Всего пользователей: <b>{total}</b></blockquote>'
    )

    sent_ok   = 0
    sent_fail = 0

    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            sent_ok += 1
        except Exception:
            sent_fail += 1
        await asyncio.sleep(0.05)

    await bot.edit_message_text(
        chat_id=admin_uid,
        message_id=status_msg.message_id,
        text=(
            f'<tg-emoji emoji-id="{EMOJI_NEWS}">📢</tg-emoji> <b>Рассылка завершена!</b>\n\n'
            f'<blockquote>'
            f'✅  Доставлено: <b>{sent_ok}</b>\n'
            f'❌  Не доставлено: <b>{sent_fail}</b>\n'
            f'📊  Всего: <b>{total}</b>'
            f'</blockquote>'
        ),
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────
#  Баланс
# ─────────────────────────────────────────
_BALANCE_WORDS = {
    "б", "b",
    "bal", "balance",
    "баланс", "бал", "балик",
}


async def _send_balance(message: Message):
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


@dp.message(Command("b", "б", "bal", "balance", "баланс", "бал", "балик"))
async def cmd_balance_slash(message: Message):
    await _send_balance(message)


@low_priority_router.message(F.text)
async def cmd_low_priority_text(message: Message):
    text = (message.text or "").strip()

    # ── Рулетка: текстовая ставка (100 7 / 100 к / 100 чет) ──
    if is_roulette_bet(text):
        await handle_roulette_bet(message)
        return

    # ── Рулетка: «го» — запуск игры ──
    if is_roulette_go(text):
        await handle_roulette_go(message)
        # молча игнорируем если нет ставок или пользователь не участник
        return

    # ── Дуэли (текстовые команды) ──
    if is_duel_command(text):
        db_get_or_create_user(message.from_user)
        await handle_duel_command(message)
        return

    if is_mygames_command(text):
        await handle_mygames(message)
        return

    if is_del_command(text):
        await handle_del(message)
        return

    # ── Баланс ──
    if " " in text or "\n" in text:
        return
    if text.lower() not in _BALANCE_WORDS:
        return

    await _send_balance(message)


# ─────────────────────────────────────────
#  low_priority_router подключается ПОСЛЕДНИМ
# ─────────────────────────────────────────
dp.include_router(low_priority_router)
dp.include_router(game_low_router)


# ─────────────────────────────────────────
#  Запуск
# ─────────────────────────────────────────
async def main():
    global _BOT_USERNAME
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Webhook удален")

    bot_info       = await bot.get_me()
    _BOT_USERNAME  = bot_info.username
    print(f"✅ Бот: @{_BOT_USERNAME}")

    init_db()
    init_exchange_db()
    inject_to_modules(bot)
    asyncio.create_task(mine_watchdog())
    asyncio.create_task(exchange_watchdog())
    asyncio.create_task(_cleanup_task())
    print("✅ Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
