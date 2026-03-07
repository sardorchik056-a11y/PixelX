import asyncio
import random
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from database import db_get_mine_user, db_save_mine_user

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
EMOJI_MINES    = "5393480373944459905"
EMOJI_GOLD     = "5278467510604160626"
EMOJI_BACK     = "5906771962734057347"
EMOJI_WALLET   = "5443127283898405358"
EMOJI_SELL     = "5312441427764989435"
EMOJI_BAG      = "5278702045883292456"
EMOJI_SHOP     = "5312361253610475399"
EMOJI_PROGRESS = "5231200819986047254"
EMOJI_REFRESH  = "5402186569006210455"
EMOJI_OWNED    = "5206607081334906820"
EMOJI_LOCKED   = "5210952531676504517"

NOX_TO_PX       = 5
TICK_MINUTES    = 5
REFRESH_SECONDS = 120

PICKAXE_EMOJI_ID = "5197371802136892976"

def pickaxe_icon() -> str:
    return f'<tg-emoji emoji-id="{PICKAXE_EMOJI_ID}">⛏</tg-emoji>'

PICKAXES = [
    {"id":  1, "name": "Кирка-I",    "price":      0, "nox_min":   5, "nox_max":  15, "hours":  3},
    {"id":  2, "name": "Кирка-II",   "price":    2500, "nox_min":  10, "nox_max":  22, "hours":  4},
    {"id":  3, "name": "Кирка-III",  "price":    5000, "nox_min":  17, "nox_max":  30, "hours":  5},
    {"id":  4, "name": "Кирка-IV",   "price":   12000, "nox_min":  29, "nox_max":  50, "hours":  6},
    {"id":  5, "name": "Кирка-V",    "price":   25000, "nox_min":  50, "nox_max":  85, "hours":  8},
    {"id":  6, "name": "Кирка-VI",   "price":   55000, "nox_min":  70, "nox_max": 110, "hours": 10},
    {"id":  7, "name": "Кирка-VII",  "price":  135000, "nox_min": 155, "nox_max": 290, "hours": 12},
    {"id":  8, "name": "Кирка-VIII", "price":  355500, "nox_min": 270, "nox_max": 415, "hours": 16},
    {"id":  9, "name": "Кирка-IX",   "price":  800000, "nox_min": 490, "nox_max": 645, "hours": 20},
    {"id": 10, "name": "Кирка-X",    "price": 1200000, "nox_min": 565, "nox_max": 780, "hours": 24},
    {"id": 11, "name": "Кирка-XI",   "price": 1800000, "nox_min": 645, "nox_max": 820, "hours": 30},
    {"id": 12, "name": "Кирка-XII",  "price": 2600000, "nox_min": 780, "nox_max": 970, "hours": 36},
    {"id": 13, "name": "Кирка-XIII", "price": 4000000, "nox_min": 920, "nox_max": 1340, "hours": 48},
    {"id": 14, "name": "Кирка-XIV",  "price": 6000000, "nox_min": 1270, "nox_max": 1620, "hours": 60},
    {"id": 15, "name": "Кирка-XV",   "price": 9999999, "nox_min": 1940, "nox_max": 2530, "hours": 72},
]
PICKAXE_BY_ID = {p["id"]: p for p in PICKAXES}

# ─────────────────────────────────────────
#  Bot ref
# ─────────────────────────────────────────
_bot_ref: Bot | None = None

def set_bot_ref(bot: Bot):
    global _bot_ref
    _bot_ref = bot

# ─────────────────────────────────────────
#  In-memory локи — защита от двойных операций
# ─────────────────────────────────────────
_selling: set[int] = set()   # юзеры в процессе продажи
_buying:  set[int] = set()   # юзеры в процессе покупки


# ─────────────────────────────────────────
#  Получение / сохранение данных шахты
# ─────────────────────────────────────────
def get_mine_user(uid: int) -> dict:
    return db_get_mine_user(uid)

def save_mine_user(uid: int, data: dict):
    db_save_mine_user(uid, data)


# ─────────────────────────────────────────
#  Бизнес-логика
# ─────────────────────────────────────────
def calc_ticks(data: dict) -> int:
    if not data["mining_start"]:
        return 0
    now     = datetime.now()
    end     = data["mining_end"]
    elapsed = (min(now, end) - data["mining_start"]).total_seconds()
    return int(elapsed / 60 / TICK_MINUTES)

