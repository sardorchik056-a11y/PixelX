import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager

DB_PATH = "bot.db"

# ─────────────────────────────────────────
#  Подключение
# ─────────────────────────────────────────
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    with get_conn() as conn:
        conn.execute("UPDATE users SET px = ROUND(px + ?, 2) WHERE id = ?", (amount, uid))


def db_spend_px(uid: int, amount: float):
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET px = MAX(0, ROUND(px - ?, 2)) WHERE id = ?
        """, (amount, uid))


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
    owned_json    = json.dumps(list(data["owned"]))
    mining_start  = data["mining_start"].isoformat() if data["mining_start"] else None
    mining_end    = data["mining_end"].isoformat()   if data["mining_end"]   else None
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
