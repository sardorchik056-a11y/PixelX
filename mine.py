import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ─────────────────────────────────────────
#  Owner guard (инжектируется из bot_menu.py)
# ─────────────────────────────────────────
set_owner_fn = None
is_owner_fn  = None

mine_router = Router()

# ─────────────────────────────────────────
#  Custom Emoji IDs
# ─────────────────────────────────────────
EMOJI_MINES       = "5305699699204837855"
EMOJI_GOLD        = "5278467510604160626"
EMOJI_BACK        = "5906771962734057347"
EMOJI_WALLET      = "5443127283898405358"

# ─────────────────────────────────────────
#  NOX → PX курс
# ─────────────────────────────────────────
NOX_TO_PX = 15

# ─────────────────────────────────────────
#  15 кирок
# ─────────────────────────────────────────
PICKAXES = [
    {"id": 1,  "name": "Деревянная",      "emoji": "🪓", "price": 0,     "interval": 3,  "nox_min": 2,   "nox_max": 7,   "hours": 3},
    {"id": 2,  "name": "Каменная",        "emoji": "⛏",  "price": 150,   "interval": 3,  "nox_min": 3,   "nox_max": 9,   "hours": 4},
    {"id": 3,  "name": "Заточенная",      "emoji": "🔪", "price": 350,   "interval": 3,  "nox_min": 4,   "nox_max": 12,  "hours": 5},
    {"id": 4,  "name": "Железная",        "emoji": "⚒️", "price": 700,   "interval": 5,  "nox_min": 7,   "nox_max": 18,  "hours": 6},
    {"id": 5,  "name": "Закалённая",      "emoji": "🛠",  "price": 1200,  "interval": 5,  "nox_min": 9,   "nox_max": 22,  "hours": 8},
    {"id": 6,  "name": "Острая стальная", "emoji": "⚔️", "price": 2000,  "interval": 5,  "nox_min": 12,  "nox_max": 25,  "hours": 10},
    {"id": 7,  "name": "Золотая",         "emoji": "🥇", "price": 3500,  "interval": 8,  "nox_min": 18,  "nox_max": 35,  "hours": 12},
    {"id": 8,  "name": "Позолоченная",    "emoji": "✨", "price": 5500,  "interval": 8,  "nox_min": 22,  "nox_max": 42,  "hours": 16},
    {"id": 9,  "name": "Королевская",     "emoji": "👑", "price": 8000,  "interval": 8,  "nox_min": 28,  "nox_max": 50,  "hours": 20},
    {"id": 10, "name": "Кристальная",     "emoji": "💎", "price": 12000, "interval": 12, "nox_min": 40,  "nox_max": 70,  "hours": 24},
    {"id": 11, "name": "Лазуритовая",     "emoji": "🔷", "price": 18000, "interval": 12, "nox_min": 50,  "nox_max": 85,  "hours": 30},
    {"id": 12, "name": "Рубиновая",       "emoji": "❤️‍🔥","price": 26000, "interval": 15, "nox_min": 65,  "nox_max": 110, "hours": 36},
    {"id": 13, "name": "Драконья",        "emoji": "🐉", "price": 40000, "interval": 18, "nox_min": 90,  "nox_max": 150, "hours": 48},
    {"id": 14, "name": "Теневая",         "emoji": "🌑", "price": 60000, "interval": 20, "nox_min": 120, "nox_max": 200, "hours": 60},
    {"id": 15, "name": "Вечная",          "emoji": "♾️", "price": 99999, "interval": 25, "nox_min": 170, "nox_max": 280, "hours": 72},
]
PICKAXE_BY_ID = {p["id"]: p for p in PICKAXES}

# ─────────────────────────────────────────
#  In-memory БД шахты
# ─────────────────────────────────────────
mine_data: dict[int, dict] = {}

def get_mine_user(user_id: int) -> dict:
    if user_id not in mine_data:
        mine_data[user_id] = {
            "nox":         0.0,
            "pickaxe_id":  1,
            "owned":       {1},
            "mining_end":  None,
            "pending_nox": 0.0,
        }
    return mine_data[user_id]

def auto_collect_if_done(data: dict, pickaxe: dict) -> float:
    if data["mining_end"] and datetime.now() >= data["mining_end"]:
        intervals = int(pickaxe["hours"] * 60 / pickaxe["interval"])
        earned    = round(sum(
            random.uniform(pickaxe["nox_min"], pickaxe["nox_max"])
            for _ in range(intervals)
        ), 2)
        data["nox"]        += earned
        data["pending_nox"] = 0.0
        data["mining_end"]  = None
        return earned
    return 0.0

