"""
treyd.py — Модуль «Биржа» для PixelX бота

Изменения v3:
  • Цена в списке лотов — за ВЕСЬ лот (не за 10к)
  • Кнопка выбора диапазона Px при просмотре лотов
  • Лимит 5 активных лотов на одного пользователя
  • Статистика покупок (кол-во, потрачено $, получено Px)
  • Защита от дублей через SELECT FOR UPDATE (атомарная блокировка)
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from aiogram import Bot, Router, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from database import db_get_or_create_user, db_get_px, db_try_spend_px, db_add_px
from treyd_db import (
    db_get_usd_balance, db_add_usd, db_try_spend_usd,
    db_create_listing, db_get_active_listings, db_get_my_listings,
    db_get_listing, db_mark_listing_sold, db_cancel_listing_by_owner,
    db_expire_listings, db_get_seller_stats, db_get_seller_last_sales,
    db_create_invoice_record, db_mark_invoice_paid,
    db_create_withdraw_request, db_get_withdraw_request,
    db_get_pending_withdraw_requests, db_approve_withdraw_request,
    db_reject_withdraw_request, db_get_withdraw_stats,
    db_count_active_listings_by_seller,
    db_get_buyer_stats,
    db_mark_listing_sold_atomic,
)

logger = logging.getLogger(__name__)

# ── Конфиг ─────────────────────────────────────────────────
CRYPTOBOT_API_URL   = "https://pay.crypt.bot/api"
# CRYPTOBOT_API_URL = "https://testnet-pay.crypt.bot/api"

SELL_MIN_PX          = 50_000
SELL_MAX_PX          = 1_000_000_000
MAX_ACTIVE_LOTS      = 5   # лимит активных лотов на одного продавца

PRICE_PER_10K_MIN    = 0.20
PRICE_PER_10K_MAX    = 1.00

COMMISSION_SELL      = 0.15
COMMISSION_CANCEL    = 0.05
COMMISSION_WITHDRAW  = 0.03
WITHDRAW_MIN_USD     = 1.00

EXPIRE_DAYS_OPTIONS  = [7, 14, 30]
LISTINGS_PER_PAGE    = 10
INVOICE_POLL_SECS    = 2
INVOICE_MAX_SECS     = 600

# Диапазоны фильтра Px — 4 кнопки под списком лотов
# range_idx=-1 означает «все лоты» (фильтр не активен)
PX_RANGES = [
    ("50к–200к",   50_000,      200_000),
    ("200к–500к",  200_000,     500_000),
    ("500к–1М",    500_000,     1_000_000),
    ("1М–5М",      1_000_000,   5_000_000),
]

# ── Emoji ───────────────────────────────────────────────────
EMOJI_EXCHANGE = "5402186569006210455"
EMOJI_BACK     = "5906771962734057347"
EMOJI_GOLD     = "5278467510604160626"
EMOJI_SUCCESS  = "5206607081334906820"
EMOJI_WARN     = "5287231198098117669"
EMOJI_STATS    = "5231200819986047254"
EMOJI_SELL     = "5429651785352501917"
EMOJI_BUY      = "5206607081334906820"
EMOJI_WITHDRAW = "5443127283898405358"
EMOJI_BUY = "5449683594425410231"
EMOJI_SELL = "5447183459602669338"
EMOJI_VIV = "5445355530111437729"
EMOJI_TAKE = "5206607081334906820"
EMOJI_REJECT = "5210952531676504517"

# ── Инжектируемые зависимости ───────────────────────────────
is_owner_fn  = lambda mid, uid: True
set_owner_fn = lambda mid, uid: None
_bot_ref: Optional[Bot] = None
ADMIN_IDS: list[int] = []


def set_bot_ref(b: Bot):
    global _bot_ref
    _bot_ref = b


def set_admin_ids(ids: list[int]):
    global ADMIN_IDS
    ADMIN_IDS = ids


# ── FSM ─────────────────────────────────────────────────────
class SellStates(StatesGroup):
    waiting_amount   = State()
    waiting_price    = State()
    waiting_duration = State()
    confirm          = State()


class WithdrawStates(StatesGroup):
    waiting_amount = State()
    confirm        = State()


class FilterStates(StatesGroup):
    waiting_px = State()


# ── Router ──────────────────────────────────────────────────
exchange_router = Router()


# ── CryptoPay ───────────────────────────────────────────────
async def _cryptopay_request(method: str, payload: dict) -> dict:
    token = os.getenv("CRYPTOBOT_TOKEN", "")
    if not token:
        raise RuntimeError("CRYPTOBOT_TOKEN не задан в .env!")
    headers = {"Crypto-Pay-API-Token": token}
    url = f"{CRYPTOBOT_API_URL}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            return await resp.json()


async def _create_invoice(amount_usd: float, description: str, payload: str) -> dict:
    result = await _cryptopay_request("createInvoice", {
        "currency_type": "fiat", "fiat": "USD",
        "amount": str(round(amount_usd, 2)),
        "description": description, "payload": payload,
        "paid_btn_name": "callback", "paid_btn_url": "https://t.me/",
        "allow_comments": False, "allow_anonymous": False,
    })
    if result.get("ok"):
        return result["result"]
    raise RuntimeError(f"CryptoPay createInvoice: {result}")


async def _check_invoice(invoice_id: int) -> str:
    result = await _cryptopay_request("getInvoices", {"invoice_ids": str(invoice_id)})
    if result.get("ok"):
        items = result["result"].get("items", [])
        if items:
            return items[0].get("status", "active")
    return "active"


async def _create_withdraw_check(amount_usd: float) -> str:
    result = await _cryptopay_request("createCheck", {
        "asset": "USDT", "amount": str(round(amount_usd, 2)),
    })
    if result.get("ok"):
        return result["result"]["bot_check_url"]
    raise RuntimeError(f"CryptoPay createCheck: {result}")


# ── Хелперы ─────────────────────────────────────────────────
def _lot_price_range(amount_px: float) -> tuple[float, float]:
    p_max = round(PRICE_PER_10K_MIN * (amount_px / 10_000), 2)
    p_max = max(p_max, PRICE_PER_10K_MIN)
    return PRICE_PER_10K_MIN, p_max


def _lot_total_price(lot: dict) -> float:
    """Итоговая цена лота = price_per_10k * px_amount / 10000."""
    return round(lot["price_per_10k"] * (lot["px_amount"] / 10_000), 2)


def _seller_earn(lot_price: float) -> float:
    return round(lot_price * (1 - COMMISSION_SELL), 4)


async def _edit_or_send(
    chat_id: int, msg_id: Optional[int],
    text: str, keyboard: InlineKeyboardMarkup,
) -> Optional[int]:
    if _bot_ref is None:
        logger.error("_bot_ref is None — set_bot_ref() не был вызван!")
        return msg_id
    if msg_id:
        try:
            await _bot_ref.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text,
                reply_markup=keyboard, parse_mode=ParseMode.HTML,
            )
            return msg_id
        except Exception as e:
            logger.debug("edit_message_text failed (%s), sending new", e)
    sent = await _bot_ref.send_message(
        chat_id, text, reply_markup=keyboard, parse_mode=ParseMode.HTML
    )
    return sent.message_id


# ── Клавиатуры ──────────────────────────────────────────────
def _kb_exchange_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Купить",     callback_data="ex_buy_0_-1", icon_custom_emoji_id=EMOJI_BUY),
            InlineKeyboardButton(text="Продать",    callback_data="ex_sell_start", icon_custom_emoji_id=EMOJI_SELL),
        ],
        [
            InlineKeyboardButton(text="Вывод",      callback_data="ex_withdraw", icon_custom_emoji_id=EMOJI_VIV),
            InlineKeyboardButton(text="Статистика", callback_data="ex_stats", icon_custom_emoji_id=EMOJI_STATS),
        ],
        [
            InlineKeyboardButton(
                text="Назад", callback_data="main_menu",
                icon_custom_emoji_id=EMOJI_BACK
            ),
        ],
    ])


def _kb_back_exchange() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Назад", callback_data="exchange",
            icon_custom_emoji_id=EMOJI_BACK
        )
    ]])


def _kb_cancel_sell() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Отмена", callback_data="exchange",
            icon_custom_emoji_id=EMOJI_BACK
        )
    ]])


def _kb_duration() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7 дней",  callback_data="ex_dur_7"),
            InlineKeyboardButton(text="14 дней", callback_data="ex_dur_14"),
            InlineKeyboardButton(text="30 дней", callback_data="ex_dur_30"),
        ],
        [InlineKeyboardButton(
            text="Отмена", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK
        )],
    ])


def _kb_confirm_sell() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Подтвердить", callback_data="ex_sell_confirm", icon_custom_emoji_id=EMOJI_TAKE),
            InlineKeyboardButton(text="Отмена",       callback_data="exchange", icon_custom_emoji_id=EMOJI_REJECT),
        ],
    ])


def _kb_listings(listings: list, page: int, total_pages: int, range_idx: int) -> InlineKeyboardMarkup:
    """
    range_idx: -1 = все лоты, 0-3 = конкретный диапазон PX_RANGES.
    4 кнопки диапазона всегда видны под списком; активный помечен ✅.
    """
    rows = []
    for lot in listings:
        lot_price = _lot_total_price(lot)
        label = f'{int(lot["px_amount"]):,} Px → ${lot_price:.2f}'
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f'ex_lot_{lot["id"]}_{range_idx}'
        )])
    # Навигация по страницам
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_buy_{page - 1}_{range_idx}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_buy_{page + 1}_{range_idx}"))
    if nav:
        rows.append(nav)
    # 4 кнопки диапазона (2+2)
    range_row1 = []
    range_row2 = []
    for i, (label, _, _) in enumerate(PX_RANGES):
        mark = "✅ " if i == range_idx else ""
        btn = InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"ex_range_{i}")
        if i < 2:
            range_row1.append(btn)
        else:
            range_row2.append(btn)
    rows.append(range_row1)
    rows.append(range_row2)
    rows.append([InlineKeyboardButton(
        text="Назад", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_lot_detail(lot_id: int, range_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Купить", callback_data=f"ex_buy_lot_{lot_id}"),
            InlineKeyboardButton(
                text="Назад", callback_data=f"ex_buy_0_{range_idx}", icon_custom_emoji_id=EMOJI_BACK
            ),
        ],
    ])


def _kb_cancel_withdraw() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Отмена", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK
        )
    ]])


def _kb_confirm_withdraw() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=" Подать заявку", callback_data="ex_withdraw_confirm", icon_custom_emoji_id=EMOJI_TAKE),
            InlineKeyboardButton(text=" Отмена",         callback_data="exchange", icon_custom_emoji_id=EMOJI_REJECT),
        ],
    ])


def _kb_stats() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💹Мои лоты", callback_data="ex_my_lots_0")],
        [InlineKeyboardButton(
            text="Назад", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK
        )],
    ])


def _kb_my_lots(lots: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    for lot in lots:
        lot_price = _lot_total_price(lot)
        label = f'#{lot["id"]} · {int(lot["px_amount"]):,} Px · ${lot_price:.2f}'
        rows.append([InlineKeyboardButton(
            text=label, callback_data=f'ex_my_lot_{lot["id"]}'
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_my_lots_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_my_lots_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        text="Назад", callback_data="ex_stats", icon_custom_emoji_id=EMOJI_BACK
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_my_lot_detail(lot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Отменить лот", callback_data=f"ex_cancel_lot_{lot_id}")],
        [InlineKeyboardButton(
            text="Назад", callback_data="ex_my_lots_0", icon_custom_emoji_id=EMOJI_BACK
        )],
    ])


def _kb_confirm_cancel_lot(lot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да, отменить", callback_data=f"ex_cancel_lot_ok_{lot_id}", icon_custom_emoji_id=EMOJI_TAKE),
            InlineKeyboardButton(text="Нет",           callback_data=f"ex_my_lot_{lot_id}", icon_custom_emoji_id=EMOJI_REJECT),
        ],
    ])


# ── Тексты ──────────────────────────────────────────────────
def _text_main(uid: int) -> str:
    usd = db_get_usd_balance(uid)
    px  = db_get_px(uid)
    return (
        f'<tg-emoji emoji-id="{EMOJI_EXCHANGE}">💱</tg-emoji> <b>Биржа PixelX</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5197434882321567830">💱</tg-emoji>  <b>Баланс $:</b> <code>${usd:.2f}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  '
        f'<b>Баланс Px:</b> <code>{px:,.0f} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<b><tg-emoji emoji-id="5429651785352501917">💱</tg-emoji>Покупка $-Px <code>50000-1mlrd Px</code></b>\n'
        f'<b><tg-emoji emoji-id="5429518319243775957">💱</tg-emoji>Продажа Px-$ <code>50000-1mlrd Px</code></b>\n'
        f'</blockquote>\n'
        f'<blockquote><b><tg-emoji emoji-id="5197288647275071607">💱</tg-emoji> Надёжность превыше всего</b></blockquote>\n'
        f'<blockquote><b><tg-emoji emoji-id="5201691993775818138">💱</tg-emoji>Ваша безопасность — наш приоритет! Средства хранятся в защищенном резерве до завершения сделки!</b></blockquote>'
    )


# ════════════════════════════════════════════════════════════
#  ВХОД НА БИРЖУ
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data == "exchange")
async def cb_exchange(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    await state.clear()
    uid = call.from_user.id
    db_get_or_create_user(call.from_user)
    await call.message.edit_text(_text_main(uid), reply_markup=_kb_exchange_main())
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  ПРОДАЖА — шаг 1: количество Px
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data == "ex_sell_start")
async def cb_sell_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid = call.from_user.id
    active_count = db_count_active_listings_by_seller(uid)
    if active_count >= MAX_ACTIVE_LOTS:
        await call.answer(
            f"❌ Лимит: максимум {MAX_ACTIVE_LOTS} активных лотов!",
            show_alert=True
        )
        return

    await state.set_state(SellStates.waiting_amount)
    await state.update_data(sell_msg_id=call.message.message_id)
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Продажа Px</b>\n\n'
        f'<blockquote>'
        f'<b><tg-emoji emoji-id="5197269100878907942">💱</tg-emoji>Введите количество Px для продажи:</b>'
        f'</blockquote>\n'
        f'<blockquote>' 
        f'<b><tg-emoji emoji-id="5429518319243775957">💱</tg-emoji>Минимум: {SELL_MIN_PX:,} Px</b>\n'
        f'<b><tg-emoji emoji-id="5429651785352501917">💱</tg-emoji>Максимум: {SELL_MAX_PX:,} Px</b>\n\n'
        f'</blockquote>',
        reply_markup=_kb_cancel_sell(),
    )
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@exchange_router.message(SellStates.waiting_amount)
async def handle_sell_amount(message: Message, state: FSMContext):
    uid = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass
    data        = await state.get_data()
    sell_msg_id = data.get("sell_msg_id")

    raw = (message.text or "").strip().replace(",", "").replace(" ", "").replace("_", "")
    try:
        amount = float(raw)
    except ValueError:
        return

    async def _err(text: str):
        new_mid = await _edit_or_send(uid, sell_msg_id, text, _kb_cancel_sell())
        if new_mid and new_mid != sell_msg_id:
            await state.update_data(sell_msg_id=new_mid)
            set_owner_fn(new_mid, uid)

    if amount < SELL_MIN_PX:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Минимум {SELL_MIN_PX:,} Px!</b>\n\nВведите другое количество:'
        )
        return
    if amount > SELL_MAX_PX:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Максимум {SELL_MAX_PX:,} Px!</b>\n\nВведите другое количество:'
        )
        return
    if db_get_px(uid) < amount:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно Px на балансе!</b>\n\nВведите другое количество:'
        )
        return

    # Повторная проверка лимита лотов
    active_count = db_count_active_listings_by_seller(uid)
    if active_count >= MAX_ACTIVE_LOTS:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Лимит активных лотов ({MAX_ACTIVE_LOTS}) достигнут!</b>\n'
            f'Дождитесь продажи или отмените существующий лот.'
        )
        await state.clear()
        return

    p_min, p_max = _lot_price_range(amount)
    await state.update_data(sell_amount=amount)
    await state.set_state(SellStates.waiting_price)

    new_mid = await _edit_or_send(
        uid, sell_msg_id,
        f'<tg-emoji emoji-id="5197434882321567830">📤</tg-emoji> <b>Цена лота</b>\n\n'
        f'<blockquote>'
        f'<b><tg-emoji emoji-id="5325547803936572038">📤</tg-emoji>Количество: {amount:,.0f} Px</b>\n\n'
        f'<b><tg-emoji emoji-id="5197269100878907942">📤</tg-emoji>Введите итоговую цену лота в $:</b>\n'
        f'От <b>${p_min:.2f}</b> до <b>${p_max:.2f}</b>'
        f'</blockquote>',
        _kb_cancel_sell(),
    )
    await state.update_data(sell_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


# ════════════════════════════════════════════════════════════
#  ПРОДАЖА — шаг 2: цена лота
# ════════════════════════════════════════════════════════════
@exchange_router.message(SellStates.waiting_price)
async def handle_sell_price(message: Message, state: FSMContext):
    uid = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass
    data        = await state.get_data()
    sell_msg_id = data.get("sell_msg_id")
    amount      = data.get("sell_amount", 0)

    raw = (message.text or "").strip().replace(",", ".")
    try:
        lot_price = float(raw)
    except ValueError:
        return

    p_min, p_max = _lot_price_range(amount)
    eps = 0.001
    if not (p_min - eps <= lot_price <= p_max + eps):
        new_mid = await _edit_or_send(
            uid, sell_msg_id,
            f'<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji> '
            f'<b>Цена вне диапазона!</b>\n\n'
            f'Введите от <b>${p_min:.2f}</b> до <b>${p_max:.2f}</b>:',
            _kb_cancel_sell(),
        )
        await state.update_data(sell_msg_id=new_mid)
        set_owner_fn(new_mid, uid)
        return

    lot_price     = max(p_min, min(lot_price, p_max))
    price_per_10k = round(lot_price / (amount / 10_000), 4)
    earn          = _seller_earn(lot_price)

    await state.update_data(sell_lot_price=lot_price, sell_price_per_10k=price_per_10k)
    await state.set_state(SellStates.waiting_duration)

    new_mid = await _edit_or_send(
        uid, sell_msg_id,
        f'<tg-emoji emoji-id="5274055917766202507">📤</tg-emoji> <b>Срок размещения</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5325547803936572038">📤</tg-emoji>Количество: <b>{amount:,.0f} Px</b>\n'
        f'<tg-emoji emoji-id="5427168083074628963">📤</tg-emoji>Цена лота: <b>${lot_price:.2f}</b>\n'
        f'</blockquote>\n\n'
        f'<blockquote>Выберите срок размещения ниже:</blockquote>',
        _kb_duration(),
    )
    await state.update_data(sell_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


# ════════════════════════════════════════════════════════════
#  ПРОДАЖА — шаг 3: срок
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data.startswith("ex_dur_"))
async def cb_sell_duration(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    days = int(call.data.split("_")[-1])
    if days not in EXPIRE_DAYS_OPTIONS:
        await call.answer("Неверный вариант", show_alert=True)
        return

    data      = await state.get_data()
    amount    = data.get("sell_amount", 0)
    lot_price = data.get("sell_lot_price", 0)
    earn      = _seller_earn(lot_price)
    expire    = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

    await state.update_data(sell_days=days)
    await state.set_state(SellStates.confirm)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="5456140674028019486">📤</tg-emoji> <b>Подтверждение продажи</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5325547803936572038">📤</tg-emoji>  Количество: <b>{amount:,.0f} Px</b>\n'
        f'<tg-emoji emoji-id="5427168083074628963">📤</tg-emoji>  Цена лота: <b>${lot_price:.2f}</b>\n'
        f'<tg-emoji emoji-id="5274055917766202507">📤</tg-emoji>  Срок: <b>{days} дн.</b> (до {expire})\n\n'
        f'<tg-emoji emoji-id="5274099962655816924">📤</tg-emoji>  Px спишутся с баланса немедленно!'
        f'</blockquote>',
        reply_markup=_kb_confirm_sell(),
    )
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  ПРОДАЖА — шаг 4: создание лота
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data == "ex_sell_confirm")
async def cb_sell_confirm(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    data          = await state.get_data()
    uid           = call.from_user.id
    amount        = data.get("sell_amount")
    price_per_10k = data.get("sell_price_per_10k")
    lot_price     = data.get("sell_lot_price")
    days          = data.get("sell_days")

    if not all([amount, price_per_10k, lot_price, days]):
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        await state.clear()
        return

    # Финальная проверка лимита перед созданием
    active_count = db_count_active_listings_by_seller(uid)
    if active_count >= MAX_ACTIVE_LOTS:
        await call.message.edit_text(
            f'<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji> '
            f'<b>Лимит активных лотов ({MAX_ACTIVE_LOTS}) достигнут!</b>',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    if not db_try_spend_px(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji> '
            f'<b>Недостаточно Px на балансе!</b>',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    uname      = call.from_user.username or call.from_user.first_name
    listing_id = db_create_listing(
        seller_id=uid, seller_name=uname,
        px_amount=amount, price_per_10k=price_per_10k, days=days,
    )
    await state.clear()

    earn = _seller_earn(lot_price)
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Лот создан!</b>\n\n'
        f'<blockquote>'
        f'🆔  ID: <code>#{listing_id}</code>\n'
        f'<tg-emoji emoji-id="5427168083074628963">⚠️</tg-emoji>  {amount:,.0f} Px за ${lot_price:.2f}\n'
        f'<tg-emoji emoji-id="5456140674028019486">⚠️</tg-emoji>  Активен {days} дней'
        f'</blockquote>',
        reply_markup=_kb_back_exchange(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.callback_query(F.data.startswith("ex_range_"))
async def cb_select_range(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    # range_idx — индекс в PX_RANGES (0..3)
    range_idx = int(call.data.split("_")[-1])
    uid = call.from_user.id
    await _show_buy_list(call, uid, page=0, range_idx=range_idx)


# ════════════════════════════════════════════════════════════
#  ПОКУПКА — список лотов
# ════════════════════════════════════════════════════════════
async def _show_buy_list(call: CallbackQuery, uid: int, page: int, range_idx: int):
    """
    Хелпер: рендерит список лотов.
    range_idx = -1 → все лоты (фильтр не активен).
    range_idx = 0..3 → конкретный диапазон из PX_RANGES.
    """
    if range_idx >= 0:
        label, px_min, px_max = PX_RANGES[range_idx]
        range_label = label
    else:
        px_min, px_max = 0, 10_000_000_000
        range_label = "Все"

    all_lots    = db_get_active_listings(exclude_uid=uid, px_min=px_min, px_max=px_max)
    total_pages = max(1, (len(all_lots) + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))
    chunk       = all_lots[page * LISTINGS_PER_PAGE:(page + 1) * LISTINGS_PER_PAGE]

    # Строим клавиатуру с 4 кнопками диапазона внизу (даже если лотов нет)
    kb = _kb_listings(chunk, page, total_pages, range_idx)

    if not all_lots:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5231200819986047254">⚠️</tg-emoji>Диапазон: <b>{range_label}</b>\n\n'
            f'Лотов в этом диапазоне нет. Выберите другой!'
            f'</blockquote>'
        )
    else:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>'
            f'<tg-emoji emoji-id="5231200819986047254">⚠️</tg-emoji>Диапазон: <b>{range_label}</b>\n'
            f'Лотов: <b>{len(all_lots)}</b>\n'
            f'<tg-emoji emoji-id="5282843764451195532">⚠️</tg-emoji>Страница: <b>{page + 1} / {total_pages}</b>\n\n'
            f'Нажмите на лот для подробностей:'
            f'</blockquote>'
        )

    await call.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.callback_query(F.data.regexp(r'^ex_buy_\d+_-?\d+$'))
async def cb_buy_list(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid = call.from_user.id
    # формат: ex_buy_{page}_{range_idx}  где range_idx может быть -1
    # split даёт ['ex', 'buy', page, range_idx] или ['ex', 'buy', page, '-1'] -> нужно join last
    raw = call.data[len("ex_buy_"):]        # "0_-1" или "0_2"
    p_str, r_str = raw.rsplit("_", 1)
    page      = int(p_str)
    range_idx = int(r_str)
    await _show_buy_list(call, uid, page, range_idx)


# ════════════════════════════════════════════════════════════
#  ПОКУПКА — детали лота
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data.regexp(r'^ex_lot_\d+_\d+$'))
async def cb_lot_detail(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    parts     = call.data.split("_")
    lot_id    = int(parts[2])
    range_idx = int(parts[3]) if len(parts) > 3 else 0
    uid       = call.from_user.id
    lot       = db_get_listing(lot_id)

    if not lot or lot["status"] != "active":
        await call.answer("❌ Лот уже недоступен!", show_alert=True)
        return
    if lot["seller_id"] == uid:
        await call.answer("❌ Это ваш лот!", show_alert=True)
        return

    stats     = db_get_seller_stats(lot["seller_id"])
    lot_price = _lot_total_price(lot)
    expire    = datetime.fromisoformat(lot["expires_at"]).strftime("%d.%m.%Y")

    await call.message.edit_text(
        f'<tg-emoji emoji-id="5402186569006210455">💸</tg-emoji> <b>Лот #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5906581476639513176">⚠️</tg-emoji>  Продавец: <b>@{lot["seller_name"]}</b>\n'
        f'<tg-emoji emoji-id="5429518319243775957">⚠️</tg-emoji>  Продаж: <b>{stats["total_sales"]}</b>\n'
        f'<tg-emoji emoji-id="5429651785352501917">⚠️</tg-emoji>  Выручка продавца (итого): <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5400362079783770689">⚠️</tg-emoji>  Px: <b>{int(lot["px_amount"]):,} Px</b>\n'
        f'<tg-emoji emoji-id="5427168083074628963">⚠️</tg-emoji>  <b>Цена лота: ${lot_price:.2f}</b>\n'
        f'<tg-emoji emoji-id="5456140674028019486">⚠️</tg-emoji>  Активен до: {expire}'
        f'</blockquote>',
        reply_markup=_kb_lot_detail(lot_id, range_idx),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  ПОКУПКА — создание инвойса (с защитой от дублей)
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data.startswith("ex_buy_lot_"))
async def cb_buy_lot(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    lot_id = int(call.data.split("_")[-1])
    uid    = call.from_user.id
    lot    = db_get_listing(lot_id)

    if not lot or lot["status"] != "active":
        await call.answer("❌ Лот уже недоступен!", show_alert=True)
        return
    if lot["seller_id"] == uid:
        await call.answer("❌ Нельзя купить собственный лот!", show_alert=True)
        return

    lot_price  = _lot_total_price(lot)

    try:
        invoice = await _create_invoice(
            amount_usd  = lot_price,
            description = f'PixelX: {int(lot["px_amount"]):,} Px (лот #{lot_id})',
            payload     = f'buy:{lot_id}:{uid}',
        )
    except Exception as e:
        logger.error("Invoice error: %s", e)
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Ошибка создания счёта.</b> Попробуйте позже.',
            reply_markup=_kb_back_exchange(),
        )
        return

    invoice_id = invoice["invoice_id"]
    pay_url    = invoice["bot_invoice_url"]
    buyer_name = call.from_user.username or call.from_user.first_name

    db_create_invoice_record(
        invoice_id=invoice_id, lot_id=lot_id,
        buyer_id=uid, buyer_name=buyer_name, amount_usd=lot_price,
    )

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Оплата лота #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'📦  {int(lot["px_amount"]):,} Px\n'
        f'💵  К оплате: <b>${lot_price:.2f}</b>\n\n'
        f'Нажмите кнопку и оплатите счёт.\n'
        f'Счёт действителен <b>10 минут</b>.'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
            [InlineKeyboardButton(
                text="Назад", callback_data="ex_buy_0_0", icon_custom_emoji_id=EMOJI_BACK
            )],
        ]),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()

    asyncio.create_task(_poll_invoice(
        invoice_id=invoice_id, lot_id=lot_id,
        buyer_id=uid, buyer_name=buyer_name,
        chat_id=uid, msg_id=call.message.message_id,
        total_usd=lot_price,
    ))


# ════════════════════════════════════════════════════════════
#  Поллинг инвойса — атомарная защита от дублей
# ════════════════════════════════════════════════════════════
async def _poll_invoice(
    invoice_id: int, lot_id: int,
    buyer_id: int, buyer_name: str,
    chat_id: int, msg_id: int, total_usd: float,
):
    deadline = time.monotonic() + INVOICE_MAX_SECS
    while time.monotonic() < deadline:
        await asyncio.sleep(INVOICE_POLL_SECS)
        try:
            status = await _check_invoice(invoice_id)
        except Exception as e:
            logger.warning("Poll error: %s", e)
            continue

        if status == "paid":
            # Атомарно помечаем лот проданным — защита от race condition/дублей
            sold = db_mark_listing_sold_atomic(lot_id, buyer_id)
            if not sold:
                # Лот уже куплен другим или снят
                try:
                    await _bot_ref.send_message(
                        chat_id,
                        f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
                        f'<b>Лот #{lot_id} уже недоступен.</b>\n'
                        f'Обратитесь в поддержку для возврата средств.',
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
                return

            lot = db_get_listing(lot_id)
            db_mark_invoice_paid(invoice_id)

            seller_earn = round(total_usd * (1 - COMMISSION_SELL), 4)
            db_add_usd(lot["seller_id"], seller_earn)
            db_add_px(buyer_id, lot["px_amount"])

            # Уведомление продавцу
            try:
                await _bot_ref.send_message(
                    lot["seller_id"],
                    f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                    f'<b>Лот #{lot_id} куплен!</b>\n\n'
                    f'<blockquote>'
                    f'👤  Покупатель: @{buyer_name}\n'
                    f'📦  Продано: {int(lot["px_amount"]):,} Px\n'
                    f'💵  Зачислено: <b>${seller_earn:.2f}</b>'
                    f'</blockquote>',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            try:
                await _bot_ref.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=(
                        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                        f'<b>Покупка успешна!</b>\n\n'
                        f'<blockquote>'
                        f'📦  Зачислено: <b>{int(lot["px_amount"]):,} Px</b>\n'
                        f'💵  Оплачено: ${total_usd:.2f}'
                        f'</blockquote>'
                    ),
                    reply_markup=_kb_back_exchange(),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        elif status in ("expired", "cancelled"):
            break

    try:
        await _bot_ref.send_message(
            chat_id,
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Время оплаты истекло.</b> Счёт для лота #{lot_id} аннулирован.',
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  МОИ ЛОТЫ
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data.startswith("ex_my_lots_"))
async def cb_my_lots(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid  = call.from_user.id
    page = int(call.data.split("_")[-1])

    lots        = db_get_my_listings(uid)
    total_pages = max(1, (len(lots) + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))
    chunk       = lots[page * LISTINGS_PER_PAGE:(page + 1) * LISTINGS_PER_PAGE]

    if not lots:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Мои лоты</b>\n\n'
            f'<blockquote>У вас нет активных лотов.\nЛимит: {MAX_ACTIVE_LOTS} лотов</blockquote>'
        )
        await call.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="Назад", callback_data="ex_stats", icon_custom_emoji_id=EMOJI_BACK
                )
            ]])
        )
    else:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Мои лоты</b>\n\n'
            f'<blockquote>'
            f'Активных: <b>{len(lots)} / {MAX_ACTIVE_LOTS}</b>\n'
            f'Страница: <b>{page + 1} / {total_pages}</b>\n\n'
            f'Нажмите на лот для управления:'
            f'</blockquote>'
        )
        await call.message.edit_text(text, reply_markup=_kb_my_lots(chunk, page, total_pages))

    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.callback_query(F.data.startswith("ex_my_lot_"))
async def cb_my_lot_detail(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    lot_id = int(call.data.split("_")[-1])
    uid    = call.from_user.id
    lot    = db_get_listing(lot_id)

    if not lot or lot["seller_id"] != uid or lot["status"] != "active":
        await call.answer("❌ Лот не найден или уже неактивен!", show_alert=True)
        return

    lot_price  = _lot_total_price(lot)
    refund     = round(lot["px_amount"] * (1 - COMMISSION_CANCEL))
    expire     = datetime.fromisoformat(lot["expires_at"]).strftime("%d.%m.%Y")

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Лот #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'📦  Количество: <b>{int(lot["px_amount"]):,} Px</b>\n'
        f'🏷  Цена лота: <b>${lot_price:.2f}</b>\n'
        f'⏳  Активен до: {expire}'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'⚠️  При отмене — штраф <b>5%</b>:\n'
        f'🔙  Возврат: <b>{int(refund):,} Px</b>\n'
        f'💸  Штраф: <b>{int(lot["px_amount"] - refund):,} Px</b>'
        f'</blockquote>',
        reply_markup=_kb_my_lot_detail(lot_id),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.callback_query(F.data.startswith("ex_cancel_lot_") & ~F.data.startswith("ex_cancel_lot_ok_"))
async def cb_cancel_lot_ask(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    lot_id = int(call.data.split("_")[-1])
    uid    = call.from_user.id
    lot    = db_get_listing(lot_id)

    if not lot or lot["seller_id"] != uid or lot["status"] != "active":
        await call.answer("❌ Лот не найден!", show_alert=True)
        return

    refund = round(lot["px_amount"] * (1 - COMMISSION_CANCEL))
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> <b>Подтвердите отмену</b>\n\n'
        f'<blockquote>'
        f'Лот <b>#{lot_id}</b> будет удалён.\n'
        f'Вам вернётся <b>{int(refund):,} Px</b> (штраф 5%).\n\n'
        f'Вы уверены?'
        f'</blockquote>',
        reply_markup=_kb_confirm_cancel_lot(lot_id),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.callback_query(F.data.startswith("ex_cancel_lot_ok_"))
async def cb_cancel_lot_confirm(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    lot_id = int(call.data.split("_")[-1])
    uid    = call.from_user.id
    lot    = db_get_listing(lot_id)

    if not lot or lot["seller_id"] != uid:
        await call.answer("❌ Лот не найден!", show_alert=True)
        return

    cancelled = db_cancel_listing_by_owner(lot_id, uid)
    if not cancelled:
        await call.answer("❌ Лот уже неактивен!", show_alert=True)
        return

    refund = round(lot["px_amount"] * (1 - COMMISSION_CANCEL))
    db_add_px(uid, refund)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Лот #{lot_id} отменён</b>\n\n'
        f'<blockquote>'
        f'🔙  Возвращено: <b>{int(refund):,} Px</b>\n'
        f'💸  Удержан штраф: <b>{int(lot["px_amount"] - refund):,} Px</b>'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Назад", callback_data="ex_my_lots_0", icon_custom_emoji_id=EMOJI_BACK
            )
        ]])
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  ВЫВОД
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data == "ex_withdraw")
async def cb_withdraw(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    uid = call.from_user.id
    bal = db_get_usd_balance(uid)
    await state.set_state(WithdrawStates.waiting_amount)
    await state.update_data(withdraw_msg_id=call.message.message_id)
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> <b>Вывод средств</b>\n\n'
        f'<blockquote>'
        f'💵  Баланс $: <b>${bal:.2f}</b>\n\n'
        f'Введите сумму для вывода:\n'
        f'Минимум: <b>${WITHDRAW_MIN_USD:.2f}</b>\n'
        f'Комиссия: <b>3%</b>\n\n'
        f'⏳ Выплата после одобрения админом'
        f'</blockquote>',
        reply_markup=_kb_cancel_withdraw(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@exchange_router.message(WithdrawStates.waiting_amount)
async def handle_withdraw_amount(message: Message, state: FSMContext):
    uid = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass
    data            = await state.get_data()
    withdraw_msg_id = data.get("withdraw_msg_id")

    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        return

    async def _err(text: str):
        new_mid = await _edit_or_send(uid, withdraw_msg_id, text, _kb_cancel_withdraw())
        if new_mid and new_mid != withdraw_msg_id:
            await state.update_data(withdraw_msg_id=new_mid)
            set_owner_fn(new_mid, uid)

    if amount < WITHDRAW_MIN_USD:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Минимум ${WITHDRAW_MIN_USD:.2f}!</b>\n\nВведите сумму:'
        )
        return

    bal = db_get_usd_balance(uid)
    if amount > bal:
        await _err(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно средств!</b>\n'
            f'Баланс: <b>${bal:.2f}</b>\n\nВведите сумму:'
        )
        return

    net = round(amount * (1 - COMMISSION_WITHDRAW), 4)
    await state.update_data(withdraw_amount=amount, withdraw_net=net)
    await state.set_state(WithdrawStates.confirm)

    new_mid = await _edit_or_send(
        uid, withdraw_msg_id,
        f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> <b>Подтверждение заявки</b>\n\n'
        f'<blockquote>'
        f'💵  Выводите: <b>${amount:.2f}</b>\n'
        f'📉  Комиссия (3%): <b>${amount * COMMISSION_WITHDRAW:.2f}</b>\n'
        f'✅  К получению: <b>${net:.2f}</b>\n\n'
        f'⏳  Заявка будет одобрена администратором.\n'
        f'После одобрения придёт чек CryptoBot (USDT).'
        f'</blockquote>',
        _kb_confirm_withdraw(),
    )
    await state.update_data(withdraw_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


@exchange_router.callback_query(F.data == "ex_withdraw_confirm")
async def cb_withdraw_confirm(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    data   = await state.get_data()
    uid    = call.from_user.id
    amount = data.get("withdraw_amount")
    net    = data.get("withdraw_net")

    if not amount:
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        await state.clear()
        return

    if not db_try_spend_usd(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно средств!</b>',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    uname  = call.from_user.username or call.from_user.first_name
    req_id = db_create_withdraw_request(uid, uname, amount, net)
    await state.clear()

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Заявка подана!</b>\n\n'
        f'<blockquote>'
        f'🆔  Заявка: <code>#{req_id}</code>\n'
        f'💵  Сумма: <b>${amount:.2f}</b>\n'
        f'✅  К получению: <b>${net:.2f}</b>\n\n'
        f'⏳  Ожидайте одобрения администратора.\n'
        f'После одобрения вам придёт чек CryptoBot.'
        f'</blockquote>',
        reply_markup=_kb_back_exchange(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  СТАТИСТИКА (продажи + покупки)
# ════════════════════════════════════════════════════════════
@exchange_router.callback_query(F.data == "ex_stats")
async def cb_ex_stats(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid    = call.from_user.id
    stats  = db_get_seller_stats(uid)
    bstats = db_get_buyer_stats(uid)
    wstats = db_get_withdraw_stats(uid)
    bal    = db_get_usd_balance(uid)
    active = db_count_active_listings_by_seller(uid)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Биржа — Статистика</b>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5197434882321567830">💱</tg-emoji>  Баланс $: <b>${bal:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5429518319243775957">💱</tg-emoji>  <b>Продажи</b>\n'
        f'<tg-emoji emoji-id="5456140674028019486">💱</tg-emoji>Лотов активно: <b>{active} / {MAX_ACTIVE_LOTS}</b>\n'
        f'<tg-emoji emoji-id="5447183459602669338">💱</tg-emoji>Продаж: <b>{stats["total_sales"]}</b>\n'
        f'<tg-emoji emoji-id="5244837092042750681">💱</tg-emoji>Заработано: <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5429651785352501917">💱</tg-emoji>  <b>Покупки</b>\n'
        f'<tg-emoji emoji-id="5449683594425410231">💱</tg-emoji>Куплено лотов: <b>{bstats["total_buys"]}</b>\n'
        f'<tg-emoji emoji-id="5397916757333654639">💱</tg-emoji>Получено Px: <b>{int(bstats["total_px_received"]):,} Px</b>\n'
        f'<tg-emoji emoji-id="5246762912428603768">💱</tg-emoji>Потрачено всего: <b>${bstats["total_spent"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'<tg-emoji emoji-id="5231200819986047254">💱</tg-emoji>  Выводов всего: <b>{wstats["count"]}</b>\n'
        f'<tg-emoji emoji-id="5445355530111437729">💱</tg-emoji>  Выведено всего: <b>${wstats["total"]:.2f}</b>'
        f'</blockquote>\n'
        f'<b><i><tg-emoji emoji-id="5386367538735104399">💱</tg-emoji>Статистика обновляется в реальном времени!</i></b>',
        reply_markup=_kb_stats(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  АДМИН — /check
# ════════════════════════════════════════════════════════════
@exchange_router.message(Command("check"))
async def cmd_check(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            '<b>Использование:</b>\n'
            '<code>/check @username</code>\n'
            '<code>/check 123456789</code>'
        )
        return

    target_raw = args[1].strip()

    from database import get_conn as _main_get_conn
    with _main_get_conn() as conn:
        if target_raw.lstrip('-').isdigit():
            row = conn.execute(
                "SELECT * FROM users WHERE id=?", (int(target_raw),)
            ).fetchone()
        else:
            uname = target_raw.lstrip('@')
            row = conn.execute(
                "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (uname,)
            ).fetchone()

    if not row:
        await message.answer(f'❌ Пользователь <code>{target_raw}</code> не найден.')
        return

    row         = dict(row)
    target_uid  = row["id"]
    display     = f'@{row["username"]}' if row["username"] else row["first_name"]
    usd_bal     = db_get_usd_balance(target_uid)
    sales       = db_get_seller_last_sales(target_uid, limit=5)
    bstats      = db_get_buyer_stats(target_uid)
    active      = db_count_active_listings_by_seller(target_uid)

    lines = [
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> '
        f'<b>Биржа: {display}</b> (ID: <code>{target_uid}</code>)\n\n'
        f'<blockquote>'
        f'💵  Баланс $: <b>${usd_bal:.2f}</b>\n'
        f'📦  Активных лотов: <b>{active} / {MAX_ACTIVE_LOTS}</b>\n'
        f'💸  Куплено лотов: <b>{bstats["total_buys"]}</b> · '
        f'потрачено <b>${bstats["total_spent"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<b>Последние продажи ({len(sales)}):</b>'
    ]
    if not sales:
        lines.append('<blockquote>Продаж нет.</blockquote>')
    else:
        for s in sales:
            lot_price = round(s["price_per_10k"] * s["px_amount"] / 10_000, 2)
            earn      = round(lot_price * 0.85, 2)
            date      = s["created_at"][:10]
            lines.append(
                f'<blockquote>'
                f'🆔 #{s["id"]} · {int(s["px_amount"]):,} Px · ${lot_price:.2f} · '
                f'получил ${earn:.2f} · {date}'
                f'</blockquote>'
            )

    await message.answer("\n".join(lines))


# ════════════════════════════════════════════════════════════
#  АДМИН — /checkw
# ════════════════════════════════════════════════════════════
@exchange_router.message(Command("checkw"))
async def cmd_checkw(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    reqs = db_get_pending_withdraw_requests()

    if not reqs:
        await message.answer(
            f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> '
            f'<b>Заявки на вывод</b>\n\n'
            f'<blockquote>Нет ожидающих заявок.</blockquote>'
        )
        return

    lines = [
        f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> '
        f'<b>Заявки на вывод ({len(reqs)} шт.):</b>\n'
    ]
    for r in reqs:
        date = r["created_at"][:16].replace("T", " ")
        lines.append(
            f'<blockquote>'
            f'🆔 <b>#{r["id"]}</b>\n'
            f'👤 @{r["username"]} (ID: <code>{r["user_id"]}</code>)\n'
            f'💵 ${r["amount_usd"]:.2f} → к выплате <b>${r["net_usd"]:.2f}</b>\n'
            f'🕐 {date}'
            f'</blockquote>'
        )
    lines.append('\n<code>/take #id</code> — одобрить · <code>/reject #id</code> — отклонить')

    await message.answer("\n".join(lines))


# ════════════════════════════════════════════════════════════
#  АДМИН — /take
# ════════════════════════════════════════════════════════════
@exchange_router.message(Command("take"))
async def cmd_take(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer('<b>Использование:</b> <code>/take #123</code>')
        return

    raw = args[1].strip().lstrip('#')
    if not raw.isdigit():
        await message.answer('❌ Укажите ID заявки: <code>/take #123</code>')
        return

    req_id = int(raw)
    req    = db_approve_withdraw_request(req_id)

    if not req:
        await message.answer(f'❌ Заявка <b>#{req_id}</b> не найдена или уже обработана.')
        return

    try:
        check_url = await _create_withdraw_check(req["net_usd"])
    except Exception as e:
        logger.error("Withdraw check error on /take: %s", e)
        db_add_usd(req["user_id"], req["amount_usd"])
        from treyd_db import get_conn as _tdb_conn
        with _tdb_conn() as conn:
            conn.execute(
                "UPDATE exchange_withdraw_requests SET status='pending', resolved_at=NULL WHERE id=?",
                (req_id,)
            )
        await message.answer(
            f'❌ Ошибка создания чека для заявки <b>#{req_id}</b>.\n'
            f'Средства возвращены, заявка снова pending.'
        )
        return

    try:
        await _bot_ref.send_message(
            req["user_id"],
            f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
            f'<b>Вывод #{req_id} одобрен!</b>\n\n'
            f'<blockquote>'
            f'💵  Списано: <b>${req["amount_usd"]:.2f}</b>\n'
            f'✅  К получению: <b>${req["net_usd"]:.2f}</b>\n\n'
            f'Нажмите на кнопку ниже для получения чека:'
            f'</blockquote>',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎁 Получить чек", url=check_url)
            ]]),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Can't send check to user %s: %s", req["user_id"], e)

    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
        f'<b>Заявка #{req_id} одобрена!</b>\n\n'
        f'<blockquote>'
        f'👤 @{req["username"]}\n'
        f'💵 ${req["amount_usd"]:.2f} → выплачено <b>${req["net_usd"]:.2f}</b>'
        f'</blockquote>'
    )


# ════════════════════════════════════════════════════════════
#  АДМИН — /reject
# ════════════════════════════════════════════════════════════
@exchange_router.message(Command("reject"))
async def cmd_reject(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer('<b>Использование:</b> <code>/reject #123</code>')
        return

    raw = args[1].strip().lstrip('#')
    if not raw.isdigit():
        await message.answer('❌ Укажите ID заявки: <code>/reject #123</code>')
        return

    req_id = int(raw)
    req    = db_reject_withdraw_request(req_id)

    if not req:
        await message.answer(f'❌ Заявка <b>#{req_id}</b> не найдена или уже обработана.')
        return

    db_add_usd(req["user_id"], req["amount_usd"])

    try:
        await _bot_ref.send_message(
            req["user_id"],
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Заявка на вывод #{req_id} отклонена</b>\n\n'
            f'<blockquote>'
            f'💵  Возвращено на баланс: <b>${req["amount_usd"]:.2f}</b>'
            f'</blockquote>',
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await message.answer(
        f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
        f'<b>Заявка #{req_id} отклонена.</b>\n\n'
        f'<blockquote>'
        f'👤 @{req["username"]}\n'
        f'💵 ${req["amount_usd"]:.2f} возвращено на баланс пользователя.'
        f'</blockquote>'
    )


# ════════════════════════════════════════════════════════════
#  Watchdog
# ════════════════════════════════════════════════════════════
async def exchange_watchdog():
    while True:
        await asyncio.sleep(600)
        try:
            expired = db_expire_listings()
            for lot in expired:
                db_add_px(lot["seller_id"], lot["px_amount"])
                try:
                    await _bot_ref.send_message(
                        lot["seller_id"],
                        f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
                        f'<b>Лот #{lot["id"]} истёк!</b>\n\n'
                        f'<blockquote>'
                        f'Срок действия лота закончился.\n'
                        f'📦  Возвращено: <b>{int(lot["px_amount"]):,} Px</b>'
                        f'</blockquote>',
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error("Exchange watchdog: %s", e)
