"""
Data layer for the eSIM bot.

Storage backends:
  * Turso (cloud, persistent) — used when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set.
  * Local SQLite file — used otherwise (local dev, or a host with a real volume).

The SQL is plain SQLite in both cases; only the connection differs.

Schema map
  countries        الدول (تتضاف وتتحذف لوحدها)
  products         الباقات جوه كل دولة
  stock            الشرايح نفسها: كود LPA + المورد + التكلفة + الضمان + الحالة
  orders           عمليات البيع، وكل طلب مربوط بالشريحة اللي اتباعت فيه
  users            العملاء وأرصدتهم
  ledger           كل حركة رصيد (شحن/شراء/استرجاع/يدوي) — أساس التقارير المالية
  topups           إيصالات الشحن من Binance (منع تكرار نفس رقم المعاملة)
  staff            الأدمن والصلاحيات
  warranty_claims  طلبات الضمان
  audit_log        سجل تصرفات الأدمن
  settings         إعدادات عامة (صيانة .. إلخ)
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

log = logging.getLogger(__name__)

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
USE_REMOTE = bool(TURSO_URL and TURSO_TOKEN)

DB_DIR = os.environ.get("DB_DIR", ".")
DB_PATH = os.path.join(DB_DIR, "esim_bot.db")


def utcnow() -> str:
    """Timestamp format used for every new row: 'YYYY-MM-DD HH:MM:SS' in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- connection

class Row(dict):
    """Dict-like row that also supports positional access, mirroring sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        try:
            return super().__getitem__(key)
        except KeyError:
            return default


class _Cursor:
    def __init__(self, cur):
        self._cur = cur
        self._cols = [d[0] for d in cur.description] if cur.description else []

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, dict):
            return Row(row)
        return Row(zip(self._cols, row))

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        return getattr(self._cur, "lastrowid", None)


class _RemoteConn:
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return _Cursor(self._raw.execute(sql, params))

    def insert(self, sql, params=()):
        cur = self.execute(sql, params)
        rowid = cur.lastrowid
        if rowid is None:
            rowid = self.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return rowid

    def commit(self):
        self._raw.commit()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


class _LocalConn:
    """Wrapped the same way as the remote connection so both backends hand back
    identical dict-like rows — handler code never has to care which is in use."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return _Cursor(self._raw.execute(sql, params))

    def insert(self, sql, params=()):
        return self._raw.execute(sql, params).lastrowid

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def _connect_remote():
    errors = []
    try:
        import libsql
        return libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
    except Exception as e:
        errors.append(f"libsql: {e}")
    try:
        import turso_serverless
        return turso_serverless.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    except Exception as e:
        errors.append(f"turso_serverless: {e}")
    raise RuntimeError("couldn't connect to Turso -> " + " | ".join(errors))


@contextmanager
def get_conn():
    if USE_REMOTE:
        conn = _RemoteConn(_connect_remote())
    else:
        raw = sqlite3.connect(DB_PATH)
        raw.row_factory = sqlite3.Row
        conn = _LocalConn(raw)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _is_unique_violation(exc) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    text = str(exc).upper()
    return "UNIQUE" in text or "CONSTRAINT" in text


def _try(conn, sql):
    """Runs DDL that is expected to fail when it was already applied."""
    try:
        conn.execute(sql)
    except Exception:
        pass


# ---------------------------------------------------------------- schema

