"""
roulette.py — Игра «Рулетка» для PixelX-бота
"""

import asyncio
import re
import random
import time
import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode

log = logging.getLogger(__name__)

roulette_router = Router()

# ─────────────────────────────────────────
#  Injected references
# ─────────────────────────────────────────
_bot              = None
_db_get_px        = None
_db_add_px        = None
_db_try_spend_px  = None
_db_get_or_create = None
_db_save_result   = None
_db_get_last      = None
_db_update_stats  = None
is_owner_fn       = None
set_owner_fn      = None


def set_bot_ref(bot) -> None:
    global _bot
    _bot = bot


def set_db_fns(get_px, add_px, try_spend_px, get_or_create, update_stats=None) -> None:
    global _db_get_px, _db_add_px, _db_try_spend_px, _db_get_or_create, _db_update_stats
    _db_get_px        = get_px
    _db_add_px        = add_px
    _db_try_spend_px  = try_spend_px
    _db_get_or_create = get_or_create
    _db_update_stats  = update_stats


def set_db_log_fns(save_result_fn, get_last_fn) -> None:
    global _db_save_result, _db_get_last
    _db_save_result = save_result_fn
    _db_get_last    = get_last_fn


# ─────────────────────────────────────────
#  Константы
# ─────────────────────────────────────────
MAX_BETS_PER_PLAYER = 36
MAX_BETS_TOTAL      = 200
AUTO_START_DELAY    = 120
GO_COOLDOWN         = 15

BET_MIN             = 10
BET_MAX             = 100_000_000

BET_RATE_LIMIT      = 2.0

MULTIPLIER_NUMBER   = 35.0
MULTIPLIER_OTHER    = 1.9

# ── Множители для диапазонов (по количеству чисел в диапазоне) ──
# 2 числа → 18x, 3 → 12x, 4 → 8x, 5 → 6x, 6 → 5x,
# 7 → 4.5x, 8 → 4x, 9 → 3.5x, 10 → 3x,
# 11-13 → 2.7x, 14-18 → 2x, 19-25 → 1.4x, 26-30 → 1.1x
RANGE_MULTIPLIERS: dict[int, float] = {
    2:  18.0,
    3:  12.0,
    4:  8.0,
    5:  6.0,
    6:  5.0,
    7:  4.5,
    8:  4.0,
    9:  3.5,
    10: 3.0,
    11: 2.7,
    12: 2.7,
    13: 2.7,
    14: 2.0,
    15: 2.0,
    16: 2.0,
    17: 2.0,
    18: 2.0,
    19: 1.4,
    20: 1.4,
    21: 1.4,
    22: 1.4,
    23: 1.4,
    24: 1.4,
    25: 1.4,
    26: 1.1,
    27: 1.1,
    28: 1.1,
    29: 1.1,
    30: 1.1,
}
RANGE_MAX_SIZE = 30  # максимальный размер диапазона

RED_NUMBERS   = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18,
                            19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK_NUMBERS = frozenset({2, 4, 6, 8, 10, 11, 13, 15, 17,
                            20, 22, 24, 26, 28, 29, 31, 33, 35})

