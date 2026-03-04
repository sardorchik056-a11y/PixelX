import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ─────────────────────────────────────────
#  Инжектируемые функции из bot_menu.py
# ─────────────────────────────────────────
set_owner_fn = None
is_owner_fn  = None
get_px_fn    = None
add_px_fn    = None
spend_px_fn  = None

mine_router = Router()

# ─────────────────────────────────────────
#  Emoji IDs
# ─────────────────────────────────────────
EMOJI_MINES  = "5305699699204837855"
EMOJI_GOLD   = "5278467510604160626"
EMOJI_BACK   = "5906771962734057347"
EMOJI_WALLET = "5443127283898405358"

NOX_TO_PX        = 15   # 1 Nox = 15 Px
TICK_MINUTES     = 10   # каждые 10 минут начисляется порция
REFRESH_SECONDS  = 120  # авто-обновление прогресс-экрана

# ─────────────────────────────────────────
#  15 кирок  (nox_min, nox_max — за 10 мин)
# ─────────────────────────────────────────
PICKAXES = [
    {"id":  1, "name": "Деревянная",      "emoji": "🪓", "price":      0, "nox_min":   5, "nox_max":  15, "hours":  3},
    {"id":  2, "name": "Каменная",        "emoji": "⛏",  "price":    150, "nox_min":  10, "nox_max":  22, "hours":  4},
    {"id":  3, "name": "Заточенная",      "emoji": "🔪", "price":    350, "nox_min":  15, "nox_max":  30, "hours":  5},
    {"id":  4, "name": "Железная",        "emoji": "⚒️", "price":    700, "nox_min":  22, "nox_max":  42, "hours":  6},
    {"id":  5, "name": "Закалённая",      "emoji": "🛠",  "price":   1200, "nox_min":  30, "nox_max":  55, "hours":  8},
    {"id":  6, "name": "Острая стальная", "emoji": "⚔️", "price":   2000, "nox_min":  40, "nox_max":  70, "hours": 10},
    {"id":  7, "name": "Золотая",         "emoji": "🥇", "price":   3500, "nox_min":  55, "nox_max":  90, "hours": 12},
    {"id":  8, "name": "Позолоченная",    "emoji": "✨", "price":   5500, "nox_min":  70, "nox_max": 115, "hours": 16},
    {"id":  9, "name": "Королевская",     "emoji": "👑", "price":   8000, "nox_min":  90, "nox_max": 145, "hours": 20},
    {"id": 10, "name": "Кристальная",     "emoji": "💎", "price":  12000, "nox_min": 115, "nox_max": 180, "hours": 24},
    {"id": 11, "name": "Лазуритовая",     "emoji": "🔷", "price":  18000, "nox_min": 145, "nox_max": 220, "hours": 30},
    {"id": 12, "name": "Рубиновая",       "emoji": "❤️‍🔥","price":  26000, "nox_min": 180, "nox_max": 270, "hours": 36},
    {"id": 13, "name": "Драконья",        "emoji": "🐉", "price":  40000, "nox_min": 220, "nox_max": 340, "hours": 48},
    {"id": 14, "name": "Теневая",         "emoji": "🌑", "price":  60000, "nox_min": 270, "nox_max": 420, "hours": 60},
    {"id": 15, "name": "Вечная",          "emoji": "♾️", "price":  99999, "nox_min": 340, "nox_max": 530, "hours": 72},
]
PICKAXE_BY_ID = {p["id"]: p for p in PICKAXES}

# ─────────────────────────────────────────
#  In-memory БД шахты
# ─────────────────────────────────────────
# mine_data[uid] = {
#   nox: float            — готовый баланс (можно продать)
#   pickaxe_id: int
#   owned: set[int]
#   mining_start: datetime | None
#   mining_end:   datetime | None
#   ticks_paid:   int      — сколько тиков уже начислено
#   accumulated:  float    — накоплено за текущий сеанс (ещё не в nox)
# }
mine_data: dict[int, dict] = {}