def init_db():
    if not USE_REMOTE:
        os.makedirs(DB_DIR, exist_ok=True)
    log.info("storage backend: %s", "Turso (cloud)" if USE_REMOTE else f"local SQLite at {DB_PATH}")

    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            flag TEXT DEFAULT '🌍',
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT NOT NULL,
            flag TEXT DEFAULT '🌍',
            data_amount TEXT NOT NULL,
            validity_days INTEGER NOT NULL,
            price_usdt REAL NOT NULL,
            description TEXT DEFAULT '',
            active INTEGER DEFAULT 1
        )""")
        for sql in (
            "ALTER TABLE products ADD COLUMN country_id INTEGER",
            "ALTER TABLE products ADD COLUMN warranty_days INTEGER DEFAULT 0",
            "ALTER TABLE products ADD COLUMN created_at TEXT",
            "ALTER TABLE products ADD COLUMN daily_limit TEXT DEFAULT ''",
        ):
            _try(conn, sql)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            lpa_code TEXT NOT NULL,
            is_used INTEGER DEFAULT 0,
            order_id INTEGER
        )""")
        for sql in (
            "ALTER TABLE stock ADD COLUMN status TEXT",
            "ALTER TABLE stock ADD COLUMN supplier TEXT DEFAULT ''",
            "ALTER TABLE stock ADD COLUMN cost_price REAL DEFAULT 0",
            "ALTER TABLE stock ADD COLUMN validity_days INTEGER",
            "ALTER TABLE stock ADD COLUMN warranty_days INTEGER DEFAULT 0",
            "ALTER TABLE stock ADD COLUMN batch_id TEXT",
            "ALTER TABLE stock ADD COLUMN note TEXT DEFAULT ''",
            "ALTER TABLE stock ADD COLUMN added_at TEXT",
            "ALTER TABLE stock ADD COLUMN added_by INTEGER",
            "ALTER TABLE stock ADD COLUMN sold_at TEXT",
        ):
            _try(conn, sql)
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_stock_lpa ON stock(lpa_code)")
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_stock_status ON stock(product_id, status)")
        # Backfill status for rows created before the column existed. is_used is
        # the source of truth for those, so this also repairs a wrong default.
        _try(conn, "UPDATE stock SET status = 'sold' "
                   "WHERE is_used = 1 AND (status IS NULL OR status = 'available')")
        _try(conn, "UPDATE stock SET status = 'available' WHERE status IS NULL")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            product_id INTEGER NOT NULL,
            price REAL NOT NULL,
            tx_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )""")
        for sql in (
            "ALTER TABLE orders ADD COLUMN stock_id INTEGER",
            "ALTER TABLE orders ADD COLUMN cost_price REAL DEFAULT 0",
            "ALTER TABLE orders ADD COLUMN warranty_until TEXT",
            "ALTER TABLE orders ADD COLUMN product_label TEXT",
            "ALTER TABLE orders ADD COLUMN delivered_at TEXT",
            "ALTER TABLE orders ADD COLUMN group_id TEXT",
        ):
            _try(conn, sql)
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            joined_at TEXT,
            balance REAL DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            blocked_bot INTEGER DEFAULT 0
        )""")
        for sql in (
            "ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN blocked_bot INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN first_name TEXT",
            "ALTER TABLE users ADD COLUMN note TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN last_seen TEXT",
        ):
            _try(conn, sql)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            balance_after REAL,
            ref_type TEXT,
            ref_id INTEGER,
            note TEXT DEFAULT '',
            admin_id INTEGER,
            created_at TEXT
        )""")
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_ledger_created ON ledger(created_at)")
        _try(conn, "CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id)")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tx_id TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT
        )""")
        _try(conn, "ALTER TABLE topups ADD COLUMN method TEXT DEFAULT 'binance'")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'admin',
            perms TEXT DEFAULT '',
            added_by INTEGER,
            added_at TEXT
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS warranty_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            resolution TEXT DEFAULT '',
            replacement_stock_id INTEGER,
            created_at TEXT,
            resolved_at TEXT,
            resolved_by INTEGER
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        )""")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS price_tiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            min_qty INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            created_at TEXT
        )""")
        _try(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_tier_product_qty "
                   "ON price_tiers(product_id, min_qty)")

        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

        _migrate_countries(conn)


def _migrate_countries(conn):
    """Old schema stored the country as free text on every product. Build the
    countries table out of what's already there and link the products to it."""
    rows = conn.execute(
        "SELECT DISTINCT country, flag FROM products WHERE country IS NOT NULL"
    ).fetchall()
    for r in rows:
        name = (r["country"] or "").strip()
        if not name:
            continue
        existing = conn.execute("SELECT id FROM countries WHERE name = ?", (name,)).fetchone()
        if existing:
            cid = existing["id"]
        else:
            cid = conn.insert(
                "INSERT INTO countries (name, flag, active, created_at) VALUES (?, ?, 1, ?)",
                (name, r["flag"] or "🌍", utcnow()),
            )
        conn.execute(
            "UPDATE products SET country_id = ? WHERE country = ? AND (country_id IS NULL OR country_id = 0)",
            (cid, name),
        )


# ---------------------------------------------------------------- settings

def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def is_maintenance() -> bool:
    return get_setting("maintenance", "0") == "1"


def set_maintenance(on: bool):
    set_setting("maintenance", "1" if on else "0")


def get_maintenance_message():
    return get_setting(
        "maintenance_message",
        "🛠️ البوت تحت الصيانة حاليًا، هنرجع قريب. شكرًا لصبرك 🙏",
    )


def set_maintenance_message(text):
    set_setting("maintenance_message", text)