ROULETTE_STICKERS: dict[int, tuple[str, str]] = {
    0:  ("green", "CAACAgIAAxkBAAIGwmmxFg5jCcqad0pAyUvPy7r_JZ6DAAIxcQACwY-oS-C0sJmfrQEJOgQ"),
    1:  ("red",   "CAACAgIAAxkBAAIHImmxFm5HHmsAAsdeaDd6LUqjQJDyBNgACYm0AAsV_qUvwV2I-O_92MzoE"),
    2:  ("black", "CAACAgIAAxkBAAIGommxFfXhc3pa86VoCY3D7ezK5tC-AAK7cAACa3ypS4wePbZsMruEOgQ"),
    3:  ("red",   "CAACAgIAAxkBAAIGzmmxFhgXXOI5qggmOjR2CzIvBbX2AAJ_awAChs2pS_k2EHscsHMtOgQ"),
    4:  ("black", "CAACAgIAAxkBAAIG8mmxFih70Ku7WVcsnDGCzw8Bq8jVAAIZbAACCZaoS8Npzo5cAAFidjoE"),
    5:  ("red",   "CAACAgIAAxkBAAIGjmmxFcwx5cG6c1_Y6LX2xcZueEUMAAJobwAC9nSpSzXRYISSrFfcOgQ"),
    6:  ("black", "CAACAgIAAxkBAAIHAmmxFl5RgBFKdAlmKEDI5FlBkEMCAAIicAACSSCpS6betiFUYw5jOgQ"),
    7:  ("red",   "CAACAgIAAxkBAAIHHmmxFm16lZUJQidRVMva1ngeVroRAAKmZQACDFCwS846uoxbMOz3OgQ"),
    8:  ("black", "CAACAgIAAxkBAAIG_mmxFly2JRMyDBC4aam1Gd8bR0stAAJzaQACjTKpSyt4s_ED4n5pOgQ"),
    9:  ("red",   "CAACAgIAAxkBAAIHBmmxFmG8ipxgVGJx1gwEVJpzNXI6AAKDZgACtT6pS8GwDlCmkxgEOgQ"),
    10: ("black", "CAACAgIAAxkBAAIGxmmxFg8e_4kLkc2ebW7T-GgDpD9mAAIIbAACf0qoS2X1__wZ-cAsOgQ"),
    11: ("black", "CAACAgIAAxkBAAIGsmmxFgABr4ZG-QkbbB1Ft8ouY6-NggAC3msAAjl-qUtgCWpsiik4pDoE"),
    12: ("red",   "CAACAgIAAxkBAAIG0mmxFhkHN1YzOW8UR_xVrfSS9WBOAAJzdwACpmSoSxkFgdm1vgewOgQ"),
    13: ("black", "CAACAgIAAxkBAAIHGmmxFmt_ECZDdOvGhhT_MhceimNQAAL1ZQACpS2wS7gD91hUGXcTOgQ"),
    14: ("red",   "CAACAgIAAxkBAAIHDmmxFmXZAXyl_W-I0NVreBelm5OEAAJodQACbTqpS5tpSEceRFC0OgQ"),
    15: ("black", "CAACAgIAAxkBAAIG7mmxFib-1gQx4fWs25SiJP6dyOQ2AAJecgACuDmpS56q2j8hpkidOgQ"),
    16: ("red",   "CAACAgIAAxkBAAIGummxFgWsytUKjaIfCHJ0dPqAGyZTAALedAACXYuoS2LbTcv4YdnxOgQ"),
    17: ("black", "CAACAgIAAxkBAAIG1mmxFhu8FN9FHOA6GfUTEQXPjPqJAAL5cQADyqhLPL6kTTa3_sM6BA"),
    18: ("red",   "CAACAgIAAxkBAAIGymmxFhPCoXfumTN29BekjsgekV4EAAK7cQAClqipS3j426tQd1ALOgQ"),
    19: ("red",   "CAACAgIAAxkBAAIGpmmxFfqBWDdSAAFkIEmgy6lXnfFoDwAC028AAhRvqEs4hAdYEq6-sDoE"),
    20: ("black", "CAACAgIAAxkBAAIG4mmxFiFOGpkOKfVkdkYSVieKLY99AAKaYwACf62pS4iiUDSFR0a7OgQ"),
    21: ("red",   "CAACAgIAAxkBAAIG5mmxFiP2V4XPpAkuW6oeeL6amDWHAAIOeQACQmGoSyHZWALvvqtFOgQ"),
    22: ("black", "CAACAgIAAxkBAAIHCmmxFmOdJ1J1VHxzQYtadWC82ZiYAAKidQACW3ioS57bJaI8iXxFOgQ"),
    23: ("red",   "CAACAgIAAxkBAAIGvmmxFgzE04ymQzXtslj4O7d05GlSAALFcQACeY2oSxlUW8fv_LmXOgQ"),
    24: ("black", "CAACAgIAAxkBAAIGlmmxFe45QvXxbVdGcNTtcfDLxP9oAALieQACsXGwS7coCmwujqd8OgQ"),
    25: ("red",   "CAACAgIAAxkBAAIG6mmxFiW_3Vnq_0aOX16-dVa98eJ0AAJ_cwACSKqpS3Z1Rtbz5CD0OgQ"),
    26: ("black", "CAACAgIAAxkBAAIHFmmxFmp4uQSquM98SKOF9uMSNaRaAAI-awAC__mxS4aGkpB9THDAOgQ"),
    27: ("red",   "CAACAgIAAxkBAAIGrmmxFf6vt6M3L8zFe1evpgm_mSy4AAI9bQACmPWoS_AIYwu8GfFtOgQ"),
    28: ("black", "CAACAgIAAxkBAAIHEmmxFmgfB68vwrJDjCKdMkkpM8kOAAK7bAACJSSoSxMweWRCg47LOgQ"),
    29: ("black", "CAACAgIAAxkBAAIG3mmxFh6QFf7ARdPTnSc6zY2vGd_jAALfbgACHtWpS17RRzdqh8rDOgQ"),
    30: ("red",   "CAACAgIAAxkBAAIG2mmxFh0_fHAchCg-CsAmKqa65cIUAALcbQACOgawS6a-krzx53S7OgQ"),
    31: ("black", "CAACAgIAAxkBAAIGtmmxFgIEOaBaijlOdBAbhG6bLd6aAAIWbwACZGapS8XIF1b-veMEOgQ"),
    32: ("red",   "CAACAgIAAxkBAAIGmmmxFfElX-Nkpo2txQX6wzs0Es0KAAJjcQACUEKxS6dWwVP1E7HPOgQ"),
    33: ("black", "CAACAgIAAxkBAAIGnmmxFfP4Dyl1YvOE-qeAuIis1c8JAAJRcgACKJuxS7u3yZqd0ZC5OgQ"),
    34: ("red",   "CAACAgIAAxkBAAIG9mmxFle3QS3T2g6q32euzva_QnsdAAJpdwACrqOxSwZCP_cVJSUTOgQ"),
    35: ("black", "CAACAgIAAxkBAAIGqmmxFfwz2hCnOloTRVoJyLEd4zlLAAK9aAACzZeoSwtiUBA0gaUNOgQ"),
    36: ("red",   "CAACAgIAAxkBAAIG-mmxFllu0LHZEboDBiE8nHndHm-zAAJRbwACL0moS4HHKaEP45LdOgQ"),
}