def time_left_str(end: datetime) -> str:
    delta = end - datetime.now()
    if delta.total_seconds() <= 0:
        return "завершено"
    h = int(delta.total_seconds() // 3600)
    m = int((delta.total_seconds() % 3600) // 60)
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def mine_main_keyboard(data: dict) -> InlineKeyboardMarkup:
    rows = []
    if data["mining_end"] is None:
        rows.append([InlineKeyboardButton(text="⛏ Начать копание",  callback_data="mine_start_pick")])
    else:
        rows.append([InlineKeyboardButton(text="⏳ Идёт копание...", callback_data="mine_status")])
    rows.append([
        InlineKeyboardButton(text="💰 Продать Nox",    callback_data="mine_sell"),
        InlineKeyboardButton(text="🎒 Мои кирки",      callback_data="mine_owned"),
    ])
    rows.append([
        InlineKeyboardButton(text="🪓 Магазин кирок",  callback_data="mine_shop_0"),
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
        mark  = "✅" if p["id"] in owned else "🔒"
        label = f"{mark} {p['emoji']} {p['name']} — {p['price']:,} Px"
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
            text=f"{p['emoji']} {p['name']}  ·  каждые {p['interval']} мин  ·  {p['nox_min']}–{p['nox_max']} Nox",
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
    pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]
    auto_collect_if_done(data, pickaxe)
    nox_px  = round(data["nox"] * NOX_TO_PX, 2)

    if data["mining_end"]:
        tl     = time_left_str(data["mining_end"])
        status = (
            f'\n<blockquote>'
            f'⏳  <b>Копание идёт</b>\n'
            f'🕐  <b>До завершения:</b> <code>{tl}</code>\n'
            f'🔒  <i>Nox зачислится автоматически</i>'
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
    tiers = {0: "🪵 Деревянный", 1: "⚙️ Железный", 2: "🥇 Золотой", 3: "💎 Кристальный", 4: "🐉 Легендарный"}
    items = PICKAXES[page * 5:(page + 1) * 5]
    lines = ""
    for p in items:
        lines += (
            f'\n{p["emoji"]} <b>{p["name"]}</b>\n'
            f'   💵 <code>{p["price"]:,} Px</code>  ·  '
            f'⚡ каждые <code>{p["interval"]} мин</code>  ·  '
            f'📦 <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>  ·  '
            f'⏱ <code>{p["hours"]} ч</code>\n'
        )
    return (
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Магазин кирок</b> — {tiers.get(page, "")}\n\n'
        f'<blockquote>1 Nox = <b>{NOX_TO_PX} Px</b>  ·  Nox зачисляется автоматически по окончании цикла</blockquote>\n'
        f'{lines}'
    )


# ─────────────────────────────────────────
#  Хэндлеры mine_router
# ─────────────────────────────────────────

@mine_router.callback_query(F.data == "mine")
async def cb_mine(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    uid     = call.from_user.id
    data    = get_mine_user(uid)
    pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]
    auto_collect_if_done(data, pickaxe)
    await call.message.edit_text(mine_main_text(uid), reply_markup=mine_main_keyboard(data))
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer()


@mine_router.callback_query(F.data == "mine_status")
async def cb_mine_status(call: CallbackQuery):
    data = get_mine_user(call.from_user.id)
    if data["mining_end"]:
        await call.answer(f"⏳ Завершится через {time_left_str(data['mining_end'])}", show_alert=True)
    else:
        await call.answer("✅ Копание завершено, Nox зачислен!", show_alert=True)


@mine_router.callback_query(F.data == "mine_start_pick")
async def cb_mine_start_pick(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data = get_mine_user(call.from_user.id)
    if data["mining_end"]:
        await call.answer("⛏ Копание уже идёт!", show_alert=True); return
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Выберите кирку</b>\n\n'
        f'<blockquote>Копание нельзя прервать.\nNox зачислится автоматически по окончании цикла.</blockquote>',
        reply_markup=pick_select_keyboard(data["owned"])
    )
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_equip_"))
async def cb_mine_equip(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    pid  = int(call.data.split("_")[2])
    data = get_mine_user(call.from_user.id)
    if pid not in data["owned"]:
        await call.answer("❌ У вас нет этой кирки!", show_alert=True); return
    if data["mining_end"]:
        await call.answer("⛏ Копание уже идёт!", show_alert=True); return
    pickaxe            = PICKAXE_BY_ID[pid]
    data["pickaxe_id"] = pid
    data["mining_end"] = datetime.now() + timedelta(hours=pickaxe["hours"])
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Копание началось!</b>\n\n'
        f'<blockquote>'
        f'{pickaxe["emoji"]}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Интервал:</b> каждые <code>{pickaxe["interval"]} мин</code>\n'
        f'📦  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code>\n'
        f'⏱  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'🔒  <b>Прервать нельзя — Nox зачислится автоматически</b>'
        f'</blockquote>',
        reply_markup=back_mine_keyboard()
    )
    if set_owner_fn:
        set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer("⛏ Копание запущено!")


@mine_router.callback_query(F.data == "mine_owned")
async def cb_mine_owned(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data    = get_mine_user(call.from_user.id)
    current = PICKAXE_BY_ID[data["pickaxe_id"]]
    lines   = ""
    for pid in sorted(data["owned"]):
        p      = PICKAXE_BY_ID[pid]
        active = "  🟢 <b>Активна</b>" if pid == data["pickaxe_id"] else ""
        lines += f'\n{p["emoji"]} <b>{p["name"]}</b>{active}\n'
        lines += f'   ⚡ каждые <code>{p["interval"]} мин</code>  ·  📦 <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>  ·  ⏱ <code>{p["hours"]} ч</code>\n'
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Мои кирки</b>\n\n'
        f'<blockquote>Текущая: {current["emoji"]} {current["name"]}</blockquote>'
        f'{lines}',
        reply_markup=back_mine_keyboard()
    )
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_shop_"))
async def cb_mine_shop(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    page = int(call.data.split("_")[2])
    data = get_mine_user(call.from_user.id)
    await call.message.edit_text(shop_page_text(page), reply_markup=shop_keyboard(page, data["owned"]))
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_buy_"))
async def cb_mine_buy(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    pid     = int(call.data.split("_")[2])
    pickaxe = PICKAXE_BY_ID[pid]
    uid     = call.from_user.id
    data    = get_mine_user(uid)
    if pid in data["owned"]:
        await call.answer("✅ У вас уже есть эта кирка!", show_alert=True); return
    # Списываем Px через get_px_fn
    if get_px_fn and spend_px_fn:
        user_px = get_px_fn(uid)
        if user_px < pickaxe["price"]:
            await call.answer(f'❌ Недостаточно Px!\nНужно: {pickaxe["price"]:,}\nУ вас: {user_px:,}', show_alert=True); return
        spend_px_fn(uid, pickaxe["price"])
    data["owned"].add(pid)
    await call.answer(f'✅ Куплено: {pickaxe["emoji"]} {pickaxe["name"]}!', show_alert=True)
    page = (pid - 1) // 5
    await call.message.edit_text(shop_page_text(page), reply_markup=shop_keyboard(page, data["owned"]))


@mine_router.callback_query(F.data == "mine_sell")
async def cb_mine_sell(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    uid  = call.from_user.id
    data = get_mine_user(uid)
    if data["nox"] <= 0:
        await call.answer("❌ Нет Nox для продажи!", show_alert=True); return
    nox_amount = data["nox"]
    px_earned  = round(nox_amount * NOX_TO_PX, 2)
    if add_px_fn:
        add_px_fn(uid, px_earned)
    data["nox"] = 0.0
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji> <b>Продажа Nox</b>\n\n'
        f'<blockquote>'
        f'⛏  <b>Продано:</b> <code>{nox_amount:.2f} Nox</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_WALLET}">💰</tg-emoji>  <b>Получено:</b> <code>+{px_earned:.2f} Px</code>\n'
        f'📊  <b>Курс:</b> <code>1 Nox = {NOX_TO_PX} Px</code>'
        f'</blockquote>',
        reply_markup=back_mine_keyboard()
    )
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer(f"✅ +{px_earned:.2f} Px зачислено!")


# ─────────────────────────────────────────
#  Px-функции (инжектируются из bot_menu.py)
# ─────────────────────────────────────────
get_px_fn   = None
add_px_fn   = None
spend_px_fn = None


# ─────────────────────────────────────────
#  Watchdog — автозачисление Nox
# ─────────────────────────────────────────
async def mine_watchdog():
    while True:
        await asyncio.sleep(60)
        now = datetime.now()
        for uid, data in mine_data.items():
            if data["mining_end"] and now >= data["mining_end"]:
                pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]
                auto_collect_if_done(data, pickaxe)