DEFAULT_DELIVERY_NOTE = (
    "⚠️ إخلاء مسؤولية\n"
    "استخدام هذه الشريحة يقع على مسؤوليتك الشخصية بالكامل. لست مسؤولاً عن أي سوء "
    "استخدام، أو مخالفة للقوانين والأنظمة، أو أي فعل يُغضب الله سبحانه وتعالى أو "
    "يخالف الشرع. كل شخص محاسب على أفعاله."
)


def get_delivery_note():
    """The footer printed under every delivered eSIM."""
    return get_setting("delivery_note", DEFAULT_DELIVERY_NOTE)


def set_delivery_note(text):
    set_setting("delivery_note", text)


# ---------------------------------------------------------------- audit

def log_action(admin_id, action, details=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (admin_id, action, str(details)[:500], utcnow()),
        )


def list_audit(limit=30):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


# ---------------------------------------------------------------- staff

def get_staff(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM staff WHERE user_id = ?", (user_id,)).fetchone()


def list_staff():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM staff ORDER BY added_at").fetchall()


def upsert_staff(user_id, username, role, perms, added_by):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO staff (user_id, username, role, perms, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
            "role = excluded.role, perms = excluded.perms",
            (user_id, username, role, perms, added_by, utcnow()),
        )


def remove_staff(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM staff WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------- users

def upsert_user(telegram_id, username, first_name=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name, joined_at, balance) "
            "VALUES (?, ?, ?, ?, 0)",
            (telegram_id, username, first_name, utcnow()),
        )
        conn.execute(
            "UPDATE users SET username = COALESCE(?, username), "
            "first_name = COALESCE(?, first_name), last_seen = ? WHERE telegram_id = ?",
            (username, first_name, utcnow(), telegram_id),
        )


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()


def find_users(text, limit=15):
    """Finds customers by id, username or first name."""
    like = f"%{text.lstrip('@')}%"
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE CAST(telegram_id AS TEXT) LIKE ? "
            "OR username LIKE ? OR first_name LIKE ? ORDER BY telegram_id LIMIT ?",
            (like, like, like, limit),
        ).fetchall()


def list_users(limit=50, offset=0, order="balance"):
    col = {"balance": "balance DESC", "new": "joined_at DESC"}.get(order, "balance DESC")
    with get_conn() as conn:
        return conn.execute(
            f"SELECT * FROM users ORDER BY {col} LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()


def count_users():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def mark_blocked_bot(user_id, blocked: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET blocked_bot = ? WHERE telegram_id = ?",
            (1 if blocked else 0, user_id),
        )


def list_blocked_bot_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id, username, balance FROM users WHERE blocked_bot = 1 ORDER BY telegram_id"
        ).fetchall()


def count_blocked_bot():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users WHERE blocked_bot = 1").fetchone()["c"]


def is_banned(user_id) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return bool(row["is_banned"]) if row else False


def set_banned(user_id, banned: bool):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, joined_at, balance) VALUES (?, NULL, ?, 0)",
            (user_id, utcnow()),
        )
        conn.execute(
            "UPDATE users SET is_banned = ? WHERE telegram_id = ?", (1 if banned else 0, user_id)
        )


def list_banned_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id, username FROM users WHERE is_banned = 1 ORDER BY telegram_id"
        ).fetchall()


def get_all_user_ids(skip_banned=True):
    with get_conn() as conn:
        sql = "SELECT telegram_id FROM users"
        if skip_banned:
            sql += " WHERE is_banned = 0"
        return [r["telegram_id"] for r in conn.execute(sql).fetchall()]


# ---------------------------------------------------------------- wallet + ledger

def _add_ledger(conn, user_id, kind, amount, ref_type=None, ref_id=None, note="", admin_id=None):
    row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    balance_after = row["balance"] if row else 0
    conn.execute(
        "INSERT INTO ledger (user_id, kind, amount, balance_after, ref_type, ref_id, note, admin_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, kind, amount, balance_after, ref_type, ref_id, note, admin_id, utcnow()),
    )


def get_balance(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        return row["balance"] if row else 0.0


def add_balance(user_id, amount, kind="manual_credit", note="", admin_id=None, ref_type=None, ref_id=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, joined_at, balance) VALUES (?, NULL, ?, 0)",
            (user_id, utcnow()),
        )
        conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, user_id))
        _add_ledger(conn, user_id, kind, amount, ref_type, ref_id, note, admin_id)


