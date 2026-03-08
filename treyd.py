"""
treyd.py — Модуль «Биржа» для PixelX бота
==========================================
Логика:
  • Отдельный $-баланс у каждого пользователя
  • Продажа Px за $: мин 50 000 Px, макс 1 000 000 000 Px
  • Цена за 10 000 Px: от 0.20$ до 1.00$ (пропорционально объёму)
  • Время экспозиции: 7 / 14 / 30 дней
  • Покупка: 10 лотов на страницу (пагинация)
  • Оплата через CryptoBot invoice
  • Комиссия продавцу: 85% (15% биржа)
  • Вывод: мин $1, комиссия 3%, чек CryptoBot (USDT)
  • Автопроверка инвойса каждые 2 сек (до 10 мин)
  • По истечению срока — Px возвращаются продавцу (watchdog каждые 10 мин)
"""

import asyncio
import logging
import time
import os
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from aiogram import Bot, Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode

from database import (
    db_get_or_create_user,
    db_get_px,
    db_try_spend_px,
    db_add_px,
)
from treyd_db import (
    db_get_usd_balance,
    db_add_usd,
    db_try_spend_usd,
    db_create_listing,
    db_get_active_listings,
    db_get_listing,
    db_mark_listing_sold,
    db_expire_listings,
    db_get_seller_stats,
    db_create_invoice_record,
    db_mark_invoice_paid,
    db_get_withdraw_stats,
    db_record_withdraw,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
#  Конфиг
# ──────────────────────────────────────────────────────────
CRYPTOBOT_TOKEN   = os.getenv("CRYPTOBOT_TOKEN", "")
CRYPTOBOT_API_URL = "https://pay.crypt.bot/api"   # mainnet
# CRYPTOBOT_API_URL = "https://testnet-pay.crypt.bot/api"  # testnet

SELL_MIN_PX         = 50_000
SELL_MAX_PX         = 1_000_000_000
PRICE_PER_10K_MIN   = 0.20
PRICE_PER_10K_MAX   = 1.00
COMMISSION_SELL     = 0.15   # 15% от суммы покупки — биржа
COMMISSION_WITHDRAW = 0.03   # 3% при выводе
WITHDRAW_MIN_USD    = 1.00

EXPIRE_DAYS_OPTIONS = [7, 14, 30]
LISTINGS_PER_PAGE   = 10
INVOICE_POLL_SECS   = 2
INVOICE_MAX_SECS    = 600    # 10 минут ожидания оплаты

# ──────────────────────────────────────────────────────────
#  Emoji
# ──────────────────────────────────────────────────────────
EMOJI_EXCHANGE = "5402186569006210455"
EMOJI_BACK     = "5906771962734057347"
EMOJI_GOLD     = "5278467510604160626"
EMOJI_SUCCESS  = "5206607081334906820"
EMOJI_WARN     = "5287231198098117669"
EMOJI_STATS    = "5231200819986047254"
EMOJI_SELL     = "5429651785352501917"
EMOJI_BUY      = "5206607081334906820"
EMOJI_WITHDRAW = "5443127283898405358"

# ──────────────────────────────────────────────────────────
#  Инжектируемые функции (из main.py)
# ──────────────────────────────────────────────────────────
is_owner_fn  = lambda mid, uid: True
set_owner_fn = lambda mid, uid: None
_bot_ref: Optional[Bot] = None


def set_bot_ref(b: Bot):
    global _bot_ref
    _bot_ref = b


# ──────────────────────────────────────────────────────────
#  FSM состояния
# ──────────────────────────────────────────────────────────
class SellStates(StatesGroup):
    waiting_amount   = State()
    waiting_price    = State()
    waiting_duration = State()
    confirm          = State()


class WithdrawStates(StatesGroup):
    waiting_amount = State()
    confirm        = State()


# ──────────────────────────────────────────────────────────
#  Router
# ──────────────────────────────────────────────────────────
exchange_router = Router()


# ──────────────────────────────────────────────────────────
#  CryptoPay helpers
# ──────────────────────────────────────────────────────────
async def _cryptopay_request(method: str, payload: dict) -> dict:
    if not CRYPTOBOT_TOKEN:
        raise RuntimeError("CRYPTOBOT_TOKEN не задан в .env!")
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    url = f"{CRYPTOBOT_API_URL}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            return await resp.json()


async def _create_invoice(amount_usd: float, description: str, payload: str) -> dict:
    result = await _cryptopay_request("createInvoice", {
        "currency_type":   "fiat",
        "fiat":            "USD",
        "amount":          str(round(amount_usd, 2)),
        "description":     description,
        "payload":         payload,
        "paid_btn_name":   "callback",
        "paid_btn_url":    "https://t.me/",   # замените на @username вашего бота
        "allow_comments":  False,
        "allow_anonymous": False,
    })
    if result.get("ok"):
        return result["result"]
    raise RuntimeError(f"CryptoPay createInvoice: {result}")


async def _check_invoice(invoice_id: int) -> str:
    """Вернуть статус инвойса: 'active' | 'paid' | 'expired'."""
    result = await _cryptopay_request("getInvoices", {"invoice_ids": str(invoice_id)})
    if result.get("ok"):
        items = result["result"].get("items", [])
        if items:
            return items[0].get("status", "active")
    return "active"


async def _create_withdraw_check(amount_usd: float) -> str:
    """Создать чек CryptoBot. Вернуть ссылку."""
    result = await _cryptopay_request("createCheck", {
        "asset":  "USDT",
        "amount": str(round(amount_usd, 2)),
    })
    if result.get("ok"):
        return result["result"]["bot_check_url"]
    raise RuntimeError(f"CryptoPay createCheck: {result}")


# ──────────────────────────────────────────────────────────
#  Хелперы
# ──────────────────────────────────────────────────────────
def _price_range(amount_px: float) -> tuple[float, float]:
    """Диапазон цены за 10 000 Px в зависимости от объёма."""
    ratio   = min(amount_px / SELL_MAX_PX, 1.0)
    p_max   = PRICE_PER_10K_MIN + ratio * (PRICE_PER_10K_MAX - PRICE_PER_10K_MIN)
    return PRICE_PER_10K_MIN, round(p_max, 2)


def _total_price(amount_px: float, price_per_10k: float) -> float:
    return round(price_per_10k * (amount_px / 10_000), 4)


async def _edit_or_send(
    chat_id: int, msg_id: Optional[int],
    text: str, keyboard: InlineKeyboardMarkup,
) -> Optional[int]:
    """Редактировать сообщение или отправить новое. Вернуть message_id."""
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
            logger.debug("edit_message_text failed (%s), sending new message", e)
    sent = await _bot_ref.send_message(chat_id, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return sent.message_id


# ──────────────────────────────────────────────────────────
#  Клавиатуры
# ──────────────────────────────────────────────────────────
def _kb_exchange_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Купить",    callback_data="ex_buy_0"),
            InlineKeyboardButton(text="📤 Продать",   callback_data="ex_sell_start"),
        ],
        [
            InlineKeyboardButton(text="🏦 Вывод",     callback_data="ex_withdraw"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="ex_stats"),
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
        [
            InlineKeyboardButton(
                text="Отмена", callback_data="exchange",
                icon_custom_emoji_id=EMOJI_BACK
            )
        ],
    ])


