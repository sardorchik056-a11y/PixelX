import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ─────────────────────────────────────────
#  Owner guard (инжектируется из main.py)
# ─────────────────────────────────────────
set_owner_fn = None
is_owner_fn  = None

mine_router = Router()

# ─────────────────────────────────────────
#  Custom Emoji IDs (из вашего проекта)
# ─────────────────────────────────────────
EMOJI_MINES       = "5307996024738395492"
EMOJI_GOLD        = "5278467510604160626"
EMOJI_BACK        = "5906771962734057347"
EMOJI_DEVELOPMENT = "5445355530111437729"
EMOJI_WALLET      = "5443127283898405358"
EMOJI_STATS       = "5231200819986047254"

# ─────────────────────────────────────────
#  NOX → PX курс
# ─────────────────────────────────────────
NOX_TO_PX = 15  # 1 Nox = 15 Px

# ─────────────────────────────────────────
#  15 видов кирок
#  (name, price_px, interval_min, nox_min, nox_max, max_hours)
# ─────────────────────────────────────────
PICKAXES = [
    # tier 1 — деревянные
    {"id": 1,  "name": "Деревянная",       "emoji": "🪓", "price": 0,     "interval": 3,   "nox_min": 2,   "nox_max": 7,    "hours": 3},
    {"id": 2,  "name": "Каменная",         "emoji": "⛏",  "price": 150,   "interval": 3,   "nox_min": 3,   "nox_max": 9,    "hours": 4},
    {"id": 3,  "name": "Заточенная",       "emoji": "🔪", "price": 350,   "interval": 3,   "nox_min": 4,   "nox_max": 12,   "hours": 5},
    # tier 2 — железные
    {"id": 4,  "name": "Железная",         "emoji": "⚒️", "price": 700,   "interval": 5,   "nox_min": 7,   "nox_max": 18,   "hours": 6},
    {"id": 5,  "name": "Закалённая",       "emoji": "🛠",  "price": 1200,  "interval": 5,   "nox_min": 9,   "nox_max": 22,   "hours": 8},
    {"id": 6,  "name": "Острая стальная",  "emoji": "⚔️", "price": 2000,  "interval": 5,   "nox_min": 12,  "nox_max": 25,   "hours": 10},
    # tier 3 — золотые
    {"id": 7,  "name": "Золотая",          "emoji": "🥇", "price": 3500,  "interval": 8,   "nox_min": 18,  "nox_max": 35,   "hours": 12},
    {"id": 8,  "name": "Позолоченная",     "emoji": "✨", "price": 5500,  "interval": 8,   "nox_min": 22,  "nox_max": 42,   "hours": 16},
    {"id": 9,  "name": "Королевская",      "emoji": "👑", "price": 8000,  "interval": 8,   "nox_min": 28,  "nox_max": 50,   "hours": 20},
    # tier 4 — кристальные
    {"id": 10, "name": "Кристальная",      "emoji": "💎", "price": 12000, "interval": 12,  "nox_min": 40,  "nox_max": 70,   "hours": 24},
    {"id": 11, "name": "Лазуритовая",      "emoji": "🔷", "price": 18000, "interval": 12,  "nox_min": 50,  "nox_max": 85,   "hours": 30},
    {"id": 12, "name": "Рубиновая",        "emoji": "❤️‍🔥","price": 26000, "interval": 15,  "nox_min": 65,  "nox_max": 110,  "hours": 36},
    # tier 5 — легендарные
    {"id": 13, "name": "Драконья",         "emoji": "🐉", "price": 40000, "interval": 18,  "nox_min": 90,  "nox_max": 150,  "hours": 48},
    {"id": 14, "name": "Теневая",          "emoji": "🌑", "price": 60000, "interval": 20,  "nox_min": 120, "nox_max": 200,  "hours": 60},
    {"id": 15, "name": "Вечная",           "emoji": "♾️", "price": 99999, "interval": 25,  "nox_min": 170, "nox_max": 280,  "hours": 72},
]

PICKAXE_BY_ID = {p["id"]: p for p in PICKAXES}

# ─────────────────────────────────────────
#  In-memory хранилище шахты
#  В реальном боте → заменить на БД
# ─────────────────────────────────────────
# mine_data[user_id] = {
#   "nox": float,           — баланс Nox
#   "pickaxe_id": int,      — текущая кирка (None если нет)
#   "owned": set[int],      — купленные кирки
#   "mining_end": datetime, — когда закончится копание (None если не копает)
#   "last_collect": datetime
# }
mine_data: dict[int, dict] = {}

def get_mine_user(user_id: int) -> dict:
    if user_id not in mine_data:
        mine_data[user_id] = {
            "nox":          0.0,
            "pickaxe_id":   1,       # деревянная — бесплатная, есть у всех
            "owned":        {1},
            "mining_end":   None,
            "pending_nox":  0.0,     # накоплено, ждёт окончания
        }
    return mine_data[user_id]