def deduct_balance(user_id, amount, kind="purchase", note="", admin_id=None, ref_type=None, ref_id=None):
    """Atomically deducts if there is enough balance. Returns True/False."""
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if not row or row["balance"] < amount:
            return False
        conn.execute("UPDATE users SET balance = balance - ? WHERE telegram_id = ?", (amount, user_id))
        _add_ledger(conn, user_id, kind, -abs(amount), ref_type, ref_id, note, admin_id)
        return True


def create_topup(user_id, tx_id, amount, method="binance"):
    """Records a wallet top-up. False if this tx_id was already used."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO topups (user_id, tx_id, amount, method, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, tx_id, amount, method, utcnow()),
            )
        except Exception as e:
            if _is_unique_violation(e):
                return False
            raise
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, joined_at, balance) VALUES (?, NULL, ?, 0)",
            (user_id, utcnow()),
        )
        conn.execute("UPDATE users SET balance = balance + ? WHERE telegram_id = ?", (amount, user_id))
        _add_ledger(conn, user_id, "topup", amount, "topup", None, f"tx {tx_id}")
        return True


def get_user_ledger(user_id, limit=20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()


# ---------------------------------------------------------------- countries

def add_country(name, flag="🌍"):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM countries WHERE name = ?", (name,)).fetchone()
        if existing:
            return None
        return conn.insert(
            "INSERT INTO countries (name, flag, active, created_at) VALUES (?, ?, 1, ?)",
            (name, flag, utcnow()),
        )


def get_country(country_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM countries WHERE id = ?", (country_id,)).fetchone()


def list_countries(active_only=True):
    with get_conn() as conn:
        sql = "SELECT * FROM countries"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY sort_order, name"
        return conn.execute(sql).fetchall()


def list_countries_with_stock():
    """Countries that have at least one active package, with how many SIMs are
    available across all of them — that is what the customer list shows."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT c.id, c.name, c.flag,
                   COUNT(DISTINCT p.id) AS plans,
                   COALESCE(SUM(CASE WHEN s.status = 'available' THEN 1 ELSE 0 END), 0) AS available
            FROM countries c
            JOIN products p ON p.country_id = c.id AND p.active = 1
            LEFT JOIN stock s ON s.product_id = p.id
            WHERE c.active = 1
            GROUP BY c.id, c.name, c.flag
            ORDER BY c.sort_order, c.name
        """).fetchall()


def update_country(country_id, name=None, flag=None, active=None):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM countries WHERE id = ?", (country_id,)).fetchone()
        if not row:
            return False
        new_name = name if name is not None else row["name"]
        new_flag = flag if flag is not None else row["flag"]
        new_active = row["active"] if active is None else (1 if active else 0)
        conn.execute(
            "UPDATE countries SET name = ?, flag = ?, active = ? WHERE id = ?",
            (new_name, new_flag, new_active, country_id),
        )
        # keep the denormalised copy on products in sync
        conn.execute(
            "UPDATE products SET country = ?, flag = ? WHERE country_id = ?",
            (new_name, new_flag, country_id),
        )
        return True


def delete_country(country_id):
    """Deletes a country, its packages and their unsold SIMs. Sold SIMs and old
    orders stay untouched so history and warranty lookups keep working."""
    with get_conn() as conn:
        products = conn.execute(
            "SELECT id FROM products WHERE country_id = ?", (country_id,)
        ).fetchall()
        for p in products:
            conn.execute(
                "DELETE FROM stock WHERE product_id = ? AND status = 'available'", (p["id"],)
            )
            conn.execute("DELETE FROM products WHERE id = ?", (p["id"],))
        conn.execute("DELETE FROM countries WHERE id = ?", (country_id,))
        return len(products)


def country_stats(country_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(DISTINCT p.id) AS plans,
                   COALESCE(SUM(CASE WHEN s.status = 'available' THEN 1 ELSE 0 END), 0) AS available,
                   COALESCE(SUM(CASE WHEN s.status = 'sold' THEN 1 ELSE 0 END), 0) AS sold
            FROM products p LEFT JOIN stock s ON s.product_id = p.id
            WHERE p.country_id = ?
        """, (country_id,)).fetchone()


# ---------------------------------------------------------------- products

def add_product(country_id, data_amount, validity_days, price_usdt, description="", warranty_days=0):
    with get_conn() as conn:
        c = conn.execute("SELECT * FROM countries WHERE id = ?", (country_id,)).fetchone()
        if not c:
            return None
        return conn.insert(
            "INSERT INTO products (country_id, country, flag, data_amount, validity_days, "
            "price_usdt, description, warranty_days, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (country_id, c["name"], c["flag"], data_amount, validity_days,
             price_usdt, description, warranty_days, utcnow()),
        )


