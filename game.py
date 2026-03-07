"""
game.py — модуль игр PixelX
─────────────────────────────────────────────────────────────────────────────
ВАЖНО — уязвимость закрыта:
  Средства НЕ возвращаются после того, как бот отправил кубик (dice).
  Логика разбита на две фазы:
    Фаза 1 (до броска)  → ошибка = возврат средств
    Фаза 2 (после броска) → ошибка = только лог, средства НЕ возвращаются,
                            т.к. исход уже определён Telegram-сервером
─────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Tuple

from aiogram import Bot, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import (
    db_get_px,
    db_add_px,
    db_try_spend_px,
    db_record_game_result,
)

# ── Реферальная комиссия (опционально) ──────────────────────────────────────
try:
    from referrals import notify_referrer_commission
except ImportError:
    async def notify_referrer_commission(user_id: int, bet_amount: float):
        pass

logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────
#  Конфигурация
# ─────────────────────────────────────────
MIN_BET            = 1.0
MAX_BET            = 10_000.0
RATE_LIMIT_SECONDS = 3

# ─────────────────────────────────────────
#  Emoji IDs
# ─────────────────────────────────────────
EMOJI_BACK    = "5906771962734057347"
EMOJI_NECHET  = "5330320040883411678"
EMOJI_CHET    = "5391032818111363540"
EMOJI_MORE    = "5449683594425410231"
EMOJI_LESS    = "5447183459602669338"
EMOJI_2MORE   = "5429651785352501917"
EMOJI_2LESS   = "5429518319243775957"
EMOJI_NUMBER  = "5456140674028019486"
EMOJI_GOAL    = "5206607081334906820"
EMOJI_3POINT  = "5397782960512444700"
EMOJI_MISS    = "5210952531676504517"

# ─────────────────────────────────────────
#  Конфиги ставок
# ─────────────────────────────────────────
DICE_BET_TYPES = {
    'куб_нечет':   {'name': 'Нечетное',         'values': [1,3,5],   'multiplier': 1.9},
    'куб_чет':     {'name': 'Четное',            'values': [2,4,6],   'multiplier': 1.9},
    'куб_мал':     {'name': 'Меньше (1–3)',      'values': [1,2,3],   'multiplier': 1.9},
    'куб_бол':     {'name': 'Больше (4–6)',      'values': [4,5,6],   'multiplier': 1.9},
    'куб_2меньше': {'name': 'Оба меньше 4',      'multiplier': 3.8,   'special': 'double_dice'},
    'куб_2больше': {'name': 'Оба больше 3',      'multiplier': 3.8,   'special': 'double_dice'},
    'куб_1':       {'name': '1',                 'values': [1],       'multiplier': 5.7},
    'куб_2':       {'name': '2',                 'values': [2],       'multiplier': 5.7},
    'куб_3':       {'name': '3',                 'values': [3],       'multiplier': 5.7},
    'куб_4':       {'name': '4',                 'values': [4],       'multiplier': 5.7},
    'куб_5':       {'name': '5',                 'values': [5],       'multiplier': 5.7},
    'куб_6':       {'name': '6',                 'values': [6],       'multiplier': 5.7},
}

BASKETBALL_BET_TYPES = {
    'баскет_гол':   {'name': 'Гол (2 очка)',  'values': [4,5],  'multiplier': 1.85},
    'баскет_мимо':  {'name': 'Мимо',          'values': [1,2,3],'multiplier': 1.7},
    'баскет_3очка': {'name': '3-очковый',     'values': [5],    'multiplier': 5.7},
}

FOOTBALL_BET_TYPES = {
    'футбол_гол':  {'name': 'Гол',  'values': [3,4,5], 'multiplier': 1.35},
    'футбол_мимо': {'name': 'Мимо', 'values': [1,2],   'multiplier': 1.75},
}

DART_BET_TYPES = {
    'дартс_белое':   {'name': 'Белое',  'values': [3,5],     'multiplier': 2.35},
    'дартс_красное': {'name': 'Красное','values': [2,4,6],   'multiplier': 1.9},
    'дартс_мимо':    {'name': 'Мимо',   'values': [1],       'multiplier': 5.7},
    'дартс_центр':   {'name': 'Центр',  'values': [6],       'multiplier': 5.7},
}

BOWLING_BET_TYPES = {
    'боулинг_поражение': {'name': 'Поражение','values': [], 'multiplier': 1.8, 'special': 'bowling_vs'},
    'боулинг_победа':    {'name': 'Победа',   'values': [], 'multiplier': 1.8, 'special': 'bowling_vs'},
    'боулинг_страйк':    {'name': 'Страйк',   'values': [6],'multiplier': 5.7},
}

# ─────────────────────────────────────────
#  Маппинги для текстовых команд
# ─────────────────────────────────────────
COMMAND_MAPPING = {
    'фут': 'футбол', 'fut': 'футбол', 'foot': 'футбол',
    'футбол': 'футбол', 'football': 'футбол',
    'баскет': 'баскет', 'basket': 'баскет', 'basketball': 'баскет',
    'баскетбол': 'баскет', 'bask': 'баскет',
    'куб': 'куб', 'dice': 'куб', 'кубик': 'куб', 'cube': 'куб',
    'дартс': 'дартс', 'dart': 'дартс', 'darts': 'дартс', 'дарт': 'дартс',
    'боулинг': 'боулинг', 'bowling': 'боулинг', 'боул': 'боулинг', 'bowl': 'боулинг',
}

BET_TYPE_MAPPING = {
    '3очка': 'баскет_3очка', '3points': 'баскет_3очка', 'три': 'баскет_3очка', 'three': 'баскет_3очка',
    'нечет': 'куб_нечет', 'odd': 'куб_нечет', 'нечетное': 'куб_нечет', 'нечётное': 'куб_нечет',
    'чет': 'куб_чет', 'even': 'куб_чет', 'четное': 'куб_чет', 'чётное': 'куб_чет',
    'мал': 'куб_мал', 'small': 'куб_мал', 'меньше': 'куб_мал', 'less': 'куб_мал',
    'бол': 'куб_бол', 'big': 'куб_бол', 'больше': 'куб_бол', 'more': 'куб_бол',
    '2меньше': 'куб_2меньше', '2less': 'куб_2меньше', '2мал': 'куб_2меньше',
    '2больше': 'куб_2больше', '2more': 'куб_2больше', '2бол': 'куб_2больше',
    '1': 'куб_1', '2': 'куб_2', '3': 'куб_3',
    '4': 'куб_4', '5': 'куб_5', '6': 'куб_6',
    'белое': 'дартс_белое', 'white': 'дартс_белое', 'бел': 'дартс_белое',
    'красное': 'дартс_красное', 'red': 'дартс_красное', 'крас': 'дартс_красное',
    'центр': 'дартс_центр', 'center': 'дартс_центр', 'bull': 'дартс_центр',
    'победа': 'боулинг_победа', 'win': 'боулинг_победа', 'victory': 'боулинг_победа',
    'поражение': 'боулинг_поражение', 'lose': 'боулинг_поражение', 'loss': 'боулинг_поражение',
    'страйк': 'боулинг_страйк', 'strike': 'боулинг_страйк',
}

# ─────────────────────────────────────────
#  FSM
# ─────────────────────────────────────────
class BetStates(StatesGroup):
    waiting_for_amount = State()

# ─────────────────────────────────────────
#  Состояние модуля (инициализируется из main.py)
# ─────────────────────────────────────────
_bot: Bot = None
_active_games: Dict[int, datetime] = {}          # user_id → время начала
_pending_bets: Dict[int, str] = {}               # user_id → bet_type
_rate_limit: Dict[int, datetime] = {}            # user_id → последняя ставка
is_owner_fn  = lambda mid, uid: True
set_owner_fn = lambda mid, uid: None

game_router = Router()


def init_game(bot: Bot):
    global _bot
    _bot = bot


# ─────────────────────────────────────────
#  Вспомогательные функции
# ─────────────────────────────────────────
def _get_bet_config(bet_type: str) -> Optional[dict]:
    for table in (DICE_BET_TYPES, BASKETBALL_BET_TYPES,
                  FOOTBALL_BET_TYPES, DART_BET_TYPES, BOWLING_BET_TYPES):
        if bet_type in table:
            return table[bet_type]
    return None


def _dice_emoji(bet_type: str) -> str:
    if bet_type.startswith('куб_'):      return "🎲"
    if bet_type.startswith('баскет_'):   return "🏀"
    if bet_type.startswith('футбол_'):   return "⚽"
    if bet_type.startswith('дартс_'):    return "🎯"
    if bet_type.startswith('боулинг_'):  return "🎳"
    return "🎲"


def _check_rate_limit(uid: int) -> Tuple[bool, float]:
    now = datetime.now()
    if uid in _rate_limit:
        elapsed = (now - _rate_limit[uid]).total_seconds()
        if elapsed < RATE_LIMIT_SECONDS:
            return False, round(RATE_LIMIT_SECONDS - elapsed, 1)
    _rate_limit[uid] = now
    return True, 0.0


def _nickname(user) -> str:
    name = (user.first_name or "")
    if user.last_name:
        name += f" {user.last_name}"
    return name.strip() or user.username or "Игрок"


def _parse_bet_command(text: str) -> Optional[Tuple[str, float]]:
    text = text.strip().lstrip('/').lower()
    parts = text.split()
    if len(parts) < 3:
        return None
    game, bet_key = parts[0], parts[1]
    try:
        amount = float(parts[2])
    except ValueError:
        return None
    if not (MIN_BET <= amount <= MAX_BET):
        return None
    game_prefix = COMMAND_MAPPING.get(game)
    if not game_prefix:
        return None
    # Специальные ключи гол/мимо зависят от игры
    if game_prefix == 'баскет':
        if bet_key in ('гол', 'goal'):   full = 'баскет_гол'
        elif bet_key in ('мимо', 'miss'): full = 'баскет_мимо'
        else:                             full = BET_TYPE_MAPPING.get(bet_key)
    elif game_prefix == 'футбол':
        if bet_key in ('гол', 'goal'):   full = 'футбол_гол'
        elif bet_key in ('мимо', 'miss'): full = 'футбол_мимо'
        else:                             full = BET_TYPE_MAPPING.get(bet_key)
    elif game_prefix == 'дартс':
        if bet_key in ('мимо', 'miss'):  full = 'дартс_мимо'
        else:                             full = BET_TYPE_MAPPING.get(bet_key)
    else:
        full = BET_TYPE_MAPPING.get(bet_key)
    if not full or not full.startswith(game_prefix):
        return None
    return full, amount


def is_bet_command(text: str) -> bool:
    if not text:
        return False
    text = text.strip().lstrip('/').lower()
    parts = text.split()
    return len(parts) >= 3 and parts[0] in COMMAND_MAPPING


# ─────────────────────────────────────────
#  Игровые функции
# ─────────────────────────────────────────
WIN_TEXT  = (
    "<b>{name} — Вы выиграли! 🎉</b>\n\n"
    "<blockquote><code>{winnings:.2f}</code> Px зачислены на баланс!</blockquote>\n"
    "<blockquote>🎉 Поздравляем!</blockquote>"
)
LOSE_TEXT = (
    "<b>{name} — Вы проиграли ❌</b>\n\n"
    "<blockquote><b><i>Это не повод сдаваться! Пробуй снова!</i></b></blockquote>\n"
    "<blockquote>🍀 Желаем удачи!</blockquote>"
)


async def _play_single(chat_id: int, uid: int, name: str, amount: float,
                       bet_type: str, cfg: dict, reply_msg: Message = None):
    """
    Фаза 1 → бросок кубика (ошибка здесь = raise → вызывающий вернёт деньги).
    Фаза 2 → определение исхода и отправка результата (ошибка = только лог).
    """
    emoji = _dice_emoji(bet_type)

    # ── ФАЗА 1: отправка кубика ──────────────────────────────────────────────
    kwargs = {"chat_id": chat_id, "emoji": emoji}
    if reply_msg:
        kwargs["reply_to_message_id"] = reply_msg.message_id
    dice_msg = await _bot.send_dice(**kwargs)   # если упадёт — вызывающий вернёт деньги

    # ── ФАЗА 2: исход — деньги НЕ возвращаем ни при каких ошибках ────────────
    try:
        await asyncio.sleep(3)
        is_win = dice_msg.dice.value in cfg.get('values', [])

        if is_win:
            winnings = round(amount * cfg['multiplier'], 2)
            db_add_px(uid, winnings)
            db_record_game_result(uid, amount, winnings)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await dice_msg.reply(WIN_TEXT.format(name=name, winnings=winnings), parse_mode='HTML')
        else:
            db_record_game_result(uid, amount, 0.0)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await dice_msg.reply(LOSE_TEXT.format(name=name), parse_mode='HTML')

    except Exception as e:
        logging.error(f"[game] Ошибка в фазе 2 (single, uid={uid}): {e}")
        # Исход определён Telegram-сервером — средства НЕ возвращаются


async def _play_double_dice(chat_id: int, uid: int, name: str, amount: float,
                            bet_type: str, cfg: dict, reply_msg: Message = None):
    """Два кубика. Возврат средств только если первый бросок не случился."""
    kwargs = {"chat_id": chat_id, "emoji": "🎲"}
    if reply_msg:
        kwargs["reply_to_message_id"] = reply_msg.message_id

    # ── ФАЗА 1: первый кубик ─────────────────────────────────────────────────
    dice1 = await _bot.send_dice(**kwargs)

    # ── ФАЗА 2: остальное — без возврата ─────────────────────────────────────
    try:
        await asyncio.sleep(2)
        dice2 = await _bot.send_dice(chat_id=chat_id, emoji="🎲")
        await asyncio.sleep(3)

        v1, v2 = dice1.dice.value, dice2.dice.value
        is_win = (v1 < 4 and v2 < 4) if bet_type == 'куб_2меньше' else (v1 > 3 and v2 > 3)

        if is_win:
            winnings = round(amount * cfg['multiplier'], 2)
            db_add_px(uid, winnings)
            db_record_game_result(uid, amount, winnings)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await dice2.reply(WIN_TEXT.format(name=name, winnings=winnings), parse_mode='HTML')
        else:
            db_record_game_result(uid, amount, 0.0)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await dice2.reply(LOSE_TEXT.format(name=name), parse_mode='HTML')

    except Exception as e:
        logging.error(f"[game] Ошибка в фазе 2 (double, uid={uid}): {e}")


async def _play_bowling_vs(chat_id: int, uid: int, name: str, amount: float,
                           bet_type: str, cfg: dict, reply_msg: Message = None):
    """Боулинг vs бот. Возврат средств только если первый бросок не случился."""
    kwargs = {"chat_id": chat_id, "emoji": "🎳"}
    if reply_msg:
        kwargs["reply_to_message_id"] = reply_msg.message_id

    # ── ФАЗА 1: первый бросок игрока ─────────────────────────────────────────
    p_roll = await _bot.send_dice(**kwargs)

    # ── ФАЗА 2: без возврата ─────────────────────────────────────────────────
    try:
        await asyncio.sleep(2)
        b_roll = await _bot.send_dice(chat_id=chat_id, emoji="🎳")
        await asyncio.sleep(3)

        pv = p_roll.dice.value
        bv = b_roll.dice.value

        # Ничья — перебрасываем до победителя
        reruns = 0
        while pv == bv and reruns < 5:
            reruns += 1
            await p_roll.reply("⚖️ Ничья! Переброс...")
            await asyncio.sleep(1)
            p_roll = await _bot.send_dice(chat_id=chat_id, emoji="🎳")
            await asyncio.sleep(2)
            b_roll = await _bot.send_dice(chat_id=chat_id, emoji="🎳")
            await asyncio.sleep(3)
            pv = p_roll.dice.value
            bv = b_roll.dice.value

        if bet_type == 'боулинг_победа':
            is_win = pv > bv
        elif bet_type == 'боулинг_поражение':
            is_win = pv < bv
        else:
            is_win = False

        if is_win:
            winnings = round(amount * cfg['multiplier'], 2)
            db_add_px(uid, winnings)
            db_record_game_result(uid, amount, winnings)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await b_roll.reply(WIN_TEXT.format(name=name, winnings=winnings), parse_mode='HTML')
        else:
            db_record_game_result(uid, amount, 0.0)
            asyncio.create_task(notify_referrer_commission(uid, amount))
            await b_roll.reply(LOSE_TEXT.format(name=name), parse_mode='HTML')

    except Exception as e:
        logging.error(f"[game] Ошибка в фазе 2 (bowling, uid={uid}): {e}")


# ─────────────────────────────────────────
#  Единая точка запуска игры
# ─────────────────────────────────────────
async def _run_game(chat_id: int, uid: int, name: str, amount: float,
                    bet_type: str, cfg: dict, reply_msg: Message = None):
    """
    Вызывает нужную игровую функцию.
    Если кубик ЕЩЁ НЕ был брошен (Фаза 1 упала) → поднимаем исключение выше,
    чтобы вызывающий вернул деньги.
    Если кубик уже брошен → исключение глотается внутри play-функций.
    """
    if bet_type in ('куб_2меньше', 'куб_2больше'):
        await _play_double_dice(chat_id, uid, name, amount, bet_type, cfg, reply_msg)
    elif bet_type.startswith('боулинг_') and cfg.get('special') == 'bowling_vs':
        await _play_bowling_vs(chat_id, uid, name, amount, bet_type, cfg, reply_msg)
    else:
        await _play_single(chat_id, uid, name, amount, bet_type, cfg, reply_msg)


# ─────────────────────────────────────────
#  Общий движок: списание → игра → освобождение слота
# ─────────────────────────────────────────
async def _execute_bet(uid: int, name: str, amount: float,
                       bet_type: str, reply_msg: Message, fallback_chat_id: int):
    """
    1. Списывает ставку атомарно (db_try_spend_px).
    2. Запускает игру.
    3. Если Фаза 1 упала → возвращает деньги (безопасно, кубик не летел).
    4. Фаза 2 никогда не возвращает деньги.
    """
    cfg = _get_bet_config(bet_type)
    if cfg is None:
        await _bot.send_message(fallback_chat_id, "❌ Неизвестный тип ставки.")
        return

    # Атомарное списание
    if not db_try_spend_px(uid, amount):
        await _bot.send_message(
            fallback_chat_id,
            "<blockquote><b>❌ Недостаточно средств!</b></blockquote>",
            parse_mode='HTML'
        )
        return

    _active_games[uid] = datetime.now()
    try:
        await _run_game(fallback_chat_id, uid, name, amount, bet_type, cfg, reply_msg)
    except Exception as e:
        # Сюда попадаем только если кубик НЕ был брошен (Фаза 1 упала)
        logging.error(f"[game] Ошибка Фазы 1 (uid={uid}): {e} — возвращаем средства")
        db_add_px(uid, amount)
        try:
            await _bot.send_message(fallback_chat_id, "❌ Ошибка соединения. Ставка возвращена.")
        except Exception:
            pass
    finally:
        _active_games.pop(uid, None)


# ─────────────────────────────────────────
#  Меню игр
# ─────────────────────────────────────────
def games_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎲 Кубик",     callback_data="game_menu_dice"),
            InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_menu_basketball"),
        ],
        [
            InlineKeyboardButton(text="⚽ Футбол",    callback_data="game_menu_football"),
            InlineKeyboardButton(text="🎯 Дартс",     callback_data="game_menu_darts"),
        ],
        [
            InlineKeyboardButton(text="🎳 Боулинг",   callback_data="game_menu_bowling"),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="main_menu", icon_custom_emoji_id=EMOJI_BACK),
        ],
    ])


GAMES_TEXT = (
    "<b>🎮 Игры</b>\n\n"
    "<blockquote>Выберите игру и сделайте ставку.\n"
    "Минимум: <code>1 Px</code> | Максимум: <code>10 000 Px</code></blockquote>\n\n"
    "<blockquote><b>Текстовые команды:</b>\n"
    "  <code>куб чет 100</code>\n"
    "  <code>баскет гол 50</code>\n"
    "  <code>футбол мимо 200</code>\n"
    "  <code>дартс центр 500</code>\n"
    "  <code>боулинг победа 150</code>\n"
    "</blockquote>"
)


# ─────────────────────────────────────────
#  Callback-хэндлеры меню игр
# ─────────────────────────────────────────
@game_router.callback_query(F.data == "games")
async def cb_games(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True)
        return
    await call.message.edit_text(GAMES_TEXT, reply_markup=games_keyboard(), parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_dice")
async def cb_dice_menu(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Нечет (x1.9)",  callback_data="bet_куб_нечет",   icon_custom_emoji_id=EMOJI_NECHET),
            InlineKeyboardButton(text="Чет (x1.9)",    callback_data="bet_куб_чет",     icon_custom_emoji_id=EMOJI_CHET),
        ],
        [
            InlineKeyboardButton(text="Меньше (x1.9)", callback_data="bet_куб_мал",     icon_custom_emoji_id=EMOJI_LESS),
            InlineKeyboardButton(text="Больше (x1.9)", callback_data="bet_куб_бол",     icon_custom_emoji_id=EMOJI_MORE),
        ],
        [
            InlineKeyboardButton(text="2-меньше (x3.8)", callback_data="bet_куб_2меньше", icon_custom_emoji_id=EMOJI_2LESS),
            InlineKeyboardButton(text="2-больше (x3.8)", callback_data="bet_куб_2больше", icon_custom_emoji_id=EMOJI_2MORE),
        ],
        [
            InlineKeyboardButton(text="Точное число (x5.7)", callback_data="game_menu_dice_exact", icon_custom_emoji_id=EMOJI_NUMBER),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>🎲 Кубик — выберите тип ставки:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_dice_exact")
async def cb_dice_exact(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1️⃣ (x5.7)", callback_data="bet_куб_1"),
            InlineKeyboardButton(text="2️⃣ (x5.7)", callback_data="bet_куб_2"),
            InlineKeyboardButton(text="3️⃣ (x5.7)", callback_data="bet_куб_3"),
        ],
        [
            InlineKeyboardButton(text="4️⃣ (x5.7)", callback_data="bet_куб_4"),
            InlineKeyboardButton(text="5️⃣ (x5.7)", callback_data="bet_куб_5"),
            InlineKeyboardButton(text="6️⃣ (x5.7)", callback_data="bet_куб_6"),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="game_menu_dice", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>🎰 Точное число — выберите:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_basketball")
async def cb_basketball_menu(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3-очковый (x5.7)", callback_data="bet_баскет_3очка", icon_custom_emoji_id=EMOJI_3POINT)],
        [
            InlineKeyboardButton(text="Гол (x1.85)", callback_data="bet_баскет_гол",  icon_custom_emoji_id=EMOJI_GOAL),
            InlineKeyboardButton(text="Мимо (x1.7)", callback_data="bet_баскет_мимо", icon_custom_emoji_id=EMOJI_MISS),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>🏀 Баскетбол — выберите исход:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_football")
async def cb_football_menu(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Гол (x1.35)",  callback_data="bet_футбол_гол",  icon_custom_emoji_id=EMOJI_GOAL),
            InlineKeyboardButton(text="Мимо (x1.75)", callback_data="bet_футбол_мимо", icon_custom_emoji_id=EMOJI_MISS),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>⚽ Футбол — выберите исход:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_darts")
async def cb_darts_menu(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚪ Белое (x2.35)",   callback_data="bet_дартс_белое"),
            InlineKeyboardButton(text="🔴 Красное (x1.9)",  callback_data="bet_дартс_красное"),
        ],
        [
            InlineKeyboardButton(text="Центр (x5.7)", callback_data="bet_дартс_центр", icon_custom_emoji_id=EMOJI_3POINT),
            InlineKeyboardButton(text="Мимо (x5.7)",  callback_data="bet_дартс_мимо",  icon_custom_emoji_id=EMOJI_MISS),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>🎯 Дартс — выберите исход:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


@game_router.callback_query(F.data == "game_menu_bowling")
async def cb_bowling_menu(call: CallbackQuery):
    if not is_owner_fn(call.message.message_id, call.from_user.id):
        await call.answer("🚫 Это не ваша кнопка!", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Победа (x1.8)",    callback_data="bet_боулинг_победа",    icon_custom_emoji_id=EMOJI_GOAL),
            InlineKeyboardButton(text="Поражение (x1.8)", callback_data="bet_боулинг_поражение", icon_custom_emoji_id=EMOJI_MISS),
        ],
        [InlineKeyboardButton(text="Страйк (x5.7)", callback_data="bet_боулинг_страйк", icon_custom_emoji_id=EMOJI_3POINT)],
        [InlineKeyboardButton(text="Назад", callback_data="games", icon_custom_emoji_id=EMOJI_BACK)],
    ])
    await call.message.edit_text("<blockquote><b>🎳 Боулинг — выберите исход:</b></blockquote>",
                                 reply_markup=markup, parse_mode='HTML')
    set_owner_fn(call.message.message_id, call.from_user.id)
    await call.answer()


# ─────────────────────────────────────────
#  Запрос суммы ставки (callback bet_*)
# ─────────────────────────────────────────
@game_router.callback_query(F.data.startswith("bet_"))
async def cb_request_amount(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id

    allowed, wait = _check_rate_limit(uid)
    if not allowed:
        await call.answer(f"⏳ Подождите {wait} сек", show_alert=True); return

    if uid in _active_games:
        await call.answer("⏳ Дождитесь окончания игры!", show_alert=True); return

    bet_type = call.data[4:]  # убираем "bet_"
    if _get_bet_config(bet_type) is None:
        await call.answer("❌ Неизвестная ставка", show_alert=True); return

    balance = db_get_px(uid)
    _pending_bets[uid] = bet_type

    await state.set_state(BetStates.waiting_for_amount)

    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="cancel_bet", icon_custom_emoji_id=EMOJI_BACK)
    ]])
    await call.message.edit_text(
        f"<blockquote><b>💰 Введите сумму ставки</b>\n\n"
        f"Ваш баланс: <code>{balance:.2f} Px</code>\n"
        f"Мин: <code>{MIN_BET}</code> | Макс: <code>{MAX_BET}</code></blockquote>",
        parse_mode='HTML', reply_markup=markup
    )
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


@game_router.callback_query(F.data == "cancel_bet")
async def cb_cancel_bet(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    _pending_bets.pop(uid, None)
    await state.clear()
    await call.message.edit_text(GAMES_TEXT, reply_markup=games_keyboard(), parse_mode='HTML')
    set_owner_fn(call.message.message_id, uid)
    await call.answer()


# ─────────────────────────────────────────
#  Обработка введённой суммы (FSM)
# ─────────────────────────────────────────
@game_router.message(BetStates.waiting_for_amount)
async def msg_process_amount(message: Message, state: FSMContext):
    uid = message.from_user.id

    if uid not in _pending_bets:
        await state.clear()
        return

    if uid in _active_games:
        await message.answer("⏳ Дождитесь окончания текущей игры!")
        return

    try:
        amount = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("❌ Введите числовое значение, например: <code>100</code>", parse_mode='HTML')
        return

    if amount < MIN_BET:
        await message.answer(f"❌ Минимальная ставка: <code>{MIN_BET} Px</code>", parse_mode='HTML')
        return
    if amount > MAX_BET:
        await message.answer(f"❌ Максимальная ставка: <code>{MAX_BET} Px</code>", parse_mode='HTML')
        return

    bet_type = _pending_bets.pop(uid, None)
    await state.clear()

    if bet_type is None:
        await message.answer("❌ Сессия ставки истекла. Начните заново.")
        return

    name = _nickname(message.from_user)
    await _execute_bet(uid, name, amount, bet_type, message, message.chat.id)


# ─────────────────────────────────────────
#  Текстовые команды (куб чет 100, etc.)
# ─────────────────────────────────────────
@game_router.message(lambda m: is_bet_command(m.text or ""))
async def msg_text_bet(message: Message):
    uid = message.from_user.id

    allowed, wait = _check_rate_limit(uid)
    if not allowed:
        await message.answer(f"⏳ Подождите {wait} сек перед следующей ставкой")
        return

    if uid in _active_games:
        await message.answer("⏳ Дождитесь окончания текущей игры!")
        return

    parsed = _parse_bet_command(message.text)
    if parsed is None:
        await message.answer(
            "<blockquote>❌ <b>Неверный формат!</b>\n\n"
            "Пример: <code>куб чет 100</code></blockquote>",
            parse_mode='HTML'
        )
        return

    bet_type, amount = parsed
    name = _nickname(message.from_user)
    await _execute_bet(uid, name, amount, bet_type, message, message.chat.id)