def get_mine_user(uid: int) -> dict:
    if uid not in mine_data:
        mine_data[uid] = {
            "nox":          0.0,
            "pickaxe_id":   1,
            "owned":        {1},
            "mining_start": None,
            "mining_end":   None,
            "ticks_paid":   0,
            "accumulated":  0.0,
        }
    return mine_data[uid]


# ─────────────────────────────────────────
#  Бизнес-логика
# ─────────────────────────────────────────
def calc_ticks(data: dict) -> int:
    """Сколько 10-минутных тиков прошло с начала."""
    if not data["mining_start"]:
        return 0
    now     = datetime.now()
    end     = data["mining_end"]
    elapsed = (min(now, end) - data["mining_start"]).total_seconds()
    return int(elapsed / 60 / TICK_MINUTES)

def apply_new_ticks(data: dict, pickaxe: dict):
    """Начисляет новые тики в accumulated."""
    total_ticks = calc_ticks(data)
    new_ticks   = total_ticks - data["ticks_paid"]
    if new_ticks <= 0:
        return 0.0
    earned = sum(
        round(random.uniform(pickaxe["nox_min"], pickaxe["nox_max"]), 2)
        for _ in range(new_ticks)
    )
    data["accumulated"]  += earned
    data["ticks_paid"]   += new_ticks
    return earned

def finalize_mining(data: dict, pickaxe: dict):
    """Завершает сеанс — переносит accumulated → nox."""
    apply_new_ticks(data, pickaxe)
    data["nox"]          += data["accumulated"]
    data["accumulated"]   = 0.0
    data["mining_start"] = None
    data["mining_end"]   = None
    data["ticks_paid"]   = 0

def is_done(data: dict) -> bool:
    return data["mining_end"] is not None and datetime.now() >= data["mining_end"]