def apply_new_ticks(data: dict, pickaxe: dict) -> float:
    total_ticks = calc_ticks(data)
    new_ticks   = total_ticks - data["ticks_paid"]
    if new_ticks <= 0:
        return 0.0
    earned = sum(
        round(random.uniform(pickaxe["nox_min"], pickaxe["nox_max"]), 2)
        for _ in range(new_ticks)
    )
    data["accumulated"] += earned
    data["ticks_paid"]  += new_ticks
    return earned

def finalize_mining(data: dict, pickaxe: dict):
    # Идемпотентность: если mining_end уже None — уже финализировано
    if data["mining_end"] is None:
        return
    apply_new_ticks(data, pickaxe)
    data["nox"]          += data["accumulated"]
    data["accumulated"]   = 0.0
    data["mining_start"]  = None
    data["mining_end"]    = None
    data["ticks_paid"]    = 0

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
        rows.append([InlineKeyboardButton(text="Прогресс копания", callback_data="mine_progress", icon_custom_emoji_id=EMOJI_PROGRESS)])
    else:
        rows.append([InlineKeyboardButton(text="Начать копание",   callback_data="mine_start_pick", icon_custom_emoji_id=PICKAXE_EMOJI_ID)])
    rows.append([
        InlineKeyboardButton(text="Продать Nox", callback_data="mine_sell",  icon_custom_emoji_id=EMOJI_SELL),
        InlineKeyboardButton(text="Мои кирки",   callback_data="mine_owned", icon_custom_emoji_id=EMOJI_BAG),
    ])
    rows.append([InlineKeyboardButton(text="Магазин кирок", callback_data="mine_shop_0", icon_custom_emoji_id=EMOJI_SHOP)])
    rows.append([InlineKeyboardButton(text="Назад",         callback_data="main_menu",   icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def progress_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data="mine_progress", icon_custom_emoji_id=EMOJI_REFRESH)],
        [InlineKeyboardButton(text="Назад",    callback_data="mine",          icon_custom_emoji_id=EMOJI_BACK)],
    ])