def get_product(product_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()


def list_products(country_id=None, active_only=True):
    with get_conn() as conn:
        where, params = [], []
        if country_id is not None:
            where.append("country_id = ?")
            params.append(country_id)
        if active_only:
            where.append("active = 1")
        sql = "SELECT * FROM products"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY country, price_usdt"
        return conn.execute(sql, tuple(params)).fetchall()


def list_products_with_stock(country_id=None, active_only=True):
    """Packages plus their available / sold counts — one query instead of N."""
    with get_conn() as conn:
        where, params = [], []
        if country_id is not None:
            where.append("p.country_id = ?")
            params.append(country_id)
        if active_only:
            where.append("p.active = 1")
        sql = """
            SELECT p.*,
                   COALESCE(SUM(CASE WHEN s.status = 'available' THEN 1 ELSE 0 END), 0) AS available,
                   COALESCE(SUM(CASE WHEN s.status = 'sold' THEN 1 ELSE 0 END), 0) AS sold
            FROM products p LEFT JOIN stock s ON s.product_id = p.id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY p.id ORDER BY p.country, p.price_usdt"
        return conn.execute(sql, tuple(params)).fetchall()


def update_product(product_id, **fields):
    allowed = {"data_amount", "validity_days", "price_usdt", "description",
               "warranty_days", "active", "daily_limit"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(product_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return True


def delete_product(product_id):
    """Hard-deletes a package, its bulk tiers and its unsold SIMs; sold SIMs and
    orders stay."""
    with get_conn() as conn:
        conn.execute("DELETE FROM stock WHERE product_id = ? AND status = 'available'", (product_id,))
        conn.execute("DELETE FROM price_tiers WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))


def count_available_stock(product_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM stock WHERE product_id = ? AND status = 'available'",
            (product_id,),
        ).fetchone()["c"]


def product_label(p) -> str:
    if not p:
        return "باقة محذوفة"
    return f"{p['flag']} {p['country']} — {p['data_amount']} / {p['validity_days']} يوم"


# ---------------------------------------------------------------- stock

def lpa_exists(lpa_code) -> bool:
    with get_conn() as conn:
        return bool(conn.execute(
            "SELECT 1 AS x FROM stock WHERE lpa_code = ? LIMIT 1", (lpa_code,)
        ).fetchone())


def add_stock_batch(product_id, codes, supplier="", cost_price=0.0, validity_days=None,
                    warranty_days=0, batch_id=None, added_by=None, note=""):
    """Adds many SIMs at once. Returns (added, duplicates_skipped)."""
    added, dupes = 0, []
    with get_conn() as conn:
        for code in codes:
            code = code.strip()
            if not code:
                continue
            exists = conn.execute(
                "SELECT 1 AS x FROM stock WHERE lpa_code = ? LIMIT 1", (code,)
            ).fetchone()
            if exists:
                dupes.append(code)
                continue
            conn.execute(
                "INSERT INTO stock (product_id, lpa_code, is_used, status, supplier, cost_price, "
                "validity_days, warranty_days, batch_id, note, added_at, added_by) "
                "VALUES (?, ?, 0, 'available', ?, ?, ?, ?, ?, ?, ?, ?)",
                (product_id, code, supplier, cost_price, validity_days, warranty_days,
                 batch_id, note, utcnow(), added_by),
            )
            added += 1
    return added, dupes


def get_stock(stock_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM stock WHERE id = ?", (stock_id,)).fetchone()


def reserve_stock(product_id, order_id):
    """Takes the oldest available SIM for a package and marks it sold (FIFO).
    Returns the stock row, or None when nothing is available."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM stock WHERE product_id = ? AND status = 'available' ORDER BY id LIMIT 1",
            (product_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE stock SET status = 'sold', is_used = 1, order_id = ?, sold_at = ? "
            "WHERE id = ? AND status = 'available'",
            (order_id, utcnow(), row["id"]),
        )
        check = conn.execute(
            "SELECT * FROM stock WHERE id = ? AND order_id = ?", (row["id"], order_id)
        ).fetchone()
        return check


def void_stock(stock_id, note=""):
    """Takes a SIM out of circulation without deleting it (bad code, burned, etc)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE stock SET status = 'void', note = ? WHERE id = ? AND status = 'available'",
            (note, stock_id),
        )


def delete_stock(stock_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM stock WHERE id = ? AND status = 'available'", (stock_id,))


def list_stock(product_id=None, status=None, supplier=None, limit=50, offset=0):
    where, params = [], []
    if product_id is not None:
        where.append("s.product_id = ?")
        params.append(product_id)
    if status:
        where.append("s.status = ?")
        params.append(status)
    if supplier:
        where.append("s.supplier = ?")
        params.append(supplier)
    sql = """SELECT s.*, p.country, p.flag, p.data_amount, p.price_usdt,
                    o.user_id AS buyer_id, o.username AS buyer_name
             FROM stock s
             LEFT JOIN products p ON p.id = s.product_id
             LEFT JOIN orders o ON o.id = s.order_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def search_stock(text, limit=20):
    """Free-text search over LPA code, supplier, batch and country."""
    like = f"%{text}%"
    with get_conn() as conn:
        return conn.execute("""
            SELECT s.*, p.country, p.flag, p.data_amount, p.price_usdt,
                   o.user_id AS buyer_id, o.username AS buyer_name
            FROM stock s
            LEFT JOIN products p ON p.id = s.product_id
            LEFT JOIN orders o ON o.id = s.order_id
            WHERE s.lpa_code LIKE ? OR s.supplier LIKE ? OR s.batch_id LIKE ?
               OR p.country LIKE ? OR CAST(s.id AS TEXT) = ?
            ORDER BY s.id DESC LIMIT ?
        """, (like, like, like, like, text, limit)).fetchall()


def stock_detail(stock_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT s.*, p.country, p.flag, p.data_amount, p.validity_days AS plan_days,
                   p.price_usdt, o.id AS order_no, o.user_id AS buyer_id,
                   o.username AS buyer_name, o.price AS sold_price,
                   o.created_at AS order_at, o.warranty_until
            FROM stock s
            LEFT JOIN products p ON p.id = s.product_id
            LEFT JOIN orders o ON o.id = s.order_id
            WHERE s.id = ?
        """, (stock_id,)).fetchone()


def stock_counts():
    with get_conn() as conn:
        return conn.execute("""
            SELECT COALESCE(SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END), 0) AS available,
                   COALESCE(SUM(CASE WHEN status = 'sold' THEN 1 ELSE 0 END), 0) AS sold,
                   COALESCE(SUM(CASE WHEN status = 'void' THEN 1 ELSE 0 END), 0) AS void,
                   COUNT(*) AS total
            FROM stock
        """).fetchone()


def inventory_value():
    """What the unsold stock cost you, and what it would bring in if it all sold."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(*) AS units,
                   COALESCE(SUM(s.cost_price), 0) AS cost_value,
                   COALESCE(SUM(p.price_usdt), 0) AS sale_value
            FROM stock s LEFT JOIN products p ON p.id = s.product_id
            WHERE s.status = 'available'
        """).fetchone()


def list_suppliers():
    with get_conn() as conn:
        return conn.execute("""
            SELECT COALESCE(NULLIF(supplier, ''), 'غير محدد') AS supplier,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
                   SUM(CASE WHEN status = 'sold' THEN 1 ELSE 0 END) AS sold,
                   COALESCE(SUM(cost_price), 0) AS spent
            FROM stock GROUP BY supplier ORDER BY total DESC
        """).fetchall()


def low_stock_products(threshold=3):
    with get_conn() as conn:
        return conn.execute("""
            SELECT p.*, COALESCE(SUM(CASE WHEN s.status = 'available' THEN 1 ELSE 0 END), 0) AS available
            FROM products p LEFT JOIN stock s ON s.product_id = p.id
            WHERE p.active = 1
            GROUP BY p.id HAVING available <= ?
            ORDER BY available, p.country
        """, (threshold,)).fetchall()


# ---------------------------------------------------------------- orders

# ---------------------------------------------------------------- bulk pricing
# A tier says "starting from N SIMs, each one costs X". The tier with the
# largest min_qty that the quantity reaches is the one that applies.

def add_tier(product_id, min_qty, unit_price):
    """Adds a tier, or overwrites the one already sitting on that quantity."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM price_tiers WHERE product_id = ? AND min_qty = ?",
            (product_id, min_qty),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE price_tiers SET unit_price = ? WHERE id = ?",
                (unit_price, existing["id"]),
            )
            return existing["id"]
        return conn.insert(
            "INSERT INTO price_tiers (product_id, min_qty, unit_price, created_at) "
            "VALUES (?, ?, ?, ?)",
            (product_id, min_qty, unit_price, utcnow()),
        )


