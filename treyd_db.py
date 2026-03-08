"""
treyd_db.py — База данных модуля Биржи

Изменения v3:
  • db_count_active_listings_by_seller — лимит 5 лотов
  • db_get_active_listings — фильтрация по диапазону Px (px_min, px_max)
  • db_mark_listing_sold_atomic — атомарная продажа (защита от дублей/race condition)
  • db_get_buyer_stats — статистика покупок (кол-во, Px, $)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = "bot.db"


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


def init_exchange_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS exchange_balances (
            user_id     INTEGER PRIMARY KEY,
            usd_balance REAL    NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS exchange_listings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id       INTEGER NOT NULL,
            seller_name     TEXT    NOT NULL,
            px_amount       REAL    NOT NULL,
            price_per_10k   REAL    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'active',
            buyer_id        INTEGER,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            expires_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS exchange_invoices (
            invoice_id  INTEGER PRIMARY KEY,
            lot_id      INTEGER NOT NULL,
            buyer_id    INTEGER NOT NULL,
            buyer_name  TEXT    NOT NULL,
            amount_usd  REAL    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        -- Заявки на вывод (ручное одобрение админом)
        CREATE TABLE IF NOT EXISTS exchange_withdraw_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            username    TEXT    NOT NULL DEFAULT '',
            amount_usd  REAL    NOT NULL,
            net_usd     REAL    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        -- История одобренных выводов (для статистики)
        CREATE TABLE IF NOT EXISTS exchange_withdrawals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount_usd  REAL    NOT NULL,
            net_usd     REAL    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ex_listings_status   ON exchange_listings(status);
        CREATE INDEX IF NOT EXISTS idx_ex_listings_expires  ON exchange_listings(expires_at);
        CREATE INDEX IF NOT EXISTS idx_ex_listings_seller   ON exchange_listings(seller_id);
        CREATE INDEX IF NOT EXISTS idx_ex_listings_buyer    ON exchange_listings(buyer_id);
        CREATE INDEX IF NOT EXISTS idx_ex_invoices_lot      ON exchange_invoices(lot_id);
        CREATE INDEX IF NOT EXISTS idx_ex_withdrawals_user  ON exchange_withdrawals(user_id);
        CREATE INDEX IF NOT EXISTS idx_ex_wreq_status       ON exchange_withdraw_requests(status);
        CREATE INDEX IF NOT EXISTS idx_ex_wreq_user         ON exchange_withdraw_requests(user_id);
        """)
    print("✅ БД Биржи инициализирована")


# ── USD баланс ──────────────────────────────────────────────
def db_get_usd_balance(user_id: int) -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT usd_balance FROM exchange_balances WHERE user_id=?", (user_id,)
        ).fetchone()
        return float(row["usd_balance"]) if row else 0.0


def db_add_usd(user_id: int, amount: float):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO exchange_balances(user_id, usd_balance)
            VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                usd_balance = ROUND(usd_balance + excluded.usd_balance, 4)
        """, (user_id, amount))


def db_try_spend_usd(user_id: int, amount: float) -> bool:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE exchange_balances
            SET usd_balance = ROUND(usd_balance - ?, 4)
            WHERE user_id=? AND usd_balance >= ?
        """, (amount, user_id, amount))
        return cur.rowcount > 0


# ── Лоты ───────────────────────────────────────────────────
def db_create_listing(
    seller_id: int, seller_name: str,
    px_amount: float, price_per_10k: float, days: int,
) -> int:
    expires = (datetime.now() + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO exchange_listings(seller_id, seller_name, px_amount, price_per_10k, expires_at)
            VALUES(?, ?, ?, ?, ?)
        """, (seller_id, seller_name, px_amount, price_per_10k, expires))
        return cur.lastrowid


def db_count_active_listings_by_seller(seller_id: int) -> int:
    """Количество активных лотов продавца (для лимита)."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM exchange_listings
            WHERE seller_id=? AND status='active' AND expires_at > ?
        """, (seller_id, now)).fetchone()
        return row["cnt"] if row else 0


def db_get_active_listings(
    exclude_uid: Optional[int] = None,
    px_min: float = 0,
    px_max: float = 10_000_000_000,
) -> list[dict]:
    """Активные лоты с опциональным фильтром по диапазону Px."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        if exclude_uid:
            rows = conn.execute("""
                SELECT * FROM exchange_listings
                WHERE status='active'
                  AND expires_at > ?
                  AND seller_id != ?
                  AND px_amount >= ?
                  AND px_amount <= ?
                ORDER BY created_at DESC
            """, (now, exclude_uid, px_min, px_max)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM exchange_listings
                WHERE status='active'
                  AND expires_at > ?
                  AND px_amount >= ?
                  AND px_amount <= ?
                ORDER BY created_at DESC
            """, (now, px_min, px_max)).fetchall()
        return [dict(r) for r in rows]


def db_get_my_listings(user_id: int) -> list[dict]:
    """Активные лоты пользователя."""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM exchange_listings
            WHERE seller_id=? AND status='active' AND expires_at > ?
            ORDER BY created_at DESC
        """, (user_id, now)).fetchall()
        return [dict(r) for r in rows]


def db_get_listing(listing_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM exchange_listings WHERE id=?", (listing_id,)
        ).fetchone()
        return dict(row) if row else None


def db_mark_listing_sold(listing_id: int, buyer_id: int):
    """Пометить лот проданным (без атомарности, для внутреннего использования)."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE exchange_listings SET status='sold', buyer_id=?
            WHERE id=? AND status='active'
        """, (buyer_id, listing_id))


