"""
gold.py — Золото (честная версия)
─────────────────────────────────────────────────────────────────
Честность: на каждом уровне 1 бомба из 2 ячеек — позиция бомбы
определяется ЗАРАНЕЕ при создании сессии (random.randint).
─────────────────────────────────────────────────────────────────
"""

import random
import re
import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode

from database import db_get_px, db_add_px, db_try_spend_px, db_record_game_result

try:
    from leaders import record_game_result
except ImportError:
    def record_game_result(user_id, name, bet, win): pass

# ── Emoji ──────────────────────────────────────────────────────────
EMOJI_BACK    = "5906771962734057347"
EMOJI_GOAL    = "5206607081334906820"
EMOJI_3POINT  = "5397782960512444700"
EMOJI_COIN    = "5197434882321567830"
EMOJI_WIN     = "5278467510604160626"
EMOJI_BET     = "5305699699204837855"
EMOJI_MULT    = "5330320040883411678"
EMOJI_NEXT    = "5391032818111363540"
EMOJI_FLOOR   = "5197503331215361533"
EMOJI_INPUT   = "5197269100878907942"
EMOJI_LOSS    = "5447183459602669338"
EMOJI_BOMB2   = "5210952531676504517"
EMOJI_CASHOUT = "5312441427764989435"
EMOJI_TROPHY  = "5461151367559141950"
EMOJI_MULT2   = "5429651785352501917"

# ── Ячейки ─────────────────────────────────────────────────────────
CELL_HIDDEN   = "🌑"
CELL_GOLD     = "💰"
CELL_BOMB     = "🧨"
CELL_EXPLODE  = "💥"
CELL_SAFE_REV = "▪️"
CELL_FUTURE   = "🌑"

# ── Конфигурация ───────────────────────────────────────────────────
FLOORS             = 7
CELLS              = 2
INACTIVITY_TIMEOUT = 300

MIN_BET = 10.0
MAX_BET = 100_000_000.0

GOLD_MULTIPLIERS = [1.9, 3.8, 7.6, 14.5, 29.9, 56.78, 116.84]

# ── FSM ────────────────────────────────────────────────────────────
class GoldGame(StatesGroup):
    choosing_bet = State()
    playing      = State()

gold_router = Router()

_sessions:         dict = {}
_timeout_tasks:    dict = {}
_user_locks:       dict = {}
_bet_locks:        dict = {}
_game_board_owner: dict = {}

def _noop_set_owner(message_id: int, user_id: int): pass
def _noop_is_owner(message_id: int, user_id: int) -> bool: return True
set_owner_fn = _noop_set_owner
is_owner_fn  = _noop_is_owner


# ── Локеры ─────────────────────────────────────────────────────────
def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

def _get_bet_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _bet_locks:
        _bet_locks[user_id] = asyncio.Lock()
    return _bet_locks[user_id]

def _nickname(from_user) -> str:
    name = (from_user.first_name or "")
    if getattr(from_user, 'last_name', None):
        name += f" {from_user.last_name}"
    return name.strip() or getattr(from_user, 'username', None) or f"User {from_user.id}"


# ── Таймаут бездействия ────────────────────────────────────────────
def _cancel_timeout(user_id: int):
    task = _timeout_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()

def _start_timeout(user_id: int, bot: Bot):
    _cancel_timeout(user_id)
    _timeout_tasks[user_id] = asyncio.create_task(_inactivity_watcher(user_id, bot))