def _kb_confirm_sell() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="ex_sell_confirm"),
            InlineKeyboardButton(text="❌ Отмена",       callback_data="exchange"),
        ],
    ])


def _kb_listings(listings: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    for lot in listings:
        total = _total_price(lot["px_amount"], lot["price_per_10k"])
        label = f'{int(lot["px_amount"]):,} Px → ${total:.2f}'
        rows.append([InlineKeyboardButton(text=label, callback_data=f'ex_lot_{lot["id"]}')])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_buy_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_buy_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        text="Назад", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_lot_detail(lot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Купить", callback_data=f"ex_buy_lot_{lot_id}"),
            InlineKeyboardButton(
                text="Назад", callback_data="ex_buy_0",
                icon_custom_emoji_id=EMOJI_BACK
            ),
        ],
    ])


def _kb_cancel_withdraw() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Отмена", callback_data="exchange",
            icon_custom_emoji_id=EMOJI_BACK
        )
    ]])


def _kb_confirm_withdraw() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Вывести", callback_data="ex_withdraw_confirm"),
            InlineKeyboardButton(text="❌ Отмена",  callback_data="exchange"),
        ],
    ])


# ──────────────────────────────────────────────────────────
#  Тексты
# ──────────────────────────────────────────────────────────
def _text_main(uid: int) -> str:
    usd = db_get_usd_balance(uid)
    px  = db_get_px(uid)
    return (
        f'<tg-emoji emoji-id="{EMOJI_EXCHANGE}">💱</tg-emoji> <b>Биржа PixelX</b>\n\n'
        f'<blockquote>'
        f'💵  <b>Баланс $:</b> <code>${usd:.2f}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  '
        f'<b>Баланс Px:</b> <code>{px:,.0f} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'Продавайте Px за $, покупайте лоты других игроков.\n'
        f'Комиссия: <b>15%</b> от продажи · <b>3%</b> при выводе'
        f'</blockquote>'
    )