def db_mark_listing_sold_atomic(listing_id: int, buyer_id: int) -> bool:
    """
    Атомарно помечает лот проданным.
    Возвращает True если успешно (лот был active и теперь sold).
    Возвращает False если лот уже был куплен другим (race condition/дубль).
    SQLite гарантирует атомарность UPDATE — только один поток выиграет.
    """
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE exchange_listings
            SET status='sold', buyer_id=?
            WHERE id=? AND status='active'
        """, (buyer_id, listing_id))
        return cur.rowcount > 0


def db_cancel_listing_by_owner(listing_id: int, seller_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE exchange_listings SET status='cancelled'
            WHERE id=? AND seller_id=? AND status='active'
        """, (listing_id, seller_id))
        return cur.rowcount > 0


def db_expire_listings() -> list[dict]:
    now = datetime.now().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM exchange_listings
            WHERE status='active' AND expires_at <= ?
        """, (now,)).fetchall()
        expired = [dict(r) for r in rows]
        if expired:
            ids = tuple(r["id"] for r in expired)
            ph  = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE exchange_listings SET status='expired' WHERE id IN ({ph})", ids
            )
        return expired


def db_get_seller_stats(seller_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_sales,
                COALESCE(SUM(price_per_10k * px_amount / 10000.0 * 0.85), 0) as total_earned
            FROM exchange_listings
            WHERE seller_id=? AND status='sold'
        """, (seller_id,)).fetchone()
        return {
            "total_sales":  row["total_sales"]           if row else 0,
            "total_earned": float(row["total_earned"])   if row else 0.0,
        }


def db_get_buyer_stats(buyer_id: int) -> dict:
    """Статистика покупок: кол-во лотов, суммарно Px, суммарно потрачено $."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_buys,
                COALESCE(SUM(px_amount), 0) as total_px_received,
                COALESCE(SUM(price_per_10k * px_amount / 10000.0), 0) as total_spent
            FROM exchange_listings
            WHERE buyer_id=? AND status='sold'
        """, (buyer_id,)).fetchone()
        return {
            "total_buys":        row["total_buys"]              if row else 0,
            "total_px_received": float(row["total_px_received"]) if row else 0.0,
            "total_spent":       float(row["total_spent"])       if row else 0.0,
        }


def db_get_seller_last_sales(seller_id: int, limit: int = 5) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM exchange_listings
            WHERE seller_id=? AND status='sold'
            ORDER BY created_at DESC
            LIMIT ?
        """, (seller_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Инвойсы ────────────────────────────────────────────────
def db_create_invoice_record(
    invoice_id: int, lot_id: int,
    buyer_id: int, buyer_name: str, amount_usd: float,
):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO exchange_invoices
                (invoice_id, lot_id, buyer_id, buyer_name, amount_usd)
            VALUES(?, ?, ?, ?, ?)
        """, (invoice_id, lot_id, buyer_id, buyer_name, amount_usd))


def db_get_invoice_record(invoice_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM exchange_invoices WHERE invoice_id=?", (invoice_id,)
        ).fetchone()
        return dict(row) if row else None


def db_mark_invoice_paid(invoice_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE exchange_invoices SET status='paid' WHERE invoice_id=?", (invoice_id,)
        )


# ── Заявки на вывод ─────────────────────────────────────────
def db_create_withdraw_request(
    user_id: int, username: str, amount_usd: float, net_usd: float
) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO exchange_withdraw_requests(user_id, username, amount_usd, net_usd)
            VALUES(?, ?, ?, ?)
        """, (user_id, username, amount_usd, net_usd))
        return cur.lastrowid


def db_get_withdraw_request(req_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM exchange_withdraw_requests WHERE id=?", (req_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pending_withdraw_requests() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM exchange_withdraw_requests
            WHERE status='pending'
            ORDER BY created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]


def db_approve_withdraw_request(req_id: int) -> Optional[dict]:
    now = datetime.now().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM exchange_withdraw_requests WHERE id=? AND status='pending'",
            (req_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("""
            UPDATE exchange_withdraw_requests
            SET status='approved', resolved_at=?
            WHERE id=?
        """, (now, req_id))
        conn.execute("""
            INSERT INTO exchange_withdrawals(user_id, amount_usd, net_usd)
            VALUES(?, ?, ?)
        """, (row["user_id"], row["amount_usd"], row["net_usd"]))
        return dict(row)


def db_reject_withdraw_request(req_id: int) -> Optional[dict]:
    now = datetime.now().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM exchange_withdraw_requests WHERE id=? AND status='pending'",
            (req_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("""
            UPDATE exchange_withdraw_requests
            SET status='rejected', resolved_at=?
            WHERE id=?
        """, (now, req_id))
        return dict(row)


# ── Статистика выводов ──────────────────────────────────────
def db_get_withdraw_stats(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt, COALESCE(SUM(net_usd), 0) as total
            FROM exchange_withdrawals WHERE user_id=?
        """, (user_id,)).fetchone()
        return {
            "count": row["cnt"]            if row else 0,
            "total": float(row["total"])   if row else 0.0,
        }
