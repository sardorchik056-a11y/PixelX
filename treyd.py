"""
treyd.py — Модуль «Биржа» для PixelX бота
==========================================
Логика:
  • Отдельный $-баланс у каждого пользователя
  • Продажа Px за $: мин 50 000 Px, макс 1 000 000 000 Px
  • Цена за 10 000 Px: от 0.20$ до 1.00$ (пропорционально объёму)
  • Время экспозиции: 7 / 14 / 30 дней
  • Покупка: 10 лотов на страницу (пагинация)
  • Оплата через CryptoBot (@send / @CryptoBot invoice)
  • Комиссия продавцу: 85% (15% уходит бирже)
  • Вывод: мин $1, комиссия 3%
  • Автопроверка инвойса каждые 2 сек (до 10 мин)
  • По истечению срока — Px возвращаются продавцу
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
    db_get_user,
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
    db_cancel_listing,
    db_expire_listings,
    db_get_seller_stats,
    db_create_invoice_record,
    db_get_invoice_record,
    db_mark_invoice_paid,
    db_get_withdraw_stats,
    db_record_withdraw,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
#  Конфиг
# ──────────────────────────────────────────────────────────
CRYPTOBOT_TOKEN    = os.getenv("CRYPTOBOT_TOKEN", "")   # токен CryptoPay
CRYPTOBOT_API_URL  = "https://pay.crypt.bot/api"        # mainnet
# CRYPTOBOT_API_URL= "https://testnet-pay.crypt.bot/api" # testnet

SELL_MIN_PX        = 50_000
SELL_MAX_PX        = 1_000_000_000
PRICE_PER_10K_MIN  = 0.20   # $
PRICE_PER_10K_MAX  = 1.00   # $
COMMISSION_SELL    = 0.15   # 15% биржа при продаже
COMMISSION_WITHDRAW= 0.03   # 3% при выводе
WITHDRAW_MIN_USD   = 1.00   # $ мин вывод

EXPIRE_DAYS_OPTIONS = [7, 14, 30]

LISTINGS_PER_PAGE  = 10
INVOICE_POLL_SECS  = 2
INVOICE_MAX_SECS   = 600    # 10 минут ожидания оплаты

EMOJI_EXCHANGE     = "5402186569006210455"
EMOJI_SELL         = "5278467510604160626"
EMOJI_BUY          = "5206607081334906820"
EMOJI_WITHDRAW     = "5443127283898405358"
EMOJI_STATS        = "5231200819986047254"
EMOJI_BACK         = "5906771962734057347"
EMOJI_GOLD         = "5278467510604160626"
EMOJI_SUCCESS      = "5206607081334906820"
EMOJI_WARN         = "5287231198098117669"

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
    waiting_amount    = State()
    waiting_price     = State()
    waiting_duration  = State()
    confirm           = State()


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
    """Выполнить запрос к CryptoPay API."""
    if not CRYPTOBOT_TOKEN:
        raise RuntimeError("CRYPTOBOT_TOKEN не задан!")
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    url = f"{CRYPTOBOT_API_URL}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
    return data


async def create_invoice(amount_usd: float, description: str, payload: str) -> dict:
    """Создать инвойс в CryptoPay. Возвращает данные инвойса."""
    result = await _cryptopay_request("createInvoice", {
        "currency_type": "fiat",
        "fiat":          "USD",
        "amount":        str(round(amount_usd, 2)),
        "description":   description,
        "payload":       payload,
        "paid_btn_name": "callback",
        "paid_btn_url":  "https://t.me/",   # заменить на ссылку на бот
        "allow_comments": False,
        "allow_anonymous": False,
    })
    if result.get("ok"):
        return result["result"]
    raise RuntimeError(f"CryptoPay createInvoice error: {result}")


async def check_invoice(invoice_id: int) -> str:
    """Проверить статус инвойса. Возвращает 'active'|'paid'|'expired'."""
    result = await _cryptopay_request("getInvoices", {"invoice_ids": str(invoice_id)})
    if result.get("ok"):
        items = result["result"].get("items", [])
        if items:
            return items[0].get("status", "active")
    return "active"


async def create_withdraw_check(amount_usd: float) -> str:
    """Создать чек для вывода. Возвращает ссылку на чек."""
    result = await _cryptopay_request("createCheck", {
        "asset":    "USDT",
        "amount":   str(round(amount_usd, 2)),
        "pin_to_user_id": None,
    })
    if result.get("ok"):
        return result["result"]["bot_check_url"]
    raise RuntimeError(f"CryptoPay createCheck error: {result}")


# ──────────────────────────────────────────────────────────
#  Клавиатуры
# ──────────────────────────────────────────────────────────
def exchange_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💸 Купить",   callback_data="ex_buy_0"),
            InlineKeyboardButton(text="📤 Продать",  callback_data="ex_sell_start"),
        ],
        [
            InlineKeyboardButton(text="🏦 Вывод",    callback_data="ex_withdraw"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="ex_stats"),
        ],
        [
            InlineKeyboardButton(text="Назад",       callback_data="main_menu",
                                 icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


def back_exchange_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Назад", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK)
    ]])


def cancel_sell_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK)
    ]])


def duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7 дней",  callback_data="ex_dur_7"),
            InlineKeyboardButton(text="14 дней", callback_data="ex_dur_14"),
            InlineKeyboardButton(text="30 дней", callback_data="ex_dur_30"),
        ],
        [InlineKeyboardButton(text="Отмена", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK)],
    ])


def confirm_sell_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="ex_sell_confirm"),
            InlineKeyboardButton(text="❌ Отмена",       callback_data="exchange"),
        ],
    ])


def listings_keyboard(listings: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = []
    for lot in listings:
        total_price = lot["price_per_10k"] * (lot["px_amount"] / 10_000)
        label = f'{lot["px_amount"]:,} Px / ${total_price:.2f}'
        rows.append([InlineKeyboardButton(text=label, callback_data=f'ex_lot_{lot["id"]}')])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"ex_buy_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"ex_buy_{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Назад", callback_data="exchange",
                                      icon_custom_emoji_id=EMOJI_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lot_detail_keyboard(lot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Купить", callback_data=f"ex_buy_lot_{lot_id}"),
            InlineKeyboardButton(text="Назад",     callback_data="ex_buy_0",
                                 icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


def cancel_withdraw_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="exchange", icon_custom_emoji_id=EMOJI_BACK)
    ]])


def confirm_withdraw_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Вывести", callback_data="ex_withdraw_confirm"),
            InlineKeyboardButton(text="❌ Отмена",  callback_data="exchange"),
        ],
    ])


# ──────────────────────────────────────────────────────────
#  Тексты
# ──────────────────────────────────────────────────────────
def _exchange_main_text(uid: int) -> str:
    usd_bal = db_get_usd_balance(uid)
    px_bal  = db_get_px(uid)
    return (
        f'<tg-emoji emoji-id="{EMOJI_EXCHANGE}">💱</tg-emoji> <b>Биржа PixelX</b>\n\n'
        f'<blockquote>'
        f'💵  <b>Баланс $:</b> <code>${usd_bal:.2f}</code>\n'
        f'<tg-emoji emoji-id="{EMOJI_GOLD}">⚡</tg-emoji>  <b>Баланс Px:</b> <code>{px_bal:,.0f} Px</code>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'Продавайте Px за $, покупайте лоты других игроков.\n'
        f'Комиссия биржи: <b>15%</b> от продажи · <b>3%</b> при выводе'
        f'</blockquote>'
    )


def _price_range_for_amount(amount_px: float) -> tuple[float, float]:
    """Вычислить диапазон цен за 10 000 Px для данного объёма."""
    ratio   = min(amount_px / SELL_MAX_PX, 1.0)
    max_cap = PRICE_PER_10K_MIN + ratio * (PRICE_PER_10K_MAX - PRICE_PER_10K_MIN)
    return PRICE_PER_10K_MIN, round(max_cap, 2)


def _total_price(amount_px: float, price_per_10k: float) -> float:
    return round(price_per_10k * (amount_px / 10_000), 4)


# ──────────────────────────────────────────────────────────
#  Хэндлер входа на биржу (из main.py "exchange")
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data == "exchange")
async def cb_exchange(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    await state.clear()
    uid = call.from_user.id
    db_get_or_create_user(call.from_user)
    await call.message.edit_text(_exchange_main_text(uid), reply_markup=exchange_main_keyboard())
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПРОДАЖА
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
        f'Введите количество Px для продажи.\n'
        f'Минимум: <b>{SELL_MIN_PX:,} Px</b>\n'
        f'Максимум: <b>{SELL_MAX_PX:,} Px</b>'
        f'</blockquote>',
        reply_markup=cancel_sell_keyboard()
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

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    try:
        amount = float(raw)
    except ValueError:
        return

    if amount < SELL_MIN_PX:
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Минимум {SELL_MIN_PX:,} Px!</b>\n\nВведите другое количество:'
        )
        if sell_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, sell_msg_id, err,
                                                 reply_markup=cancel_sell_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    if amount > SELL_MAX_PX:
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Максимум {SELL_MAX_PX:,} Px!</b>\n\nВведите другое количество:'
        )
        if sell_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, sell_msg_id, err,
                                                 reply_markup=cancel_sell_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    # Проверяем баланс
    if db_get_px(uid) < amount:
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно Px на балансе!</b>'
        )
        if sell_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, sell_msg_id, err,
                                                 reply_markup=cancel_sell_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    p_min, p_max = _price_range_for_amount(amount)
    await state.update_data(sell_amount=amount)
    await state.set_state(SellStates.waiting_price)

    text = (
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Цена продажи</b>\n\n'
        f'<blockquote>'
        f'Количество: <b>{amount:,.0f} Px</b>\n\n'
        f'Введите цену за <b>10 000 Px</b> в $:\n'
        f'Доступный диапазон: <b>${p_min:.2f} — ${p_max:.2f}</b>'
        f'</blockquote>'
    )
    if sell_msg_id:
        try:
            await _bot_ref.edit_message_text(uid, sell_msg_id, text,
                                             reply_markup=cancel_sell_keyboard(),
                                             parse_mode=ParseMode.HTML)
            set_owner_fn(sell_msg_id, uid)
        except Exception:
            pass


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

    p_min, p_max = _price_range_for_amount(amount)
    if not (p_min <= price <= p_max):
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Цена вне диапазона!</b>\n\n'
            f'Введите цену от <b>${p_min:.2f}</b> до <b>${p_max:.2f}</b> за 10 000 Px:'
        )
        if sell_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, sell_msg_id, err,
                                                 reply_markup=cancel_sell_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    await state.update_data(sell_price=price)
    await state.set_state(SellStates.waiting_duration)

    total = _total_price(amount, price)
    after_commission = total * (1 - COMMISSION_SELL)

    text = (
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Срок размещения</b>\n\n'
        f'<blockquote>'
        f'Количество: <b>{amount:,.0f} Px</b>\n'
        f'Цена за 10 000 Px: <b>${price:.2f}</b>\n'
        f'Итого покупатель платит: <b>${total:.2f}</b>\n'
        f'Вы получите (85%): <b>${after_commission:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>Выберите срок размещения лота:</blockquote>'
    )
    if sell_msg_id:
        try:
            await _bot_ref.edit_message_text(uid, sell_msg_id, text,
                                             reply_markup=duration_keyboard(),
                                             parse_mode=ParseMode.HTML)
            set_owner_fn(sell_msg_id, uid)
        except Exception:
            pass


@exchange_router.callback_query(F.data.startswith("ex_dur_"))
async def cb_sell_duration(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    days = int(call.data.split("_")[-1])
    if days not in EXPIRE_DAYS_OPTIONS:
        await call.answer("Неверный вариант", show_alert=True)
        return

    await state.update_data(sell_days=days)
    data   = await state.get_data()
    amount = data["sell_amount"]
    price  = data["sell_price"]
    total  = _total_price(amount, price)
    after  = total * (1 - COMMISSION_SELL)
    expire = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")

    text = (
        f'<tg-emoji emoji-id="{EMOJI_SELL}">📤</tg-emoji> <b>Подтверждение продажи</b>\n\n'
        f'<blockquote>'
        f'📦  Количество: <b>{amount:,.0f} Px</b>\n'
        f'💲  Цена за 10 000 Px: <b>${price:.2f}</b>\n'
        f'🏷  Итоговая цена: <b>${total:.2f}</b>\n'
        f'✅  Вы получите: <b>${after:.2f}</b>\n'
        f'⏳  Срок: <b>{days} дней</b> (до {expire})\n'
        f'⚠️  Px будут сняты с вашего баланса немедленно!'
        f'</blockquote>'
    )
    await state.set_state(SellStates.confirm)
    await call.message.edit_text(text, reply_markup=confirm_sell_keyboard())
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


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

    # Безопасное списание Px
    if not db_try_spend_px(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно Px на балансе!</b>',
            reply_markup=back_exchange_keyboard()
        )
        await state.clear()
        return

    listing_id = db_create_listing(
        seller_id     = uid,
        seller_name   = call.from_user.username or call.from_user.first_name,
        px_amount     = amount,
        price_per_10k = price,
        days          = days,
    )
    await state.clear()

    total = _total_price(amount, price)
    after = total * (1 - COMMISSION_SELL)
    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Лот создан!</b>\n\n'
        f'<blockquote>'
        f'🆔  ID лота: <code>#{listing_id}</code>\n'
        f'📦  {amount:,.0f} Px за ${total:.2f}\n'
        f'💵  Вы получите: ${after:.2f} (после продажи)\n'
        f'⏳  Лот активен {days} дней'
        f'</blockquote>',
        reply_markup=back_exchange_keyboard()
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПОКУПКА — список лотов
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data.startswith("ex_buy_") & ~F.data.startswith("ex_buy_lot_"))
async def cb_buy_list(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    uid  = call.from_user.id
    page = int(call.data.split("_")[-1])

    all_listings = db_get_active_listings(exclude_uid=uid)   # не показываем свои лоты
    total_pages  = max(1, (len(all_listings) + LISTINGS_PER_PAGE - 1) // LISTINGS_PER_PAGE)
    page         = max(0, min(page, total_pages - 1))
    chunk        = all_listings[page * LISTINGS_PER_PAGE:(page + 1) * LISTINGS_PER_PAGE]

    if not all_listings:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>Пока нет активных лотов. Загляните позже!</blockquote>'
        )
        await call.message.edit_text(text, reply_markup=back_exchange_keyboard())
    else:
        text = (
            f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Покупка Px</b>\n\n'
            f'<blockquote>'
            f'Доступно лотов: <b>{len(all_listings)}</b>\n'
            f'Страница: <b>{page + 1}/{total_pages}</b>\n\n'
            f'Нажмите на лот для подробностей:'
            f'</blockquote>'
        )
        await call.message.edit_text(text, reply_markup=listings_keyboard(chunk, page, total_pages))

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
    lot    = db_get_listing(lot_id)

    if not lot or lot["status"] != "active":
        await call.answer("❌ Лот уже недоступен!", show_alert=True)
        return

    uid = call.from_user.id
    if lot["seller_id"] == uid:
        await call.answer("❌ Это ваш лот!", show_alert=True)
        return

    stats  = db_get_seller_stats(lot["seller_id"])
    total  = _total_price(lot["px_amount"], lot["price_per_10k"])
    expire = datetime.fromisoformat(lot["expires_at"]).strftime("%d.%m.%Y")

    text = (
        f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Лот #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'👤  Продавец: <b>@{lot["seller_name"]}</b>\n'
        f'🏆  Продаж: <b>{stats["total_sales"]}</b>\n'
        f'💵  Выручка продавца (всего): <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📦  Количество Px: <b>{lot["px_amount"]:,.0f} Px</b>\n'
        f'💲  Цена за 10 000 Px: <b>${lot["price_per_10k"]:.2f}</b>\n'
        f'🏷  <b>Итого к оплате: ${total:.2f}</b>\n'
        f'⏳  Активен до: {expire}'
        f'</blockquote>'
    )
    await call.message.edit_text(text, reply_markup=lot_detail_keyboard(lot_id))
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  ПОКУПКА — создание счёта и ожидание оплаты
# ──────────────────────────────────────────────────────────
@exchange_router.callback_query(F.data.startswith("ex_buy_lot_"))
async def cb_buy_lot(call: CallbackQuery, state: FSMContext):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return

    lot_id = int(call.data.split("_")[-1])
    lot    = db_get_listing(lot_id)
    uid    = call.from_user.id

    if not lot or lot["status"] != "active":
        await call.answer("❌ Лот уже недоступен!", show_alert=True)
        return

    if lot["seller_id"] == uid:
        await call.answer("❌ Нельзя купить собственный лот!", show_alert=True)
        return

    total   = _total_price(lot["px_amount"], lot["price_per_10k"])

    # Создаём инвойс
    try:
        invoice = await create_invoice(
            amount_usd  = total,
            description = f'PixelX: покупка {lot["px_amount"]:,} Px (лот #{lot_id})',
            payload     = f'buy:{lot_id}:{uid}',
        )
    except Exception as e:
        logger.error("Invoice creation failed: %s", e)
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Ошибка создания счёта.</b> Попробуйте позже.',
            reply_markup=back_exchange_keyboard()
        )
        return

    invoice_id  = invoice["invoice_id"]
    pay_url     = invoice["bot_invoice_url"]

    db_create_invoice_record(
        invoice_id  = invoice_id,
        lot_id      = lot_id,
        buyer_id    = uid,
        buyer_name  = call.from_user.username or call.from_user.first_name,
        amount_usd  = total,
    )

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_BUY}">💸</tg-emoji> <b>Оплата лота #{lot_id}</b>\n\n'
        f'<blockquote>'
        f'📦  {lot["px_amount"]:,.0f} Px\n'
        f'💵  К оплате: <b>${total:.2f}</b>\n\n'
        f'Нажмите кнопку ниже для оплаты.\n'
        f'Счёт действителен <b>10 минут</b>.'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text="Назад", callback_data="ex_buy_0",
                                  icon_custom_emoji_id=EMOJI_BACK)],
        ])
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()

    # Запускаем фоновую проверку
    asyncio.create_task(_poll_invoice(
        invoice_id  = invoice_id,
        lot_id      = lot_id,
        buyer_id    = uid,
        buyer_name  = call.from_user.username or call.from_user.first_name,
        chat_id     = uid,
        msg_id      = call.message.message_id,
        total_usd   = total,
    ))


# ──────────────────────────────────────────────────────────
#  Фоновая проверка инвойса
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
    """Каждые 2 сек проверяем статус оплаты — до 10 мин."""
    deadline = time.monotonic() + INVOICE_MAX_SECS

    while time.monotonic() < deadline:
        await asyncio.sleep(INVOICE_POLL_SECS)

        try:
            status = await check_invoice(invoice_id)
        except Exception as e:
            logger.warning("Invoice poll error: %s", e)
            continue

        if status == "paid":
            # Проверяем лот ещё раз (вдруг уже купили)
            lot = db_get_listing(lot_id)
            if not lot or lot["status"] != "active":
                await _bot_ref.send_message(
                    chat_id,
                    f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
                    f'<b>Лот #{lot_id} уже недоступен.</b>\n'
                    f'Обратитесь в поддержку для возврата средств.',
                    parse_mode=ParseMode.HTML
                )
                return

            # Закрываем лот
            db_mark_listing_sold(lot_id, buyer_id)
            db_mark_invoice_paid(invoice_id)

            # Начисляем продавцу 85%
            seller_earn = total_usd * (1 - COMMISSION_SELL)
            db_add_usd(lot["seller_id"], seller_earn)

            # Начисляем покупателю Px
            db_add_px(buyer_id, lot["px_amount"])

            # Уведомление продавцу
            try:
                await _bot_ref.send_message(
                    lot["seller_id"],
                    f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                    f'<b>Сделка #{lot_id} куплена!</b>\n\n'
                    f'<blockquote>'
                    f'👤  Покупатель: @{buyer_name}\n'
                    f'📦  Продано: {lot["px_amount"]:,.0f} Px\n'
                    f'💵  Начислено: <b>${seller_earn:.2f}</b> (85%)'
                    f'</blockquote>',
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

            # Сообщение покупателю
            try:
                await _bot_ref.edit_message_text(
                    chat_id    = chat_id,
                    message_id = msg_id,
                    text       = (
                        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> '
                        f'<b>Покупка успешна!</b>\n\n'
                        f'<blockquote>'
                        f'📦  Зачислено: <b>{lot["px_amount"]:,.0f} Px</b>\n'
                        f'💵  Оплачено: ${total_usd:.2f}'
                        f'</blockquote>'
                    ),
                    reply_markup=back_exchange_keyboard(),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            return

        elif status in ("expired", "cancelled"):
            break

    # Время вышло — инвойс истёк
    try:
        await _bot_ref.send_message(
            chat_id,
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Время оплаты истекло.</b> Счёт для лота #{lot_id} аннулирован.',
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
#  ВЫВОД
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
        f'💵  Ваш $ баланс: <b>${bal:.2f}</b>\n\n'
        f'Введите сумму для вывода.\n'
        f'Минимум: <b>${WITHDRAW_MIN_USD:.2f}</b>\n'
        f'Комиссия: <b>3%</b> (зачислено меньше указанной суммы)'
        f'</blockquote>',
        reply_markup=cancel_withdraw_keyboard()
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

    if amount < WITHDRAW_MIN_USD:
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Минимальная сумма вывода — ${WITHDRAW_MIN_USD:.2f}!</b>\n\nВведите сумму:'
        )
        if withdraw_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, withdraw_msg_id, err,
                                                 reply_markup=cancel_withdraw_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    bal = db_get_usd_balance(uid)
    if amount > bal:
        err = (
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно средств!</b>\n'
            f'Ваш баланс: ${bal:.2f}\n\nВведите сумму:'
        )
        if withdraw_msg_id:
            try:
                await _bot_ref.edit_message_text(uid, withdraw_msg_id, err,
                                                 reply_markup=cancel_withdraw_keyboard(),
                                                 parse_mode=ParseMode.HTML)
            except Exception:
                pass
        return

    net = amount * (1 - COMMISSION_WITHDRAW)
    await state.update_data(withdraw_amount=amount, withdraw_net=net)
    await state.set_state(WithdrawStates.confirm)

    text = (
        f'<tg-emoji emoji-id="{EMOJI_WITHDRAW}">🏦</tg-emoji> <b>Подтверждение вывода</b>\n\n'
        f'<blockquote>'
        f'💵  Вы выводите: <b>${amount:.2f}</b>\n'
        f'📉  Комиссия (3%): <b>${amount * COMMISSION_WITHDRAW:.2f}</b>\n'
        f'✅  Вы получите: <b>${net:.2f}</b>\n\n'
        f'Средства придут в виде чека CryptoBot (USDT)'
        f'</blockquote>'
    )
    if withdraw_msg_id:
        try:
            await _bot_ref.edit_message_text(uid, withdraw_msg_id, text,
                                             reply_markup=confirm_withdraw_keyboard(),
                                             parse_mode=ParseMode.HTML)
            set_owner_fn(withdraw_msg_id, uid)
        except Exception:
            pass


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
        await call.answer("Данные устарели.", show_alert=True)
        await state.clear()
        return

    # Списываем средства
    if not db_try_spend_usd(uid, amount):
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Недостаточно средств!</b>',
            reply_markup=back_exchange_keyboard()
        )
        await state.clear()
        return

    # Создаём чек
    try:
        check_url = await create_withdraw_check(net)
    except Exception as e:
        logger.error("Withdraw check creation failed: %s", e)
        # Возвращаем деньги при ошибке
        db_add_usd(uid, amount)
        await call.message.edit_text(
            f'<tg-emoji emoji-id="{EMOJI_WARN}">⚠️</tg-emoji> '
            f'<b>Ошибка создания чека.</b> Средства возвращены. Попробуйте позже.',
            reply_markup=back_exchange_keyboard()
        )
        await state.clear()
        return

    db_record_withdraw(uid, amount, net)
    await state.clear()

    await call.message.edit_text(
        f'<tg-emoji emoji-id="{EMOJI_SUCCESS}">✅</tg-emoji> <b>Вывод оформлен!</b>\n\n'
        f'<blockquote>'
        f'💵  Списано: <b>${amount:.2f}</b>\n'
        f'✅  К получению: <b>${net:.2f}</b>\n\n'
        f'Нажмите кнопку ниже, чтобы получить чек:'
        f'</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Получить чек", url=check_url)],
            [InlineKeyboardButton(text="Назад", callback_data="exchange",
                                  icon_custom_emoji_id=EMOJI_BACK)],
        ])
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

    text = (
        f'<tg-emoji emoji-id="{EMOJI_STATS}">📊</tg-emoji> <b>Биржа — Статистика</b>\n\n'
        f'<blockquote>'
        f'💵  Баланс $: <b>${bal:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'📤  Продаж: <b>{stats["total_sales"]}</b>\n'
        f'💰  Заработано: <b>${stats["total_earned"]:.2f}</b>'
        f'</blockquote>\n\n'
        f'<blockquote>'
        f'🏦  Выводов: <b>{wstats["count"]}</b>\n'
        f'💸  Выведено всего: <b>${wstats["total"]:.2f}</b>'
        f'</blockquote>'
    )
    await call.message.edit_text(text, reply_markup=back_exchange_keyboard())
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ──────────────────────────────────────────────────────────
#  Фоновый сторож истёкших лотов (запускается из main.py)
# ──────────────────────────────────────────────────────────
async def exchange_watchdog():
    """Каждые 10 мин проверяем истёкшие лоты и возвращаем Px продавцам."""
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
                        f'📦  Возвращено: <b>{lot["px_amount"]:,.0f} Px</b>'
                        f'</blockquote>',
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error("Exchange watchdog error: %s", e)