async def _inactivity_watcher(user_id: int, bot: Bot):
    try:
        await asyncio.sleep(INACTIVITY_TIMEOUT)
    except asyncio.CancelledError:
        return

    lock = _get_user_lock(user_id)
    async with lock:
        session = _sessions.pop(user_id, None)
        if session is None or session.get('finishing'):
            return
        session['finishing'] = True

    bet = session.get('bet', 0)
    if bet > 0:
        db_add_px(user_id, bet)
        logging.info(f"[gold] Таймаут user={user_id}, ставка {bet} возвращена.")

    msg_id  = session.get('message_id')
    chat_id = session.get('chat_id')
    if msg_id and chat_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=(
                    "<blockquote><b>⏰ Игра закрыта!</b></blockquote>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ── Хелперы ────────────────────────────────────────────────────────
def _has_active_game(user_id: int) -> bool:
    return user_id in _sessions

def get_multiplier(floors_passed: int) -> float:
    if floors_passed == 0:
        return 1.0
    return GOLD_MULTIPLIERS[min(floors_passed - 1, len(GOLD_MULTIPLIERS) - 1)]

def get_next_mult(floors_passed: int) -> float:
    if floors_passed >= len(GOLD_MULTIPLIERS):
        return GOLD_MULTIPLIERS[-1]
    return GOLD_MULTIPLIERS[floors_passed]

def _active_game_error_text(session: dict) -> str:
    bet           = session['bet']
    floors_passed = session['floors_passed']
    mult          = get_multiplier(floors_passed)
    return (
        f"<blockquote><b>⚠️У вас уже есть активная игра!</b></blockquote>"
    )

def _validate_bet(bet: float) -> str | None:
    import math
    if not math.isfinite(bet) or bet <= 0:
        return "<b>❌Некорректная сумма ставки!</b>"
    if bet < MIN_BET:
        return f"<b>❌Минимальная ставка: {MIN_BET}Px</b>"
    if bet > MAX_BET:
        return f"<b>❌Максимальная ставка: {int(MAX_BET):,}Px</b>"
    return None


# ── Создание сессии ────────────────────────────────────────────────
def _create_session(bet: float, chat_id: int, owner_id: int = 0) -> dict:
    floors = []
    for _ in range(FLOORS):
        floors.append({
            'bomb_col': random.randint(0, CELLS - 1),
            'chosen':   None,
            'is_bomb':  None,
        })
    return {
        'bet':              bet,
        'current_floor':    0,
        'floors_passed':    0,
        'floors':           floors,
        'message_id':       None,
        'chat_id':          chat_id,
        'owner_id':         owner_id,
        'finishing':        False,
        'processing_cells': set(),
    }


# ── Тексты ─────────────────────────────────────────────────────────
def game_text(session: dict) -> str:
    bet           = session['bet']
    floors_passed = session['floors_passed']
    mult          = get_multiplier(floors_passed)
    next_mult     = get_next_mult(floors_passed)
    floor_num     = session['current_floor'] + 1

    return (
        f'<blockquote><b>💰 Золото</b></blockquote>\n\n'
        f"<blockquote>"
        f'<tg-emoji emoji-id="5427168083074628963">👋</tg-emoji>Ставка: <code>{bet}Px</code>\n'
        f'<tg-emoji emoji-id="5391032818111363540">👋</tg-emoji>Уровень: <b>{floor_num}/{FLOORS}</b>\n'
        f'<tg-emoji emoji-id="5397782960512444700">👋</tg-emoji>Текущий: <b><code>x{mult}</code></b>\n'
        f'<tg-emoji emoji-id="5416117059207572332">👋</tg-emoji>Следующий: <b><code>x{next_mult}</code></b>\n'
        f"</blockquote>\n\n"
        f"<blockquote><b><i>Выберите ячейку — за одной спрятано золото!</i></b></blockquote>"
    )


# ── Клавиатура ─────────────────────────────────────────────────────
def build_gold_keyboard(session: dict, game_over: bool = False) -> InlineKeyboardMarkup:
    current_floor = session['current_floor']
    floors_passed = session['floors_passed']
    floors        = session['floors']
    rows          = []

    for floor_idx in range(FLOORS - 1, -1, -1):
        floor_data = floors[floor_idx]
        chosen     = floor_data['chosen']
        bomb_col   = floor_data['bomb_col']
        mult       = GOLD_MULTIPLIERS[floor_idx]
        btn_row    = [InlineKeyboardButton(text=f"x{mult}", callback_data="gold_noop")]

        if game_over:
            for col in range(CELLS):
                if col == chosen and bomb_col == col:   text = CELL_EXPLODE
                elif col == bomb_col:                   text = CELL_BOMB
                elif col == chosen:                     text = CELL_GOLD
                else:                                   text = CELL_SAFE_REV
                btn_row.append(InlineKeyboardButton(text=text, callback_data="gold_noop"))

        elif floor_idx < current_floor:
            for col in range(CELLS):
                text = CELL_GOLD if col == chosen else CELL_HIDDEN
                btn_row.append(InlineKeyboardButton(text=text, callback_data="gold_noop"))

        elif floor_idx == current_floor:
            for col in range(CELLS):
                btn_row.append(InlineKeyboardButton(
                    text=CELL_HIDDEN,
                    callback_data=f"gold_cell_{floor_idx}_{col}"
                ))
        else:
            for col in range(CELLS):
                btn_row.append(InlineKeyboardButton(text=CELL_FUTURE, callback_data="gold_noop"))

        rows.append(btn_row)

    if not game_over:
        ctrl = []
        if floors_passed > 0:
            mult    = get_multiplier(floors_passed)
            cashout = round(session['bet'] * mult, 2)
            ctrl.append(InlineKeyboardButton(
                text=f"Забрать {cashout}", callback_data="gold_cashout",
                icon_custom_emoji_id=EMOJI_GOAL
            ))
        ctrl.append(InlineKeyboardButton(text="Выйти", callback_data="gold_exit",
                                         icon_custom_emoji_id=EMOJI_BACK))
        rows.append(ctrl)
    else:
        rows.append([
            InlineKeyboardButton(text="Снова", callback_data="gold_play_again",
                                 icon_custom_emoji_id=EMOJI_3POINT),
            InlineKeyboardButton(text="Выйти", callback_data="gold_exit",
                                 icon_custom_emoji_id=EMOJI_BACK),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Публичный вход ─────────────────────────────────────────────────
async def show_gold_menu(callback: CallbackQuery, state: FSMContext = None):
    user_id = callback.from_user.id
    if _has_active_game(user_id):
        await callback.answer("⚠️Завершите текущую игру!", show_alert=True); return

    balance = db_get_px(user_id)
    await callback.message.edit_text(
        f'<blockquote><b>💰 Золото</b></blockquote>\n\n'
        f'<blockquote><b><tg-emoji emoji-id="5197269100878907942">👋</tg-emoji> Введите сумму ставки:</b></blockquote>',
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)
        ]])
    )
    set_owner_fn(callback.message.message_id, user_id)
    if state is not None:
        await state.set_state(GoldGame.choosing_bet)
    await callback.answer()