# ─────────────────────────────────────────
#  Бизнес-логика
# ─────────────────────────────────────────
def compute_pending(data: dict, pickaxe: dict) -> float:
    """Считает сколько Nox накоплено за время копания (ещё не начислено)."""
    if data["mining_end"] is None:
        return 0.0
    now = datetime.now()
    end = data["mining_end"]
    # Определяем сколько интервалов прошло
    start = end - timedelta(hours=pickaxe["hours"])
    elapsed = min(now, end) - start
    intervals = int(elapsed.total_seconds() / 60 / pickaxe["interval"])
    total = sum(random.uniform(pickaxe["nox_min"], pickaxe["nox_max"]) for _ in range(intervals))
    return round(total, 2)

def is_mining_done(data: dict) -> bool:
    if data["mining_end"] is None:
        return False
    return datetime.now() >= data["mining_end"]

def time_left_str(end: datetime) -> str:
    delta = end - datetime.now()
    if delta.total_seconds() <= 0:
        return "завершено"
    h = int(delta.total_seconds() // 3600)
    m = int((delta.total_seconds() % 3600) // 60)
    s = int(delta.total_seconds() % 60)
    if h > 0:
        return f"{h}ч {m}м"
    if m > 0:
        return f"{m}м {s}с"
    return f"{s}с"

def auto_collect_if_done(data: dict, pickaxe: dict):
    """Если время вышло — автоматически начисляет Nox в баланс."""
    if data["mining_end"] and datetime.now() >= data["mining_end"]:
        earned = data.get("pending_nox", 0.0)
        if earned == 0.0:
            # Считаем полный цикл
            intervals = int(pickaxe["hours"] * 60 / pickaxe["interval"])
            earned = round(sum(
                random.uniform(pickaxe["nox_min"], pickaxe["nox_max"])
                for _ in range(intervals)
            ), 2)
        data["nox"] += earned
        data["pending_nox"] = 0.0
        data["mining_end"]  = None
        return earned
    return 0.0


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def mine_main_keyboard(data: dict) -> InlineKeyboardMarkup:
    rows = []
    # Кнопки действий
    action_row = []
    if data["mining_end"] is None:
        action_row.append(InlineKeyboardButton(text="⛏ Начать копание", callback_data="mine_start_pick"))
    else:
        action_row.append(InlineKeyboardButton(text="⏳ Копание...", callback_data="mine_status"))
    action_row.append(InlineKeyboardButton(text="💰 Продать Nox", callback_data="mine_sell"))
    rows.append(action_row)

    rows.append([
        InlineKeyboardButton(text="🪓 Магазин кирок", callback_data="mine_shop_0"),
        InlineKeyboardButton(text="🎒 Мои кирки",     callback_data="mine_owned"),
    ])
    rows.append([
        InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def shop_keyboard(page: int, owned: set) -> InlineKeyboardMarkup:
    per_page = 5
    start    = page * per_page
    items    = PICKAXES[start:start + per_page]
    rows     = []
    for p in items:
        label = f"{'✅' if p['id'] in owned else '🔒'} {p['emoji']} {p['name']} — {p['price']:,} Px"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mine_buy_{p['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"mine_shop_{page-1}"))
    if start + per_page < len(PICKAXES):
        nav.append(InlineKeyboardButton(text="Далее ▶", callback_data=f"mine_shop_{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Назад", callback_data="mine", icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def pick_select_keyboard(owned: set) -> InlineKeyboardMarkup:
    rows = []
    for pid in sorted(owned):
        p = PICKAXE_BY_ID[pid]
        rows.append([InlineKeyboardButton(
            text=f"{p['emoji']} {p['name']} (каждые {p['interval']} мин | +{p['nox_min']}-{p['nox_max']} Nox)",
            callback_data=f"mine_equip_{pid}"
        )])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="mine", icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_mine_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="mine", icon_custom_emoji_id=EMOJI_BACK)
    ]])


# ─────────────────────────────────────────
#  Тексты
# ─────────────────────────────────────────
def mine_main_text(user_id: int) -> str:
    data    = get_mine_user(user_id)
    pid     = data["pickaxe_id"]
    pickaxe = PICKAXE_BY_ID[pid]

    # Авто-начисление если готово
    auto_collect_if_done(data, pickaxe)

    nox_px  = round(data["nox"] * NOX_TO_PX, 2)
    status  = ""
    if data["mining_end"]:
        tl = time_left_str(data["mining_end"])
        status = (
            f'\n<blockquote>'
            f'⏳  <b>Копание идёт</b>\n'
            f'🕐  <b>До сбора:</b> <code>{tl}</code>\n'
            f'🔒  <i>Собрать нельзя — придёт автоматически</i>'
            f'</blockquote>'
        )
    else:
        status = (
            f'\n<blockquote>'
            f'💤  <b>Шахта простаивает</b>\n'
            f'⛏  <i>Нажмите "Начать копание"</i>'
            f'</blockquote>'
        )

    return (
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Шахта</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💎</tg-emoji>  <b>Баланс Nox:</b> <code>{data["nox"]:.2f} Nox</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_WALLET}">💰</tg-emoji>  <b>≈ в Px:</b> <code>{nox_px:.2f} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'{pickaxe["emoji"]}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Интервал:</b> каждые <code>{pickaxe["interval"]} мин</code>\n'
        f'📦  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code>\n'
        f'⏱  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>'
        f'</blockquote>'
        f'{status}'
    )

def shop_page_text(page: int) -> str:
    per_page = 5
    start    = page * per_page
    items    = PICKAXES[start:start + per_page]
    tiers    = {1: "🪵 Деревянный тир", 2: "⚙️ Железный тир",
                3: "🥇 Золотой тир",   4: "💎 Кристальный тир", 5: "🐉 Легендарный тир"}
    tier_num = page + 1
    tier_name = tiers.get(tier_num, "")

    lines = ""
    for p in items:
        lines += (
            f'\n{p["emoji"]} <b>{p["name"]}</b>\n'
            f'   💵 Цена: <code>{p["price"]:,} Px</code> | '
            f'⚡ каждые <code>{p["interval"]} мин</code>\n'
            f'   📦 <code>{p["nox_min"]}–{p["nox_max"]} Nox</code> | '
            f'⏱ цикл <code>{p["hours"]} ч</code>\n'
        )

    return (
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Магазин кирок</b> — {tier_name}\n\n'
        f'<blockquote>'
        f'1 Nox = <b>{NOX_TO_PX} Px</b>\n'
        f'Кирки добывают Nox автоматически.\n'
        f'Собрать нельзя — зачисляется по окончании цикла.'
        f'</blockquote>\n'
        f'{lines}'
    )


# ─────────────────────────────────────────
#  Хэндлеры
# ─────────────────────────────────────────

# Главный экран шахты
async def show_mine_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    data    = get_mine_user(user_id)
    pid     = data["pickaxe_id"]
    auto_collect_if_done(data, PICKAXE_BY_ID[pid])

    await callback.message.edit_text(
        mine_main_text(user_id),
        reply_markup=mine_main_keyboard(data)
    )
    if set_owner_fn:
        set_owner_fn(callback.message.message_id, user_id)
    await callback.answer()


@mine_router.callback_query(F.data == "mine")
async def cb_mine(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await show_mine_menu(callback)


# Статус копания
@mine_router.callback_query(F.data == "mine_status")
async def cb_mine_status(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data = get_mine_user(callback.from_user.id)
    if data["mining_end"]:
        tl = time_left_str(data["mining_end"])
        await callback.answer(f"⏳ Копание завершится через {tl}", show_alert=True)
    else:
        await callback.answer("✅ Копание завершено, Nox зачислен!", show_alert=True)


# Выбор кирки перед стартом
@mine_router.callback_query(F.data == "mine_start_pick")
async def cb_mine_start_pick(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data = get_mine_user(callback.from_user.id)
    if data["mining_end"]:
        await callback.answer("⛏ Копание уже идёт!", show_alert=True); return

    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Выберите кирку для копания</b>\n\n'
        f'<blockquote>Копание нельзя прервать.\nNox зачислится автоматически по окончании цикла.</blockquote>',
        reply_markup=pick_select_keyboard(data["owned"])
    )
    await callback.answer()


# Экипировать кирку и начать копание
@mine_router.callback_query(F.data.startswith("mine_equip_"))
async def cb_mine_equip(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    pid  = int(callback.data.split("_")[2])
    data = get_mine_user(callback.from_user.id)

    if pid not in data["owned"]:
        await callback.answer("❌ У вас нет этой кирки!", show_alert=True); return
    if data["mining_end"]:
        await callback.answer("⛏ Копание уже идёт!", show_alert=True); return

    pickaxe           = PICKAXE_BY_ID[pid]
    data["pickaxe_id"] = pid
    data["mining_end"] = datetime.now() + timedelta(hours=pickaxe["hours"])
    data["pending_nox"] = 0.0

    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Копание началось!</b>\n\n'
        f'<blockquote>'
        f'{pickaxe["emoji"]}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Интервал:</b> каждые <code>{pickaxe["interval"]} мин</code>\n'
        f'📦  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code>\n'
        f'⏱  <b>Завершится через:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'🔒  <b>Прервать нельзя — Nox придёт автоматически</b>'
        f'</blockquote>',
        reply_markup=back_mine_keyboard()
    )
    if set_owner_fn:
        set_owner_fn(callback.message.message_id, callback.from_user.id)
    await callback.answer("⛏ Копание запущено!")


# Мои кирки
@mine_router.callback_query(F.data == "mine_owned")
async def cb_mine_owned(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data    = get_mine_user(callback.from_user.id)
    current = PICKAXE_BY_ID[data["pickaxe_id"]]
    lines   = ""
    for pid in sorted(data["owned"]):
        p      = PICKAXE_BY_ID[pid]
        active = " 🟢 <b>Активна</b>" if pid == data["pickaxe_id"] else ""
        lines += f'\n{p["emoji"]} <b>{p["name"]}</b>{active}\n'
        lines += f'   ⚡ каждые <code>{p["interval"]} мин</code> | 📦 <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>\n'

    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Мои кирки</b>\n\n'
        f'<blockquote>Текущая: {current["emoji"]} {current["name"]}</blockquote>\n'
        f'{lines}',
        reply_markup=back_mine_keyboard()
    )
    await callback.answer()


# Магазин — страница
@mine_router.callback_query(F.data.startswith("mine_shop_"))
async def cb_mine_shop(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    page = int(callback.data.split("_")[2])
    data = get_mine_user(callback.from_user.id)
    await callback.message.edit_text(
        shop_page_text(page),
        reply_markup=shop_keyboard(page, data["owned"])
    )
    await callback.answer()


# Купить кирку
@mine_router.callback_query(F.data.startswith("mine_buy_"))
async def cb_mine_buy(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    pid      = int(callback.data.split("_")[2])
    pickaxe  = PICKAXE_BY_ID[pid]
    user_id  = callback.from_user.id
    data     = get_mine_user(user_id)

    if pid in data["owned"]:
        await callback.answer("✅ У вас уже есть эта кирка!", show_alert=True); return

    # Импортируем хранилище Px из main
    try:
        from bot_menu import USERS_DB
        user_px = USERS_DB.get(user_id, {}).get("px", 0)
        if user_px < pickaxe["price"]:
            await callback.answer(
                f'❌ Недостаточно Px!\nНужно: {pickaxe["price"]:,} Px\nУ вас: {user_px:,} Px',
                show_alert=True
            ); return
        USERS_DB[user_id]["px"] -= pickaxe["price"]
    except ImportError:
        pass  # В продакшене заменить на реальную БД

    data["owned"].add(pid)
    await callback.answer(f'✅ Куплено: {pickaxe["emoji"]} {pickaxe["name"]}!', show_alert=True)

    # Обновляем страницу магазина
    page = (pid - 1) // 5
    await callback.message.edit_text(
        shop_page_text(page),
        reply_markup=shop_keyboard(page, data["owned"])
    )


# Продать весь Nox
@mine_router.callback_query(F.data == "mine_sell")
async def cb_mine_sell(callback: CallbackQuery):
    if is_owner_fn and not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    user_id = callback.from_user.id
    data    = get_mine_user(user_id)

    if data["nox"] <= 0:
        await callback.answer("❌ Нет Nox для продажи!", show_alert=True); return

    nox_amount = data["nox"]
    px_earned  = round(nox_amount * NOX_TO_PX, 2)

    # Начисляем Px
    try:
        from bot_menu import USERS_DB
        if user_id not in USERS_DB:
            USERS_DB[user_id] = {"px": 0}
        USERS_DB[user_id]["px"] = USERS_DB[user_id].get("px", 0) + px_earned
    except ImportError:
        pass  # В продакшене заменить на реальную БД

    data["nox"] = 0.0

    await callback.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji> <b>Продажа Nox</b>\n\n'
        f'<blockquote>'
        f'⛏  <b>Продано:</b> <code>{nox_amount:.2f} Nox</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_WALLET}">💰</tg-emoji>  <b>Получено:</b> <code>+{px_earned:.2f} Px</code>\n'
        f'📊  <b>Курс:</b> <code>1 Nox = {NOX_TO_PX} Px</code>'
        f'</blockquote>',
        reply_markup=back_mine_keyboard()
    )
    if set_owner_fn:
        set_owner_fn(callback.message.message_id, user_id)
    await callback.answer(f"✅ +{px_earned:.2f} Px зачислено!")


# ─────────────────────────────────────────
#  Фоновая задача — автозачисление Nox
# ─────────────────────────────────────────
async def mine_watchdog():
    """Каждые 60 сек проверяет завершённые циклы и начисляет Nox."""
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        for uid, data in mine_data.items():
            if data["mining_end"] and now >= data["mining_end"]:
                pid     = data["pickaxe_id"]
                pickaxe = PICKAXE_BY_ID[pid]
                auto_collect_if_done(data, pickaxe)