# ──────────────────────────────────────────────────────────
#  ВХОД НА БИРЖУ
# ──────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────
#  ПРОДАЖА — шаг 1: количество Px
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data == "ex_sell_start")
async def cb_sell_start(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    await state.set_state(SellStates.waiting_amount)
    await state.update_data(sell_msg_id=call.message.message_id)
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Продажа Px</b>\n\n'
        f'<blockquote>'
        f'Введите количество Px для продажи:\n\n'
        f'Минимум: <b>{SELL_MIN_PX:,} Px</b>\n'
        f'Максимум: <b>{SELL_MAX_PX:,} Px</b>'
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

    p_min, p_max = _price_range(amount)
    await state.update_data(sell_amount=amount)
    await state.set_state(SellStates.waiting_price)

    new_mid = await _edit_or_send(
        uid, sell_msg_id,
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Цена продажи</b>\n\n'
        f'<blockquote>'
        f'Количество: <b>{amount:,.0f} Px</b>\n\n'
        f'Введите цену за <b>10 000 Px</b> в $:\n'
        f'Диапазон: <b>${p_min:.2f} — ${p_max:.2f}</b>'
        f'</blockquote>',
        _kb_cancel_sell(),
    )
    await state.update_data(sell_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


# ──────────────────────────────────────────────────────────
#  ПРОДАЖА — шаг 2: цена
# ──────────────────────────────────────────────────────────
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
        price = float(raw)
    except ValueError:
        return

    p_min, p_max = _price_range(amount)
    if not (p_min - 0.001 <= price <= p_max + 0.001):
        await _edit_or_send(
        uid, sell_msg_id,
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Цена вне диапазона!</b>\n\n'
            f'Введите цену от <b>${p_min:.2f}</b> до <b>${p_max:.2f}</b> за 10 000 Px:',
            _kb_cancel_sell(),
        )
        return

    price = max(p_min, min(price, p_max))
    total = _total_price(amount, price)
    after = total * (1 - COMMISSION_SELL)

    await state.update_data(sell_price=price)
    await state.set_state(SellStates.waiting_duration)

    new_mid = await _edit_or_send(
        uid, sell_msg_id,
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Срок размещения</b>\n\n'
        f'<blockquote>'
        f'Количество: <b>{amount:,.0f} Px</b>\n'
        f'Цена за 10 000 Px: <b>${price:.2f}</b>\n'
        f'Покупатель заплатит: <b>${total:.4f}</b>\n'
        f'Вы получите (85%): <b>${after:.4f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>Выберите срок размещения лота:</blockquote>',
        _kb_duration(),
    )
    await state.update_data(sell_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


# ──────────────────────────────────────────────────────────
#  ПРОДАЖА — шаг 3: срок
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data.startswith("ex_dur_"))
async def cb_sell_duration(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    days = int(call.data.split("_")[-1])
    if days not in EXPIRE_DAYS_OPTIONS:
        await call.answer("Неверный вариант", show_alert=True)
        return

    data   = await state.get_data()
    amount = data.get("sell_amount", 0)
    price  = data.get("sell_price", 0)
    total  = _total_price(amount, price)
    after  = total * (1 - COMMISSION_SELL)
    expire = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

    await state.update_data(sell_days=days)
    await state.set_state(SellStates.confirm)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Подтверждение продажи</b>\n\n'
        f'<blockquote>'
        f'📦  Количество: <b>{amount:,.0f} Px</b>\n'
        f'💲  Цена за 10 000 Px: <b>${price:.2f}</b>\n'
        f'🏷  Покупатель заплатит: <b>${total:.4f}</b>\n'
        f'✅  Вы получите: <b>${after:.4f}</b>\n'
        f'⏳  Срок: <b>{days} дн.</b> (до {expire})\n\n'
        f'⚠️  Px спишутся с баланса немедленно!'
        f'</blockquote>',
        reply_markup=_kb_confirm_sell(),
    )
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПРОДАЖА — шаг 4: подтверждение
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data == "ex_sell_confirm")
async def cb_sell_confirm(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    data   = await state.get_data()
    uid    = call.from_user.id
    amount = data.get("sell_amount")
    price  = data.get("sell_price")
    days   = data.get("sell_days")

    if not all([amount, price, days]):
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        await state.clear()
        return

    # Атомарное списание Px
    if not db_try_spend_px(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно Px на балансе!</b>',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    uname      = call.from_user.username or call.from_user.first_name
    listing_id = db_create_listing(
        seller_id=uid, seller_name=uname,
        px_amount=amount, price_per_10k=price, days=days,
    )
    await state.clear()

    total = _total_price(amount, price)
    after = total * (1 - COMMISSION_SELL)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Лот создан!</b>\n\n'
        f'<blockquote>'
        f'🆔  ID: <code>#{listing_id}</code>\n'
        f'📦  {amount:,.0f} Px за ${total:.4f}\n'
        f'💵  Получите после продажи: <b>${after:.4f}</b>\n'
        f'⏳  Активен {days} дней'
        f'</blockquote>',
        reply_markup=_kb_back_exchange(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПОКУПКА — список лотов
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(
    F.data.startswith("ex_buy_") & ~F.data.startswith("ex_buy_lot_")
)
async def cb_buy_list(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid  = call.from_user.id
    page = int(call.data.split("_")[-1])

    all_lots    = db_get_active_listings(exclude_uid=uid)
    total_pages = max(1, (len(all_lots) + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE)
    page        = max(0, min(page, total_pages - 1))
    chunk       = all_lots[page * LISTINGS_PER_PAGE:(page + 1) * LISTINGS_PER_PAGE]

    if not all_lots:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>Активных лотов пока нет. Загляните позже!</blockquote>'
        )
        await call.message.edit_text(text, reply_markup=_kb_back_exchange())
    else:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>'
            f'Лотов на бирже: <b>{len(all_lots)}</b>\n'
            f'Страница: <b>{page + 1} / {total_pages}</b>\n\n'
            f'Нажмите на лот для подробностей:'
            f'</blockquote>'
        )
        await call.message.edit_text(text, reply_markup=_kb_listings(chunk, page, total_pages))

    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПОКУПКА — детали лота
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data.startswith("ex_lot_"))
async def cb_lot_detail(call: CallbackQuery, state: FSMContext):
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
        await call.answer("❌ Это ваш лот!", show_alert=True)
        return

    stats  = db_get_seller_stats(lot["seller_id"])
    total  = _total_price(lot["px_amount"], lot["price_per_10k"])
    expire = datetime.fromisoformat(lot["expires_at"]).strftime("%d.%m.%Y")

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Лот #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'👤  Продавец: <b>@{lot["seller_name"]}</b>\n'
        f'🏆  Продаж: <b>{stats["total_sales"]}</b>\n'
        f'💵  Выручка продавца (итого): <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📦  Px: <b>{int(lot["px_amount"]):,} Px</b>\n'
        f'💲  Цена за 10 000 Px: <b>${lot["price_per_10k"]:.2f}</b>\n'
        f'🏷  <b>К оплате: ${total:.4f}</b>\n'
        f'⏳  Активен до: {expire}'
        f'</blockquote>',
        reply_markup=_kb_lot_detail(lot_id),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПОКУПКА — создание инвойса
# ──────────────────────────────────────────────────────────
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

    total = _total_price(lot["px_amount"], lot["price_per_10k"])

    try:
        invoice = await _create_invoice(
            amount_usd  = total,
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
        buyer_id=uid, buyer_name=buyer_name, amount_usd=total,
    )

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Оплата лота #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'📦  {int(lot["px_amount"]):,} Px\n'
        f'💵  К оплате: <b>${total:.4f}</b>\n\n'
        f'Нажмите кнопку ниже и оплатите счёт.\n'
        f'Счёт действителен <b>10 минут</b>.'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
            [InlineKeyboardButton(
                text="Назад", callback_data="ex_buy_0",
                icon_custom_emoji_id=EMOJI_BACK
            )],
        ]),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()

    # Запускаем фоновый поллинг
    asyncio.create_task(_poll_invoice(
        invoice_id=invoice_id,
        lot_id=lot_id,
        buyer_id=uid,
        buyer_name=buyer_name,
        chat_id=uid,
        msg_id=call.message.message_id,
        total_usd=total,
    ))


# ──────────────────────────────────────────────────────────
#  Фоновая проверка инвойса каждые 2 сек
# ──────────────────────────────────────────────────────────
async def _poll_invoice(
    invoice_id: int,
    lot_id:     int,
    buyer_id:   int,
    buyer_name: str,
    chat_id:    int,
    msg_id:     int,
    total_usd:  float,
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
            # Повторная проверка лота — защита от гонки
            lot = db_get_listing(lot_id)
            if not lot or lot["status"] != "active":
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

            db_mark_listing_sold(lot_id, buyer_id)
            db_mark_invoice_paid(invoice_id)

            seller_earn = total_usd * (1 - COMMISSION_SELL)
            db_add_usd(lot["seller_id"], seller_earn)
            db_add_px(buyer_id, lot["px_amount"])

            # Уведомление продавцу
            try:
                await _bot_ref.send_message(
                    lot["seller_id"],
                    f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                    f'<b>Сделка #{lot_id} куплена!</b>\n\n'
                    f'<blockquote>'
                    f'👤  Покупатель: @{buyer_name}\n'
                    f'📦  Продано: {int(lot["px_amount"]):,} Px\n'
                    f'💵  Зачислено: <b>${seller_earn:.4f}</b>'
                    f'</blockquote>',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            # Сообщение покупателю
            try:
                await _bot_ref.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=(
                        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                        f'<b>Покупка успешна!</b>\n\n'
                        f'<blockquote>'
                        f'📦  Зачислено: <b>{int(lot["px_amount"]):,} Px</b>\n'
                        f'💵  Оплачено: ${total_usd:.4f}'
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

    # Таймаут — сообщаем покупателю
    try:
        await _bot_ref.send_message(
            chat_id,
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Время оплаты истекло.</b> Счёт для лота #{lot_id} аннулирован.',
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
#  ВЫВОД — шаг 1: сумма
# ──────────────────────────────────────────────────────────
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
        f'Комиссия: <b>3%</b>'
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
        await state.update_data(withdraw_msg_id=new_mid)

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
        f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> <b>Подтверждение вывода</b>\n\n'
        f'<blockquote>'
        f'💵  Выводите: <b>${amount:.2f}</b>\n'
        f'📉  Комиссия (3%): <b>${amount * COMMISSION_WITHDRAW:.4f}</b>\n'
        f'✅  Получите: <b>${net:.4f}</b>\n\n'
        f'Средства придут чеком CryptoBot (USDT)'
        f'</blockquote>',
        _kb_confirm_withdraw(),
    )
    await state.update_data(withdraw_msg_id=new_mid)
    set_owner_fn(new_mid, uid)


# ──────────────────────────────────────────────────────────
#  ВЫВОД — шаг 2: подтверждение
# ──────────────────────────────────────────────────────────
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

    # Атомарное списание
    if not db_try_spend_usd(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно средств!</b>',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    try:
        check_url = await _create_withdraw_check(net)
    except Exception as e:
        logger.error("Withdraw check error: %s", e)
        db_add_usd(uid, amount)   # возврат при ошибке
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Ошибка создания чека.</b> Средства возвращены. Попробуйте позже.',
            reply_markup=_kb_back_exchange(),
        )
        await state.clear()
        return

    db_record_withdraw(uid, amount, net)
    await state.clear()

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Вывод оформлен!</b>\n\n'
        f'<blockquote>'
        f'💵  Списано: <b>${amount:.2f}</b>\n'
        f'✅  К получению: <b>${net:.4f}</b>\n\n'
        f'Нажмите кнопку ниже для получения чека:'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Получить чек", url=check_url)],
            [InlineKeyboardButton(
                text="Назад", callback_data="exchange",
                icon_custom_emoji_id=EMOJI_BACK
            )],
        ]),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  СТАТИСТИКА
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data == "ex_stats")
async def cb_ex_stats(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid    = call.from_user.id
    stats  = db_get_seller_stats(uid)
    wstats = db_get_withdraw_stats(uid)
    bal    = db_get_usd_balance(uid)

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Биржа — Статистика</b>\n\n'
        f'<blockquote>'
        f'💵  Баланс $: <b>${bal:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📤  Продаж совершено: <b>{stats["total_sales"]}</b>\n'
        f'💰  Заработано: <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'🏦  Выводов: <b>{wstats["count"]}</b>\n'
        f'💸  Выведено всего: <b>${wstats["total"]:.2f}</b>'
        f'</blockquote>',
        reply_markup=_kb_back_exchange(),
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  Watchdog: возврат Px по истёкшим лотам (каждые 10 мин)
# ──────────────────────────────────────────────────────────
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
                        f'<b>Сделка #{lot["id"]} истекла!</b>\n\n'
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