# ─────────────────────────────────────────
#  Состояние игры (по chat_id)
# ─────────────────────────────────────────
_games:        dict[int, dict]         = {}
_game_history: dict[int, list[dict]]   = {}
_auto_tasks:   dict[int, asyncio.Task] = {}
_bet_rate:     dict[int, float]        = {}


def _get_game(chat_id: int) -> dict:
    if chat_id not in _games:
        _games[chat_id] = {
            "bets":           [],
            "running":        False,
            "first_bet_time": None,
            "lock":           asyncio.Lock(),
        }
    return _games[chat_id]


def _get_history(chat_id: int) -> list[dict]:
    if chat_id not in _game_history:
        _game_history[chat_id] = []
    return _game_history[chat_id]


def _push_history(chat_id: int, number: int, color: str) -> None:
    h = _get_history(chat_id)
    h.append({"number": number, "color": color})
    while len(h) > 10:
        h.pop(0)


# ─────────────────────────────────────────
#  Вспомогательные функции
# ─────────────────────────────────────────
def _color(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in RED_NUMBERS else "black"


_C_EMOJI = {"red": "🔴", "black": "⚫", "green": "🟢"}


def _ce(color: str) -> str:
    return _C_EMOJI.get(color, "⚪")


def _check_win(bet_type: str, bet_value, result: int) -> bool:
    if bet_type == "number":
        return result == bet_value
    if bet_type == "range":
        # bet_value = (lo, hi) — включительно
        lo, hi = bet_value
        return lo <= result <= hi
    if bet_type == "red":
        return result in RED_NUMBERS
    if bet_type == "black":
        return result in BLACK_NUMBERS
    if bet_type == "even":
        return result != 0 and result % 2 == 0
    if bet_type == "odd":
        return result != 0 and result % 2 == 1
    return False


def _mult(bet_type: str, bet_value=None) -> float:
    if bet_type == "number":
        return MULTIPLIER_NUMBER
    if bet_type == "range":
        lo, hi = bet_value
        size = hi - lo + 1
        return RANGE_MULTIPLIERS.get(size, 1.0)
    return MULTIPLIER_OTHER


def _bet_label(bet_type: str, bet_value) -> str:
    if bet_type == "range":
        lo, hi = bet_value
        size = hi - lo + 1
        mult = RANGE_MULTIPLIERS.get(size, 1.0)
        return f"Диапазон {lo}–{hi} (×{mult})"
    labels = {
        "number": f"Число {bet_value}",
        "red":    "🔴 Красное",
        "black":  "⚫ Чёрное",
        "even":   "Чётное",
        "odd":    "Нечётное",
    }
    return labels.get(bet_type, bet_type)


def _user_link(uid: int, username, fname: str) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={uid}">{fname}</a>'


def _count_player_bets(game: dict, uid: int) -> int:
    return sum(1 for b in game["bets"] if b["uid"] == uid)


# ─────────────────────────────────────────
#  Парсер диапазона
#  Принимает строку вида "5-20" или "0-10"
#  Возвращает (lo, hi) или None
# ─────────────────────────────────────────
def _parse_range(text: str):
    """
    Парсит диапазон вида 'lo-hi', например '5-20', '0-36'.
    Возвращает (lo, hi) если корректно, иначе None.
    Условия:
      - lo < hi
      - lo >= 0, hi <= 36
      - размер (hi - lo + 1) <= RANGE_MAX_SIZE
      - размер >= 2
    """
    m = re.match(r'^(\d+)-(\d+)$', text.strip())
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2))
    if lo >= hi:
        return None
    if lo < 0 or hi > 36:
        return None
    size = hi - lo + 1
    if size < 2 or size > RANGE_MAX_SIZE:
        return None
    return (lo, hi)


