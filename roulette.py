"""
roulette.py — Игра «Рулетка» для PixelX-бота
=============================================

Команды:
  /r СУММА СТАВКА   — сделать ставку
  го                — запустить игру (только участник с ставкой)
  /rlog             — последние 10 игр

Виды ставок:
  /r 100 7          — число 7         (×35)
  /r 100 4-17-32    — числа 4,17,32   (×35 каждое)
  /r 100 к          — красное         (×1.9)
  /r 100 ч          — чёрное          (×1.9)
  /r 100 чет        — чётное          (×1.9)
  /r 100 нечет      — нечётное        (×1.9)

Автозапуск: через 15 сек после первой ставки в раунде.
Максимум 20 ставок на раунд (сумма всех игроков).
Ставки списываются сразу; при выигрыше начисляется amount × multiplier.
"""

import asyncio
import random
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode

roulette_router = Router()

# ─────────────────────────────────────────
#  Injected references (заполняются из main.py)
# ─────────────────────────────────────────
_bot                    = None
_db_get_px              = None
_db_add_px              = None
_db_try_spend_px        = None
_db_get_or_create       = None
_db_save_result         = None   # db_roulette_save_result
_db_get_last            = None   # db_roulette_get_last
is_owner_fn             = None
set_owner_fn            = None


def set_bot_ref(bot) -> None:
    global _bot
    _bot = bot


def set_db_fns(get_px, add_px, try_spend_px, get_or_create) -> None:
    global _db_get_px, _db_add_px, _db_try_spend_px, _db_get_or_create
    _db_get_px        = get_px
    _db_add_px        = add_px
    _db_try_spend_px  = try_spend_px
    _db_get_or_create = get_or_create


def set_db_log_fns(save_result_fn, get_last_fn) -> None:
    """Подключить функции логирования рулетки из database.py."""
    global _db_save_result, _db_get_last
    _db_save_result = save_result_fn
    _db_get_last    = get_last_fn


# ─────────────────────────────────────────
#  Константы
# ─────────────────────────────────────────
MAX_BETS_PER_GAME  = 20
AUTO_START_DELAY   = 15      # секунд
BET_MIN            = 1
BET_MAX            = 1_000_000

MULTIPLIER_NUMBER  = 35.0
MULTIPLIER_OTHER   = 1.9     # красное/чёрное/чёт/нечёт

RED_NUMBERS   = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18,
                            19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK_NUMBERS = frozenset({2, 4, 6, 8, 10, 11, 13, 15, 17,
                            20, 22, 24, 26, 28, 29, 31, 33, 35})

# Примечание: числа 11 и 27 используют одинаковый sticker file_id
#             (возможно ошибка в исходных данных — проверь сам).
#             То же для пары 32 / 33.
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
    11: ("black", "CAACAgIAAxkBAAIGrmmxFf6vt6M3L8zFe1evpgm_mSy4AAI9bQACmPWoS_AIYwu8GfFtOgQ"),  # ⚠ тот же ID что у 27
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
    27: ("red",   "CAACAgIAAxkBAAIGrmmxFf6vt6M3L8zFe1evpgm_mSy4AAI9bQACmPWoS_AIYwu8GfFtOgQ"),  # ⚠ тот же ID что у 11
    28: ("black", "CAACAgIAAxkBAAIHEmmxFmgfB68vwrJDjCKdMkkpM8kOAAK7bAACJSSoSxMweWRCg47LOgQ"),
    29: ("black", "CAACAgIAAxkBAAIG3mmxFh6QFf7ARdPTnSc6zY2vGd_jAALfbgACHtWpS17RRzdqh8rDOgQ"),
    30: ("red",   "CAACAgIAAxkBAAIG2mmxFh0_fHAchCg-CsAmKqa65cIUAALcbQACOgawS6a-krzx53S7OgQ"),
    31: ("black", "CAACAgIAAxkBAAIGtmmxFgIEOaBaijlOdBAbhG6bLd6aAAIWbwACZGapS8XIF1b-veMEOgQ"),
    32: ("red",   "CAACAgIAAxkBAAIGmmmxFfElX-Nkpo2txQX6wzs0Es0KAAJjcQACUEKxS6dWwVP1E7HPOgQ"),  # ⚠ тот же ID что у 33
    33: ("black", "CAACAgIAAxkBAAIGmmmxFfElX-Nkpo2txQX6wzs0Es0KAAJjcQACUEKxS6dWwVP1E7HPOgQ"),  # ⚠ тот же ID что у 32
    34: ("red",   "CAACAgIAAxkBAAIG9mmxFle3QS3T2g6q32euzva_QnsdAAJpdwACrqOxSwZCP_cVJSUTOgQ"),
    35: ("black", "CAACAgIAAxkBAAIGqmmxFfwz2hCnOloTRVoJyLEd4zlLAAK9aAACzZeoSwtiUBA0gaUNOgQ"),
    36: ("red",   "CAACAgIAAxkBAAIG-mmxFllu0LHZEboDBiE8nHndHm-zAAJRbwACL0moS4HHKaEP45LdOgQ"),
}