def time_left_str(end: datetime) -> str:
    delta = end - datetime.now()
    if delta.total_seconds() <= 0:
        return "завершено ✅"
    h = int(delta.total_seconds() // 3600)
    m = int((delta.total_seconds() % 3600) // 60)
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"

def progress_bar(current_ticks: int, max_ticks: int, length: int = 12) -> str:
    if max_ticks == 0:
        return "▓" * length
    filled = int(length * min(current_ticks, max_ticks) / max_ticks)
    return "▓" * filled + "░" * (length - filled)


# ─────────────────────────────────────────
#  Клавиатуры
# ─────────────────────────────────────────
def mine_main_keyboard(is_mining: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_mining:
        rows.append([InlineKeyboardButton(text="📊 Прогресс копания", callback_data="mine_progress")])
    else:
        rows.append([InlineKeyboardButton(text="⛏ Начать копание",   callback_data="mine_start_pick")])
    rows.append([
        InlineKeyboardButton(text="💰 Продать Nox",  callback_data="mine_sell"),
        InlineKeyboardButton(text="🎒 Мои кирки",    callback_data="mine_owned"),
    ])
    rows.append([InlineKeyboardButton(text="🪓 Магазин кирок", callback_data="mine_shop_0")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def progress_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить",  callback_data="mine_progress")],
        [InlineKeyboardButton(text="Назад",        callback_data="mine", icon_custom_emoji_id=EMOJI_BACK)],
    ])

def shop_keyboard(page: int, owned: set) -> InlineKeyboardMarkup:
    per_page = 5
    start    = page * per_page
    rows     = []
    for p in PICKAXES[start:start + per_page]:
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
        avg = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        rows.append([InlineKeyboardButton(
            text=f"{p['emoji']} {p['name']}  ·  ~{avg} Nox/10мин  ·  {p['hours']}ч",
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
def mine_main_text(uid: int) -> str:
    data    = get_mine_user(uid)
    pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]

    if is_done(data):
        finalize_mining(data, pickaxe)

    nox_px  = round(data["nox"] * NOX_TO_PX, 2)
    max_ticks = int(pickaxe["hours"] * 60 / TICK_MINUTES)
    avg_per_tick = round((pickaxe["nox_min"] + pickaxe["nox_max"]) / 2, 1)
    avg_total    = round(avg_per_tick * max_ticks, 1)

    if data["mining_end"]:
        tl = time_left_str(data["mining_end"])
        status = (
            f'\n<blockquote>'
            f'⏳  <b>Идёт копание</b>  —  осталось <code>{tl}</code>\n'
            f'🔒  <i>Нажмите "Прогресс копания" чтобы следить</i>'
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
        f'⚡  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code> / 10 мин\n'
        f'⏱  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'📈  <b>Ожидаемо за цикл:</b> <code>~{avg_total} Nox</code>'
        f'</blockquote>'
        f'{status}'
    )

def progress_text(uid: int) -> str:
    data    = get_mine_user(uid)
    pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]

    if is_done(data):
        finalize_mining(data, pickaxe)
        return (
            f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Копание завершено!</b>\n\n'
            f'<blockquote>'
            f'✅  Nox зачислен в баланс шахты\n'
            f'💰  <b>Баланс:</b> <code>{data["nox"]:.2f} Nox</code>'
            f'</blockquote>'
        )

    apply_new_ticks(data, pickaxe)

    max_ticks  = int(pickaxe["hours"] * 60 / TICK_MINUTES)
    done_ticks = data["ticks_paid"]
    bar        = progress_bar(done_ticks, max_ticks)
    tl         = time_left_str(data["mining_end"])
    pct        = round(done_ticks / max_ticks * 100) if max_ticks else 100
    now_str    = datetime.now().strftime("%H:%M:%S")

    return (
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Прогресс копания</b>\n\n'
        f'<blockquote>'
        f'{pickaxe["emoji"]}  <b>{pickaxe["name"]}</b>\n'
        f'⚡  <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code> / 10 мин'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<code>{bar}</code>  <b>{pct}%</b>\n\n'
        f'💎  <b>Выкопано:</b> <code>{data["accumulated"]:.2f} Nox</code>\n'
        f'⏳  <b>До конца:</b> <code>{tl}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'🕐  <i>Обновлено: {now_str}  ·  авто каждые 2 мин</i>'
        f'</blockquote>'
    )


# ─────────────────────────────────────────
#  Хэндлеры
# ─────────────────────────────────────────

@mine_router.callback_query(F.data == "mine")
async def cb_mine(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    uid  = call.from_user.id
    data = get_mine_user(uid)
    if is_done(data):
        finalize_mining(data, PICKAXE_BY_ID[data["pickaxe_id"]])
    await call.message.edit_text(
        mine_main_text(uid),
        reply_markup=mine_main_keyboard(data["mining_end"] is not None)
    )
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer()


@mine_router.callback_query(F.data == "mine_progress")
async def cb_mine_progress(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    uid  = call.from_user.id
    data = get_mine_user(uid)
    if not data["mining_end"]:
        await call.answer("⛏ Копание не запущено!", show_alert=True); return

    # Регистрируем это сообщение для авто-обновления

    await call.message.edit_text(progress_text(uid), reply_markup=progress_keyboard())
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer("🔄 Обновлено!")


@mine_router.callback_query(F.data == "mine_start_pick")
async def cb_mine_start_pick(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data = get_mine_user(call.from_user.id)
    if data["mining_end"]:
        await call.answer("⛏ Копание уже идёт!", show_alert=True); return
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Выберите кирку</b>\n\n'
        f'<blockquote>'
        f'Nox зачислится автоматически по окончании цикла.\n'
        f'Прогресс можно отслеживать каждые 2 минуты.'
        f'</blockquote>',
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
    pickaxe             = PICKAXE_BY_ID[pid]
    data["pickaxe_id"]  = pid
    data["mining_start"]= datetime.now()
    data["mining_end"]  = datetime.now() + timedelta(hours=pickaxe["hours"])
    data["ticks_paid"]  = 0
    data["accumulated"] = 0.0
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Копание началось!</b>\n\n'
        f'<blockquote>'
        f'{pickaxe["emoji"]}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code> / 10 мин\n'
        f'⏱  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'🔒  <b>Nox зачислится автоматически в конце</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📊  <i>Используйте "Прогресс копания" для слежения</i>'
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
        avg    = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        lines += (
            f'\n{p["emoji"]} <b>{p["name"]}</b>{active}\n'
            f'   ⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/10мин  ·  '
            f'~<code>{avg}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
        )
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
    tiers = {0: "🪵 Деревянный", 1: "⚙️ Железный", 2: "🥇 Золотой", 3: "💎 Кристальный", 4: "🐉 Легендарный"}
    items = PICKAXES[page * 5:(page + 1) * 5]
    lines = ""
    for p in items:
        avg   = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        mark  = "✅" if p["id"] in data["owned"] else "🔒"
        lines += (
            f'\n{mark} {p["emoji"]} <b>{p["name"]}</b>\n'
            f'   💵 <code>{p["price"]:,} Px</code>  ·  '
            f'⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/10мин  ·  '
            f'~<code>{avg}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
        )
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Магазин кирок</b> — {tiers.get(page, "")}\n\n'
        f'<blockquote>1 Nox = <b>{NOX_TO_PX} Px</b>  ·  каждые <b>10 мин</b> капает порция Nox</blockquote>\n'
        f'{lines}',
        reply_markup=shop_keyboard(page, data["owned"])
    )
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
    if get_px_fn and spend_px_fn:
        user_px = get_px_fn(uid)
        if user_px < pickaxe["price"]:
            await call.answer(
                f'❌ Недостаточно Px!\nНужно: {pickaxe["price"]:,}\nУ вас: {user_px:,}',
                show_alert=True
            ); return
        spend_px_fn(uid, pickaxe["price"])
    data["owned"].add(pid)
    await call.answer(f'✅ Куплено: {pickaxe["emoji"]} {pickaxe["name"]}!', show_alert=True)
    page = (pid - 1) // 5
    # обновляем магазин
    await cb_mine_shop.__wrapped__(call) if hasattr(cb_mine_shop, '__wrapped__') else None
    # просто редактируем снова
    tiers = {0: "🪵 Деревянный", 1: "⚙️ Железный", 2: "🥇 Золотой", 3: "💎 Кристальный", 4: "🐉 Легендарный"}
    items = PICKAXES[page * 5:(page + 1) * 5]
    lines = ""
    for p in items:
        avg  = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        mark = "✅" if p["id"] in data["owned"] else "🔒"
        lines += (
            f'\n{mark} {p["emoji"]} <b>{p["name"]}</b>\n'
            f'   💵 <code>{p["price"]:,} Px</code>  ·  '
            f'⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/10мин  ·  '
            f'~<code>{round((p["nox_min"]+p["nox_max"])/2,1)}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
        )
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_MINES}">⛏</tg-emoji> <b>Магазин кирок</b> — {tiers.get(page, "")}\n\n'
        f'<blockquote>1 Nox = <b>{NOX_TO_PX} Px</b>  ·  каждые <b>10 мин</b> капает порция Nox</blockquote>\n'
        f'{lines}',
        reply_markup=shop_keyboard(page, data["owned"])
    )


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
#  Watchdog — авто-обновление прогресса + финализация
# ─────────────────────────────────────────

async def mine_watchdog():
    while True:
        await asyncio.sleep(REFRESH_SECONDS)  # каждые 2 минуты
        now = datetime.now()

        for uid, data in list(mine_data.items()):
            if not data["mining_end"]:
                continue

            pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]

            # Финализируем если время вышло
            if now >= data["mining_end"]:
                finalize_mining(data, pickaxe)
                continue

            # Начисляем новые тики
            apply_new_ticks(data, pickaxe)

            # Обновляем прогресс-сообщение если есть
            if uid in progress_watchers and _bot_ref:
                chat_id, msg_id = progress_watchers[uid]
                try:
                    await _bot_ref.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=progress_text(uid),
                        reply_markup=progress_keyboard(),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest:
                    pass  # сообщение не изменилось или удалено
