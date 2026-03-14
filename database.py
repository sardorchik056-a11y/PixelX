import sqlite3
import json
import json as _json
from datetime import datetime
from contextlib import contextmanager

DB_PATH = "bot.db"

# ─────────────────────────────────────────
#  Подключение
# ─────────────────────────────────────────
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────
#  Создание таблиц
# ─────────────────────────────────────────
def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY,
                first_name    TEXT    NOT NULL DEFAULT '',
                last_name     TEXT    NOT NULL DEFAULT '',
                username      TEXT    NOT NULL DEFAULT '',
                px            REAL    NOT NULL DEFAULT 0,
                games_played  INTEGER NOT NULL DEFAULT 0,
                total_won     REAL    NOT NULL DEFAULT 0,
                total_lost    REAL    NOT NULL DEFAULT 0,
                registered_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mine (
                uid           INTEGER PRIMARY KEY,
                nox           REAL    NOT NULL DEFAULT 0,
                pickaxe_id    INTEGER NOT NULL DEFAULT 1,
                owned         TEXT    NOT NULL DEFAULT '[1]',
                mining_start  TEXT,
                mining_end    TEXT,
                ticks_paid    INTEGER NOT NULL DEFAULT 0,
                accumulated   REAL    NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS referrals (
                invitee_id    INTEGER PRIMARY KEY,
                inviter_id    INTEGER NOT NULL,
                rewarded      INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL,
                FOREIGN KEY (invitee_id) REFERENCES users(id),
                FOREIGN KEY (inviter_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id);

            CREATE TABLE IF NOT EXISTS bonus (
                uid           INTEGER PRIMARY KEY,
                last_bonus_at TEXT,
                FOREIGN KEY (uid) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS promocodes (
                code          TEXT    PRIMARY KEY,
                reward        REAL    NOT NULL,
                max_uses      INTEGER NOT NULL,
                used_count    INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_used (
                code          TEXT    NOT NULL,
                uid           INTEGER NOT NULL,
                used_at       TEXT    NOT NULL,
                PRIMARY KEY (code, uid)
            );

            CREATE TABLE IF NOT EXISTS roulette_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id       INTEGER NOT NULL,
                result_number INTEGER NOT NULL,
                result_color  TEXT    NOT NULL,
                played_at     TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_roulette_log_chat ON roulette_log(chat_id);

            CREATE TABLE IF NOT EXISTS mines_sessions (
                uid            INTEGER PRIMARY KEY,
                board          TEXT    NOT NULL,
                mine_positions TEXT    NOT NULL,
                revealed       TEXT    NOT NULL,
                mines_count    INTEGER NOT NULL,
                bet            REAL    NOT NULL,
                gems_opened    INTEGER NOT NULL DEFAULT 0,
                message_id     INTEGER,
                chat_id        INTEGER NOT NULL,
                created_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gold_sessions (
                uid            INTEGER PRIMARY KEY,
                bet            REAL    NOT NULL,
                current_floor  INTEGER NOT NULL DEFAULT 0,
                floors_passed  INTEGER NOT NULL DEFAULT 0,
                floors         TEXT    NOT NULL,
                message_id     INTEGER,
                chat_id        INTEGER NOT NULL,
                created_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tower_sessions (
                uid            INTEGER PRIMARY KEY,
                difficulty     INTEGER NOT NULL,
                bet            REAL    NOT NULL,
                current_floor  INTEGER NOT NULL DEFAULT 0,
                floors_passed  INTEGER NOT NULL DEFAULT 0,
                floors         TEXT    NOT NULL,
                message_id     INTEGER,
                chat_id        INTEGER NOT NULL,
                created_at     TEXT    NOT NULL
            );
        """)
    print("✅ БД инициализирована")


# ─────────────────────────────────────────
#  Пользователи
# ─────────────────────────────────────────
def db_get_or_create_user(user) -> dict:
    uid = user.id
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            now = datetime.now().isoformat()
            conn.execute("""
                INSERT INTO users (id, first_name, last_name, username, px,
                                   games_played, total_won, total_lost, registered_at)
                VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?)
            """, (uid, user.first_name or "", user.last_name or "", user.username or "", now))
            return {
                "id":            uid,
                "first_name":    user.first_name or "",
                "last_name":     user.last_name  or "",
                "username":      user.username   or "",
                "px":            0,
                "games_played":  0,
                "total_won":     0.0,
                "total_lost":    0.0,
                "registered_at": datetime.fromisoformat(now),
            }
        else:
            conn.execute("""
                UPDATE users SET first_name=?, last_name=?, username=? WHERE id=?
            """, (user.first_name or "", user.last_name or "", user.username or "", uid))
            return _row_to_user(dict(row))


def db_get_user(uid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return _row_to_user(dict(row)) if row else None


def db_get_px(uid: int) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT px FROM users WHERE id = ?", (uid,)).fetchone()
        return row["px"] if row else 0.0


def db_add_px(uid: int, amount: float):
    if amount <= 0:
        return
    with get_conn() as conn:
        conn.execute("UPDATE users SET px = ROUND(px + ?, 2) WHERE id = ?", (amount, uid))


def db_spend_px(uid: int, amount: float):
    if amount <= 0:
        return
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET px = MAX(0, ROUND(px - ?, 2)) WHERE id = ?
        """, (amount, uid))


def db_try_spend_px(uid: int, amount: float) -> bool:
    if amount <= 0:
        return False
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE users
            SET px = ROUND(px - ?, 2)
            WHERE id = ? AND px >= ?
        """, (amount, uid, amount))
        return cur.rowcount > 0


def db_record_game_result(uid: int, bet: float, won: float):
    with get_conn() as conn:
        if won > 0:
            conn.execute("""
                UPDATE users
                SET games_played = games_played + 1,
                    total_won    = ROUND(total_won + ?, 2)
                WHERE id = ?
            """, (won, uid))
        else:
            conn.execute("""
                UPDATE users
                SET games_played = games_played + 1,
                    total_lost   = ROUND(total_lost + ?, 2)
                WHERE id = ?
            """, (bet, uid))


async def save_game_result(uid: int, _game_name: str, won: float, bet: float = 0.0):
    db_record_game_result(uid, bet, won)


def db_update_game_stats(uid: int, won: float = 0.0, lost: float = 0.0) -> None:
    """
    Обновляет статистику игрока для рулетки.
    - won  > 0: чистая прибыль (без учёта возврата ставки)
    - lost > 0: проигранная сумма (полная ставка)
    Каждый вызов увеличивает games_played на 1.
    """
    with get_conn() as conn:
        conn.execute("""
            UPDATE users
            SET games_played = games_played + 1,
                total_won    = ROUND(total_won  + ?, 2),
                total_lost   = ROUND(total_lost + ?, 2)
            WHERE id = ?
        """, (won, lost, uid))


def _row_to_user(row: dict) -> dict:
    row["registered_at"] = datetime.fromisoformat(row["registered_at"])
    return row


# ─────────────────────────────────────────
#  Шахта
# ─────────────────────────────────────────
def db_get_mine_user(uid: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mine WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            conn.execute("""
                INSERT INTO mine (uid, nox, pickaxe_id, owned, mining_start,
                                  mining_end, ticks_paid, accumulated)
                VALUES (?, 0, 1, '[1]', NULL, NULL, 0, 0)
            """, (uid,))
            return _default_mine()
        return _row_to_mine(dict(row))


def db_save_mine_user(uid: int, data: dict):
    owned_json   = json.dumps(list(data["owned"]))
    mining_start = data["mining_start"].isoformat() if data["mining_start"] else None
    mining_end   = data["mining_end"].isoformat()   if data["mining_end"]   else None
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO mine (uid, nox, pickaxe_id, owned, mining_start,
                              mining_end, ticks_paid, accumulated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                nox          = excluded.nox,
                pickaxe_id   = excluded.pickaxe_id,
                owned        = excluded.owned,
                mining_start = excluded.mining_start,
                mining_end   = excluded.mining_end,
                ticks_paid   = excluded.ticks_paid,
                accumulated  = excluded.accumulated
        """, (uid, data["nox"], data["pickaxe_id"], owned_json,
              mining_start, mining_end, data["ticks_paid"], data["accumulated"]))


def _default_mine() -> dict:
    return {
        "nox":          0.0,
        "pickaxe_id":   1,
        "owned":        {1},
        "mining_start": None,
        "mining_end":   None,
        "ticks_paid":   0,
        "accumulated":  0.0,
    }


def _row_to_mine(row: dict) -> dict:
    return {
        "nox":          row["nox"],
        "pickaxe_id":   row["pickaxe_id"],
        "owned":        set(json.loads(row["owned"])),
        "mining_start": datetime.fromisoformat(row["mining_start"]) if row["mining_start"] else None,
        "mining_end":   datetime.fromisoformat(row["mining_end"])   if row["mining_end"]   else None,
        "ticks_paid":   row["ticks_paid"],
        "accumulated":  row["accumulated"],
    }


# ─────────────────────────────────────────
#  Рефералы
# ─────────────────────────────────────────
REFERRAL_REWARD_PX = 1000


def db_register_referral(invitee_id: int, inviter_id: int) -> bool:
    if invitee_id == inviter_id:
        return False
    with get_conn() as conn:
        inviter_exists = conn.execute(
            "SELECT 1 FROM users WHERE id = ?", (inviter_id,)
        ).fetchone()
        if not inviter_exists:
            return False

        already = conn.execute(
            "SELECT 1 FROM referrals WHERE invitee_id = ?", (invitee_id,)
        ).fetchone()
        if already:
            return False

        now = datetime.now().isoformat()
        try:
            conn.execute("""
                INSERT INTO referrals (invitee_id, inviter_id, rewarded, created_at)
                VALUES (?, ?, 0, ?)
            """, (invitee_id, inviter_id, now))
            return True
        except sqlite3.IntegrityError:
            return False


def db_try_reward_referral(invitee_id: int) -> int | None:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE referrals SET rewarded = 1
            WHERE invitee_id = ? AND rewarded = 0
        """, (invitee_id,))
        if cur.rowcount == 0:
            return None

        row = conn.execute(
            "SELECT inviter_id FROM referrals WHERE invitee_id = ?", (invitee_id,)
        ).fetchone()
        if not row:
            return None

        inviter_id = row["inviter_id"]
        conn.execute(
            "UPDATE users SET px = ROUND(px + ?, 2) WHERE id = ?",
            (REFERRAL_REWARD_PX, inviter_id)
        )
        return inviter_id


def db_get_referral_stats(uid: int) -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM referrals WHERE inviter_id = ?", (uid,)
        ).fetchone()["cnt"]
        rewarded = conn.execute(
            "SELECT COUNT(*) as cnt FROM referrals WHERE inviter_id = ? AND rewarded = 1", (uid,)
        ).fetchone()["cnt"]
        earned = rewarded * REFERRAL_REWARD_PX
        return {
            "total":    total,
            "rewarded": rewarded,
            "earned":   earned,
        }


def db_is_already_referred(uid: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM referrals WHERE invitee_id = ?", (uid,)
        ).fetchone()
        return row is not None


# ─────────────────────────────────────────
#  Промокоды
# ─────────────────────────────────────────
def db_create_promo(code: str, reward: float, max_uses: int) -> bool:
    now = datetime.now().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM promocodes WHERE code = ?", (code,)
        ).fetchone()
        if existing:
            return False
        conn.execute("""
            INSERT INTO promocodes (code, reward, max_uses, used_count, created_at)
            VALUES (?, ?, ?, 0, ?)
        """, (code, reward, max_uses, now))
        return True


def db_use_promo(uid: int, code: str) -> dict:
    code = code.strip().upper()
    with get_conn() as conn:
        promo = conn.execute(
            "SELECT * FROM promocodes WHERE code = ?", (code,)
        ).fetchone()

        if not promo:
            return {"ok": False, "reason": "not_found", "reward": 0}

        if promo["used_count"] >= promo["max_uses"]:
            return {"ok": False, "reason": "expired", "reward": 0}

        already = conn.execute(
            "SELECT 1 FROM promo_used WHERE code = ? AND uid = ?", (code, uid)
        ).fetchone()
        if already:
            return {"ok": False, "reason": "already_used", "reward": 0}

        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO promo_used (code, uid, used_at) VALUES (?, ?, ?)",
            (code, uid, now)
        )
        conn.execute(
            "UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?",
            (code,)
        )
        conn.execute(
            "UPDATE users SET px = ROUND(px + ?, 2) WHERE id = ?",
            (promo["reward"], uid)
        )
        return {"ok": True, "reason": None, "reward": float(promo["reward"])}


# ─────────────────────────────────────────
#  Рулетка — лог в БД
#  (история также хранится в памяти в roulette.py,
#   эти функции нужны для персистентности между перезапусками)
# ─────────────────────────────────────────
def db_roulette_save_result(chat_id: int, number: int, color: str) -> None:
    """Сохранить результат раунда рулетки в БД."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO roulette_log (chat_id, result_number, result_color, played_at)
            VALUES (?, ?, ?, ?)
        """, (chat_id, number, color, now))


def db_roulette_get_last(chat_id: int, limit: int = 10) -> list[dict]:
    """Получить последние N результатов рулетки для чата."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT result_number, result_color, played_at
            FROM roulette_log
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (chat_id, limit)).fetchall()
    return [{"number": r["result_number"], "color": r["result_color"],
             "played_at": r["played_at"]} for r in rows]


# ─────────────────────────────────────────
#  Мины — персистентные сессии
# ─────────────────────────────────────────
def db_mines_save_session(uid: int, session: dict) -> None:
    """Сохранить или обновить активную сессию игры в мины."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO mines_sessions
                (uid, board, mine_positions, revealed, mines_count,
                 bet, gems_opened, message_id, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                board          = excluded.board,
                mine_positions = excluded.mine_positions,
                revealed       = excluded.revealed,
                mines_count    = excluded.mines_count,
                bet            = excluded.bet,
                gems_opened    = excluded.gems_opened,
                message_id     = excluded.message_id,
                chat_id        = excluded.chat_id,
                created_at     = excluded.created_at
        """, (
            uid,
            _json.dumps(session['board']),
            _json.dumps(list(session['mine_positions'])),
            _json.dumps(session['revealed']),
            session['mines_count'],
            session['bet'],
            session.get('gems_opened', 0),
            session.get('message_id'),
            session['chat_id'],
            now,
        ))


def db_mines_delete_session(uid: int) -> None:
    """Удалить сессию игры в мины после завершения."""
    with get_conn() as conn:
        conn.execute("DELETE FROM mines_sessions WHERE uid = ?", (uid,))


def db_mines_load_all_sessions() -> list[dict]:
    """Загрузить все активные сессии мин при старте бота."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM mines_sessions").fetchall()
    result = []
    for row in rows:
        result.append({
            'uid':              row['uid'],
            'board':            _json.loads(row['board']),
            'mine_positions':   set(_json.loads(row['mine_positions'])),
            'revealed':         _json.loads(row['revealed']),
            'mines_count':      row['mines_count'],
            'bet':              row['bet'],
            'gems_opened':      row['gems_opened'],
            'message_id':       row['message_id'],
            'chat_id':          row['chat_id'],
            'finishing':        False,
            'processing_cells': set(),
            'owner_id':         row['uid'],
        })
    return result


# ─────────────────────────────────────────
#  Золото — персистентные сессии
# ─────────────────────────────────────────
def db_gold_save_session(uid: int, session: dict) -> None:
    """Сохранить или обновить активную сессию игры Золото."""
    now = datetime.now().isoformat()

    # floors содержит sets (bomb_col — int, chosen — int|None, is_bomb — bool|None)
    # Сериализуем в список словарей с обычными типами
    floors_data = []
    for f in session['floors']:
        floors_data.append({
            'bomb_col': f['bomb_col'],
            'chosen':   f['chosen'],
            'is_bomb':  f['is_bomb'],
        })

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO gold_sessions
                (uid, bet, current_floor, floors_passed, floors, message_id, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                bet           = excluded.bet,
                current_floor = excluded.current_floor,
                floors_passed = excluded.floors_passed,
                floors        = excluded.floors,
                message_id    = excluded.message_id,
                chat_id       = excluded.chat_id,
                created_at    = excluded.created_at
        """, (
            uid,
            session['bet'],
            session['current_floor'],
            session['floors_passed'],
            _json.dumps(floors_data),
            session.get('message_id'),
            session['chat_id'],
            now,
        ))