def list_tiers(product_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM price_tiers WHERE product_id = ? AND min_qty > 1 ORDER BY min_qty",
            (product_id,),
        ).fetchall()


def get_tier(tier_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM price_tiers WHERE id = ?", (tier_id,)).fetchone()


def delete_tier(tier_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM price_tiers WHERE id = ?", (tier_id,))


def delete_tiers_for_product(product_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM price_tiers WHERE product_id = ?", (product_id,))


def unit_price_for(product_id, qty):
    """Returns (unit_price, applied_min_qty). applied_min_qty is None at retail."""
    with get_conn() as conn:
        p = conn.execute("SELECT price_usdt FROM products WHERE id = ?", (product_id,)).fetchone()
        base = p["price_usdt"] if p else 0.0
        tier = conn.execute(
            "SELECT * FROM price_tiers WHERE product_id = ? AND min_qty <= ? AND min_qty > 1 "
            "ORDER BY min_qty DESC LIMIT 1",
            (product_id, qty),
        ).fetchone()
        if tier:
            return tier["unit_price"], tier["min_qty"]
        return base, None


# ---------------------------------------------------------------- orders

def create_order(user_id, username, product_id, price, tx_id, cost_price=0.0,
                 product_label_text="", warranty_until=None, group_id=None):
    with get_conn() as conn:
        return conn.insert(
            "INSERT INTO orders (user_id, username, product_id, price, tx_id, status, "
            "cost_price, product_label, warranty_until, group_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (user_id, username, product_id, price, tx_id, cost_price,
             product_label_text, warranty_until, group_id, utcnow()),
        )


def get_order_group(group_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.*, s.lpa_code, s.id AS sim_id
            FROM orders o LEFT JOIN stock s ON s.id = o.stock_id
            WHERE o.group_id = ? ORDER BY o.id
        """, (group_id,)).fetchall()


def get_order(order_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def order_detail(order_id):
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.*, s.lpa_code, s.supplier, s.id AS sim_id, s.warranty_days,
                   p.country, p.flag, p.data_amount
            FROM orders o
            LEFT JOIN stock s ON s.id = o.stock_id
            LEFT JOIN products p ON p.id = o.product_id
            WHERE o.id = ?
        """, (order_id,)).fetchone()


def attach_stock_to_order(order_id, stock_id, cost_price, warranty_until):
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET stock_id = ?, cost_price = ?, warranty_until = ?, "
            "status = 'approved', delivered_at = ? WHERE id = ?",
            (stock_id, cost_price, warranty_until, utcnow(), order_id),
        )


def update_order_status(order_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def get_user_orders(user_id, limit=30):
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.*, s.lpa_code, p.country, p.flag, p.data_amount
            FROM orders o
            LEFT JOIN stock s ON s.id = o.stock_id
            LEFT JOIN products p ON p.id = o.product_id
            WHERE o.user_id = ? ORDER BY o.id DESC LIMIT ?
        """, (user_id, limit)).fetchall()


def get_pending_orders():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY id").fetchall()


def search_orders(text, limit=20):
    like = f"%{text}%"
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.*, p.country, p.flag, p.data_amount, s.lpa_code
            FROM orders o
            LEFT JOIN products p ON p.id = o.product_id
            LEFT JOIN stock s ON s.id = o.stock_id
            WHERE CAST(o.id AS TEXT) = ? OR CAST(o.user_id AS TEXT) LIKE ?
               OR o.username LIKE ? OR s.lpa_code LIKE ?
            ORDER BY o.id DESC LIMIT ?
        """, (text, like, like, like, limit)).fetchall()


def tx_id_already_used(tx_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE tx_id = ? AND status = 'approved'", (tx_id,)
        ).fetchone()["c"] > 0


# ---------------------------------------------------------------- warranty

def create_claim(order_id, user_id, reason):
    with get_conn() as conn:
        return conn.insert(
            "INSERT INTO warranty_claims (order_id, user_id, reason, status, created_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (order_id, user_id, reason, utcnow()),
        )


def list_claims(status="open", limit=30):
    with get_conn() as conn:
        sql = "SELECT * FROM warranty_claims"
        params = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, tuple(params)).fetchall()