# ── Хендлеры callback ──────────────────────────────────────────────
@gold_router.callback_query(F.data == "gold_menu")
async def gold_menu_callback(callback: CallbackQuery, state: FSMContext):
    if not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await show_gold_menu(callback, state)


@gold_router.callback_query(F.data == "gold_play_again")
async def gold_play_again(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    _sessions.pop(caller_id, None)
    _cancel_timeout(caller_id)
    await state.clear()
    await show_gold_menu(callback, state)


@gold_router.callback_query(F.data == "gold_exit")
async def gold_exit(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return

    session = _sessions.get(caller_id)
    if session and not session.get('finishing'):
        bet = session.get('bet', 0)
        if bet > 0:
            db_add_px(caller_id, bet)
        _sessions.pop(caller_id, None)
        _cancel_timeout(caller_id)

    await state.clear()
    from game import GAMES_TEXT, games_keyboard
    await callback.message.edit_text(GAMES_TEXT, reply_markup=games_keyboard(), parse_mode="HTML")
    await callback.answer()


@gold_router.callback_query(F.data == "gold_noop")
async def gold_noop(callback: CallbackQuery):
    await callback.answer()


@gold_router.callback_query(F.data.startswith("gold_cell_"))
async def gold_cell_handler(callback: CallbackQuery, state: FSMContext):
    caller_id = callback.from_user.id
    msg_id    = callback.message.message_id

    parts = callback.data.split("_")
    if len(parts) != 4:
        await callback.answer(); return
    try:
        floor_idx = int(parts[2])
        col       = int(parts[3])
    except ValueError:
        await callback.answer(); return

    if floor_idx < 0 or floor_idx >= FLOORS or col < 0 or col >= CELLS:
        await callback.answer(); return

    board_owner = _game_board_owner.get(msg_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return

    session = _sessions.get(caller_id)
    user_id = caller_id

    if not session:
        await callback.answer("🚫 Игра не найдена!", show_alert=True); return
    if session.get('message_id') != msg_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    if session.get('finishing'):
        await callback.answer(); return
    if floor_idx != session['current_floor']:
        await callback.answer(); return

    processing = session.setdefault('processing_cells', set())
    if col in processing:
        await callback.answer(); return

    lock = _get_user_lock(user_id)
    async with lock:
        session = _sessions.get(user_id)
        if not session or session.get('finishing'):
            await callback.answer(); return
        if floor_idx != session['current_floor']:
            await callback.answer(); return
        processing = session.setdefault('processing_cells', set())
        if col in processing:
            await callback.answer(); return
        processing.add(col)

    try:
        _start_timeout(user_id, callback.bot)

        floor_data = session['floors'][floor_idx]
        floor_data['chosen'] = col

        is_bomb = (col == floor_data['bomb_col'])
        floor_data['is_bomb'] = is_bomb

        name = _nickname(callback.from_user)

        if is_bomb:
            bet = session['bet']

            async with _get_user_lock(user_id):
                if session.get('finishing'): return
                session['finishing'] = True
                _sessions.pop(user_id, None)

            _cancel_timeout(user_id)
            await state.clear()

            record_game_result(user_id, name, bet, 0.0)
            db_record_game_result(user_id, bet, 0.0)

            balance = db_get_px(user_id)
            await callback.message.edit_text(
                f"<blockquote><b>💥 Вы нашли бомбу!</b></blockquote>\n\n"
                f"<blockquote>"
                f'<tg-emoji emoji-id="5429518319243775957">👋</tg-emoji>Потеряно: <code>{bet}</code> Px\n'
                f'<tg-emoji emoji-id="5278467510604160626">👋</tg-emoji>Баланс: <code>{balance:.2f}</code> Px'
                f"</blockquote>\n\n"
                f"<blockquote><b><i>Шахта обвалилась! Попробуйте снова!</i></b></blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=build_gold_keyboard(session, game_over=True)
            )
            set_owner_fn(msg_id, user_id)
            _game_board_owner[msg_id] = user_id
            await callback.answer("💥 Бомба!")

        else:
            session['floors_passed'] += 1
            session['current_floor'] += 1
            floors_passed = session['floors_passed']
            mult          = get_multiplier(floors_passed)

            if session['current_floor'] >= FLOORS:
                bet = session['bet']

                async with _get_user_lock(user_id):
                    if session.get('finishing'): return
                    session['finishing'] = True
                    _sessions.pop(user_id, None)

                winnings = round(bet * mult, 2)
                db_add_px(user_id, winnings)
                _cancel_timeout(user_id)
                await state.clear()

                record_game_result(user_id, name, bet, winnings)
                db_record_game_result(user_id, bet, winnings)

                balance = db_get_px(user_id)
                await callback.message.edit_text(
                    f'<blockquote><b><tg-emoji emoji-id="5461151367559141950">👋</tg-emoji> Вы добыли всё золото!</b></blockquote>\n\n'
                    f"<blockquote>"
                    f'<tg-emoji emoji-id="5201691993775818138">👋</tg-emoji>Множитель: <b>x{mult}</b>\n'
                    f'<tg-emoji emoji-id="5312441427764989435">👋</tg-emoji>Выигрыш: <code>{winnings}</code> Px\n'
                    f'<tg-emoji emoji-id="5278467510604160626">👋</tg-emoji>Баланс: <code>{balance:.2f}</code> Px'
                    f"</blockquote>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Играть снова", callback_data="gold_cashout_again",
                                              icon_custom_emoji_id=EMOJI_3POINT)],
                        [InlineKeyboardButton(text="Выйти", callback_data="gold_cashout_exit",
                                              icon_custom_emoji_id=EMOJI_BACK)],
                    ])
                )
                set_owner_fn(msg_id, user_id)
                _game_board_owner[msg_id] = user_id
                await callback.answer("Победа!")
            else:
                await callback.message.edit_text(
                    game_text(session), parse_mode=ParseMode.HTML,
                    reply_markup=build_gold_keyboard(session)
                )
                await callback.answer(f"x{mult}")

    finally:
        s = _sessions.get(user_id)
        if s:
            s.get('processing_cells', set()).discard(col)