# ─────────────────────────────────────────
#  Парсер ставки
# ─────────────────────────────────────────
# ─────────────────────────────────────────
#  Парсер одиночного типа ставки
#  Принимает ОДИН токен (слово/число/диапазон)
#  Возвращает ("тип", значение) | ("range_error", текст) | None
# ─────────────────────────────────────────
_EVEN_WORDS  = {"чет", "четное", "чётное", "even"}
_ODD_WORDS   = {"нечет", "нечётное", "нечетное", "нечёт", "odd"}
_RED_WORDS   = {"к", "красное", "крас", "red"}
_BLACK_WORDS = {"ч", "черное", "чёрное", "black", "чер"}


def _parse_token(token: str):
    """
    Парсит один токен ставки.
    Возвращает (bet_type, bet_value) | ("range_error", token) | None
    """
    t = token.strip().lower()
    if not t:
        return None
    if t in _EVEN_WORDS:
        return ("even", None)
    if t in _ODD_WORDS:
        return ("odd", None)
    if t in _RED_WORDS:
        return ("red", None)
    if t in _BLACK_WORDS:
        return ("black", None)
    # Диапазон вида lo-hi
    if re.match(r'^\d+-\d+$', t):
        rr = _parse_range(t)
        if rr is None:
            return ("range_error", token)
        return ("range", rr)
    # Одиночное число
    if t.isdigit():
        n = int(t)
        if n > 36:
            return None
        return ("number", n)
    return None


def _parse_bet(text: str):
    """
    Парсит строку ставки: СУММА ТИП [ТИП2 ТИП3 ...]
    Примеры:
      100 к
      100 5-20
      100 2-30 2-24 3-7 к
      100 7 14 21
    Возвращает:
      (amount, [(bet_type, bet_value), ...])  — успех
      ("range_error", token)                  — некорректный диапазон
      None                                    — не распознано
    """
    text = text.strip()
    # Первый токен — сумма
    parts = text.split()
    if len(parts) < 2:
        return None

    amount_str = parts[0].replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        return None
    if amount <= 0:
        return None

    # Остальные токены — типы ставок
    bet_tokens = parts[1:]
    new_bets: list[tuple] = []

    for token in bet_tokens:
        result = _parse_token(token)
        if result is None:
            return None
        if isinstance(result, tuple) and result[0] == "range_error":
            return result  # пробрасываем ошибку диапазона
        new_bets.append(result)

    if not new_bets:
        return None

    return amount, new_bets


