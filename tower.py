"""
tower.py — Башня (честная версия)
─────────────────────────────────────────────────────────────────
Честность: бомбы расставляются ЗАРАНЕЕ при создании сессии
(random.sample). Результат нажатия определяется фактическим
расположением, а не броском процента в момент клика.
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
EMOJI_BACK   = "5906771962734057347"
EMOJI_GOAL   = "5206607081334906820"
EMOJI_3POINT = "5397782960512444700"

# ── Константы ──────────────────────────────────────────────────────
FLOORS             = 6
CELLS              = 5
INACTIVITY_TIMEOUT = 300

MIN_BET = 10.0
MAX_BET = 100_000_000.0

CELL_FUTURE      = "🌑"
CELL_ACTIVE      = "🌑"
CELL_CHOSEN_SAFE = "💎"
CELL_OTHER_SAFE  = "🌑"
CELL_SAFE_REVEAL = "▪️"
CELL_BOMB        = "💣"
CELL_EXPLODE     = "💥"

DIFFICULTY_BOMBS = {1: 1, 2: 2, 3: 3, 4: 4}
DIFFICULTY_NAMES = {1: "Лёгкий", 2: "Средний", 3: "Сложный", 4: "Безумный"}
DIFFICULTY_EMOJI = {1: "🟢", 2: "🟡", 3: "🔴", 4: "💀"}

TOWER_MULTIPLIERS = {
    1: [1.19, 1.45, 1.77, 2.11, 2.79, 3.55],
    2: [1.45, 2.35, 4.04, 7.11, 11.39, 19.26],
    3: [2.0, 5.8, 14.0, 38.0, 76.2, 121.7],
    4: [4.15, 22.2, 111.5, 297.0, 1235.0, 4144.0],
}

# ── FSM ────────────────────────────────────────────────────────────
class TowerGame(StatesGroup):
    choosing_bet = State()
    playing      = State()

tower_router = Router()

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
        logging.info(f"[tower] Таймаут user={user_id}, ставка {bet} возвращена.")

    msg_id  = session.get('message_id')
    chat_id = session.get('chat_id')
    if msg_id and chat_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=(
                    "<blockquote><b>⏰ Игра закрыта</b></blockquote>\n\n"
                    f"<blockquote>🏰 Башня\n"
                    f"Ставка <code>{bet}</code> Px возвращена\n</blockquote>\n\n"
                    "<blockquote><i>Игра завершена по таймауту (5 минут).</i></blockquote>"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🏰 Играть снова", callback_data="tower_menu")
                ]])
            )
        except Exception:
            pass


# ── Хелперы ────────────────────────────────────────────────────────
def _has_active_game(user_id: int) -> bool:
    return user_id in _sessions

def get_multiplier(difficulty: int, floors_passed: int) -> float:
    if floors_passed == 0:
        return 1.0
    mults = TOWER_MULTIPLIERS.get(difficulty, [])
    return mults[min(floors_passed - 1, len(mults) - 1)] if mults else 1.0

def get_next_mult(difficulty: int, floors_passed: int) -> float:
    mults = TOWER_MULTIPLIERS.get(difficulty, [])
    if not mults or floors_passed >= len(mults):
        return mults[-1] if mults else 1.0
    return mults[floors_passed]

def _nickname(from_user) -> str:
    name = (from_user.first_name or "")
    if getattr(from_user, 'last_name', None):
        name += f" {from_user.last_name}"
    return name.strip() or getattr(from_user, 'username', None) or f"User {from_user.id}"

def _active_game_error_text(session: dict) -> str:
    diff          = session['difficulty']
    bet           = session['bet']
    floors_passed = session['floors_passed']
    mult          = get_multiplier(diff, floors_passed)
    return (
        f"<blockquote><b>⚠️ У вас уже есть активная игра!</b></blockquote>\n\n"
        f"<blockquote>"
        f"🏰 Сложность: <b>{DIFFICULTY_EMOJI[diff]} {DIFFICULTY_NAMES[diff]}</b>\n"
        f"Ставка: <code>{bet}</code> Px\n"
        f"Пройдено: <b>{floors_passed}/{FLOORS}</b> | <b>x{mult}</b>\n"
        f"</blockquote>\n\n"
        f"<blockquote><i>Завершите текущую игру.</i></blockquote>"
    )


# ── Создание сессии ────────────────────────────────────────────────
def _create_session(difficulty: int, bet: float, chat_id: int, owner_id: int = 0) -> dict:
    num_bombs = DIFFICULTY_BOMBS[difficulty]
    floors = []
    for _ in range(FLOORS):
        bomb_cols = set(random.sample(range(CELLS), num_bombs))
        floors.append({
            'bomb_cols': bomb_cols,
            'chosen':    None,
            'is_bomb':   None,
        })
    return {
        'difficulty':       difficulty,
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


# ── Клавиатура ─────────────────────────────────────────────────────
def build_tower_keyboard(session: dict, game_over: bool = False) -> InlineKeyboardMarkup:
    difficulty    = session['difficulty']
    current_floor = session['current_floor']
    floors_passed = session['floors_passed']
    floors        = session['floors']
    rows          = []

    for floor_idx in range(FLOORS - 1, -1, -1):
        floor_data = floors[floor_idx]
        chosen     = floor_data['chosen']
        bomb_cols  = floor_data['bomb_cols']
        mult       = TOWER_MULTIPLIERS[difficulty][floor_idx]
        btn_row    = [InlineKeyboardButton(text=f"x{mult}", callback_data="tower_noop")]

        if game_over:
            for col in range(CELLS):
                is_bomb = col in bomb_cols
                if col == chosen and is_bomb:   text = CELL_EXPLODE
                elif is_bomb:                   text = CELL_BOMB
                elif col == chosen:             text = CELL_CHOSEN_SAFE
                else:                           text = CELL_SAFE_REVEAL
                btn_row.append(InlineKeyboardButton(text=text, callback_data="tower_noop"))

        elif floor_idx < current_floor:
            for col in range(CELLS):
                text = CELL_CHOSEN_SAFE if col == chosen else CELL_OTHER_SAFE
                btn_row.append(InlineKeyboardButton(text=text, callback_data="tower_noop"))

        elif floor_idx == current_floor:
            for col in range(CELLS):
                btn_row.append(InlineKeyboardButton(
                    text=CELL_ACTIVE,
                    callback_data=f"tower_cell_{floor_idx}_{col}"
                ))
        else:
            for col in range(CELLS):
                btn_row.append(InlineKeyboardButton(text=CELL_FUTURE, callback_data="tower_noop"))

        rows.append(btn_row)

    if not game_over:
        ctrl = []
        if floors_passed > 0:
            mult    = get_multiplier(difficulty, floors_passed)
            cashout = round(session['bet'] * mult, 2)
            ctrl.append(InlineKeyboardButton(
                text=f"Забрать {cashout}", callback_data="tower_cashout",
                icon_custom_emoji_id=EMOJI_GOAL
            ))
        ctrl.append(InlineKeyboardButton(text="Выйти", callback_data="tower_exit",
                                         icon_custom_emoji_id=EMOJI_BACK))
        rows.append(ctrl)
    else:
        rows.append([
            InlineKeyboardButton(text="Снова", callback_data="tower_play_again",
                                 icon_custom_emoji_id=EMOJI_3POINT),
            InlineKeyboardButton(text="Выйти", callback_data="tower_exit",
                                 icon_custom_emoji_id=EMOJI_BACK),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_tower_select_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 💣 (20%)", callback_data="tower_diff_1"),
            InlineKeyboardButton(text="2 💣 (40%)", callback_data="tower_diff_2"),
        ],
        [
            InlineKeyboardButton(text="3 💣 (60%)", callback_data="tower_diff_3"),
            InlineKeyboardButton(text="4 💣 (80%)", callback_data="tower_diff_4"),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])

def game_text(session: dict) -> str:
    diff          = session['difficulty']
    bet           = session['bet']
    floors_passed = session['floors_passed']
    mult          = get_multiplier(diff, floors_passed)
    next_mult     = get_next_mult(diff, floors_passed)
    floor_num     = session['current_floor'] + 1
    num_bombs     = DIFFICULTY_BOMBS[diff]

    return (
        f"<blockquote><b>🏰 Башня</b></blockquote>\n\n"
        f"<blockquote>"
        f"Ставка: <code>{bet}</code> Px\n"
        f"{DIFFICULTY_EMOJI[diff]} Сложность: <b>{DIFFICULTY_NAMES[diff]}</b> ({num_bombs} 💣 из {CELLS})\n"
        f"Этаж: <b>{floor_num}/{FLOORS}</b>\n"
        f"Текущий: <b><code>x{mult}</code></b>\n"
        f"Следующий: <b><code>x{next_mult}</code></b>\n"
        f"</blockquote>\n\n"
        f"<blockquote><b><i>Выберите безопасную ячейку!</i></b></blockquote>"
    )


# ── Публичный вход ─────────────────────────────────────────────────
async def show_tower_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    if _has_active_game(user_id):
        await callback.answer("⚠️ Завершите текущую игру!", show_alert=True)
        return
    balance = db_get_px(user_id)
    await callback.message.edit_text(
        f"<blockquote><b>🏰 Башня</b></blockquote>\n\n"
        f"<blockquote><b>Баланс: <code>{balance:.2f}</code> Px</b></blockquote>\n\n"
        f"<blockquote><b>Выберите сложность:</b></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_tower_select_keyboard()
    )
    set_owner_fn(callback.message.message_id, user_id)
    await callback.answer()


# ── Хендлеры ───────────────────────────────────────────────────────
@tower_router.callback_query(F.data == "tower_menu")
async def tower_menu_callback(callback: CallbackQuery, state: FSMContext):
    if not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await show_tower_menu(callback)


@tower_router.callback_query(F.data.startswith("tower_diff_"))
async def tower_diff_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not is_owner_fn(callback.message.message_id, user_id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    if _has_active_game(user_id):
        await callback.answer("⚠️ Завершите текущую игру!", show_alert=True); return

    difficulty = int(callback.data.split("_")[-1])
    await state.update_data(tower_difficulty=difficulty)
    await state.set_state(TowerGame.choosing_bet)

    balance = db_get_px(user_id)
    await callback.message.edit_text(
        f"<blockquote><b>✏️ Введите сумму ставки:</b>\n"
        f"Баланс: <code>{balance:.2f}</code> Px\n"
        f"Мин: <code>{MIN_BET}</code> | Макс: <code>{int(MAX_BET):,}</code></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Назад", callback_data="tower_back_select",
                                 icon_custom_emoji_id=EMOJI_BACK)
        ]])
    )
    set_owner_fn(callback.message.message_id, user_id)
    await callback.answer()


@tower_router.callback_query(F.data == "tower_back_select")
async def tower_back_select(callback: CallbackQuery, state: FSMContext):
    if not is_owner_fn(callback.message.message_id, callback.from_user.id):
        await callback.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    await state.clear()
    await show_tower_menu(callback)


@tower_router.callback_query(F.data == "tower_play_again")
async def tower_play_again(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    _sessions.pop(caller_id, None)
    _cancel_timeout(caller_id)
    await state.clear()
    await show_tower_menu(callback)


@tower_router.callback_query(F.data == "tower_exit")
async def tower_exit(callback: CallbackQuery, state: FSMContext):
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


@tower_router.callback_query(F.data == "tower_noop")
async def tower_noop(callback: CallbackQuery):
    await callback.answer()


@tower_router.callback_query(F.data.startswith("tower_cell_"))
async def tower_cell_handler(callback: CallbackQuery, state: FSMContext):
    caller_id = callback.from_user.id
    msg_id    = callback.message.message_id

    parts     = callback.data.split("_")
    floor_idx = int(parts[2])
    col       = int(parts[3])

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
        difficulty = session['difficulty']

        is_bomb = col in floor_data['bomb_cols']
        floor_data['is_bomb'] = is_bomb

        if is_bomb:
            bet  = session['bet']
            name = _nickname(callback.from_user)

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
                f"<blockquote><b>💥 Вы попали на бомбу!</b></blockquote>\n\n"
                f"<blockquote>"
                f"Потеряно: <code>{bet}</code> Px\n"
                f"Баланс: <code>{balance:.2f}</code> Px"
                f"</blockquote>\n\n"
                f"<blockquote><b><i>Башня рухнула! Попробуйте снова!</i></b></blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=build_tower_keyboard(session, game_over=True)
            )
            set_owner_fn(msg_id, user_id)
            await callback.answer("💥 Бомба!")

        else:
            session['floors_passed'] += 1
            session['current_floor'] += 1
            floors_passed = session['floors_passed']
            mult          = get_multiplier(difficulty, floors_passed)

            if session['current_floor'] >= FLOORS:
                bet  = session['bet']
                name = _nickname(callback.from_user)

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
                    f"<blockquote><b>🏆 Вы прошли все этажи!</b></blockquote>\n\n"
                    f"<blockquote>"
                    f"Множитель: <b>x{mult}</b>\n"
                    f"Выигрыш: <code>{winnings}</code> Px\n"
                    f"Баланс: <code>{balance:.2f}</code> Px"
                    f"</blockquote>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Играть снова", callback_data="tower_cashout_again",
                                              icon_custom_emoji_id=EMOJI_3POINT)],
                        [InlineKeyboardButton(text="Выйти", callback_data="tower_cashout_exit",
                                              icon_custom_emoji_id=EMOJI_BACK)],
                    ])
                )
                set_owner_fn(msg_id, user_id)
                _game_board_owner[msg_id] = user_id
                await callback.answer("🏆 Победа!")
            else:
                await callback.message.edit_text(
                    game_text(session), parse_mode=ParseMode.HTML,
                    reply_markup=build_tower_keyboard(session)
                )
                await callback.answer(f"✅ x{mult}")

    finally:
        s = _sessions.get(user_id)
        if s:
            s.get('processing_cells', set()).discard(col)


@tower_router.callback_query(F.data == "tower_cashout")
async def tower_cashout(callback: CallbackQuery, state: FSMContext):
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
            await callback.answer("Пройдите хотя бы один этаж!", show_alert=True); return
        session['finishing'] = True
        _sessions.pop(user_id, None)

    difficulty = session['difficulty']
    bet        = session['bet']
    mult       = get_multiplier(difficulty, floors_passed)
    winnings   = round(bet * mult, 2)

    db_add_px(user_id, winnings)
    _cancel_timeout(user_id)
    await state.clear()

    name = _nickname(callback.from_user)
    record_game_result(user_id, name, bet, winnings)
    db_record_game_result(user_id, bet, winnings)

    balance = db_get_px(user_id)
    await callback.message.edit_text(
        f"<blockquote><b>💰 Кэшаут!</b></blockquote>\n\n"
        f"<blockquote>"
        f"Множитель: <b>x{mult}</b>\n"
        f"Выигрыш: <code>{winnings}</code> Px\n"
        f"Баланс: <code>{balance:.2f}</code> Px"
        f"</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Играть снова", callback_data="tower_cashout_again",
                                  icon_custom_emoji_id=EMOJI_3POINT)],
            [InlineKeyboardButton(text="Выйти", callback_data="tower_cashout_exit",
                                  icon_custom_emoji_id=EMOJI_BACK)],
        ])
    )
    set_owner_fn(msg_id, user_id)
    _game_board_owner[msg_id] = user_id
    await callback.answer(f"💰 +{winnings}!")


@tower_router.callback_query(F.data == "tower_cashout_again")
async def tower_cashout_again(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    await state.clear()
    await show_tower_menu(callback)


@tower_router.callback_query(F.data == "tower_cashout_exit")
async def tower_cashout_exit(callback: CallbackQuery, state: FSMContext):
    caller_id   = callback.from_user.id
    board_owner = _game_board_owner.get(callback.message.message_id)
    if board_owner is None or board_owner != caller_id:
        await callback.answer("🚫 Это не ваша игра!", show_alert=True); return
    await state.clear()
    from game import GAMES_TEXT, games_keyboard
    await callback.message.edit_text(GAMES_TEXT, reply_markup=games_keyboard(), parse_mode="HTML")
    await callback.answer()


# ── Ввод ставки (FSM) ──────────────────────────────────────────────
@tower_router.message(TowerGame.choosing_bet)
async def process_tower_bet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data    = await state.get_data()
    difficulty = data.get('tower_difficulty')
    if difficulty is None:
        await state.clear(); return

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
            await message.answer("❌ Введите корректную сумму."); return

        if bet < MIN_BET:
            await message.answer(f"❌ Минимальная ставка: <code>{MIN_BET}</code> Px", parse_mode="HTML"); return
        if bet > MAX_BET:
            await message.answer(f"❌ Максимальная ставка: <code>{int(MAX_BET):,}</code> Px", parse_mode="HTML"); return

        if not db_try_spend_px(user_id, bet):
            await message.answer("<blockquote><b>❌ Недостаточно средств!</b></blockquote>",
                                 parse_mode=ParseMode.HTML); return

        session = _create_session(difficulty, bet, message.chat.id, user_id)
        _sessions[user_id] = session
        await state.set_state(TowerGame.playing)

    sent = await message.answer(game_text(session), parse_mode=ParseMode.HTML,
                                reply_markup=build_tower_keyboard(session))
    session['message_id'] = sent.message_id
    set_owner_fn(sent.message_id, user_id)
    _game_board_owner[sent.message_id] = user_id
    _start_timeout(user_id, message.bot)


# ── Быстрая команда ────────────────────────────────────────────────
# Форматы: башня 500 2 | /башня 500 2 | tower 500 2 | /tower 500 2
# Молча игнорирует неверный формат, сложность не 1-4, сумму, нехватку средств
# Показывает ошибку только при активной игре

_QUICK_TOWER_RE = re.compile(
    r'^/?(?:башня|tower)\s+'
    r'(\d+(?:[.,]\d+)?)\s+'
    r'([1-4])'
    r'\s*$',
    re.IGNORECASE
)


@tower_router.message(F.text.regexp(r'^/?(?:башня|tower)\s+\S+', re.IGNORECASE))
async def tower_quick_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text    = (message.text or "").strip()

    m = _QUICK_TOWER_RE.match(text)
    if not m:
        return

    try:
        bet        = float(m.group(1).replace(',', '.'))
        difficulty = int(m.group(2))
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

        session = _create_session(difficulty, bet, message.chat.id, user_id)
        _sessions[user_id] = session
        await state.set_state(TowerGame.playing)

    sent = await message.answer(
        game_text(session), parse_mode=ParseMode.HTML,
        reply_markup=build_tower_keyboard(session)
    )
    session['message_id'] = sent.message_id
    set_owner_fn(sent.message_id, user_id)
    _game_board_owner[sent.message_id] = user_id
    _start_timeout(user_id, message.bot)