def get_claim(claim_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM warranty_claims WHERE id = ?", (claim_id,)).fetchone()


def resolve_claim(claim_id, status, resolution, admin_id, replacement_stock_id=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE warranty_claims SET status = ?, resolution = ?, resolved_at = ?, "
            "resolved_by = ?, replacement_stock_id = ? WHERE id = ?",
            (status, resolution, utcnow(), admin_id, replacement_stock_id, claim_id),
        )


def count_open_claims():
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM warranty_claims WHERE status = 'open'"
        ).fetchone()["c"]


# ---------------------------------------------------------------- reports
# Every function below takes UTC boundary strings 'YYYY-MM-DD HH:MM:SS'.

def sales_summary(start, end):
    """A bulk purchase is one row per SIM sharing a group_id, so `orders` counts
    the actual purchases and `sims` counts the SIMs that went out."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(*) AS sims,
                   COUNT(DISTINCT COALESCE(group_id, CAST(id AS TEXT))) AS orders,
                   COALESCE(SUM(price), 0) AS revenue,
                   COALESCE(SUM(cost_price), 0) AS cost,
                   COUNT(DISTINCT user_id) AS buyers
            FROM orders
            WHERE status = 'approved' AND created_at >= ? AND created_at < ?
        """, (start, end)).fetchone()


