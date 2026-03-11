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
    if bet_type == "red":
        return result in RED_NUMBERS
    if bet_type == "black":
        return result in BLACK_NUMBERS
    if bet_type == "even":
        return result != 0 and result % 2 == 0
    if bet_type == "odd":
        return result != 0 and result % 2 == 1
    return False


def _mult(bet_type: str) -> float:
    return MULTIPLIER_NUMBER if bet_type == "number" else MULTIPLIER_OTHER


def _bet_label(bet_type: str, bet_value) -> str:
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
#  Парсер ставки
# ─────────────────────────────────────────
_BET_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s+(.+)$", re.IGNORECASE | re.UNICODE)

_EVEN_WORDS  = {"чет", "четное", "чётное", "even"}
_ODD_WORDS   = {"нечет", "нечётное", "нечетное", "нечёт", "odd"}
_RED_WORDS   = {"к", "красное", "крас", "red"}
_BLACK_WORDS = {"ч", "черное", "чёрное", "black", "чер"}


def _parse_bet(text: str):
    m = _BET_RE.match(text.strip())
    if not m:
        return None

    amount_str = m.group(1).replace(",", ".")
    bet_raw    = m.group(2).strip().lower()

    try:
        amount = float(amount_str)
    except ValueError:
        return None

    if amount <= 0:
        return None

    new_bets: list[tuple] = []

    if bet_raw in _EVEN_WORDS:
        new_bets = [("even", None)]
    elif bet_raw in _ODD_WORDS:
        new_bets = [("odd", None)]
    elif bet_raw in _RED_WORDS:
        new_bets = [("red", None)]
    elif bet_raw in _BLACK_WORDS:
        new_bets = [("black", None)]
    else:
        normalized = bet_raw.replace("-", " ")
        parts = [p.strip() for p in normalized.split() if p.strip()]
        if not parts:
            return None

        num_counts: dict[int, int] = {}
        for part in parts:
            if not part.isdigit():
                return None
            n = int(part)
            if n > 36:
                return None
            num_counts[n] = num_counts.get(n, 0) + 1

        for n, count in num_counts.items():
            for _ in range(count):
                new_bets.append(("number", n))

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

    if game["lock"].locked():
        log.debug("_execute_game: lock busy for chat %s, skipping", chat_id)
        return

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

    try:
        # ── Отправляем стикер (некритично — не падаем если не получилось) ──
        stk_msg = None
        try:
            stk_msg = await _bot.send_sticker(chat_id, sticker_id)
        except Exception as e:
            log.warning("_execute_game: send_sticker failed: %s", e)

        await asyncio.sleep(3)

        if stk_msg is not None:
            try:
                await _bot.delete_message(chat_id, stk_msg.message_id)
            except Exception as e:
                log.warning("_execute_game: delete sticker failed: %s", e)

        # ── Считаем результаты ──
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
                payout = round(amt * _mult(bt), 2)
                profit = round(payout - amt, 2)
                try:
                    _db_add_px(uid, payout)
                except Exception as e:
                    log.error("_execute_game: db_add_px failed uid=%s: %s", uid, e)
                # ── Статистика: победа ──
                if _db_update_stats:
                    try:
                        _db_update_stats(uid, won=profit, lost=0.0)
                    except Exception as e:
                        log.error("_execute_game: update_stats win failed uid=%s: %s", uid, e)
                win_lines.append(
                    f'<blockquote> {link}  |  {label}  |  <b>+{profit:,.2f} Px</b></blockquote>'
                )
            else:
                # ── Статистика: проигрыш ──
                if _db_update_stats:
                    try:
                        _db_update_stats(uid, won=0.0, lost=amt)
                    except Exception as e:
                        log.error("_execute_game: update_stats lose failed uid=%s: %s", uid, e)
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

    except Exception as ex:
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
        conf_lines.append(f"<blockquote><b><code>{amount:,.2f} Px</code></b> — <b>{_bet_label(bt, bv)}</b></blockquote>")

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
            '<tg-emoji emoji-id='5420323339723881652">🎟</tg-emoji> <b>У вас нет активных ставок!</b>',
            parse_mode=ParseMode.HTML,
        )
        return

    # Считаем сумму возврата
    refund_total = round(sum(b["amount"] for b in player_bets), 2)

    # Удаляем ставки игрока из списка
    game["bets"] = [b for b in game["bets"] if b["uid"] != uid]

    # Возвращаем деньги
    try:
        _db_add_px(uid, refund_total)
    except Exception as e:
        log.error("_cancel_bets: db_add_px failed uid=%s: %s", uid, e)

    # Если ставок в раунде больше нет — отменяем автозапуск
    if not game["bets"]:
        _cancel_auto(chat_id)
        game["first_bet_time"] = None

    link = _user_link(uid, message.from_user.username, message.from_user.first_name or "?")

    await message.reply(
        f'<tg-emoji emoji-id="5420323339723881652">🎟</tg-emoji> <b>Ставки отменены!Был возврат!</b>\n\n'
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
            "<code>100 7</code>           — число 7 (×35)\n"
            "<code>100 5-17-32</code>     — числа через дефис (×35 каждое)\n"
            "<code>100 5 17 32</code>     — числа через пробел (×35 каждое)\n"
            "<code>100 к</code>           — красное (×1.9)\n"
            "<code>100 ч</code>           — чёрное (×1.9)\n"
            "<code>100 чет</code>         — чётное (×1.9)\n"
            "<code>100 нечет</code>       — нечётное (×1.9)\n\n"
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
    return _parse_bet(text) is not None


async def handle_roulette_bet(message: Message) -> bool:
    parsed = _parse_bet((message.text or "").strip())
    if parsed is None:
        return False
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