@gold_router.callback_query(F.data == "gold_cashout")
async def gold_cashout(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    msg_id  = callback.message.message_id

    board_owner = _game_board_owner.get(msg_id)
    if board_owner is None or board_owner != user_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return

    lock = _get_user_lock(user_id)
    async with lock:
        session = _sessions.get(user_id)
        if not session:
            await callback.answer("Игра не найдена.", show_alert=True); return
        if session.get('message_id') != msg_id:
            await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
        if session.get('finishing'):
            await callback.answer(); return
        floors_passed = session['floors_passed']
        if floors_passed == 0:
            await callback.answer("Пройдите хотя бы один уровень!", show_alert=True); return
        session['finishing'] = True
        _sessions.pop(user_id, None)

    bet      = session['bet']
    mult     = get_multiplier(floors_passed)
    winnings = round(bet * mult, 2)

    db_add_px(user_id, winnings)
    _cancel_timeout(user_id)
    await state.clear()

    name = _nickname(callback.from_user)
    record_game_result(user_id, name, bet, winnings)
    db_record_game_result(user_id, bet, winnings)

    balance = db_get_px(user_id)
    await callback.message.edit_text(
        f'<blockquote><b><tg-emoji emoji-id="5461151367559141950">👋</tg-emoji> Кэшаут!</b></blockquote>\n\n'
        f"<blockquote>"
        f'<tg-emoji emoji-id="5201691993775818138">👋</tg-emoji>Множитель: <b>x{mult}</b>\n'
        f'<tg-emoji emoji-id="5312441427764989435">👋</tg-emoji>Выигрыш: <code>{winnings}</code> Px\n'
        f'<tg-emoji emoji-id="5278467510604160626">👋</tg-emoji>Баланс: <code>{balance:.2f}</code> Px'
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Играть снова", callback_data="gold_cashout_again",
                                  icon_custom_emoji_id=EMOJI_3POINT)],
            [InlineKeyboardButton(text="Выйти", callback_data="gold_cashout_exit",
                                  icon_custom_emoji_id=EMOJI_BACK)],
        ])
    )
    set_owner_fn(msg_id, user_id)
    _game_board_owner[msg_id] = user_id
    await callback.answer(f" +{winnings}!")