# ─────────────────────────────────────────
#  Состояние игры (по chat_id)
# ─────────────────────────────────────────
_games:        dict[int, dict]          = {}
_game_history: dict[int, list[dict]]    = {}
_auto_tasks:   dict[int, asyncio.Task]  = {}


def _get_game(chat_id: int) -> dict:
    if chat_id not in _games:
        _games[chat_id] = {"bets": [], "running": False}
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


def _user_link(uid: int, username: str | None, fname: str) -> str:
    if username:
        return f"@{username}"
    return f'<a href="tg://user?id={uid}">{fname}</a>'


# ─────────────────────────────────────────
#  Автозапуск
# ─────────────────────────────────────────
async def _auto_start_coro(chat_id: int) -> None:
    try:
        await asyncio.sleep(AUTO_START_DELAY)
        game = _get_game(chat_id)
        if not game["running"] and game["bets"]:
            await _execute_game(chat_id)
    except asyncio.CancelledError:
        pass


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
    if game["running"] or not game["bets"]:
        return

    game["running"] = True
    _cancel_auto(chat_id)

    bets = game["bets"][:]
    game["bets"] = []

    result     = random.randint(0, 36)
    color      = _color(result)
    sticker_id = ROULETTE_STICKERS[result][1]

    try:
        # ── Отправляем стикер, ждём 3 сек, удаляем ──
        stk_msg = await _bot.send_sticker(chat_id, sticker_id)
        await asyncio.sleep(3)
        try:
            await _bot.delete_message(chat_id, stk_msg.message_id)
        except Exception:
            pass

        ce = _ce(color)
        lines: list[str] = [f"🎰 <b>Рулетка!</b>  {ce} <b>{result}</b>\n"]

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
                _db_add_px(uid, payout)
                win_lines.append(f"✅ {link}  |  {label}  |  <b>+{profit:,.2f} Px</b>")
            else:
                lose_lines.append(f"❌ {link}  |  {label}  |  <b>-{amt:,.2f} Px</b>")

        if win_lines:
            lines.append("<b>Победители:</b>")
            lines += win_lines
        if lose_lines:
            if win_lines:
                lines.append("")
            lines.append("<b>Проигравшие:</b>")
            lines += lose_lines

        _push_history(chat_id, result, color)
        if _db_save_result:
            try:
                _db_save_result(chat_id, result, color)
            except Exception:
                pass
        await _bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as ex:
        # При ошибке — возвращаем ставки
        for b in bets:
            _db_add_px(b["uid"], b["amount"])
        try:
            await _bot.send_message(
                chat_id,
                f"⚠️ <b>Ошибка рулетки.</b> Ставки возвращены.\n<code>{ex}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    finally:
        game["running"] = False


# ─────────────────────────────────────────
#  /r — поставить ставку
# ─────────────────────────────────────────
_HELP_TEXT = (
    "🎰 <b>Рулетка — поставить ставку</b>\n\n"
    "<blockquote>"
    "Формат: <code>/r СУММА СТАВКА</code>\n\n"
    "Виды ставок:\n"
    "• <code>/r 100 7</code>       — число 7          (×35)\n"
    "• <code>/r 100 4-17-32</code> — числа 4, 17, 32  (×35 каждое)\n"
    "• <code>/r 100 к</code>       — красное           (×1.9)\n"
    "• <code>/r 100 ч</code>       — чёрное            (×1.9)\n"
    "• <code>/r 100 чет</code>     — чётное            (×1.9)\n"
    "• <code>/r 100 нечет</code>   — нечётное          (×1.9)\n\n"
    "После ставок напиши <b>го</b> для немедленного запуска.\n"
    f"Автозапуск через <b>{AUTO_START_DELAY} сек.</b> после первой ставки.\n"
    f"Максимум <b>{MAX_BETS_PER_GAME}</b> ставок на раунд."
    "</blockquote>"
)


@roulette_router.message(Command("r", "рул"))
async def cmd_r(message: Message, command: CommandObject) -> None:
    uid      = message.from_user.id
    chat_id  = message.chat.id
    username = message.from_user.username
    fname    = message.from_user.first_name or ""

    _db_get_or_create(message.from_user)

    raw_args = (command.args or "").strip()
    if not raw_args:
        await message.reply(_HELP_TEXT, parse_mode=ParseMode.HTML)
        return

    args = raw_args.split(None, 1)
    if len(args) < 2:
        await message.reply(_HELP_TEXT, parse_mode=ParseMode.HTML)
        return

    amount_str, bet_raw = args[0], args[1].strip().lower()

    # ── Парсим сумму ──
    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        await message.reply("❌ Неверная сумма!")
        return

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

    # ── Парсим тип ставки ──
    new_bets: list[tuple[str, object]] = []

    if bet_raw in ("чет", "чётное", "четное", "even"):
        new_bets = [("even", None)]
    elif bet_raw in ("нечет", "нечётное", "нечетное", "odd"):
        new_bets = [("odd", None)]
    elif bet_raw in ("к", "красное", "red"):
        new_bets = [("red", None)]
    elif bet_raw in ("ч", "черное", "чёрное", "black"):
        new_bets = [("black", None)]
    else:
        # Одно или несколько чисел через дефис
        parts = [p.strip() for p in bet_raw.split("-") if p.strip()]
        if not parts:
            await message.reply("❌ Укажи ставку!")
            return
        for part in parts:
            if not part.isdigit():
                await message.reply(
                    f"❌ Неизвестная ставка: <code>{part}</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            n = int(part)
            if n > 36:
                await message.reply("❌ Число от 0 до 36!")
                return
            new_bets.append(("number", n))

    if not new_bets:
        await message.reply("❌ Не удалось распознать ставку!")
        return

    game = _get_game(chat_id)

    if game["running"]:
        await message.reply("⏳ Игра уже идёт! Дождись следующего раунда.")
        return

    # ── Проверка лимита ставок ──
    free_slots = MAX_BETS_PER_GAME - len(game["bets"])
    if len(new_bets) > free_slots:
        await message.reply(
            f"❌ Лимит ставок!\n"
            f"<blockquote>Свободных слотов: <b>{free_slots}</b> из {MAX_BETS_PER_GAME}</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Списываем средства ──
    total_cost = round(amount * len(new_bets), 2)
    if not _db_try_spend_px(uid, total_cost):
        bal = _db_get_px(uid)
        await message.reply(
            f"❌ Недостаточно Px!\n"
            f"<blockquote>"
            f"Нужно: <b>{total_cost:,.2f} Px</b>\n"
            f"Баланс: <b>{bal:,.2f} Px</b>"
            f"</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Добавляем ставки ──
    for bt, bv in new_bets:
        game["bets"].append({
            "uid":        uid,
            "username":   username,
            "first_name": fname,
            "amount":     amount,
            "bet_type":   bt,
            "bet_value":  bv,
        })

    # ── Подтверждение ──
    conf_lines: list[str] = ["🎰 <b>Ставки приняты!</b>\n"]
    for bt, bv in new_bets:
        conf_lines.append(f"<b>{amount:,.2f}</b> — {_bet_label(bt, bv)}")

    total_now = len(game["bets"])
    conf_lines.append(
        f"\n<blockquote>"
        f"Ставок в раунде: <b>{total_now}/{MAX_BETS_PER_GAME}</b>\n"
        f"Напиши <b>го</b> для старта или жди {AUTO_START_DELAY} сек."
        f"</blockquote>"
    )

    await message.reply("\n".join(conf_lines), parse_mode=ParseMode.HTML)

    # ── Планируем автозапуск (только при первой ставке в раунде) ──
    _schedule_auto(chat_id)


# ─────────────────────────────────────────
#  /rlog — история последних 10 игр
# ─────────────────────────────────────────
@roulette_router.message(Command("rlog", "рлог"))
async def cmd_rlog(message: Message) -> None:
    chat_id = message.chat.id
    history = _get_history(chat_id)

    if not history:
        await message.reply(
            "🎰 <b>История игр</b>\n\n"
            "<blockquote>Игры ещё не проводились.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines: list[str] = ["🎰 <b>Последние 10 игр</b>\n"]
    # Новые игры сверху
    for entry in reversed(history):
        n  = entry["number"]
        ce = _ce(entry["color"])
        lines.append(f"{ce}-{n}")

    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────
#  «го» — экспортируется в main.py
#  (вставляется в low_priority handler ДО дуэлей)
# ─────────────────────────────────────────
def is_roulette_go(text: str) -> bool:
    """True если текст — команда запуска рулетки."""
    return text.strip().lower() in ("го", "go")


async def handle_roulette_go(message: Message) -> bool:
    """
    Пытается запустить рулетку.
    Возвращает True если игра стартовала.
    Возвращает False если нет ставок или у игрока нет ставки.
    """
    chat_id = message.chat.id
    uid     = message.from_user.id
    game    = _get_game(chat_id)

    if game["running"] or not game["bets"]:
        return False

    # Только участники с ставкой могут запустить
    if not any(b["uid"] == uid for b in game["bets"]):
        return False

    await _execute_game(chat_id)
    return True