def shop_keyboard(page: int, owned: set) -> InlineKeyboardMarkup:
    per_page = 5
    start    = page * per_page
    rows     = []
    for p in PICKAXES[start:start + per_page]:
        emoji_id = EMOJI_OWNED if p["id"] in owned else EMOJI_LOCKED
        label    = f"{p['name']} — {p['price']:,} Px"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mine_buy_{p['id']}", icon_custom_emoji_id=emoji_id)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=f"mine_shop_{page-1}", icon_custom_emoji_id=EMOJI_BACK))
    if start + per_page < len(PICKAXES):
        nav.append(InlineKeyboardButton(text="Далее",  callback_data=f"mine_shop_{page+1}", icon_custom_emoji_id=EMOJI_REFRESH))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Назад", callback_data="mine", icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def pick_select_keyboard(owned: set) -> InlineKeyboardMarkup:
    rows = []
    for pid in sorted(owned):
        p   = PICKAXE_BY_ID[pid]
        avg = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        rows.append([InlineKeyboardButton(
            text=f"{p['name']}  ·  ~{avg} Nox/5мин  ·  {p['hours']}ч",
            callback_data=f"mine_equip_{pid}",
            icon_custom_emoji_id=PICKAXE_EMOJI_ID
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
        save_mine_user(uid, data)

    nox_px       = round(data["nox"] * NOX_TO_PX, 2)
    max_ticks    = int(pickaxe["hours"] * 60 / TICK_MINUTES)
    avg_per_tick = round((pickaxe["nox_min"] + pickaxe["nox_max"]) / 2, 1)
    avg_total    = round(avg_per_tick * max_ticks, 1)

    if data["mining_end"]:
        tl     = time_left_str(data["mining_end"])
        status = (
            f'\n<blockquote>'
            f'<tg-emoji emoji-id="5906852613629941703">⚡</tg-emoji>  <b>Идёт копание</b>  —  осталось <code>{tl}</code>\n'
            f'<tg-emoji emoji-id="5201691993775818138">⚡</tg-emoji>  <i>Нажмите "Прогресс копания" чтобы следить</i>'
            f'</blockquote>'
        )
    else:
        status = (
            f'\n<blockquote>'
            f'💤  <b>Шахта простаивает</b>\n'
            f'<tg-emoji emoji-id="5201691993775818138">⚡</tg-emoji>  <i>Нажмите "Начать копание"</i>'
            f'</blockquote>'
        )

    return (
        f'<tg-emoji emoji-id="5197371802136892976">⛏</tg-emoji> <b>Шахта</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">💎</tg-emoji>  <b>Баланс Nox:</b> <code>{data["nox"]:.2f} Nox</code>\n'
        f'<tg-emoji emoji-id="5199552030615558774">💰</tg-emoji>  <b>≈ в Px:</b> <code>{nox_px:.2f} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'{pickaxe_icon()}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox / 5 мин</code>\n'
        f'<tg-emoji emoji-id="5382194935057372936">💰</tg-emoji>  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'<tg-emoji emoji-id="5303214794336125778">💰</tg-emoji>  <b>Ожидаемо за цикл:</b> <code>~{avg_total} Nox</code>'
        f'</blockquote>'
        f'{status}'
    )

def progress_text(uid: int) -> str:
    data    = get_mine_user(uid)
    pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]

    if is_done(data):
        finalize_mining(data, pickaxe)
        save_mine_user(uid, data)
        return (
            f'<tg-emoji emoji-id="5262844652964303985">⛏</tg-emoji> <b>Копание завершено!</b>\n\n'
            f'<blockquote>'
            f' <tg-emoji emoji-id="5429651785352501917">💰</tg-emoji> Nox зачислен в баланс шахты\n'
            f'<tg-emoji emoji-id="5278467510604160626">💰</tg-emoji>  <b>Баланс:</b> <code>{data["nox"]:.2f} Nox</code>'
            f'</blockquote>'
        )

    apply_new_ticks(data, pickaxe)
    save_mine_user(uid, data)

    max_ticks  = int(pickaxe["hours"] * 60 / TICK_MINUTES)
    done_ticks = data["ticks_paid"]
    bar        = progress_bar(done_ticks, max_ticks)
    tl         = time_left_str(data["mining_end"])
    pct        = round(done_ticks / max_ticks * 100) if max_ticks else 100
    now_str    = datetime.now().strftime("%H:%M:%S")

    return (
        f'<tg-emoji emoji-id="5197371802136892976">⛏</tg-emoji> <b>Прогресс копания</b>\n\n'
        f'<blockquote>'
        f'{pickaxe_icon()}  <b>{pickaxe["name"]}</b>\n'
        f'⚡  <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code> / 5 мин'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<code>{bar}</code>  <b>{pct}%</b>\n\n'
        f'<tg-emoji emoji-id="5305699699204837855">⛏</tg-emoji>  <b>Выкопано:</b> <code>{data["accumulated"]:.2f} Nox</code>\n'
        f'⏳  <b>До конца:</b> <code>{tl}</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'🕐  <i>Обновлено: {now_str}  используйте кнопку ниже·</i>'
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
        save_mine_user(uid, data)
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

    await call.message.edit_text(progress_text(uid), reply_markup=progress_keyboard())
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
    await call.answer("Обновлено!")


@mine_router.callback_query(F.data == "mine_start_pick")
async def cb_mine_start_pick(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    data = get_mine_user(call.from_user.id)
    if data["mining_end"]:
        await call.answer("⛏ Копание уже идёт!", show_alert=True); return
    await call.message.edit_text(
        f'<tg-emoji emoji-id="5262844652964303985">⛏</tg-emoji> <b>Выберите кирку</b>\n\n'
        f'<blockquote>'
        f'Nox зачислится автоматически по окончании цикла.\n'
        f'Прогресс можно отслеживать кнопкой "Обновить".'
        f'</blockquote>',
        reply_markup=pick_select_keyboard(data["owned"])
    )
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_equip_"))
async def cb_mine_equip(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    parts = call.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await call.answer("❌ Неверные данные!", show_alert=True); return
    pid = int(parts[2])

    if pid not in PICKAXE_BY_ID:
        await call.answer("❌ Кирка не найдена!", show_alert=True); return

    uid  = call.from_user.id
    data = get_mine_user(uid)
    if pid not in data["owned"]:
        await call.answer("❌ У вас нет этой кирки!", show_alert=True); return
    if data["mining_end"]:
        await call.answer("⛏ Копание уже идёт!", show_alert=True); return

    pickaxe              = PICKAXE_BY_ID[pid]
    data["pickaxe_id"]   = pid
    data["mining_start"] = datetime.now()
    data["mining_end"]   = datetime.now() + timedelta(hours=pickaxe["hours"])
    data["ticks_paid"]   = 0
    data["accumulated"]  = 0.0
    save_mine_user(uid, data)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="5197371802136892976">⛏</tg-emoji> <b>Копание началось!</b>\n\n'
        f'<blockquote>'
        f'{pickaxe_icon()}  <b>Кирка:</b> {pickaxe["name"]}\n'
        f'⚡  <b>Добыча:</b> <code>{pickaxe["nox_min"]}–{pickaxe["nox_max"]} Nox</code> / 5 мин\n'
        f'⏱  <b>Цикл:</b> <code>{pickaxe["hours"]} ч</code>\n'
        f'<tg-emoji emoji-id="5400362079783770689">⛏</tg-emoji>  <b>Nox зачислится автоматически в конце</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f' <tg-emoji emoji-id="5201691993775818138">⛏</tg-emoji> <i>Используйте "Прогресс копания" для слежения</i>'
        f'</blockquote>',
        reply_markup=back_mine_keyboard()
    )
    if set_owner_fn:
        set_owner_fn(call.message.message_id, uid)
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
            f'\n{pickaxe_icon()} <b>{p["name"]}</b>{active}\n'
            f'   ⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/5мин  ·  '
            f'~<code>{avg}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
        )
    await call.message.edit_text(
        f'<tg-emoji emoji-id="5262844652964303985">⛏</tg-emoji> <b>Мои кирки</b>\n\n'
        f'<blockquote>Текущая: {pickaxe_icon()} {current["name"]}</blockquote>'
        f'{lines}',
        reply_markup=back_mine_keyboard()
    )
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_shop_"))
async def cb_mine_shop(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    parts = call.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await call.answer("❌ Неверные данные!", show_alert=True); return
    page = int(parts[2])
    page = max(0, min(page, (len(PICKAXES) - 1) // 5))

    data  = get_mine_user(call.from_user.id)
    tiers = {0: "1", 1: "2", 2: "3", 3: "4", 4: "5"}
    items = PICKAXES[page * 5:(page + 1) * 5]
    lines = ""
    for p in items:
        avg   = round((p["nox_min"] + p["nox_max"]) / 2, 1)
        mark  = "✅" if p["id"] in data["owned"] else "🔒"
        lines += (
            f'\n{mark} {pickaxe_icon()} <b>{p["name"]}</b>\n'
            f'    <code>{p["price"]:,} Px</code>  ·  '
            f'⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/5мин  ·  '
            f'~<code>{avg}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
        )
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SHOP}">⛏</tg-emoji> <b>Магазин кирок</b> — {tiers.get(page, "")}\n\n'
        f'<blockquote>1 Nox = <b>{NOX_TO_PX} Px</b>  ·  каждые <b>5 мин</b> капает порция Nox</blockquote>\n'
        f'{lines}',
        reply_markup=shop_keyboard(page, data["owned"])
    )
    await call.answer()


@mine_router.callback_query(F.data.startswith("mine_buy_"))
async def cb_mine_buy(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    parts = call.data.split("_")
    if len(parts) < 3 or not parts[2].isdigit():
        await call.answer("❌ Неверные данные!", show_alert=True); return
    pid = int(parts[2])

    if pid not in PICKAXE_BY_ID:
        await call.answer("❌ Кирка не найдена!", show_alert=True); return

    pickaxe = PICKAXE_BY_ID[pid]
    uid     = call.from_user.id

    # Защита от двойного нажатия «Купить»
    if uid in _buying:
        await call.answer("⏳ Подождите...", show_alert=True); return
    _buying.add(uid)

    try:
        data = get_mine_user(uid)

        if pid in data["owned"]:
            await call.answer("✅ У вас уже есть эта кирка!", show_alert=True); return

        if get_px_fn and spend_px_fn:
            user_px = get_px_fn(uid)
            if user_px < pickaxe["price"]:
                await call.answer(
                    f'❌ Недостаточно Px!\nНужно: {pickaxe["price"]:,}\nУ вас: {user_px:,}',
                    show_alert=True
                ); return

            # Атомарно: сначала добавляем кирку в owned, потом списываем Px
            data["owned"].add(pid)
            save_mine_user(uid, data)
            spend_px_fn(uid, pickaxe["price"])
        else:
            data["owned"].add(pid)
            save_mine_user(uid, data)

        await call.answer(f'✅ Куплено: {pickaxe["name"]}!', show_alert=True)

        page  = (pid - 1) // 5
        tiers = {0: "1", 1: "2", 2: "3", 3: "4", 4: "5"}
        items = PICKAXES[page * 5:(page + 1) * 5]
        lines = ""
        for p in items:
            avg  = round((p["nox_min"] + p["nox_max"]) / 2, 1)
            mark = "✅" if p["id"] in data["owned"] else "🔒"
            lines += (
                f'\n{mark} {pickaxe_icon()} <b>{p["name"]}</b>\n'
                f'    <code>{p["price"]:,} Px</code>  ·  '
                f'⚡ <code>{p["nox_min"]}–{p["nox_max"]} Nox</code>/5мин  ·  '
                f'~<code>{round((p["nox_min"]+p["nox_max"])/2, 1)}</code> avg  ·  ⏱ <code>{p["hours"]} ч</code>\n'
            )
        await call.message.edit_text(
            f'<tg-emoji emoji-id="5197371802136892976">⛏</tg-emoji> <b>Магазин кирок</b> — {tiers.get(page, "")}\n\n'
            f'<blockquote>1 Nox = <b>{NOX_TO_PX} Px</b>  ·  каждые <b>5 мин</b> капает порция Nox</blockquote>\n'
            f'{lines}',
            reply_markup=shop_keyboard(page, data["owned"])
        )
    finally:
        _buying.discard(uid)


@mine_router.callback_query(F.data == "mine_sell")
async def cb_mine_sell(call: CallbackQuery):
    if is_owner_fn and not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return

    uid = call.from_user.id

    # Защита от двойной продажи
    if uid in _selling:
        await call.answer("⏳ Подождите...", show_alert=True); return
    _selling.add(uid)

    try:
        data = get_mine_user(uid)
        if data["nox"] <= 0:
            await call.answer("❌ Нет Nox для продажи!", show_alert=True); return

        pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]
        if data["mining_end"]:
            apply_new_ticks(data, pickaxe)
            data["nox"]        += data["accumulated"]
            data["accumulated"]  = 0.0

        nox_amount = data["nox"]
        px_earned  = round(nox_amount * NOX_TO_PX, 2)

        # Сначала обнуляем Nox и сохраняем — потом начисляем Px
        # Если бот упадёт после save но до add_px — юзер потеряет Nox без Px
        # Это лучше чем получить Px и сохранить Nox (двойная прибыль)
        data["nox"] = 0.0
        save_mine_user(uid, data)

        if add_px_fn:
            add_px_fn(uid, px_earned)

        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_GOLD}">💰</tg-emoji> <b>Продажа Nox</b>\n\n'
            f'<blockquote>'
            f'⛏  <b>Продано:</b> <code>{nox_amount:.2f} Nox</code>\n'
            f'<tg-emoji emoji-id="{EMOJI_WALLET}">💰</tg-emoji>  <b>Получено:</b> <code>+{px_earned:.2f} Px</code>\n'
            f'<tg-emoji emoji-id="5231200819986047254">⛏</tg-emoji>  <b>Курс:</b> <code>1 Nox = {NOX_TO_PX} Px</code>'
            f'</blockquote>',
            reply_markup=back_mine_keyboard()
        )
        if set_owner_fn:
            set_owner_fn(call.message.message_id, uid)
        await call.answer(f"✅ +{px_earned:.2f} Px зачислено!")

    finally:
        _selling.discard(uid)


# ─────────────────────────────────────────
#  Watchdog — только финализация завершённых сеансов
# ─────────────────────────────────────────
async def mine_watchdog():
    while True:
        await asyncio.sleep(REFRESH_SECONDS)
        now = datetime.now()

        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT uid FROM mine WHERE mining_end IS NOT NULL"
            ).fetchall()
        active_uids = [r["uid"] for r in rows]

        for uid in active_uids:
            # Пропускаем если юзер прямо сейчас продаёт — не мешаем
            if uid in _selling:
                continue

            data = get_mine_user(uid)

            # Свежая проверка из БД — мог уже финализироваться через хэндлер
            if not data["mining_end"]:
                continue

            if now >= data["mining_end"]:
                pickaxe = PICKAXE_BY_ID[data["pickaxe_id"]]
                finalize_mining(data, pickaxe)
                save_mine_user(uid, data)