@gold_router.callback_query(F.data == "gold_cashout_again")
async def gold_cashout_again(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    await state.clear()
    await show_gold_menu(callback, state)


@gold_router.callback_query(F.data == "gold_cashout_exit")
async def gold_cashout_exit(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    await state.clear()
    from game import GAMES_TEXT, games_keyboard
    await callback.message.edit_text(GAMES_TEXT, reply_markup=games_keyboard(), parse_mode="HTML")
    await callback.answer()


# ── Ввод ставки (FSM) ──────────────────────────────────────────────
@gold_router.message(GoldGame.choosing_bet)
async def process_gold_bet(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if _has_active_game(user_id):
        await message.answer(_active_game_error_text(_sessions[user_id]), parse_mode=ParseMode.HTML)
        return

    bet_lock = _get_bet_lock(user_id)
    if bet_lock.locked(): return

    async with bet_lock:
        if _has_active_game(user_id):
            await message.answer(_active_game_error_text(_sessions[user_id]), parse_mode=ParseMode.HTML)
            return

        try:
            bet = float(message.text.replace(',', '.'))
        except ValueError:
            await message.answer(
                f'<blockquote>❌Введите корректную сумму!</blockquote>', parse_mode=ParseMode.HTML
            ); return

        err = _validate_bet(bet)
        if err:
            await message.answer(f'<blockquote><b>❌ {err}</b></blockquote>',
                                 parse_mode=ParseMode.HTML); return

        if not db_try_spend_px(user_id, bet):
            balance = db_get_px(user_id)
            await message.answer(
                f'<blockquote><b>❌Недостаточно средств!</b>\n'
                f'Баланс: <code>{balance:.2f}</code> Px</blockquote>',
                parse_mode=ParseMode.HTML
            ); return

        session = _create_session(bet, message.chat.id, user_id)
        _sessions[user_id] = session
        await state.set_state(GoldGame.playing)

    sent = await message.answer(game_text(session), parse_mode=ParseMode.HTML,
                                reply_markup=build_gold_keyboard(session))
    session['message_id'] = sent.message_id
    set_owner_fn(sent.message_id, user_id)
    _game_board_owner[sent.message_id] = user_id
    _start_timeout(user_id, message.bot)


# ── Быстрая команда ────────────────────────────────────────────────
# Форматы: золото 1000 | /золото 1000 | gold 1000 | /gold 1000
# Молча игнорирует неверный формат, сумму, нехватку средств
# Показывает ошибку только при активной игре

_QUICK_GOLD_RE = re.compile(
    r'^/?(?:золото|gold)\s+'
    r'(\d+(?:[.,]\d+)?)'
    r'\s*$',
    re.IGNORECASE
)


@gold_router.message(F.text.regexp(r'(?i)^/?(?:золото|gold)\s+\S+'))
async def gold_quick_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text    = (message.text or "").strip()

    m = _QUICK_GOLD_RE.match(text)
    if not m:
        return

    try:
        bet = float(m.group(1).replace(',', '.'))
    except ValueError:
        return

    if bet < MIN_BET or bet > MAX_BET:
        return

    if _has_active_game(user_id):
        await message.answer(_active_game_error_text(_sessions[user_id]), parse_mode=ParseMode.HTML)
        return

    bet_lock = _get_bet_lock(user_id)
    if bet_lock.locked():
        return

    async with bet_lock:
        if _has_active_game(user_id):
            await message.answer(_active_game_error_text(_sessions[user_id]), parse_mode=ParseMode.HTML)
            return

        if not db_try_spend_px(user_id, bet):
            return

        session = _create_session(bet, message.chat.id, user_id)
        _sessions[user_id] = session
        await state.set_state(GoldGame.playing)

    sent = await message.answer(
        game_text(session), parse_mode=ParseMode.HTML,
        reply_markup=build_gold_keyboard(session)
    )
    session['message_id'] = sent.message_id
    set_owner_fn(sent.message_id, user_id)
    _game_board_owner[sent.message_id] = user_id
    _start_timeout(user_id, message.bot)