def ledger_summary(start, end):
    with get_conn() as conn:
        return conn.execute("""
            SELECT kind, COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total
            FROM ledger WHERE created_at >= ? AND created_at < ?
            GROUP BY kind ORDER BY total DESC
        """, (start, end)).fetchall()


def topups_summary(start, end):
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total
            FROM topups WHERE created_at >= ? AND created_at < ?
        """, (start, end)).fetchone()


def total_customer_balance():
    with get_conn() as conn:
        return conn.execute("""
            SELECT COALESCE(SUM(balance), 0) AS total,
                   COUNT(CASE WHEN balance > 0 THEN 1 END) AS holders
            FROM users
        """).fetchone()


def lifetime_totals():
    with get_conn() as conn:
        sales = conn.execute("""
            SELECT COUNT(*) AS sims,
                   COUNT(DISTINCT COALESCE(group_id, CAST(id AS TEXT))) AS orders,
                   COALESCE(SUM(price), 0) AS revenue,
                   COALESCE(SUM(cost_price), 0) AS cost
            FROM orders WHERE status = 'approved'
        """).fetchone()
        credited = conn.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total FROM ledger WHERE amount > 0
        """).fetchone()
        return sales, credited


def new_users_count(start, end):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE joined_at >= ? AND joined_at < ?", (start, end)
        ).fetchone()["c"]


def top_products(start, end, limit=5):
    with get_conn() as conn:
        return conn.execute("""
            SELECT p.country, p.flag, p.data_amount, COUNT(*) AS sold,
                   COALESCE(SUM(o.price), 0) AS revenue,
                   COALESCE(SUM(o.price - o.cost_price), 0) AS profit
            FROM orders o LEFT JOIN products p ON p.id = o.product_id
            WHERE o.status = 'approved' AND o.created_at >= ? AND o.created_at < ?
            GROUP BY o.product_id ORDER BY sold DESC LIMIT ?
        """, (start, end, limit)).fetchall()


def top_countries(start, end, limit=5):
    with get_conn() as conn:
        return conn.execute("""
            SELECT p.country, p.flag, COUNT(*) AS sold, COALESCE(SUM(o.price), 0) AS revenue
            FROM orders o LEFT JOIN products p ON p.id = o.product_id
            WHERE o.status = 'approved' AND o.created_at >= ? AND o.created_at < ?
            GROUP BY p.country ORDER BY revenue DESC LIMIT ?
        """, (start, end, limit)).fetchall()


def top_customers(start, end, limit=5):
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.user_id, o.username,
                   COUNT(DISTINCT COALESCE(o.group_id, CAST(o.id AS TEXT))) AS orders,
                   COUNT(*) AS sims, COALESCE(SUM(o.price), 0) AS spent
            FROM orders o
            WHERE o.status = 'approved' AND o.created_at >= ? AND o.created_at < ?
            GROUP BY o.user_id ORDER BY spent DESC LIMIT ?
        """, (start, end, limit)).fetchall()


def daily_sales(start, end):
    """Revenue per day, for the little text chart in the report."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS sims,
                   COALESCE(SUM(price), 0) AS revenue
            FROM orders WHERE status = 'approved' AND created_at >= ? AND created_at < ?
            GROUP BY day ORDER BY day
        """, (start, end)).fetchall()


def orders_in_range(start, end, limit=2000):
    """Raw rows used for the CSV export."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT o.id, o.created_at, o.user_id, o.username, p.country, p.data_amount,
                   o.price, o.cost_price, s.supplier, s.lpa_code, o.warranty_until, o.status
            FROM orders o
            LEFT JOIN products p ON p.id = o.product_id
            LEFT JOIN stock s ON s.id = o.stock_id
            WHERE o.created_at >= ? AND o.created_at < ?
            ORDER BY o.id LIMIT ?
        """, (start, end, limit)).fetchall()