def db_gold_delete_session(uid: int) -> None:
    """Удалить сессию игры Золото после завершения."""
    with get_conn() as conn:
        conn.execute("DELETE FROM gold_sessions WHERE uid = ?", (uid,))


def db_gold_load_all_sessions() -> list[dict]:
    """Загрузить все активные сессии Золото при старте бота."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM gold_sessions").fetchall()
    result = []
    for row in rows:
        floors_raw = _json.loads(row['floors'])
        floors = []
        for f in floors_raw:
            floors.append({
                'bomb_col': f['bomb_col'],
                'chosen':   f['chosen'],
                'is_bomb':  f['is_bomb'],
            })
        result.append({
            'uid':              row['uid'],
            'bet':              row['bet'],
            'current_floor':    row['current_floor'],
            'floors_passed':    row['floors_passed'],
            'floors':           floors,
            'message_id':       row['message_id'],
            'chat_id':          row['chat_id'],
            'finishing':        False,
            'processing_cells': set(),
            'owner_id':         row['uid'],
        })
    return result


# ─────────────────────────────────────────
#  Башня — персистентные сессии
# ─────────────────────────────────────────
def db_tower_save_session(uid: int, session: dict) -> None:
    """Сохранить или обновить активную сессию игры Башня."""
    now = datetime.now().isoformat()

    # floors содержит bomb_cols — set, chosen — int|None, is_bomb — bool|None
    floors_data = []
    for f in session['floors']:
        floors_data.append({
            'bomb_cols': list(f['bomb_cols']),
            'chosen':    f['chosen'],
            'is_bomb':   f['is_bomb'],
        })

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO tower_sessions
                (uid, difficulty, bet, current_floor, floors_passed, floors,
                 message_id, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                difficulty    = excluded.difficulty,
                bet           = excluded.bet,
                current_floor = excluded.current_floor,
                floors_passed = excluded.floors_passed,
                floors        = excluded.floors,
                message_id    = excluded.message_id,
                chat_id       = excluded.chat_id,
                created_at    = excluded.created_at
        """, (
            uid,
            session['difficulty'],
            session['bet'],
            session['current_floor'],
            session['floors_passed'],
            _json.dumps(floors_data),
            session.get('message_id'),
            session['chat_id'],
            now,
        ))


def db_tower_delete_session(uid: int) -> None:
    """Удалить сессию игры Башня после завершения."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tower_sessions WHERE uid = ?", (uid,))


def db_tower_load_all_sessions() -> list[dict]:
    """Загрузить все активные сессии Башня при старте бота."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tower_sessions").fetchall()
    result = []
    for row in rows:
        floors_raw = _json.loads(row['floors'])
        floors = []
        for f in floors_raw:
            floors.append({
                'bomb_cols': set(f['bomb_cols']),
                'chosen':    f['chosen'],
                'is_bomb':   f['is_bomb'],
            })
        result.append({
            'uid':              row['uid'],
            'difficulty':       row['difficulty'],
            'bet':              row['bet'],
            'current_floor':    row['current_floor'],
            'floors_passed':    row['floors_passed'],
            'floors':           floors,
            'message_id':       row['message_id'],
            'chat_id':          row['chat_id'],
            'finishing':        False,
            'processing_cells': set(),
            'owner_id':         row['uid'],
        })
    return result