# ─────────────────────────────────────────
#  Автозапуск (2 минуты)
# ─────────────────────────────────────────
async def _auto_start_coro(chat_id: int) -> None:
    try:
        await asyncio.sleep(AUTO_START_DELAY)
        game = _get_game(chat_id)
        if game["running"] or not game["bets"]:
            return
        _auto_tasks.pop(chat_id, None)
        try:
            await _bot.send_message(
                chat_id,
                "⏳ <b>2 минуты истекли — запускаю рулетку!</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.warning("auto_start: failed to send message: %s", e)
        await _execute_game(chat_id)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.exception("auto_start_coro error: %s", e)


def _schedule_auto(chat_id: int) -> None:
    existing = _auto_tasks.get(chat_id)
    if existing is None or existing.done():
        _auto_tasks[chat_id] = asyncio.create_task(_auto_start_coro(chat_id))


def _cancel_auto(chat_id: int) -> None:
    task = _auto_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


# ─────────────────────────────────────────
#  Запуск и расчёт игры
# ─────────────────────────────────────────
async def _execute_game(chat_id: int) -> None:
    game = _get_game(chat_id)

    async with game["lock"]:
        if game["running"]:
            log.debug("_execute_game: already running for chat %s", chat_id)
            return
        if not game["bets"]:
            log.debug("_execute_game: no bets for chat %s", chat_id)
            return

        game["running"]        = True
        game["first_bet_time"] = None
        _cancel_auto(chat_id)

        bets         = game["bets"][:]
        game["bets"] = []

    log.info("_execute_game: spinning for chat %s, bets=%d", chat_id, len(bets))

    result     = random.randint(0, 36)
    color      = _color(result)
    sticker_id = ROULETTE_STICKERS[result][1]
    stk_msg    = None

    try:
        try:
            stk_msg = await _bot.send_sticker(chat_id, sticker_id)
            log.info("_execute_game: sticker sent msg_id=%s", stk_msg.message_id)
        except Exception as e:
            log.warning("_execute_game: send_sticker failed: %s", e)

        await asyncio.shield(asyncio.sleep(3))

        if stk_msg is not None:
            try:
                await _bot.delete_message(chat_id, stk_msg.message_id)
                log.info("_execute_game: sticker deleted")
            except Exception as e:
                log.warning("_execute_game: delete sticker failed: %s", e)

        ce    = _ce(color)
        lines = [f'<tg-emoji emoji-id="5341498088408234504">🎟</tg-emoji><b>Рулетка!</b>  {ce} <b>{result}</b>\n']

        win_lines:  list[str] = []
        lose_lines: list[str] = []

        for b in bets:
            uid   = b["uid"]
            link  = _user_link(uid, b.get("username"), b.get("first_name", "?"))
            amt   = b["amount"]
            bt    = b["bet_type"]
            bv    = b["bet_value"]
            label = _bet_label(bt, bv)

            if _check_win(bt, bv, result):
                mult   = _mult(bt, bv)
                payout = round(amt * mult, 2)
                profit = round(payout - amt, 2)
                try:
                    _db_add_px(uid, payout)
                except Exception as e:
                    log.error("_execute_game: db_add_px failed uid=%s: %s", uid, e)
                if _db_update_stats:
                    try:
                        _db_update_stats(uid, won=profit, lost=0.0)
                    except Exception as e:
                        log.error("_execute_game: update_stats win uid=%s: %s", uid, e)
                win_lines.append(
                    f'<blockquote> {link}  |  {label}  |  <b>+{profit:,.2f} Px</b></blockquote>'
                )
            else:
                if _db_update_stats:
                    try:
                        _db_update_stats(uid, won=0.0, lost=amt)
                    except Exception as e:
                        log.error("_execute_game: update_stats lose uid=%s: %s", uid, e)
                lose_lines.append(
                    f'<blockquote> {link}  |  {label}  |  <b>-{amt:,.2f} Px</b></blockquote>'
                )

        if win_lines:
            lines.append('<tg-emoji emoji-id="5429651785352501917">🎟</tg-emoji><b>Победители:</b>')
            lines += win_lines
        if lose_lines:
            if win_lines:
                lines.append("")
            lines.append('<tg-emoji emoji-id="5429518319243775957">🎟</tg-emoji><b>Проигравшие:</b>')
            lines += lose_lines

        _push_history(chat_id, result, color)

        if _db_save_result:
            try:
                _db_save_result(chat_id, result, color)
            except Exception as e:
                log.exception("roulette db_save_result failed: %s", e)

        await _bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)
        log.info("_execute_game: done chat=%s result=%d", chat_id, result)

    except BaseException as ex:
        log.exception("_execute_game CRITICAL error chat=%s: %s", chat_id, ex)
        for b in bets:
            try:
                _db_add_px(b["uid"], b["amount"])
            except Exception as e:
                log.error("refund failed uid=%s: %s", b["uid"], e)
        try:
            await _bot.send_message(
                chat_id,
                "⚠️ <b>Ошибка рулетки. Ставки возвращены!</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        game["running"] = False
        log.info("_execute_game: running=False for chat %s", chat_id)


# ─────────────────────────────────────────
#  Логика размещения ставки
# ─────────────────────────────────────────
async def _place_bet(message: Message, amount: float, new_bets: list) -> None:
    uid      = message.from_user.id
    chat_id  = message.chat.id
    username = message.from_user.username
    fname    = message.from_user.first_name or "?"

    _db_get_or_create(message.from_user)

    if not isinstance(amount, (int, float)) or amount != amount:
        return
    amount = round(float(amount), 2)

    if amount < BET_MIN:
        await message.reply(
            f"❌ Минимальная ставка: <b>{BET_MIN} Px</b>",
            parse_mode=ParseMode.HTML,
        )
        return
    if amount > BET_MAX:
        await message.reply(
            f"❌ Максимальная ставка: <b>{BET_MAX:,.0f} Px</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    now       = time.monotonic()
    last_time = _bet_rate.get(uid, 0.0)
    if now - last_time < BET_RATE_LIMIT:
        wait = round(BET_RATE_LIMIT - (now - last_time), 1)
        await message.reply(
            f"⏳ Не так быстро! Подождите <b>{wait} сек.</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    game = _get_game(chat_id)

    if game["running"]:
        await message.reply("⏳ Игра уже идёт! Дождись следующего раунда.")
        return

    total_bets = len(game["bets"])
    if total_bets + len(new_bets) > MAX_BETS_TOTAL:
        await message.reply(
            "❌ Достигнут общий лимит раунда!\n",
            parse_mode=ParseMode.HTML,
        )
        return

    player_bets = _count_player_bets(game, uid)
    if player_bets + len(new_bets) > MAX_BETS_PER_PLAYER:
        await message.reply(
            "❌ Личный лимит ставок!\n",
            parse_mode=ParseMode.HTML,
        )
        return

    total_cost = round(amount * len(new_bets), 2)
    if total_cost <= 0:
        return

    if not _db_try_spend_px(uid, total_cost):
        await message.reply(
            "❌ Недостаточно Px!\n",
            parse_mode=ParseMode.HTML,
        )
        return

    _bet_rate[uid] = time.monotonic()

    first_bet = len(game["bets"]) == 0

    for bt, bv in new_bets:
        game["bets"].append({
            "uid":        uid,
            "username":   username,
            "first_name": fname,
            "amount":     amount,
            "bet_type":   bt,
            "bet_value":  bv,
        })

    if first_bet:
        game["first_bet_time"] = time.monotonic()
        _schedule_auto(chat_id)

    conf_lines: list[str] = ['<tg-emoji emoji-id="5206607081334906820">🎟</tg-emoji><b>Ставки приняты!</b>\n']
    for bt, bv in new_bets:
        conf_lines.append(
            f"<blockquote><b><code>{amount:,.2f} Px</code></b> — <b>{_bet_label(bt, bv)}</b></blockquote>"
        )

    conf_lines.append(
        "\n<blockquote>"
        '<tg-emoji emoji-id="5440621591387980068">🎟</tg-emoji><b>Автозапуск через 2 мин!</b>'
        "</blockquote>"
    )

    await message.reply("\n".join(conf_lines), parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────
#  Отмена ставок игрока
# ─────────────────────────────────────────
async def _cancel_bets(message: Message) -> None:
    uid     = message.from_user.id
    chat_id = message.chat.id
    game    = _get_game(chat_id)

    if game["running"]:
        await message.reply(
            "⏳ <b>Игра уже идёт — отмена невозможна!</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    player_bets = [b for b in game["bets"] if b["uid"] == uid]

    if not player_bets:
        await message.reply(
            '<tg-emoji emoji-id="5429518319243775957">🎟</tg-emoji> <b>У вас нет активных ставок.</b>',
            parse_mode=ParseMode.HTML,
        )
        return

    refund_total = round(sum(b["amount"] for b in player_bets), 2)
    game["bets"] = [b for b in game["bets"] if b["uid"] != uid]

    try:
        _db_add_px(uid, refund_total)
    except Exception as e:
        log.error("_cancel_bets: db_add_px failed uid=%s: %s", uid, e)

    if not game["bets"]:
        _cancel_auto(chat_id)
        game["first_bet_time"] = None

    link = _user_link(uid, message.from_user.username, message.from_user.first_name or "?")

    await message.reply(
        f'<tg-emoji emoji-id="5429518319243775957">🎟</tg-emoji> <b>Ставки отменены!</b>\n\n'
        f'<blockquote>'
        f'{link}  |  Отменено ставок: <b>{len(player_bets)}</b>\n'
        f'<tg-emoji emoji-id="5206607081334906820">🎟</tg-emoji>  Возврат: <b>+{refund_total:,.2f} Px</b>'
        f'</blockquote>',
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────
#  /r и /рул — команды со слешем
# ─────────────────────────────────────────
@roulette_router.message(Command("r", "рул"))
async def cmd_r(message: Message) -> None:
    text  = message.text or ""
    parts = text.split(None, 1)
    if len(parts) < 2:
        await message.reply(
            '<tg-emoji emoji-id="5334544901428229844">🎟</tg-emoji> <b>Инструкция</b>\n\n'
            "<blockquote>"
            "<code>100 7</code>              — число 7 (×35)\n"
            "<code>100 5 17 32</code>         — несколько чисел (×35 каждое)\n"
            "<code>100 к</code>              — красное (×1.9)\n"
            "<code>100 ч</code>              — чёрное (×1.9)\n"
            "<code>100 чет</code>            — чётное (×1.9)\n"
            "<code>100 нечет</code>          — нечётное (×1.9)\n"
            "<code>100 5-20</code>           — диапазон 5–20 (×2.0)\n\n"
            "<b>Несколько ставок одной командой:</b>\n"
            "<code>100 2-30 2-24 3-7</code>  — три диапазона по 100 Px\n"
            "<code>100 к ч 7</code>          — красное + чёрное + число 7\n"
            "<code>100 5-20 к нечет</code>   — диапазон + красное + нечётное\n\n"
            "<b>Множители диапазонов:</b>\n"
            "<code>2 → ×18  |  3 → ×12  |  4 → ×8  |  5 → ×6</code>\n"
            "<code>6 → ×5  |  7 → ×4.5  |  8 → ×4  |  9 → ×3.5</code>\n"
            "<code>10 → ×3  |  11–13 → ×2.7  |  14–18 → ×2</code>\n"
            "<code>19–25 → ×1.4  |  26–30 → ×1.1</code>\n\n"
            "<code>го</code>              — запустить игру\n"
            "<code>лог</code>             — последние 10 результатов\n"
            "<code>отмена / cancel</code> — отменить свои ставки\n\n"
            f'Лимит: <tg-emoji emoji-id="5420323339723881652">🎟</tg-emoji><b>{MAX_BETS_PER_PLAYER}</b> ставок на игрока!  |  '
            f'<tg-emoji emoji-id="5420323339723881652">🎟</tg-emoji><b>{MAX_BETS_TOTAL}</b> ставок в раунде'
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    parsed = _parse_bet(parts[1])
    if parsed is None:
        await message.reply(
            "❌ Не могу распознать ставку!\n",
            parse_mode=ParseMode.HTML,
        )
        return
    if isinstance(parsed, tuple) and parsed[0] == "range_error":
        await message.reply(
            f"❌ Некорректный диапазон <code>{parsed[1]}</code>\n\n"
            f"<blockquote>Диапазон должен быть вида <code>lo-hi</code>, где:\n"
            f"• lo &lt; hi\n"
            f"• оба числа от 0 до 36\n"
            f"• размер диапазона от 2 до {RANGE_MAX_SIZE} чисел</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    amount, new_bets = parsed
    await _place_bet(message, amount, new_bets)


# ─────────────────────────────────────────
#  /отмена и /cancel — команды со слешем
# ─────────────────────────────────────────
@roulette_router.message(Command("отмена", "cancel"))
async def cmd_cancel_slash(message: Message) -> None:
    await _cancel_bets(message)


# ─────────────────────────────────────────
#  лог
# ─────────────────────────────────────────
async def _send_log(message: Message) -> None:
    chat_id = message.chat.id
    history = _get_history(chat_id)

    if not history and _db_get_last:
        try:
            rows = _db_get_last(chat_id, 10)
            for r in reversed(rows):
                _push_history(chat_id, r["number"], r["color"])
            history = _get_history(chat_id)
        except Exception:
            pass

    if not history:
        await message.reply(
            '<tg-emoji emoji-id="5323442290708985472">🎟</tg-emoji> <b>История игр</b>\n\n'
            "<blockquote>Игры ещё не проводились.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines: list[str] = ['<tg-emoji emoji-id="5323442290708985472">🎟</tg-emoji> <b>Последние 10 игр</b>\n']
    for entry in reversed(history):
        n  = entry["number"]
        ce = _ce(entry["color"])
        lines.append(f"{ce} <b>{n}</b>")

    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)


@roulette_router.message(Command("rlog", "рлог", "лог"))
async def cmd_rlog(message: Message) -> None:
    await _send_log(message)


# ─────────────────────────────────────────
#  Публичные функции для low_priority_router
# ─────────────────────────────────────────

def is_roulette_go(text: str) -> bool:
    return text.strip().lower() in ("го", "go")


async def handle_roulette_go(message: Message) -> bool:
    chat_id = message.chat.id
    uid     = message.from_user.id
    game    = _get_game(chat_id)

    if not game["bets"] or game["running"]:
        return False

    if not any(b["uid"] == uid for b in game["bets"]):
        return False

    first_bet_time = game.get("first_bet_time")
    if first_bet_time is not None:
        elapsed   = time.monotonic() - first_bet_time
        remaining = GO_COOLDOWN - elapsed
        if remaining > 0:
            secs = int(remaining) + 1
            await message.reply(
                f"⏳ Подождите ещё <b>{secs} сек.</b> перед запуском!",
                parse_mode=ParseMode.HTML,
            )
            return True

    _cancel_auto(chat_id)
    await _execute_game(chat_id)
    return True


def is_roulette_bet(text: str) -> bool:
    result = _parse_bet(text)
    if result is None:
        return False
    # range_error тоже нужно обработать (покажем ошибку пользователю)
    if isinstance(result, tuple) and result[0] == "range_error":
        return True
    return True


async def handle_roulette_bet(message: Message) -> bool:
    parsed = _parse_bet((message.text or "").strip())
    if parsed is None:
        return False
    if isinstance(parsed, tuple) and parsed[0] == "range_error":
        await message.reply(
            f"❌ Некорректный диапазон <code>{parsed[1]}</code>\n\n"
            f"<blockquote>Диапазон должен быть вида <code>lo-hi</code>, где:\n"
            f"• lo &lt; hi\n"
            f"• оба числа от 0 до 36\n"
            f"• размер от 2 до {RANGE_MAX_SIZE} чисел</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return True
    amount, new_bets = parsed
    await _place_bet(message, amount, new_bets)
    return True


def is_roulette_log(text: str) -> bool:
    return text.strip().lower() in ("лог", "log")


async def handle_roulette_log(message: Message) -> bool:
    await _send_log(message)
    return True


def is_roulette_cancel(text: str) -> bool:
    return text.strip().lower() in ("отмена", "cancel")


async def handle_roulette_cancel(message: Message) -> bool:
    await _cancel_bets(message)
    return True
