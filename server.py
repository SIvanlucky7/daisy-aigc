#!/usr/bin/env python3
"""Daisy AIGC commercial scaffold.

This standalone server serves the website and exposes a small API facade:
- AIGC mode delegates to an authorized BypassAIGC instance.
- Repeat mode uses an OpenAI-compatible model API.
- Combo mode runs AIGC first, then repeat reduction.

It intentionally does not copy BypassAIGC code. Use it only with a license or
authorization that allows your commercial deployment.
"""

from __future__ import annotations

import base64
import copy
import difflib
import json
import os
import re
import asyncio
import hashlib
import hmac
import secrets
import sqlite3
import time
import zipfile
import csv
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO, StringIO
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

try:
    import libsql_client
except ImportError:
    libsql_client = None


ROOT = Path(__file__).resolve().parent
APP_VERSION = "2026-06-19-docx-report-1"


def load_dotenv() -> None:
    configured_path = os.getenv("DAISY_ENV_FILE", "").strip()
    env_path = Path(configured_path) if configured_path else ROOT / ".env"
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and not os.environ.get(key):
            os.environ[key] = value


load_dotenv()

ORDERS_FILE = Path(os.getenv("DAISY_ORDERS_FILE", str(ROOT / "orders.jsonl")))
DB_FILE = Path(os.getenv("DAISY_DB_FILE", str(ROOT / "daisy.db")))
if not ORDERS_FILE.is_absolute():
    ORDERS_FILE = ROOT / ORDERS_FILE
if not DB_FILE.is_absolute():
    DB_FILE = ROOT / DB_FILE
BYPASS_BASE_URL = os.getenv("BYPASS_BASE_URL", "http://localhost:9800/api").rstrip("/")
BYPASS_ADMIN_USERNAME = os.getenv("BYPASS_ADMIN_USERNAME", "admin")
BYPASS_ADMIN_PASSWORD = os.getenv("BYPASS_ADMIN_PASSWORD", "please-change-this-password")
BYPASS_CARD_PREFIX = os.getenv("BYPASS_CARD_PREFIX", "DAISY")
BYPASS_COMMERCIAL_AUTHORIZED = os.getenv("DAISY_BYPASS_COMMERCIAL_AUTHORIZED", "0") == "1"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
REWRITE_MODEL = os.getenv("REWRITE_MODEL", "deepseek-chat")
DEMO_FALLBACK = os.getenv("DAISY_DEMO_FALLBACK", "1") == "1"
PAYMENT_PROVIDER = os.getenv("DAISY_PAYMENT_PROVIDER", "mock").strip().lower()
MOCK_PAYMENTS_ENABLED = os.getenv("DAISY_ENABLE_MOCK_PAYMENT", "1" if DEMO_FALLBACK else "0") == "1"
MANUAL_PAYMENT_QR_URL = os.getenv("DAISY_MANUAL_PAYMENT_QR_URL", "").strip()
MANUAL_PAYMENT_ACCOUNT = os.getenv("DAISY_MANUAL_PAYMENT_ACCOUNT", "").strip()
MANUAL_PAYMENT_INSTRUCTIONS = os.getenv(
    "DAISY_MANUAL_PAYMENT_INSTRUCTIONS",
    "请扫码支付对应金额，并在付款备注中填写支付单号。付款后等待客服在后台确认入账。",
).strip()
DEFAULT_USER_ID = os.getenv("DAISY_DEFAULT_USER_ID", "demo-user")
INITIAL_BALANCE_CENTS = int(os.getenv("DAISY_INITIAL_BALANCE_CENTS", "3680"))
REGISTRATION_BONUS_CENTS = int(os.getenv("DAISY_REGISTRATION_BONUS_CENTS", "0"))
MAX_UPLOAD_BYTES = int(os.getenv("DAISY_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
MAX_EXTRACTED_CHARS = int(os.getenv("DAISY_MAX_EXTRACTED_CHARS", "6000"))
RESULT_RETENTION_DAYS = int(os.getenv("DAISY_RESULT_RETENTION_DAYS", "0"))
SESSION_COOKIE = "daisy_session"
SESSION_TTL_SECONDS = int(os.getenv("DAISY_SESSION_TTL_SECONDS", str(14 * 24 * 60 * 60)))
COOKIE_SECURE = os.getenv("DAISY_COOKIE_SECURE", "1" if os.getenv("VERCEL") else "0") == "1"
COOKIE_SAMESITE = os.getenv("DAISY_COOKIE_SAMESITE", "Lax").strip() or "Lax"
PAYMENT_WEBHOOK_SECRET = os.getenv("DAISY_PAYMENT_WEBHOOK_SECRET", "dev-secret-change-me")
API_RATE_LIMIT_PER_HOUR = int(os.getenv("DAISY_API_RATE_LIMIT_PER_HOUR", "60"))
LOGIN_MAX_FAILED_ATTEMPTS = int(os.getenv("DAISY_LOGIN_MAX_FAILED_ATTEMPTS", "8"))
LOGIN_LOCK_SECONDS = int(os.getenv("DAISY_LOGIN_LOCK_SECONDS", "900"))
ADMIN_EMAIL_LIST = [
    email.strip().lower()
    for email in os.getenv("DAISY_ADMIN_EMAILS", "admin@daisy.local").split(",")
    if email.strip()
]
ADMIN_EMAILS = set(ADMIN_EMAIL_LIST)
ADMIN_BOOTSTRAP_EMAIL = ADMIN_EMAIL_LIST[0] if ADMIN_EMAIL_LIST else "admin@daisy.local"
ADMIN_BOOTSTRAP_PASSWORD = os.getenv("DAISY_ADMIN_PASSWORD", "admin123456")
ADMIN_INITIAL_BALANCE_CENTS = int(os.getenv("DAISY_ADMIN_INITIAL_BALANCE_CENTS", "0"))
STATELESS_SESSIONS = os.getenv("DAISY_STATELESS_SESSIONS", "0") == "1"
ALLOW_EPHEMERAL_BILLING = os.getenv("DAISY_ALLOW_EPHEMERAL_BILLING", "0") == "1"
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
TURSO_CLIENT_URL = (
    f"https://{TURSO_DATABASE_URL.removeprefix('libsql://')}"
    if TURSO_DATABASE_URL.startswith("libsql://")
    else TURSO_DATABASE_URL
)
USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)
SUPABASE_URL = (
    os.getenv("VITE_SUPABASE_URL", "").strip()
    or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "").strip()
)
SUPABASE_ANON_KEY = (
    os.getenv("VITE_SUPABASE_ANON_KEY", "").strip()
    or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "").strip()
)
SUPABASE_AUTH_ENABLED = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_ADMIN_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
PUBLIC_BASE_URL = os.getenv("DAISY_PUBLIC_BASE_URL", "").strip().rstrip("/")
REQUIRE_AUTH_FOR_BILLABLE = os.getenv("DAISY_REQUIRE_AUTH_FOR_BILLABLE", "1") != "0"
DEFAULT_CORS_ORIGINS = "http://localhost:9910,http://127.0.0.1:9910,https://daisy-aigc.vercel.app,https://sivanlucky7.github.io"
ALLOWED_CORS_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("DAISY_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
}
WECHAT_APP_ID = os.getenv("WECHAT_APP_ID", "").strip()
WECHAT_APP_SECRET = os.getenv("WECHAT_APP_SECRET", "").strip()
WECHAT_REDIRECT_URI = os.getenv("WECHAT_REDIRECT_URI", "").strip()
WECHAT_OAUTH_SCOPE = os.getenv("WECHAT_OAUTH_SCOPE", "snsapi_login").strip() or "snsapi_login"
WECHAT_LOGIN_ENABLED = False

SERVICE_RATES = {
    "aigc": 100,
    "repeat": 100,
    "combo": 180,
}
LANGUAGE_RATE_MULTIPLIERS = {
    "zh": 1,
    "en": 2,
}
PLATFORM_NAMES = {
    "cnki": "知网",
    "weipu": "维普",
    "general": "通用平台",
    "gecida": "格子达(学生版)",
    "zhuque": "朱雀平台",
    "huachen": "华宸",
}

ADMIN_EXPORTS = {
    "users": {
        "filename": "daisy-users.csv",
        "headers": ["id", "email", "display_name", "balance_cents", "created_at"],
        "sql": """
            SELECT id, email, display_name, balance_cents, created_at
            FROM users
            ORDER BY created_at DESC
        """,
    },
    "orders": {
        "filename": "daisy-orders.csv",
        "headers": [
            "order_id",
            "user_id",
            "service",
            "platform",
            "language",
            "chars",
            "amount_cents",
            "engine",
            "status",
            "error",
            "created_at",
            "completed_at",
        ],
        "sql": """
            SELECT order_id, user_id, service, platform, language, chars, amount_cents,
                   engine, status, error, created_at, completed_at
            FROM orders
            ORDER BY created_at DESC
        """,
    },
    "payments": {
        "filename": "daisy-payments.csv",
        "headers": [
            "payment_id",
            "user_id",
            "amount_cents",
            "provider",
            "status",
            "provider_trade_no",
            "user_trade_no",
            "user_payment_note",
            "user_claimed_amount_cents",
            "notified_at",
            "cancel_note",
            "canceled_at",
            "created_at",
            "paid_at",
        ],
        "sql": """
            SELECT payment_id, user_id, amount_cents, provider, status,
                   provider_trade_no, user_trade_no, user_payment_note,
                   user_claimed_amount_cents, notified_at,
                   cancel_note, canceled_at,
                   created_at, paid_at
            FROM payments
            ORDER BY created_at DESC
        """,
    },
    "ledger": {
        "filename": "daisy-ledger.csv",
        "headers": [
            "id",
            "user_id",
            "type",
            "amount_cents",
            "balance_after_cents",
            "ref_id",
            "created_at",
            "note",
        ],
        "sql": """
            SELECT id, user_id, type, amount_cents, balance_after_cents,
                   ref_id, created_at, note
            FROM ledger
            ORDER BY id DESC
        """,
    },
    "api_usage": {
        "filename": "daisy-api-usage.csv",
        "headers": [
            "id",
            "api_key_id",
            "user_id",
            "order_id",
            "status",
            "chars",
            "amount_cents",
            "error",
            "created_at",
        ],
        "sql": """
            SELECT id, api_key_id, user_id, order_id, status, chars,
                   amount_cents, error, created_at
            FROM api_usage
            ORDER BY id DESC
        """,
    },
    "admin_audit": {
        "filename": "daisy-admin-audit.csv",
        "headers": [
            "id",
            "admin_user_id",
            "action",
            "target_user_id",
            "target_email",
            "amount_cents",
            "ref_id",
            "note",
            "ip",
            "created_at",
        ],
        "sql": """
            SELECT id, admin_user_id, action, target_user_id, target_email,
                   amount_cents, ref_id, note, ip, created_at
            FROM admin_audit
            ORDER BY id DESC
        """,
    },
    "support_tickets": {
        "filename": "daisy-support-tickets.csv",
        "headers": [
            "ticket_id",
            "user_id",
            "email",
            "category",
            "subject",
            "message",
            "ref_id",
            "status",
            "admin_note",
            "created_at",
            "resolved_at",
        ],
        "sql": """
            SELECT ticket_id, user_id, email, category, subject, message, ref_id,
                   status, admin_note, created_at, resolved_at
            FROM support_tickets
            ORDER BY created_at DESC
        """,
    },
}


def http_json(method: str, url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 90):
    body = None
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {detail[:500]}") from exc


def run_async(coro):
    return asyncio.run(coro)


class LibsqlRow(dict):
    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._columns = list(columns)
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def keys(self):
        return self._columns


class LibsqlCursor:
    def __init__(self, result):
        self.result = result
        self.columns = [str(column) for column in getattr(result, "columns", []) or []]
        self.rowcount = getattr(result, "rows_affected", None)
        self.lastrowid = getattr(result, "last_insert_rowid", None)

    def _row(self, row):
        if row is None:
            return None
        if isinstance(row, LibsqlRow):
            return row
        if isinstance(row, dict):
            columns = list(row.keys())
            return LibsqlRow(columns, [row[column] for column in columns])
        if hasattr(row, "asdict"):
            values = row.asdict()
            columns = list(values.keys())
            return LibsqlRow(columns, [values[column] for column in columns])
        if hasattr(row, "_mapping"):
            values = dict(row._mapping)
            columns = list(values.keys())
            return LibsqlRow(columns, [values[column] for column in columns])
        try:
            values = dict(row)
            columns = list(values.keys())
            return LibsqlRow(columns, [values[column] for column in columns])
        except (TypeError, ValueError):
            pass
        if self.columns:
            return LibsqlRow(self.columns, list(row))
        return row

    def fetchone(self):
        return self._row(self.result.rows[0]) if self.result.rows else None

    def fetchall(self):
        return [self._row(row) for row in self.result.rows]

    def __iter__(self):
        return iter(self.fetchall())


class LibsqlConnection:
    def __init__(self):
        if not libsql_client:
            raise RuntimeError("libsql-client is not installed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        return None

    def execute(self, sql: str, parameters=()):
        async def _execute():
            client = libsql_client.create_client(TURSO_CLIENT_URL, auth_token=TURSO_AUTH_TOKEN)
            try:
                return await client.execute(sql, list(parameters or ()))
            finally:
                await client.close()

        return LibsqlCursor(run_async(_execute()))

    def executescript(self, script: str):
        for statement in (part.strip() for part in script.split(";")):
            if statement:
                self.execute(statement)
        return None


def db():
    if USE_TURSO:
        return LibsqlConnection()
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def database_is_ephemeral() -> bool:
    if USE_TURSO:
        return False
    normalized = str(DB_FILE).replace("\\", "/")
    return normalized.startswith("/tmp/") or (bool(os.getenv("VERCEL")) and "/tmp/" in normalized)


def database_status() -> dict:
    ephemeral = database_is_ephemeral()
    engine = "turso-libsql" if USE_TURSO else "sqlite"
    return {
        "engine": engine,
        "path": TURSO_DATABASE_URL if USE_TURSO else str(DB_FILE),
        "ephemeral": ephemeral,
        "persistent": not ephemeral,
        "billing_allowed": (not ephemeral) or ALLOW_EPHEMERAL_BILLING,
        "allow_ephemeral_billing": ALLOW_EPHEMERAL_BILLING,
    }


def ensure_billing_storage_ready(action: str = "资金操作") -> None:
    if database_is_ephemeral() and not ALLOW_EPHEMERAL_BILLING:
        raise ValueError(
            f"当前后端使用临时数据库，已阻止{action}。"
            "请先迁移到持久数据库/香港服务器；仅内部测试时可设置 DAISY_ALLOW_EPHEMERAL_BILLING=1。"
        )


def parse_money_cents(value, field_name: str = "金额", allow_negative: bool = False) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        raise ValueError(f"请填写{field_name}")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name}格式不正确") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name}格式不正确")
    if not allow_negative and amount <= 0:
        raise ValueError(f"{field_name}必须大于 0")
    cents = int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not allow_negative and cents <= 0:
        raise ValueError(f"{field_name}必须大于 0")
    return cents


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              display_name TEXT NOT NULL,
              balance_cents INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
              order_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              service TEXT NOT NULL,
              platform TEXT NOT NULL,
              language TEXT NOT NULL,
              input_type TEXT NOT NULL DEFAULT 'text',
              source_filename TEXT,
              report_filename TEXT,
              chars INTEGER NOT NULL,
              amount_cents INTEGER NOT NULL,
              engine TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              created_at INTEGER NOT NULL,
              completed_at INTEGER,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ledger (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id TEXT NOT NULL,
              type TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              balance_after_cents INTEGER NOT NULL,
              ref_id TEXT,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS payments (
              payment_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              amount_cents INTEGER NOT NULL,
              provider TEXT NOT NULL,
              status TEXT NOT NULL,
              provider_trade_no TEXT,
              user_trade_no TEXT,
              user_payment_note TEXT,
              user_claimed_amount_cents INTEGER,
              notified_at INTEGER,
              cancel_note TEXT,
              canceled_at INTEGER,
              created_at INTEGER NOT NULL,
              paid_at INTEGER,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS api_keys (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              name TEXT NOT NULL,
              key_hash TEXT NOT NULL UNIQUE,
              key_prefix TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              last_used_at INTEGER,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS api_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              api_key_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              order_id TEXT,
              status TEXT NOT NULL,
              chars INTEGER NOT NULL DEFAULT 0,
              amount_cents INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(api_key_id) REFERENCES api_keys(id),
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL,
              ip TEXT NOT NULL,
              success INTEGER NOT NULL,
              created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_login_attempts_email_time
            ON login_attempts(email, created_at);

            CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
            ON login_attempts(ip, created_at);

            CREATE TABLE IF NOT EXISTS admin_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              admin_user_id TEXT NOT NULL,
              action TEXT NOT NULL,
              target_user_id TEXT,
              target_email TEXT,
              amount_cents INTEGER,
              ref_id TEXT,
              note TEXT,
              ip TEXT,
              created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_tickets (
              ticket_id TEXT PRIMARY KEY,
              user_id TEXT,
              email TEXT NOT NULL,
              category TEXT NOT NULL,
              subject TEXT NOT NULL,
              message TEXT NOT NULL,
              ref_id TEXT,
              status TEXT NOT NULL,
              admin_note TEXT,
              created_at INTEGER NOT NULL,
              resolved_at INTEGER,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
              state TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              redirect_path TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              expires_at INTEGER NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        for column, ddl in {
            "email": "ALTER TABLE users ADD COLUMN email TEXT",
            "password_salt": "ALTER TABLE users ADD COLUMN password_salt TEXT",
            "password_hash": "ALTER TABLE users ADD COLUMN password_hash TEXT",
            "terms_accepted_at": "ALTER TABLE users ADD COLUMN terms_accepted_at INTEGER",
            "wechat_openid": "ALTER TABLE users ADD COLUMN wechat_openid TEXT",
            "wechat_unionid": "ALTER TABLE users ADD COLUMN wechat_unionid TEXT",
            "avatar_url": "ALTER TABLE users ADD COLUMN avatar_url TEXT",
            "login_provider": "ALTER TABLE users ADD COLUMN login_provider TEXT",
            "supabase_user_id": "ALTER TABLE users ADD COLUMN supabase_user_id TEXT",
        }.items():
            if column not in columns:
                conn.execute(ddl)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_wechat_openid ON users(wechat_openid) WHERE wechat_openid IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_supabase_user_id ON users(supabase_user_id) WHERE supabase_user_id IS NOT NULL")

        order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        for column, ddl in {
            "result_text": "ALTER TABLE orders ADD COLUMN result_text TEXT",
            "input_type": "ALTER TABLE orders ADD COLUMN input_type TEXT NOT NULL DEFAULT 'text'",
            "source_filename": "ALTER TABLE orders ADD COLUMN source_filename TEXT",
            "report_filename": "ALTER TABLE orders ADD COLUMN report_filename TEXT",
            "result_docx_base64": "ALTER TABLE orders ADD COLUMN result_docx_base64 TEXT",
        }.items():
            if column not in order_columns:
                conn.execute(ddl)

        payment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(payments)").fetchall()}
        for column, ddl in {
            "user_trade_no": "ALTER TABLE payments ADD COLUMN user_trade_no TEXT",
            "user_payment_note": "ALTER TABLE payments ADD COLUMN user_payment_note TEXT",
            "user_claimed_amount_cents": "ALTER TABLE payments ADD COLUMN user_claimed_amount_cents INTEGER",
            "notified_at": "ALTER TABLE payments ADD COLUMN notified_at INTEGER",
            "cancel_note": "ALTER TABLE payments ADD COLUMN cancel_note TEXT",
            "canceled_at": "ALTER TABLE payments ADD COLUMN canceled_at INTEGER",
        }.items():
            if column not in payment_columns:
                conn.execute(ddl)

        ledger_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ledger)").fetchall()}
        if "note" not in ledger_columns:
            conn.execute("ALTER TABLE ledger ADD COLUMN note TEXT")

        row = conn.execute("SELECT id FROM users WHERE id = ?", (DEFAULT_USER_ID,)).fetchone()
        if not row:
            now = int(time.time())
            conn.execute(
                "INSERT INTO users (id, display_name, balance_cents, created_at) VALUES (?, ?, ?, ?)",
                (DEFAULT_USER_ID, "演示用户", INITIAL_BALANCE_CENTS, now),
            )
            conn.execute(
                "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (DEFAULT_USER_ID, "initial", INITIAL_BALANCE_CENTS, INITIAL_BALANCE_CENTS, None, now),
            )
        conn.execute("UPDATE users SET email = COALESCE(email, ?) WHERE id = ?", ("demo@daisy.local", DEFAULT_USER_ID))

        admin_row = conn.execute("SELECT * FROM users WHERE email = ?", (ADMIN_BOOTSTRAP_EMAIL,)).fetchone()
        if not admin_row:
            now = int(time.time())
            salt, password_hash = hash_password(ADMIN_BOOTSTRAP_PASSWORD)
            existing_admin = conn.execute("SELECT * FROM users WHERE id = ?", ("admin-user",)).fetchone()
            if existing_admin:
                conn.execute(
                    """
                    UPDATE users
                    SET display_name = ?, email = ?, password_salt = ?, password_hash = ?
                    WHERE id = ?
                    """,
                    ("管理员", ADMIN_BOOTSTRAP_EMAIL, salt, password_hash, "admin-user"),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO users (id, display_name, balance_cents, created_at, email, password_salt, password_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("admin-user", "管理员", 0, now, ADMIN_BOOTSTRAP_EMAIL, salt, password_hash),
                )
        elif not admin_row["password_salt"] or not admin_row["password_hash"]:
            salt, password_hash = hash_password(ADMIN_BOOTSTRAP_PASSWORD)
            conn.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, password_hash, admin_row["id"]),
            )
        if ADMIN_INITIAL_BALANCE_CENTS > 0:
            conn.execute(
                "UPDATE users SET balance_cents = max(balance_cents, ?) WHERE email = ?",
                (ADMIN_INITIAL_BALANCE_CENTS, ADMIN_BOOTSTRAP_EMAIL),
            )


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 150_000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    _, digest = hash_password(password, salt)
    return secrets.compare_digest(digest, password_hash)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def create_session(user_id: str) -> str:
    if STATELESS_SESSIONS:
        return create_stateless_session(user_id)
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, user_id, now, now + SESSION_TTL_SECONDS),
        )
    return session_id


def destroy_session(session_id: str | None) -> None:
    if not session_id:
        return
    if STATELESS_SESSIONS:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def session_secret() -> str:
    return PAYMENT_WEBHOOK_SECRET or ADMIN_BOOTSTRAP_PASSWORD or "daisy-session-secret"


def create_stateless_session(user_id: str) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    body = f"{user_id}:{expires_at}"
    signature = hmac.new(session_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v1:{body}:{signature}"


def verify_stateless_session(token: str) -> str | None:
    parts = token.split(":")
    if len(parts) != 4 or parts[0] != "v1":
        return None
    _, user_id, expires_at_raw, signature = parts
    try:
        expires_at = int(expires_at_raw)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    body = f"{user_id}:{expires_at}"
    expected = hmac.new(session_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    with db() as conn:
        exists = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_id if exists else None


def supabase_admin_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


def supabase_admin_find_user_by_email(email: str) -> dict | None:
    if not SUPABASE_ADMIN_ENABLED:
        return None
    lookup_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/admin/users?page=1&per_page=1000"
    payload = http_json("GET", lookup_url, headers=supabase_admin_headers(), timeout=20)
    users = payload.get("users", []) if isinstance(payload, dict) else payload
    if not isinstance(users, list):
        return None
    email = email.strip().lower()
    for item in users:
        if isinstance(item, dict) and str(item.get("email") or "").strip().lower() == email:
            return item
    return None


def supabase_admin_update_confirmed_user(user_id: str, password: str) -> dict:
    update_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/admin/users/{urllib.parse.quote(user_id, safe='')}"
    payload = {"password": password, "email_confirm": True}
    return http_json("PUT", update_url, payload, headers=supabase_admin_headers(), timeout=20) or {"id": user_id}


def supabase_admin_upsert_confirmed_user(email: str, password: str) -> dict | None:
    if not SUPABASE_ADMIN_ENABLED:
        return None
    existing = supabase_admin_find_user_by_email(email)
    if existing and existing.get("id"):
        updated = supabase_admin_update_confirmed_user(str(existing["id"]), password)
        return updated if isinstance(updated, dict) and updated.get("id") else existing

    create_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/admin/users"
    create_payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"display_name": email.split("@", 1)[0]},
    }
    try:
        created = http_json("POST", create_url, create_payload, headers=supabase_admin_headers(), timeout=20)
    except RuntimeError:
        existing = supabase_admin_find_user_by_email(email)
        if existing and existing.get("id"):
            updated = supabase_admin_update_confirmed_user(str(existing["id"]), password)
            return updated if isinstance(updated, dict) and updated.get("id") else existing
        raise
    return created if isinstance(created, dict) else None


def register_user(
    email: str,
    password: str,
    accept_terms: bool = False,
    require_supabase_admin: bool = False,
) -> dict:
    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise ValueError("请输入有效邮箱")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    if not accept_terms:
        raise ValueError("请先阅读并同意用户协议和隐私政策")
    if require_supabase_admin and not SUPABASE_ADMIN_ENABLED:
        raise ValueError("Supabase no-email signup is not configured. Set SUPABASE_SERVICE_ROLE_KEY on the backend.")
    salt, password_hash = hash_password(password)
    now = int(time.time())
    with db() as conn:
        if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("该邮箱已注册")

    supabase_user = None
    if SUPABASE_ADMIN_ENABLED:
        try:
            supabase_user = supabase_admin_upsert_confirmed_user(email, password)
        except RuntimeError as exc:
            if require_supabase_admin:
                raise ValueError(f"Supabase no-email signup failed: {exc}") from exc
            supabase_user = None
    supabase_user_id = str(supabase_user.get("id")) if isinstance(supabase_user, dict) and supabase_user.get("id") else None
    user_id = local_supabase_user_id(supabase_user_id) if supabase_user_id else uuid4().hex
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO users (
                  id, display_name, balance_cents, created_at, email,
                  password_salt, password_hash, terms_accepted_at,
                  supabase_user_id, login_provider
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    email.split("@", 1)[0],
                    REGISTRATION_BONUS_CENTS,
                    now,
                    email,
                    salt,
                    password_hash,
                    now,
                    supabase_user_id,
                    "supabase" if supabase_user_id else "local",
                ),
            )
            if REGISTRATION_BONUS_CENTS:
                conn.execute(
                    "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "registration_bonus", REGISTRATION_BONUS_CENTS, REGISTRATION_BONUS_CENTS, None, now),
                )
    except sqlite3.IntegrityError as exc:
        raise ValueError("该邮箱已注册") from exc
    return user_snapshot(user_id, authenticated=True)


def login_failure_count(email: str, ip: str) -> int:
    if LOGIN_MAX_FAILED_ATTEMPTS <= 0 or LOGIN_LOCK_SECONDS <= 0:
        return 0
    since = int(time.time()) - LOGIN_LOCK_SECONDS
    with db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE created_at < ?", (since - 60,))
        return conn.execute(
            """
            SELECT COUNT(*)
            FROM login_attempts
            WHERE success = 0 AND created_at >= ? AND (email = ? OR ip = ?)
            """,
            (since, email, ip),
        ).fetchone()[0]


def log_login_attempt(email: str, ip: str, success: bool) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO login_attempts (email, ip, success, created_at) VALUES (?, ?, ?, ?)",
            (email, ip, 1 if success else 0, int(time.time())),
        )


def login_user(email: str, password: str, require_supabase_admin: bool = False) -> dict:
    email = email.strip().lower()
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not row["password_salt"] or not row["password_hash"]:
        raise ValueError("邮箱或密码错误")
    if not verify_password(password, row["password_salt"], row["password_hash"]):
        raise ValueError("邮箱或密码错误")
    if require_supabase_admin:
        if not SUPABASE_ADMIN_ENABLED:
            raise ValueError("Supabase no-email login bootstrap is not configured. Set SUPABASE_SERVICE_ROLE_KEY on the backend.")
        try:
            supabase_user = supabase_admin_upsert_confirmed_user(email, password)
        except RuntimeError as exc:
            raise ValueError(f"Supabase no-email login bootstrap failed: {exc}") from exc
        supabase_user_id = str(supabase_user.get("id")) if isinstance(supabase_user, dict) and supabase_user.get("id") else None
        if supabase_user_id:
            with db() as conn:
                conn.execute(
                    "UPDATE users SET supabase_user_id = ?, login_provider = 'supabase' WHERE id = ?",
                    (supabase_user_id, row["id"]),
                )
    return user_snapshot(row["id"], authenticated=True)


def safe_redirect_path(value: str | None) -> str:
    value = (value or "/").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value[:300]


def request_base_url(headers) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    proto = headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
    host = headers.get("X-Forwarded-Host", headers.get("Host", f"localhost:{os.getenv('PORT', '9910')}")).split(",", 1)[0].strip()
    return f"{proto}://{host}"


def wechat_callback_url(headers) -> str:
    return WECHAT_REDIRECT_URI or f"{request_base_url(headers)}/api/wechat/callback"


def create_oauth_state(provider: str, redirect_path: str) -> str:
    now = int(time.time())
    state = secrets.token_urlsafe(24)
    with db() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
        conn.execute(
            """
            INSERT INTO oauth_states (state, provider, redirect_path, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (state, provider, safe_redirect_path(redirect_path), now, now + 10 * 60),
        )
    return state


def consume_oauth_state(provider: str, state: str) -> str:
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT redirect_path, expires_at FROM oauth_states WHERE state = ? AND provider = ?",
            (state, provider),
        ).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ? OR expires_at < ?", (state, now))
    if not row or row["expires_at"] < now:
        raise ValueError("微信登录状态已失效，请重新发起登录")
    return safe_redirect_path(row["redirect_path"])


def wechat_login_url(headers, redirect_path: str = "/") -> dict:
    if not WECHAT_LOGIN_ENABLED:
        raise ValueError("微信登录未配置，请先设置 WECHAT_APP_ID 和 WECHAT_APP_SECRET")
    state = create_oauth_state("wechat", redirect_path)
    callback = wechat_callback_url(headers)
    query = urllib.parse.urlencode(
        {
            "appid": WECHAT_APP_ID,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": WECHAT_OAUTH_SCOPE,
            "state": state,
        }
    )
    return {
        "enabled": True,
        "auth_url": f"https://open.weixin.qq.com/connect/qrconnect?{query}#wechat_redirect",
        "redirect_uri": callback,
        "state_expires_in": 600,
    }


def wechat_exchange_code(code: str) -> dict:
    query = urllib.parse.urlencode(
        {
            "appid": WECHAT_APP_ID,
            "secret": WECHAT_APP_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        }
    )
    payload = http_json("GET", f"https://api.weixin.qq.com/sns/oauth2/access_token?{query}", timeout=30)
    if not payload or payload.get("errcode"):
        raise ValueError(f"微信授权失败：{payload.get('errmsg', 'unknown error') if payload else 'empty response'}")
    return payload


def wechat_userinfo(access_token: str, openid: str) -> dict:
    query = urllib.parse.urlencode({"access_token": access_token, "openid": openid, "lang": "zh_CN"})
    try:
        payload = http_json("GET", f"https://api.weixin.qq.com/sns/userinfo?{query}", timeout=30)
    except Exception:
        return {}
    if not payload or payload.get("errcode"):
        return {}
    return payload


def login_wechat_user(oauth_payload: dict) -> dict:
    openid = str(oauth_payload.get("openid", "")).strip()
    unionid = str(oauth_payload.get("unionid", "")).strip() or None
    if not openid:
        raise ValueError("微信授权未返回 openid")
    profile = wechat_userinfo(str(oauth_payload.get("access_token", "")), openid)
    nickname = " ".join(str(profile.get("nickname") or "微信用户").strip().split())[:50] or "微信用户"
    avatar_url = str(profile.get("headimgurl") or "").strip()[:500] or None
    now = int(time.time())
    with db() as conn:
        row = None
        if unionid:
            row = conn.execute("SELECT * FROM users WHERE wechat_unionid = ?", (unionid,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM users WHERE wechat_openid = ?", (openid,)).fetchone()
        if row:
            user_id = row["id"]
            conn.execute(
                """
                UPDATE users
                SET display_name = COALESCE(NULLIF(?, ''), display_name),
                    wechat_openid = ?,
                    wechat_unionid = COALESCE(?, wechat_unionid),
                    avatar_url = COALESCE(?, avatar_url),
                    login_provider = 'wechat'
                WHERE id = ?
                """,
                (nickname, openid, unionid, avatar_url, user_id),
            )
        else:
            user_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO users (
                  id, display_name, balance_cents, created_at, email,
                  wechat_openid, wechat_unionid, avatar_url, login_provider,
                  terms_accepted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, nickname, REGISTRATION_BONUS_CENTS, now, None, openid, unionid, avatar_url, "wechat", now),
            )
            if REGISTRATION_BONUS_CENTS:
                conn.execute(
                    "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "registration_bonus", REGISTRATION_BONUS_CENTS, REGISTRATION_BONUS_CENTS, None, now),
                )
    return user_snapshot(user_id, authenticated=True)


def change_password(user_id: str, current_password: str, new_password: str, keep_session_id: str | None = None) -> dict:
    if len(new_password) < 8:
        raise ValueError("新密码至少 8 位")
    if current_password == new_password:
        raise ValueError("新密码不能和旧密码相同")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not row["password_salt"] or not row["password_hash"]:
            raise ValueError("账号未设置密码")
        if not verify_password(current_password, row["password_salt"], row["password_hash"]):
            raise ValueError("当前密码错误")
        salt, password_hash = hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (salt, password_hash, user_id),
        )
        if keep_session_id:
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND id != ?", (user_id, keep_session_id))
        else:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return user_snapshot(user_id, authenticated=True)


def local_supabase_user_id(supabase_user_id: str) -> str:
    digest = hashlib.sha256(supabase_user_id.encode("utf-8")).hexdigest()[:24]
    return f"sb-{digest}"


def fetch_supabase_profile(access_token: str) -> dict | None:
    if not SUPABASE_AUTH_ENABLED or not access_token:
        return None
    try:
        profile = http_json(
            "GET",
            f"{SUPABASE_URL.rstrip('/')}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=12,
        )
    except Exception:
        return None
    if not isinstance(profile, dict) or not profile.get("id"):
        return None
    email = str(profile.get("email") or "").strip().lower()
    if "@" not in email:
        return None
    return {"id": str(profile["id"]), "email": email}


def ensure_supabase_user(profile: dict) -> str:
    supabase_id = str(profile["id"])
    email = str(profile["email"]).strip().lower()
    local_id = local_supabase_user_id(supabase_id)
    now = int(time.time())
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE supabase_user_id = ?", (supabase_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET email = ?, login_provider = 'supabase' WHERE id = ?",
                (email, row["id"]),
            )
            return row["id"]

        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET supabase_user_id = ?, login_provider = 'supabase' WHERE id = ?",
                (supabase_id, row["id"]),
            )
            return row["id"]

        conn.execute(
            """
            INSERT INTO users
              (id, display_name, balance_cents, created_at, email, supabase_user_id, login_provider)
            VALUES (?, ?, ?, ?, ?, ?, 'supabase')
            """,
            (local_id, email.split("@", 1)[0], REGISTRATION_BONUS_CENTS, now, email, supabase_id),
        )
        if REGISTRATION_BONUS_CENTS:
            conn.execute(
                "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (local_id, "registration_bonus", REGISTRATION_BONUS_CENTS, REGISTRATION_BONUS_CENTS, None, now),
            )
    return local_id


def user_snapshot(user_id: str = DEFAULT_USER_ID, authenticated: bool = False) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        return {
            "user_id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "balance_cents": row["balance_cents"],
            "balance": row["balance_cents"] / 100,
            "authenticated": authenticated,
            "is_demo": row["id"] == DEFAULT_USER_ID,
            "is_admin": bool(row["email"] and row["email"].strip().lower() in ADMIN_EMAILS),
        }


def guest_snapshot() -> dict:
    return {
        "user_id": None,
        "email": "",
        "display_name": "访客",
        "balance_cents": 0,
        "balance": 0,
        "authenticated": False,
        "is_demo": False,
        "is_admin": False,
    }


def is_admin_user(user_id: str) -> bool:
    with db() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["email"]:
        return False
    return row["email"].strip().lower() in ADMIN_EMAILS


def list_api_keys(user_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, name, key_prefix, status, created_at, last_used_at
            FROM api_keys
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_api_key(user_id: str, name: str) -> dict:
    name = " ".join(str(name or "默认密钥").strip().split())[:40] or "默认密钥"
    api_key = "daisy_live_" + secrets.token_urlsafe(32)
    now = int(time.time())
    key_id = uuid4().hex[:12].upper()
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        active_count = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()[0]
        if active_count >= 5:
            raise ValueError("每个账号最多保留 5 个有效 API Key")
        conn.execute(
            """
            INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, status, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key_id, user_id, name, hash_api_key(api_key), api_key[:18], "active", now, None),
        )
    return {
        "id": key_id,
        "name": name,
        "key": api_key,
        "key_prefix": api_key[:18],
        "status": "active",
        "created_at": now,
    }


def revoke_api_key(user_id: str, key_id: str) -> dict:
    key_id = str(key_id or "").strip()
    if not key_id:
        raise ValueError("缺少 API Key ID")
    with db() as conn:
        row = conn.execute(
            "SELECT id FROM api_keys WHERE id = ? AND user_id = ? AND status = 'active'",
            (key_id, user_id),
        ).fetchone()
        if not row:
            raise ValueError("API Key 不存在或已撤销")
        conn.execute("UPDATE api_keys SET status = ? WHERE id = ? AND user_id = ?", ("revoked", key_id, user_id))
    return {"ok": True, "id": key_id, "status": "revoked"}


def authenticate_api_key(api_key: str) -> dict | None:
    api_key = str(api_key or "").strip()
    if not api_key:
        return None
    digest = hash_api_key(api_key)
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            """
            SELECT api_keys.id, api_keys.user_id, api_keys.name, users.email
            FROM api_keys
            JOIN users ON users.id = api_keys.user_id
            WHERE api_keys.key_hash = ? AND api_keys.status = 'active'
            """,
            (digest,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return dict(row)


def api_usage_count(api_key_id: str, window_seconds: int = 3600) -> int:
    since = int(time.time()) - window_seconds
    with db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE api_key_id = ? AND created_at >= ?",
            (api_key_id, since),
        ).fetchone()[0]


def log_api_usage(
    api_key_id: str,
    user_id: str,
    status: str,
    chars: int = 0,
    amount_cents: int = 0,
    order_id: str | None = None,
    error: str | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO api_usage (api_key_id, user_id, order_id, status, chars, amount_cents, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (api_key_id, user_id, order_id, status, chars, amount_cents, (error or "")[:160] or None, int(time.time())),
        )


def api_payload_usage(payload: dict) -> tuple[int, int]:
    text = str(payload.get("text", "")).strip()
    service = str(payload.get("service", "aigc"))
    language = str(payload.get("language", "zh"))
    chars = len(text)
    try:
        amount_cents = service_amount_cents(service, chars, language) if chars else 0
    except ValueError:
        amount_cents = 0
    return chars, amount_cents


def service_amount_cents(service: str, chars: int, language: str = "zh") -> int:
    if service not in SERVICE_RATES:
        raise ValueError("该服务需要人工客服报价")
    chars = max(1, int(chars or 0))
    multiplier = LANGUAGE_RATE_MULTIPLIERS.get(language, LANGUAGE_RATE_MULTIPLIERS["zh"])
    return max(1, (chars * SERVICE_RATES[service] * multiplier + 999) // 1000)


def recharge(amount_cents: int, user_id: str = DEFAULT_USER_ID) -> dict:
    ensure_billing_storage_ready("充值")
    if amount_cents <= 0:
        raise ValueError("充值金额必须大于 0")
    with db() as conn:
        row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        balance_after = row["balance_cents"] + amount_cents
        conn.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (balance_after, user_id))
        conn.execute(
            "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "recharge", amount_cents, balance_after, uuid4().hex[:10].upper(), int(time.time())),
        )
    return user_snapshot(user_id, authenticated=user_id != DEFAULT_USER_ID)


def payment_signature(payment_id: str, amount_cents: int, provider_trade_no: str) -> str:
    message = f"{payment_id}:{amount_cents}:{provider_trade_no}:{PAYMENT_WEBHOOK_SECRET}"
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def create_payment(amount_cents: int, user_id: str = DEFAULT_USER_ID, provider: str = PAYMENT_PROVIDER) -> dict:
    ensure_billing_storage_ready("创建充值单")
    if amount_cents <= 0:
        raise ValueError("充值金额必须大于 0")
    if amount_cents > 500_000:
        raise ValueError("单笔充值金额过大")
    payment_id = uuid4().hex[:12].upper()
    now = int(time.time())
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        conn.execute(
            """
            INSERT INTO payments (payment_id, user_id, amount_cents, provider, status, provider_trade_no, created_at, paid_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (payment_id, user_id, amount_cents, provider, "pending", None, now, None),
        )
    response = {
        "payment_id": payment_id,
        "amount_cents": amount_cents,
        "amount": amount_cents / 100,
        "provider": provider,
        "status": "pending",
        "mock_payments_enabled": MOCK_PAYMENTS_ENABLED,
    }
    if provider == "mock" and MOCK_PAYMENTS_ENABLED:
        provider_trade_no = f"MOCK{payment_id}"
        response.update(
            {
                "mock_trade_no": provider_trade_no,
                "mock_signature": payment_signature(payment_id, amount_cents, provider_trade_no),
            }
        )
    elif provider == "manual_qr":
        response.update(
            {
                "payment_message": MANUAL_PAYMENT_INSTRUCTIONS,
                "payment_qr_url": MANUAL_PAYMENT_QR_URL,
                "payment_account": MANUAL_PAYMENT_ACCOUNT,
                "payment_reference": payment_id,
                "payment_confirm_mode": "admin_manual",
            }
        )
    else:
        response["payment_message"] = "支付单已创建，请接入微信/支付宝回调后确认到账。"
    return response


def confirm_payment(payment_id: str, amount_cents: int, provider_trade_no: str, signature: str) -> dict:
    ensure_billing_storage_ready("确认充值入账")
    expected = payment_signature(payment_id, amount_cents, provider_trade_no)
    if not secrets.compare_digest(expected, signature):
        raise ValueError("支付回调签名错误")
    now = int(time.time())
    with db() as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            raise ValueError("支付订单不存在")
        if payment["amount_cents"] != amount_cents:
            raise ValueError("支付金额不匹配")
        user_id = payment["user_id"]
        if payment["status"] == "paid":
            return {**user_snapshot(user_id, authenticated=user_id != DEFAULT_USER_ID), "payment_status": "paid"}
        if payment["status"] != "pending":
            raise ValueError("支付单不是待支付状态，不能确认入账")
        user = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ValueError("用户不存在")
        balance_after = user["balance_cents"] + amount_cents
        conn.execute(
            "UPDATE payments SET status = ?, provider_trade_no = ?, paid_at = ? WHERE payment_id = ?",
            ("paid", provider_trade_no, now, payment_id),
        )
        conn.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (balance_after, user_id))
        conn.execute(
            "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "payment", amount_cents, balance_after, payment_id, now),
        )
    return {**user_snapshot(user_id, authenticated=user_id != DEFAULT_USER_ID), "payment_status": "paid"}


def recent_payments(user_id: str = DEFAULT_USER_ID, limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT payment_id, amount_cents, provider, status, provider_trade_no,
                   user_trade_no, user_payment_note, user_claimed_amount_cents,
                   notified_at, cancel_note, canceled_at,
                   created_at, paid_at
            FROM payments
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [
        {
            **dict(row),
            "amount": row["amount_cents"] / 100,
            "user_claimed_amount": (row["user_claimed_amount_cents"] / 100) if row["user_claimed_amount_cents"] is not None else None,
            "amount_mismatch": row["user_claimed_amount_cents"] is not None and row["user_claimed_amount_cents"] != row["amount_cents"],
        }
        for row in rows
    ]


def submit_payment_notice(user_id: str, payment_id: str, user_trade_no: str, note: str, claimed_amount_cents: int | None = None) -> dict:
    payment_id = str(payment_id or "").strip().upper()
    user_trade_no = " ".join(str(user_trade_no or "").strip().split())[:80]
    note = " ".join(str(note or "").strip().split())[:160]
    if not payment_id:
        raise ValueError("缺少支付单号")
    if claimed_amount_cents is None:
        raise ValueError("请填写实际付款金额")
    if claimed_amount_cents <= 0:
        raise ValueError("实际付款金额必须大于 0")
    if claimed_amount_cents > 500_000:
        raise ValueError("实际付款金额过大，请联系客服处理")
    if len(user_trade_no) < 4 and len(note) < 2:
        raise ValueError("请填写付款交易号或付款备注")

    now = int(time.time())
    with db() as conn:
        payment = conn.execute(
            "SELECT * FROM payments WHERE payment_id = ? AND user_id = ?",
            (payment_id, user_id),
        ).fetchone()
        if not payment:
            raise ValueError("支付单不存在")
        if payment["status"] != "pending":
            raise ValueError("只有待支付订单可以提交付款信息")
        if payment["provider"] == "mock":
            raise ValueError("演示支付不需要提交付款信息")
        conn.execute(
            """
            UPDATE payments
            SET user_trade_no = ?, user_payment_note = ?, user_claimed_amount_cents = ?, notified_at = ?
            WHERE payment_id = ? AND user_id = ?
            """,
            (user_trade_no or None, note or None, claimed_amount_cents, now, payment_id, user_id),
        )
    return {
        "payment_id": payment_id,
        "status": "pending",
        "review_status": "submitted",
        "user_trade_no": user_trade_no,
        "user_payment_note": note,
        "user_claimed_amount_cents": claimed_amount_cents,
        "user_claimed_amount": claimed_amount_cents / 100,
        "notified_at": now,
    }


def clean_extracted_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def w_tag(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def w_attr(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def split_text_paragraphs(text: str) -> list[str]:
    normalized = str(text or "").replace("\r", "\n")
    return [part.strip() for part in re.split(r"\n+", normalized) if part.strip()]


def docx_run(text: str, red: bool = False, bold: bool = False) -> str:
    props = []
    if red:
        props.append('<w:color w:val="FF0000"/>')
    if bold:
        props.append('<w:b/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>'


def docx_from_paragraphs(paragraphs: list[dict] | list[tuple] | list[str]) -> bytes:
    body = []
    for item in paragraphs:
        if isinstance(item, dict):
            paragraph = str(item.get("text", ""))
            red = bool(item.get("red"))
            bold = bool(item.get("bold"))
        elif isinstance(item, tuple):
            paragraph = str(item[0] if item else "")
            red = bool(item[1]) if len(item) > 1 else False
            bold = bool(item[2]) if len(item) > 2 else False
        else:
            paragraph = str(item)
            red = False
            bold = False
        if paragraph:
            runs = []
            for idx, piece in enumerate(paragraph.split("\n")):
                if idx:
                    runs.append("<w:r><w:br/></w:r>")
                runs.append(docx_run(piece, red=red, bold=bold))
            body.append(f"<w:p>{''.join(runs)}</w:p>")
        else:
            body.append("<w:p/>")
    if not body:
        body.append("<w:p/>")
    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(body)}
    <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
  </w:body>
</w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def docx_from_text(text: str, red: bool = False) -> bytes:
    return docx_from_paragraphs([{"text": paragraph, "red": red} for paragraph in split_text_paragraphs(text)])


def extract_txt(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def run_is_marked(rpr) -> bool:
    if rpr is None:
        return False
    color = rpr.find(w_tag("color"))
    if color is not None:
        value = (color.get(w_attr("val")) or "").strip().lower()
        if value and value not in {"auto", "000000", "ffffff"}:
            return True
    highlight = rpr.find(w_tag("highlight"))
    if highlight is not None:
        value = (highlight.get(w_attr("val")) or "").strip().lower()
        if value and value != "none":
            return True
    shading = rpr.find(w_tag("shd"))
    if shading is not None:
        fill = (shading.get(w_attr("fill")) or "").strip().lower()
        if fill and fill not in {"auto", "ffffff", "000000"}:
            return True
    return False


def docx_paragraphs(data: bytes, marked_only: bool = False) -> list[dict]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs = []
    for para in root.findall(f".//{{{W_NS}}}p"):
        chunks = []
        marked = False
        for run in para.findall(f".//{{{W_NS}}}r"):
            rpr = run.find(w_tag("rPr"))
            marked = marked or run_is_marked(rpr)
            for node in run:
                if node.tag == w_tag("t"):
                    chunks.append(node.text or "")
                elif node.tag == w_tag("tab"):
                    chunks.append("\t")
                elif node.tag in {w_tag("br"), w_tag("cr")}:
                    chunks.append("\n")
        paragraph = "".join(chunks).strip()
        if paragraph and (not marked_only or marked):
            paragraphs.append({"text": paragraph, "marked": marked})
    return paragraphs


def paragraph_text_and_marked(para) -> tuple[str, bool]:
    chunks = []
    marked = False
    for run in para.findall(f".//{{{W_NS}}}r"):
        rpr = run.find(w_tag("rPr"))
        marked = marked or run_is_marked(rpr)
        for node in run:
            if node.tag == w_tag("t"):
                chunks.append(node.text or "")
            elif node.tag == w_tag("tab"):
                chunks.append("\t")
            elif node.tag in {w_tag("br"), w_tag("cr")}:
                chunks.append("\n")
    return "".join(chunks).strip(), marked


def docx_paragraph_entries(data: bytes, marked_only: bool = False) -> list[dict]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    entries = []
    text_index = 0
    for para_index, para in enumerate(root.findall(f".//{{{W_NS}}}p")):
        paragraph, marked = paragraph_text_and_marked(para)
        if paragraph:
            if not marked_only or marked:
                entries.append({"para_index": para_index, "text_index": text_index, "text": paragraph, "marked": marked})
            text_index += 1
    return entries


def apply_red_to_rpr(rpr):
    if rpr is None:
        rpr = ET.Element(w_tag("rPr"))
    for color in list(rpr.findall(w_tag("color"))):
        rpr.remove(color)
    color = ET.Element(w_tag("color"))
    color.set(w_attr("val"), "FF0000")
    rpr.append(color)
    return rpr


def red_replacement_run(text: str, template_rpr=None):
    run = ET.Element(w_tag("r"))
    rpr = apply_red_to_rpr(copy.deepcopy(template_rpr) if template_rpr is not None else None)
    run.append(rpr)
    for idx, piece in enumerate(str(text or "").split("\n")):
        if idx:
            run.append(ET.Element(w_tag("br")))
        t = ET.Element(w_tag("t"))
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = piece
        run.append(t)
    return run


def replace_docx_paragraph_text(data: bytes, replacements: dict[int, str]) -> bytes:
    if not replacements:
        return data
    ET.register_namespace("w", W_NS)
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source:
        document_xml = source.read("word/document.xml")
        root = ET.fromstring(document_xml)
        text_index = 0
        for para in root.findall(f".//{{{W_NS}}}p"):
            paragraph, _marked = paragraph_text_and_marked(para)
            if paragraph:
                if text_index in replacements:
                    first_run = para.find(w_tag("r"))
                    template_rpr = first_run.find(w_tag("rPr")) if first_run is not None else None
                    preserved = [child for child in list(para) if child.tag == w_tag("pPr")]
                    for child in list(para):
                        para.remove(child)
                    for child in preserved:
                        para.append(child)
                    para.append(red_replacement_run(replacements[text_index], template_rpr))
                text_index += 1
        new_document = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = new_document if item.filename == "word/document.xml" else source.read(item.filename)
                target.writestr(item, content)
    return buffer.getvalue()


def extract_docx(data: bytes) -> str:
    return "\n".join(item["text"] for item in docx_paragraphs(data))


def extract_pdf(data: bytes) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_file(filename: str, data: bytes) -> dict:
    if not filename:
        raise ValueError("没有收到文件名")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件过大，当前限制 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        text = extract_txt(data)
    elif suffix == ".docx":
        text = extract_docx(data)
    elif suffix == ".pdf":
        text = extract_pdf(data)
    else:
        raise ValueError("暂只支持 TXT、DOCX、PDF 文件")
    text = clean_extracted_text(text)
    if not text:
        raise ValueError("没有从文件中提取到可处理文本")
    truncated = len(text) > MAX_EXTRACTED_CHARS
    if truncated:
        text = text[:MAX_EXTRACTED_CHARS]
    return {
        "filename": filename,
        "chars": len(text),
        "truncated": truncated,
        "limit": MAX_EXTRACTED_CHARS,
        "text": text,
    }


def inspect_file(filename: str, data: bytes) -> dict:
    if not filename:
        raise ValueError("没有收到文件名")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件过大，当前限制 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    paragraphs = document_paragraphs_for(filename, data)
    chars = sum(len(paragraph) for paragraph in paragraphs)
    if chars <= 0:
        raise ValueError("没有从文件中识别到可处理文字")
    return {
        "filename": filename,
        "chars": chars,
        "paragraphs": len(paragraphs),
        "text": "",
        "truncated": False,
        "limit": None,
    }


def decode_upload_base64(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        return b""
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise ValueError("上传文件内容解析失败，请重新选择文件") from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件过大，当前限制 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    return data


def normalize_for_match(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "").lower(), flags=re.UNICODE)


def paragraph_matches_target(paragraph: str, target: str) -> bool:
    para = normalize_for_match(paragraph)
    tgt = normalize_for_match(target)
    if len(para) < 12 or len(tgt) < 12:
        return False
    if tgt in para or para in tgt:
        return True
    if len(tgt) > 240:
        tgt = tgt[:240]
    return difflib.SequenceMatcher(None, para[:300], tgt).ratio() >= 0.58


def report_target_texts(report_filename: str | None, report_data: bytes, report_text: str) -> list[str]:
    targets: list[str] = []
    if report_data and str(report_filename or "").lower().endswith(".docx"):
        try:
            targets.extend(item["text"] for item in docx_paragraphs(report_data, marked_only=True))
        except Exception:
            pass
    keyword_pattern = re.compile(r"(AIGC|AI|疑似|风险|机器|生成|检测|总体|占比|率|%|红色|橙色|黄色)", re.I)
    for paragraph in split_text_paragraphs(report_text):
        if keyword_pattern.search(paragraph):
            targets.append(paragraph)
    unique = []
    seen = set()
    for target in targets:
        cleaned = " ".join(str(target).split())
        key = normalize_for_match(cleaned)[:160]
        if len(key) >= 8 and key not in seen:
            seen.add(key)
            unique.append(cleaned)
    return unique[:30]


def build_report_plan(source_text: str, source_filename: str | None, source_data: bytes, report_filename: str | None, report_data: bytes, report_text: str) -> dict | None:
    if not source_text:
        return None
    paragraphs = []
    if source_data and str(source_filename or "").lower().endswith(".docx"):
        try:
            paragraphs = [item["text"] for item in docx_paragraphs(source_data)]
        except Exception:
            paragraphs = []
    if not paragraphs:
        paragraphs = split_text_paragraphs(source_text)
    if not paragraphs:
        return None
    targets = report_target_texts(report_filename, report_data, report_text)
    indices = []
    for idx, paragraph in enumerate(paragraphs):
        if any(paragraph_matches_target(paragraph, target) for target in targets):
            indices.append(idx)
    if not indices:
        indices = [idx for idx, paragraph in enumerate(paragraphs) if paragraph.strip()]
    target_text = "\n\n".join(paragraphs[idx] for idx in indices if paragraphs[idx].strip())
    return {"paragraphs": paragraphs, "indices": indices, "target_text": target_text, "target_source": "report" if targets else "full"}


def apply_report_result(plan: dict | None, rewritten_text: str) -> list[dict]:
    if not plan:
        return [{"text": paragraph, "red": True} for paragraph in split_text_paragraphs(rewritten_text)]
    paragraphs = list(plan.get("paragraphs") or [])
    indices = list(plan.get("indices") or [])
    rewritten_paragraphs = split_text_paragraphs(rewritten_text)
    index_set = set(indices)
    output = []
    if rewritten_paragraphs and len(rewritten_paragraphs) == len(indices):
        replacements = dict(zip(indices, rewritten_paragraphs))
        for idx, paragraph in enumerate(paragraphs):
            output.append({"text": replacements[idx], "red": True} if idx in replacements else {"text": paragraph, "red": False})
        return output
    first = indices[0] if indices else None
    merged = "\n".join(rewritten_paragraphs) if rewritten_paragraphs else rewritten_text
    for idx, paragraph in enumerate(paragraphs):
        if idx == first:
            output.append({"text": merged, "red": True})
        elif idx in index_set:
            continue
        else:
            output.append({"text": paragraph, "red": False})
    return output


def result_docx_base64_for(input_type: str, result: str, plan: dict | None = None) -> str:
    if input_type == "report" and plan:
        content = docx_from_paragraphs(apply_report_result(plan, result))
    elif input_type in {"file", "report"}:
        content = docx_from_text(result, red=True)
    else:
        return ""
    return base64.b64encode(content).decode("ascii")

def demo_rewrite(text: str, service: str) -> str:
    replacements = [
        ("随着", "伴随"),
        ("快速发展", "持续演进"),
        ("明显变化", "新的变化"),
        ("围绕", "以"),
        ("展开研究", "进行分析"),
        ("试图", "希望"),
        ("较为完整", "相对完整"),
        ("分析框架", "研究框架"),
        ("本文", "本研究"),
    ]
    result = text.strip()
    for old, new in replacements:
        result = result.replace(old, new)
    if service == "repeat":
        result = result.replace("进行分析", "展开讨论").replace("研究框架", "分析路径")
    if not result.endswith(("。", ".", "！", "!", "？", "?")):
        result += "。"
    return result + "\n\n[演示模式] 未检测到可用的商业 API 或上游服务时返回此预览结果。"


def bypass_headers() -> dict:
    login = http_json(
        "POST",
        f"{BYPASS_BASE_URL}/admin/login",
        {"username": BYPASS_ADMIN_USERNAME, "password": BYPASS_ADMIN_PASSWORD},
        timeout=30,
    )
    return {"Authorization": f"Bearer {login['access_token']}"}


def bypass_create_card(headers: dict) -> str:
    card_key = f"{BYPASS_CARD_PREFIX}-{int(time.time() * 1000)}"
    http_json("POST", f"{BYPASS_BASE_URL}/admin/card-keys", {"card_key": card_key, "usage_limit": 999}, headers=headers)
    return card_key


def bypass_optimize(text: str) -> str:
    headers = bypass_headers()
    card_key = bypass_create_card(headers)
    quoted = urllib.parse.quote(card_key)
    session = http_json(
        "POST",
        f"{BYPASS_BASE_URL}/optimization/start?card_key={quoted}",
        {"original_text": text, "processing_mode": "paper_enhance"},
        timeout=90,
    )
    session_id = session["session_id"]
    status = None
    for _ in range(240):
        time.sleep(1)
        status = http_json("GET", f"{BYPASS_BASE_URL}/optimization/sessions/{session_id}/progress?card_key={quoted}", timeout=20)
        if status["status"] in {"completed", "failed", "stopped"}:
            break
    if not status or status["status"] != "completed":
        raise RuntimeError(f"BypassAIGC session ended with {status}")
    exported = http_json(
        "POST",
        f"{BYPASS_BASE_URL}/optimization/sessions/{session_id}/export?card_key={quoted}",
        {"session_id": session_id, "acknowledge_academic_integrity": True, "export_format": "txt"},
        timeout=60,
    )
    return " ".join(exported["content"].strip().split())


def bypass_aigc_optimize(text: str, platform: str = "general", language: str = "zh", report_text: str = "") -> str:
    return bypass_optimize(text)


def has_model_api_key() -> bool:
    key = OPENAI_API_KEY.strip()
    return bool(key and key not in {"sk-your-key", "replace-with-model-api-key", "your-api-key-here"})


def output_looks_broken(source: str, output: str) -> bool:
    text = str(output or "").strip()
    if not text:
        return True
    question_ratio = text.count("?") / max(1, len(text))
    source_has_cjk = any("\u4e00" <= char <= "\u9fff" for char in source)
    output_has_cjk = any("\u4e00" <= char <= "\u9fff" for char in text)
    return question_ratio > 0.2 or (source_has_cjk and not output_has_cjk)


def local_aigc_rewrite(text: str, service: str = "aigc") -> str:
    replacements = [
        ("随着", "伴随"),
        ("人工智能技术", "智能技术"),
        ("广泛使用", "被越来越多地采用"),
        ("旨在", "主要用于"),
        ("测试", "检验"),
        ("系统", "平台"),
        ("能够", "可以"),
        ("进行", "完成"),
        ("自然化改写", "更自然的表达调整"),
        ("降低", "减少"),
        ("机器生成痕迹", "机械化表达"),
        ("同时", "并且"),
        ("保持", "保留"),
        ("原意", "核心意思"),
        ("清晰", "明确"),
        ("通顺", "顺畅"),
        ("逻辑完整", "逻辑连贯"),
        ("本文", "这段内容"),
        ("工具", "系统"),
        ("商业文案", "商业文本"),
        ("学术写作", "论文写作"),
    ]
    result = " ".join(text.strip().split())
    for old, new in replacements:
        result = result.replace(old, new)

    sentences = [part.strip() for part in result.replace("；", "。").replace(";", "。").split("。") if part.strip()]
    if len(sentences) > 1:
        lead = sentences[0]
        rest = sentences[1:]
        result = "。".join(rest + [lead])
    else:
        result = sentences[0] if sentences else result

    result = result.replace("被被", "被").replace("，并且", "，同时也").replace("，可以", "，能够")
    if service == "combo":
        result = result.replace("被越来越多地采用", "逐渐进入更多使用场景").replace("完成", "实现")
    if result and result[-1] not in "。.!！？?":
        result += "。"
    return result


def cloud_aigc_optimize(text: str, platform: str = "general", language: str = "zh", report_text: str = "") -> str:
    if not has_model_api_key():
        return local_aigc_rewrite(text, "aigc")

    platform_label = PLATFORM_NAMES.get(platform, platform)
    report_hint = f"\nDetection report excerpt:\n{report_text[:3000]}\n" if report_text else ""
    prompt = (
        "Rewrite the following text to sound more natural and human-written while preserving meaning, facts, citations, "
        "technical terms, and paragraph intent. Reduce formulaic AI phrasing, vary sentence structure, and avoid adding "
        "unsupported claims. Output only the rewritten text.\n"
        f"Language: {language}\nTarget detector/platform tendency: {platform_label}{report_hint}\n\nText:\n{text}"
    )
    payload = {
        "model": REWRITE_MODEL,
        "messages": [
            {"role": "system", "content": "You are a careful academic and business text rewriting assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
    }
    result = http_json(
        "POST",
        f"{OPENAI_BASE_URL}/v1/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        timeout=120,
    )
    return result["choices"][0]["message"]["content"].strip()


def api_rewrite(text: str, platform: str, language: str, report_text: str = "") -> str:
    if not has_model_api_key():
        raise RuntimeError("OPENAI_API_KEY is not configured")
    platform_label = PLATFORM_NAMES.get(platform, platform)
    report_hint = (
        "\n\n检测报告摘录:\n"
        f"{report_text[:3000]}\n\n请优先针对报告中标记的问题段落优化原文。"
        if report_text
        else ""
    )
    prompt = (
        "你是商业文稿优化服务的降重模块。请在不改变事实、术语和引用含义的前提下，"
        "重组句式、替换重复表达、压缩模板化连接词。不要承诺检测通过率，不要新增文献。"
        f"\n语言: {language}\n检测平台倾向: {platform_label}{report_hint}\n\n待处理文本:\n{text}"
    )
    payload = {
        "model": REWRITE_MODEL,
        "messages": [
            {"role": "system", "content": "你只输出改写后的正文，不输出解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    result = http_json(
        "POST",
        f"{OPENAI_BASE_URL}/v1/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        timeout=120,
    )
    return result["choices"][0]["message"]["content"].strip()


def rewrite_with_service(text: str, service: str, platform: str, language: str, report_text: str = "") -> tuple[str, str]:
    if service == "aigc":
        try:
            result = bypass_aigc_optimize(text, platform, language, report_text)
            engine = "BypassAIGC"
            if output_looks_broken(text, result):
                raise RuntimeError("BypassAIGC returned unreadable output")
            return result, engine
        except Exception:
            if not has_model_api_key():
                raise
            return api_rewrite(text, platform, language, report_text), f"{REWRITE_MODEL} (BypassAIGC fallback)"
    if service == "repeat":
        return api_rewrite(text, platform, language, report_text), REWRITE_MODEL
    if service == "combo":
        result = bypass_aigc_optimize(text, platform, language, report_text)
        if output_looks_broken(text, result):
            raise RuntimeError("BypassAIGC returned unreadable output")
        return result, "BypassAIGC"
    raise ValueError("该服务需要人工客服报价")


def optimize_paragraphs(
    paragraphs: list[str],
    service: str,
    platform: str,
    language: str,
    report_text: str = "",
    max_chars: int = 5200,
) -> tuple[list[str], str]:
    outputs: list[str] = []
    engines: list[str] = []
    batch: list[str] = []
    batch_chars = 0

    def flush_batch() -> None:
        nonlocal batch, batch_chars
        if not batch:
            return
        joined = "\n\n".join(batch)
        result, engine = rewrite_with_service(joined, service, platform, language, report_text)
        engines.append(engine)
        rewritten = split_text_paragraphs(result)
        if len(rewritten) == len(batch):
            outputs.extend(rewritten)
        elif len(batch) == 1:
            outputs.append(result.strip())
        else:
            # If the upstream merged/split paragraphs, keep the content in place
            # instead of dropping text. The first paragraph receives the merged result.
            outputs.append(result.strip())
            outputs.extend(batch[1:])
        batch = []
        batch_chars = 0

    for paragraph in paragraphs:
        paragraph = str(paragraph or "").strip()
        if not paragraph:
            outputs.append("")
            continue
        if len(paragraph) > max_chars:
            flush_batch()
            pieces = [paragraph[idx : idx + max_chars] for idx in range(0, len(paragraph), max_chars)]
            rewritten_pieces: list[str] = []
            for piece in pieces:
                result, engine = rewrite_with_service(piece, service, platform, language, report_text)
                engines.append(engine)
                rewritten_pieces.append(result.strip())
            outputs.append("".join(rewritten_pieces))
            continue
        if batch and batch_chars + len(paragraph) + 2 > max_chars:
            flush_batch()
        batch.append(paragraph)
        batch_chars += len(paragraph) + 2
    flush_batch()
    unique_engines = []
    for engine in engines:
        if engine not in unique_engines:
            unique_engines.append(engine)
    return outputs, " + ".join(unique_engines) if unique_engines else "BypassAIGC"


def document_paragraphs_for(filename: str | None, data: bytes) -> list[str]:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix == ".docx":
        return [item["text"] for item in docx_paragraphs(data)]
    if suffix == ".pdf":
        return split_text_paragraphs(clean_extracted_text(extract_pdf(data)))
    if suffix == ".txt":
        return split_text_paragraphs(clean_extracted_text(extract_txt(data)))
    raise ValueError("暂只支持 TXT、DOCX、PDF 文件")


def build_document_docx(filename: str | None, data: bytes, replacements: dict[int, str], fallback_paragraphs: list[str]) -> bytes:
    if data and str(filename or "").lower().endswith(".docx"):
        return replace_docx_paragraph_text(data, replacements)
    output = []
    for idx, paragraph in enumerate(fallback_paragraphs):
        output.append({"text": replacements.get(idx, paragraph), "red": idx in replacements})
    return docx_from_paragraphs(output)


def optimize(payload: dict, user_id: str = DEFAULT_USER_ID) -> dict:
    ensure_billing_storage_ready("扣费处理")
    text = str(payload.get("text", "")).strip()
    report_text = str(payload.get("report_text", "")).strip()
    service = str(payload.get("service", "aigc"))
    platform = str(payload.get("platform", "general"))
    language = str(payload.get("language", "zh"))
    input_type = str(payload.get("input_type", "text")).strip() or "text"
    source_filename = str(payload.get("source_filename", "")).strip()[:180] or None
    report_filename = str(payload.get("report_filename", "")).strip()[:180] or None
    source_data = decode_upload_base64(str(payload.get("source_file_base64", "")))
    report_data = decode_upload_base64(str(payload.get("report_file_base64", "")))

    document_plan = None
    if input_type == "file":
        if not source_data or not source_filename:
            raise ValueError("请先上传需要处理的文件")
        document_paragraphs = document_paragraphs_for(source_filename, source_data)
        target_indices = [idx for idx, paragraph in enumerate(document_paragraphs) if paragraph.strip()]
        text = "\n\n".join(document_paragraphs)
        document_plan = {"paragraphs": document_paragraphs, "indices": target_indices}
    elif input_type == "report":
        if not source_data or not source_filename:
            raise ValueError("请先上传原文文件")
        if not report_data or not report_filename:
            raise ValueError("请先上传检测报告")
        document_paragraphs = document_paragraphs_for(source_filename, source_data)
        if report_data and report_filename and not report_text:
            report_text = "\n\n".join(document_paragraphs_for(report_filename, report_data))
        targets = report_target_texts(report_filename, report_data, report_text)
        target_indices = []
        for idx, paragraph in enumerate(document_paragraphs):
            if any(paragraph_matches_target(paragraph, target) for target in targets):
                target_indices.append(idx)
        if not target_indices:
            target_indices = [idx for idx, paragraph in enumerate(document_paragraphs) if paragraph.strip()]
        text = "\n\n".join(document_paragraphs)
        document_plan = {"paragraphs": document_paragraphs, "indices": target_indices}

    if not text:
        raise ValueError("请输入需要处理的文本")
    if input_type == "text" and len(text) > 6000:
        raise ValueError("单次最多处理 6000 字，请分批提交")

    report_plan = None
    work_text = text
    work_paragraphs = []
    if document_plan:
        work_paragraphs = [
            str(document_plan["paragraphs"][idx]).strip()
            for idx in document_plan["indices"]
            if str(document_plan["paragraphs"][idx]).strip()
        ]
        work_text = "\n\n".join(work_paragraphs)
    elif input_type == "report":
        report_plan = build_report_plan(text, source_filename, source_data, report_filename, report_data, report_text)
        if report_plan and report_plan.get("target_text"):
            work_text = str(report_plan["target_text"]).strip()

    original_chars = sum(len(paragraph) for paragraph in document_plan["paragraphs"]) if document_plan else len(text)
    billed_chars = sum(len(paragraph) for paragraph in work_paragraphs) if document_plan else (len(work_text.strip()) if work_text.strip() else original_chars)
    amount_cents = service_amount_cents(service, billed_chars, language)
    order_id = uuid4().hex[:10].upper()
    created_at = int(time.time())

    with db() as conn:
        row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if row["balance_cents"] < amount_cents:
            raise ValueError("余额不足，请先充值")
        conn.execute(
            """
            INSERT INTO orders (
              order_id, user_id, service, platform, language, input_type, source_filename, report_filename, chars, amount_cents,
              engine, status, error, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, user_id, service, platform, language, input_type, source_filename, report_filename, billed_chars, amount_cents, "pending", "processing", None, created_at, None),
        )

    try:
        if document_plan:
            rewritten_paragraphs, engine = optimize_paragraphs(work_paragraphs, service, platform, language, report_text)
            replacements = {}
            rewrite_cursor = 0
            for source_idx in document_plan["indices"]:
                original_paragraph = str(document_plan["paragraphs"][source_idx]).strip()
                if not original_paragraph:
                    continue
                if rewrite_cursor < len(rewritten_paragraphs):
                    replacements[source_idx] = rewritten_paragraphs[rewrite_cursor]
                rewrite_cursor += 1
            result_bytes = build_document_docx(source_filename, source_data, replacements, document_plan["paragraphs"])
            result_docx_base64 = base64.b64encode(result_bytes).decode("ascii")
            result = "\n\n".join(replacements[idx] for idx in document_plan["indices"] if idx in replacements)
        elif service == "aigc":
            try:
                result = bypass_aigc_optimize(work_text, platform, language, report_text)
                engine = "BypassAIGC"
                if output_looks_broken(work_text, result):
                    raise RuntimeError("BypassAIGC returned unreadable output")
            except Exception:
                if not has_model_api_key():
                    raise
                result = api_rewrite(work_text, platform, language, report_text)
                engine = f"{REWRITE_MODEL} (BypassAIGC fallback)"
        elif service == "repeat":
            result = api_rewrite(work_text, platform, language, report_text)
            engine = REWRITE_MODEL
        elif service == "combo":
            result = bypass_aigc_optimize(work_text, platform, language, report_text)
            engine = "BypassAIGC"
            if output_looks_broken(work_text, result):
                raise RuntimeError("BypassAIGC returned unreadable output")
        else:
            raise ValueError("该服务需要人工客服报价")
    except Exception as exc:
        message = str(exc)[:500] or "upstream failed"
        if not DEMO_FALLBACK:
            with db() as conn:
                conn.execute("UPDATE orders SET status = ?, error = ?, completed_at = ? WHERE order_id = ?", ("failed", message, int(time.time()), order_id))
            raise RuntimeError(f"BypassAIGC处理失败：{message}") from exc
        if document_plan:
            rewritten_paragraphs = [demo_rewrite(paragraph, service) for paragraph in work_paragraphs]
            replacements = {}
            rewrite_cursor = 0
            for source_idx in document_plan["indices"]:
                original_paragraph = str(document_plan["paragraphs"][source_idx]).strip()
                if not original_paragraph:
                    continue
                replacements[source_idx] = rewritten_paragraphs[rewrite_cursor]
                rewrite_cursor += 1
            result_bytes = build_document_docx(source_filename, source_data, replacements, document_plan["paragraphs"])
            result_docx_base64 = base64.b64encode(result_bytes).decode("ascii")
            result = "\n\n".join(replacements[idx] for idx in document_plan["indices"] if idx in replacements)
        else:
            result = demo_rewrite(work_text, service)
        engine = "demo-fallback"

    if not document_plan:
        result_docx_base64 = result_docx_base64_for(input_type, result, report_plan)
    display_result = result
    if input_type == "report" and report_plan:
        display_result = "\n\n".join(item["text"] for item in apply_report_result(report_plan, result) if item.get("text"))

    with db() as conn:
        row = conn.execute("SELECT balance_cents FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["balance_cents"] < amount_cents:
            conn.execute("UPDATE orders SET status = ?, error = ?, completed_at = ? WHERE order_id = ?", ("failed", "insufficient balance", int(time.time()), order_id))
            raise ValueError("余额不足，请先充值")
        balance_after = row["balance_cents"] - amount_cents
        conn.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (balance_after, user_id))
        conn.execute(
            """
            UPDATE orders
            SET engine = ?, status = ?, completed_at = ?, result_text = ?, result_docx_base64 = ?
            WHERE order_id = ?
            """,
            (engine, "completed", int(time.time()), display_result, result_docx_base64 or None, order_id),
        )
        conn.execute(
            "INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "debit", -amount_cents, balance_after, order_id, int(time.time())),
        )

    extension = "docx" if input_type in {"file", "report"} else "txt"
    order = {
        "order_id": order_id,
        "service": service,
        "platform": platform,
        "language": language,
        "chars": billed_chars,
        "original_chars": original_chars,
        "processed_chars": billed_chars,
        "amount_cents": amount_cents,
        "amount": amount_cents / 100,
        "engine": engine,
        "status": "completed",
        "created_at": created_at,
        "input_type": input_type,
        "source_filename": source_filename,
        "report_filename": report_filename,
        "download_url": f"/api/orders/{order_id}/download",
        "output_filename": f"daisy-{service}-{order_id}.{extension}",
        "balance": user_snapshot(user_id, authenticated=user_id != DEFAULT_USER_ID)["balance"],
    }
    return {**order, "result": display_result}

def recent_orders(user_id: str = DEFAULT_USER_ID, limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT order_id, service, platform, language, chars, amount_cents, engine, status, error, created_at, completed_at, result_text, result_docx_base64
            FROM orders
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    orders = []
    for row in rows:
        item = dict(row)
        item.pop("result_docx_base64", None)
        item.update(
            {
                "amount": row["amount_cents"] / 100,
                "has_result": bool(row["result_text"] or row["result_docx_base64"]),
                "result_preview": (row["result_text"] or "")[:90],
            }
        )
        orders.append(item)
    return orders

def order_detail(user_id: str, order_id: str) -> dict:
    with db() as conn:
        row = conn.execute(
            """
            SELECT order_id, service, platform, language, chars, amount_cents, engine, status, error, created_at, completed_at, result_text, result_docx_base64
            FROM orders
            WHERE user_id = ? AND order_id = ?
            """,
            (user_id, order_id),
        ).fetchone()
    if not row:
        raise ValueError("订单不存在")
    item = dict(row)
    item.pop("result_docx_base64", None)
    item.update(
        {
            "amount": row["amount_cents"] / 100,
            "has_result": bool(row["result_text"] or row["result_docx_base64"]),
            "result": row["result_text"] or "",
        }
    )
    return item

def order_result_download(user_id: str, order_id: str) -> tuple[str, bytes]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT order_id, service, input_type, source_filename, result_text, result_docx_base64
            FROM orders
            WHERE user_id = ? AND order_id = ?
            """,
            (user_id, order_id),
        ).fetchone()
    if not row:
        raise ValueError("订单不存在")
    if not row["result_text"] and not row["result_docx_base64"]:
        raise ValueError("订单暂无可下载结果")

    if row["input_type"] in {"file", "report"}:
        stem = Path(row["source_filename"] or f"daisy-{row['service']}-{row['order_id']}").stem
        filename = f"{stem}-降低AIGC结果.docx"
        if row["result_docx_base64"]:
            content = base64.b64decode(row["result_docx_base64"])
        else:
            content = docx_from_text(row["result_text"] or "", red=True)
    else:
        filename = f"daisy-{row['service']}-{row['order_id']}.txt"
        content = ("\ufeff" + (row["result_text"] or "")).encode("utf-8")
    return filename, content

def delete_order_result(user_id: str, order_id: str) -> dict:
    order_id = str(order_id or "").strip().upper()
    with db() as conn:
        row = conn.execute(
            """
            SELECT order_id, status, result_text, result_docx_base64
            FROM orders
            WHERE user_id = ? AND order_id = ?
            """,
            (user_id, order_id),
        ).fetchone()
        if not row:
            raise ValueError("订单不存在")
        if not row["result_text"] and not row["result_docx_base64"]:
            raise ValueError("订单暂无可删除结果")
        conn.execute(
            """
            UPDATE orders
            SET result_text = NULL, result_docx_base64 = NULL
            WHERE user_id = ? AND order_id = ?
            """,
            (user_id, order_id),
        )
    return {
        "ok": True,
        "order_id": order_id,
        "status": row["status"],
        "has_result": False,
    }

def cleanup_old_order_results(retention_days: int | None = None) -> dict:
    days = RESULT_RETENTION_DAYS if retention_days is None else int(retention_days)
    if days <= 0:
        return {"ok": True, "retention_days": days, "deleted": 0, "cutoff": None, "enabled": False}
    cutoff = int(time.time()) - days * 24 * 60 * 60
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET result_text = NULL, result_docx_base64 = NULL
            WHERE (result_text IS NOT NULL OR result_docx_base64 IS NOT NULL)
              AND COALESCE(completed_at, created_at) < ?
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
    return {
        "ok": True,
        "retention_days": days,
        "deleted": deleted,
        "cutoff": cutoff,
        "enabled": True,
    }

def log_admin_audit(
    admin_user_id: str,
    action: str,
    target_user_id: str | None = None,
    target_email: str | None = None,
    amount_cents: int | None = None,
    ref_id: str | None = None,
    note: str | None = None,
    ip: str | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO admin_audit
              (admin_user_id, action, target_user_id, target_email, amount_cents, ref_id, note, ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_user_id,
                action,
                target_user_id,
                target_email,
                amount_cents,
                ref_id,
                (note or "")[:200] or None,
                (ip or "")[:80] or None,
                int(time.time()),
            ),
        )


SUPPORT_CATEGORIES = {"payment", "order", "refund", "account", "other"}


def create_support_ticket(user_id: str, authenticated: bool, payload: dict) -> dict:
    email = str(payload.get("email", "")).strip().lower()
    category = str(payload.get("category", "other")).strip().lower()
    subject = " ".join(str(payload.get("subject", "")).strip().split())[:80]
    message = " ".join(str(payload.get("message", "")).strip().split())[:1000]
    ref_id = " ".join(str(payload.get("ref_id", "")).strip().split())[:80]

    if authenticated:
        with db() as conn:
            row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["email"]:
            email = row["email"]
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise ValueError("请输入有效联系邮箱")
    if category not in SUPPORT_CATEGORIES:
        category = "other"
    if len(subject) < 3:
        raise ValueError("请填写至少 3 个字的问题标题")
    if len(message) < 8:
        raise ValueError("请填写至少 8 个字的问题描述")

    ticket_id = f"TK-{uuid4().hex[:10].upper()}"
    now = int(time.time())
    owner_id = user_id if authenticated else None
    with db() as conn:
        conn.execute(
            """
            INSERT INTO support_tickets
              (ticket_id, user_id, email, category, subject, message, ref_id, status, admin_note, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticket_id, owner_id, email, category, subject, message, ref_id or None, "open", None, now, None),
        )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": "open",
        "created_at": now,
    }


def recent_support_tickets(user_id: str, authenticated: bool, limit: int = 20) -> list[dict]:
    if not authenticated:
        return []
    with db() as conn:
        rows = conn.execute(
            """
            SELECT ticket_id, email, category, subject, message, ref_id, status, admin_note, created_at, resolved_at
            FROM support_tickets
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def admin_update_ticket(admin_user_id: str, ticket_id: str, status: str, note: str, ip: str = "") -> dict:
    ticket_id = str(ticket_id or "").strip().upper()
    status = str(status or "").strip().lower()
    note = " ".join(str(note or "").strip().split())[:200]
    if not is_admin_user(admin_user_id):
        raise PermissionError("没有后台权限")
    if status not in {"open", "processing", "resolved", "closed"}:
        raise ValueError("不支持的工单状态")
    if status in {"resolved", "closed"} and len(note) < 2:
        raise ValueError("关闭工单时请填写处理备注")

    resolved_at = int(time.time()) if status in {"resolved", "closed"} else None
    with db() as conn:
        ticket = conn.execute("SELECT * FROM support_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
        if not ticket:
            raise ValueError("工单不存在")
        conn.execute(
            """
            UPDATE support_tickets
            SET status = ?, admin_note = ?, resolved_at = ?
            WHERE ticket_id = ?
            """,
            (status, note or None, resolved_at, ticket_id),
        )

    log_admin_audit(
        admin_user_id,
        "update_ticket",
        target_user_id=ticket["user_id"],
        target_email=ticket["email"],
        ref_id=ticket_id,
        note=f"{status}: {note}" if note else status,
        ip=ip,
    )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "status": status,
        "admin_note": note,
        "resolved_at": resolved_at,
    }


def admin_adjust_balance(admin_user_id: str, target_email: str, amount_cents: int, note: str, ip: str = "") -> dict:
    ensure_billing_storage_ready("后台余额调整")
    target_email = target_email.strip().lower()
    note = " ".join(note.strip().split())
    if not is_admin_user(admin_user_id):
        raise PermissionError("没有后台权限")
    if not target_email:
        raise ValueError("请输入用户邮箱")
    if amount_cents == 0:
        raise ValueError("调整金额不能为 0")
    if abs(amount_cents) > 500_000:
        raise ValueError("单次调整金额不能超过 ¥5000")
    if len(note) < 3:
        raise ValueError("请填写至少 3 个字的调整备注")
    if len(note) > 120:
        raise ValueError("备注最多 120 个字")

    now = int(time.time())
    ref_id = f"ADJ-{uuid4().hex[:10].upper()}"
    with db() as conn:
        user = conn.execute("SELECT id, email, balance_cents FROM users WHERE email = ?", (target_email,)).fetchone()
        if not user:
            raise ValueError("用户不存在")
        balance_after = user["balance_cents"] + amount_cents
        if balance_after < 0:
            raise ValueError("调整后余额不能为负数")
        conn.execute("UPDATE users SET balance_cents = ? WHERE id = ?", (balance_after, user["id"]))
        conn.execute(
            """
            INSERT INTO ledger (user_id, type, amount_cents, balance_after_cents, ref_id, created_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                "admin_adjustment",
                amount_cents,
                balance_after,
                ref_id,
                now,
                f"{note}；管理员:{admin_user_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO admin_audit
              (admin_user_id, action, target_user_id, target_email, amount_cents, ref_id, note, ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_user_id,
                "adjust_balance",
                user["id"],
                user["email"],
                amount_cents,
                ref_id,
                note,
                (ip or "")[:80] or None,
                now,
            ),
        )
    return {
        "ok": True,
        "ref_id": ref_id,
        "user_id": user["id"],
        "email": user["email"],
        "amount_cents": amount_cents,
        "amount": amount_cents / 100,
        "balance_after_cents": balance_after,
        "balance_after": balance_after / 100,
        "note": note,
    }


def admin_confirm_payment(admin_user_id: str, payment_id: str, provider_trade_no: str, note: str, ip: str = "") -> dict:
    payment_id = str(payment_id or "").strip().upper()
    provider_trade_no = " ".join(str(provider_trade_no or "").strip().split())[:80]
    note = " ".join(str(note or "").strip().split())
    if not is_admin_user(admin_user_id):
        raise PermissionError("没有后台权限")
    if not payment_id:
        raise ValueError("缺少支付单号")
    if len(provider_trade_no) < 4:
        raise ValueError("请填写真实收款交易号或核销凭证")
    if len(note) < 3:
        raise ValueError("请填写至少 3 个字的入账备注")
    if len(note) > 120:
        raise ValueError("备注最多 120 个字")

    with db() as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            raise ValueError("支付单不存在")
        if payment["status"] != "pending":
            raise ValueError("只有待支付订单可以人工入账")
        amount_cents = int(payment["amount_cents"])
        claimed_amount_cents = payment["user_claimed_amount_cents"]
        if claimed_amount_cents is not None and int(claimed_amount_cents) != amount_cents:
            raise ValueError("用户填报付款金额与支付单金额不一致，请先驳回让用户重新提交凭证")
        target_user_id = payment["user_id"]
        target = conn.execute("SELECT email FROM users WHERE id = ?", (target_user_id,)).fetchone()
        target_email = target["email"] if target else None

    signature = payment_signature(payment_id, amount_cents, provider_trade_no)
    result = confirm_payment(payment_id, amount_cents, provider_trade_no, signature)
    log_admin_audit(
        admin_user_id,
        "manual_confirm_payment",
        target_user_id=target_user_id,
        target_email=target_email,
        amount_cents=amount_cents,
        ref_id=payment_id,
        note=note,
        ip=ip,
    )
    return {
        "ok": True,
        "payment_id": payment_id,
        "provider_trade_no": provider_trade_no,
        "amount_cents": amount_cents,
        "amount": amount_cents / 100,
        "target_user_id": target_user_id,
        "target_email": target_email,
        "payment_status": result.get("payment_status", "paid"),
    }


def admin_cancel_payment(admin_user_id: str, payment_id: str, note: str, ip: str = "") -> dict:
    payment_id = str(payment_id or "").strip().upper()
    note = " ".join(str(note or "").strip().split())[:160]
    if not is_admin_user(admin_user_id):
        raise PermissionError("没有后台权限")
    if not payment_id:
        raise ValueError("缺少支付单号")
    if len(note) < 3:
        raise ValueError("请填写至少 3 个字的驳回原因")

    now = int(time.time())
    with db() as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            raise ValueError("支付单不存在")
        if payment["status"] != "pending":
            raise ValueError("只有待支付单可以驳回或取消")
        target_user_id = payment["user_id"]
        amount_cents = int(payment["amount_cents"])
        target = conn.execute("SELECT email FROM users WHERE id = ?", (target_user_id,)).fetchone()
        target_email = target["email"] if target else None
        conn.execute(
            """
            UPDATE payments
            SET status = ?, cancel_note = ?, canceled_at = ?
            WHERE payment_id = ?
            """,
            ("canceled", note, now, payment_id),
        )

    log_admin_audit(
        admin_user_id,
        "cancel_payment",
        target_user_id=target_user_id,
        target_email=target_email,
        amount_cents=amount_cents,
        ref_id=payment_id,
        note=note,
        ip=ip,
    )
    return {
        "ok": True,
        "payment_id": payment_id,
        "amount_cents": amount_cents,
        "amount": amount_cents / 100,
        "target_user_id": target_user_id,
        "target_email": target_email,
        "payment_status": "canceled",
        "cancel_note": note,
        "canceled_at": now,
    }


def admin_cleanup_order_results(admin_user_id: str, ip: str = "") -> dict:
    if not is_admin_user(admin_user_id):
        raise PermissionError("没有后台权限")
    result = cleanup_old_order_results()
    log_admin_audit(
        admin_user_id,
        "cleanup_order_results",
        note=f"retention_days={result['retention_days']}; deleted={result['deleted']}",
        ip=ip,
    )
    return result


def readiness_report() -> dict:
    def check(key: str, title: str, ok: bool, severity: str, detail: str) -> dict:
        return {
            "key": key,
            "title": title,
            "ok": ok,
            "severity": "ok" if ok else severity,
            "detail": detail,
        }

    checks = [
        check(
            "demo_fallback",
            "关闭演示 fallback",
            not DEMO_FALLBACK,
            "blocker",
            "生产环境应设置 DAISY_DEMO_FALLBACK=0，避免上游失败时返回演示结果。",
        ),
        check(
            "mock_payment",
            "关闭模拟支付",
            not MOCK_PAYMENTS_ENABLED,
            "blocker",
            "生产环境应设置 DAISY_ENABLE_MOCK_PAYMENT=0，避免用户绕过真实支付到账。",
        ),
        check(
            "payment_provider",
            "接入真实支付渠道",
            PAYMENT_PROVIDER != "mock",
            "blocker",
            "DAISY_PAYMENT_PROVIDER 当前仍为 mock，正式收款前应接入微信/支付宝等真实渠道。",
        ),
        check(
            "manual_payment_config",
            "配置人工收款信息",
            PAYMENT_PROVIDER != "manual_qr" or bool(MANUAL_PAYMENT_QR_URL or MANUAL_PAYMENT_ACCOUNT),
            "blocker",
            "manual_qr 模式需要配置 DAISY_MANUAL_PAYMENT_QR_URL 或 DAISY_MANUAL_PAYMENT_ACCOUNT，供用户完成转账。",
        ),
        check(
            "payment_secret",
            "更换支付回调密钥",
            PAYMENT_WEBHOOK_SECRET not in {"", "dev-secret-change-me", "change-this-payment-secret"},
            "blocker",
            "请设置强随机 DAISY_PAYMENT_WEBHOOK_SECRET，用于支付回调签名校验。",
        ),
        check(
            "admin_password",
            "更换默认管理员密码",
            ADMIN_BOOTSTRAP_PASSWORD not in {"", "admin123456", "change-this-admin-password"},
            "blocker",
            "请设置强密码 DAISY_ADMIN_PASSWORD，并避免使用本地演示密码。",
        ),
        check(
            "admin_email",
            "更换默认管理员邮箱",
            "admin@daisy.local" not in ADMIN_EMAILS,
            "warning",
            "建议将 DAISY_ADMIN_EMAILS 换成真实运营邮箱，便于审计和交接。",
        ),
        check(
            "openai_key",
            "配置降重模型 API",
            bool(OPENAI_API_KEY.strip()) and OPENAI_API_KEY != "sk-your-key",
            "blocker",
            "重复率优化依赖 OPENAI_API_KEY 或兼容模型密钥。",
        ),
        check(
            "bypass_password",
            "配置 BypassAIGC 后台密码",
            BYPASS_ADMIN_PASSWORD not in {"", "please-change-this-password", "change-me"},
            "blocker",
            "AIGC 优化依赖可用的 BypassAIGC 后台账号密码。",
        ),
        check(
            "bypass_license",
            "确认 BypassAIGC 商业授权",
            BYPASS_COMMERCIAL_AUTHORIZED,
            "blocker",
            "BypassAIGC 上游商用前需获得作者授权，确认后设置 DAISY_BYPASS_COMMERCIAL_AUTHORIZED=1。",
        ),
        check(
            "login_rate_limit",
            "启用登录限流",
            LOGIN_MAX_FAILED_ATTEMPTS > 0 and LOGIN_LOCK_SECONDS > 0,
            "warning",
            "建议设置 DAISY_LOGIN_MAX_FAILED_ATTEMPTS 和 DAISY_LOGIN_LOCK_SECONDS，降低后台和用户账号被撞库的风险。",
        ),
        check(
            "api_rate_limit",
            "启用 API 限流",
            API_RATE_LIMIT_PER_HOUR > 0,
            "warning",
            "建议设置 DAISY_API_RATE_LIMIT_PER_HOUR，避免第三方接入方高频刷接口。",
        ),
        check(
            "database",
            "使用持久数据库",
            not database_is_ephemeral(),
            "blocker" if database_is_ephemeral() else "warning",
            (
                "当前数据库位于临时文件系统，Vercel Serverless 重启或换实例后可能丢失余额、订单和充值记录。正式收款前请迁移到 Supabase Postgres、Neon、Turso 或独立后端持久磁盘。"
                if database_is_ephemeral()
                else "当前数据库路径不是临时目录；正式运营仍建议配置自动备份和灾难恢复。"
            ),
        ),
        check(
            "result_retention",
            "配置结果留存策略",
            RESULT_RETENTION_DAYS > 0,
            "warning",
            "建议设置 DAISY_RESULT_RETENTION_DAYS，例如 30 或 90，定期清理历史订单结果文本。",
        ),
        check(
            "contact_info",
            "替换演示客服信息",
            not (ROOT / "contact.html").read_text(encoding="utf-8", errors="ignore").count("service@daisy-aigc.local"),
            "warning",
            "请将联系页面中的演示邮箱替换为真实客服邮箱、企业主体和备案信息。",
        ),
    ]
    blockers = sum(1 for item in checks if item["severity"] == "blocker")
    warnings = sum(1 for item in checks if item["severity"] == "warning")
    return {
        "production_ready": blockers == 0,
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
    }


def admin_summary() -> dict:
    today_start = int(time.time()) - 24 * 60 * 60
    warnings = []
    with db() as conn:
        def scalar(sql: str, params=(), default=0):
            try:
                row = conn.execute(sql, params).fetchone()
                return row[0] if row else default
            except Exception as exc:
                warnings.append(str(exc))
                return default

        def rows(sql: str, params=()):
            try:
                return conn.execute(sql, params).fetchall()
            except Exception as exc:
                warnings.append(str(exc))
                return []

        stats = {
            "users": scalar("SELECT COUNT(*) FROM users"),
            "orders": scalar("SELECT COUNT(*) FROM orders"),
            "completed_orders": scalar("SELECT COUNT(*) FROM orders WHERE status = 'completed'"),
            "stored_results": scalar("SELECT COUNT(*) FROM orders WHERE result_text IS NOT NULL AND result_text != ''"),
            "paid_payments": scalar("SELECT COUNT(*) FROM payments WHERE status = 'paid'"),
            "open_tickets": scalar("SELECT COUNT(*) FROM support_tickets WHERE status IN ('open', 'processing')"),
            "api_calls_24h": scalar("SELECT COUNT(*) FROM api_usage WHERE created_at >= ?", (today_start,)),
            "revenue_cents": scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE status = 'paid'"),
            "order_debit_cents": scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM orders WHERE status = 'completed'"),
            "balance_pool_cents": scalar("SELECT COALESCE(SUM(balance_cents), 0) FROM users"),
        }
        users = rows(
            """
            SELECT id, email, display_name, balance_cents, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT 12
            """
        )
        orders = rows(
            """
            SELECT order_id, user_id, service, platform, chars, amount_cents, engine, status, error, created_at, completed_at
            FROM orders
            ORDER BY created_at DESC
            LIMIT 12
            """
        )
        payments = rows(
            """
            SELECT payments.payment_id, payments.user_id, payments.amount_cents, payments.provider,
                   payments.status, payments.provider_trade_no, payments.user_trade_no,
                   payments.user_payment_note, payments.user_claimed_amount_cents,
                   payments.notified_at, payments.cancel_note,
                   payments.canceled_at, payments.created_at, payments.paid_at, users.email
            FROM payments
            LEFT JOIN users ON users.id = payments.user_id
            ORDER BY payments.created_at DESC
            LIMIT 12
            """
        )
        ledger = rows(
            """
            SELECT id, user_id, type, amount_cents, balance_after_cents, ref_id, created_at, note
            FROM ledger
            ORDER BY id DESC
            LIMIT 12
            """
        )
        api_usage = rows(
            """
            SELECT api_usage.id, api_usage.api_key_id, api_usage.user_id, api_usage.order_id,
                   api_usage.status, api_usage.chars, api_usage.amount_cents, api_usage.error,
                   api_usage.created_at, api_keys.key_prefix, api_keys.name, users.email
            FROM api_usage
            JOIN api_keys ON api_keys.id = api_usage.api_key_id
            JOIN users ON users.id = api_usage.user_id
            ORDER BY api_usage.id DESC
            LIMIT 12
            """
        )
        admin_audit = rows(
            """
            SELECT admin_audit.id, admin_audit.admin_user_id, admin_audit.action,
                   admin_audit.target_user_id, admin_audit.target_email,
                   admin_audit.amount_cents, admin_audit.ref_id, admin_audit.note,
                   admin_audit.ip, admin_audit.created_at, users.email AS admin_email
            FROM admin_audit
            LEFT JOIN users ON users.id = admin_audit.admin_user_id
            ORDER BY admin_audit.id DESC
            LIMIT 12
            """
        )
        support_tickets = rows(
            """
            SELECT ticket_id, user_id, email, category, subject, message, ref_id,
                   status, admin_note, created_at, resolved_at
            FROM support_tickets
            ORDER BY created_at DESC
            LIMIT 12
            """
        )

    def money(row: sqlite3.Row, key: str = "amount_cents") -> dict:
        item = dict(row)
        if key in item:
            item[key.replace("_cents", "")] = item[key] / 100 if item[key] is not None else None
        if "balance_cents" in item:
            item["balance"] = item["balance_cents"] / 100 if item["balance_cents"] is not None else None
        if "balance_after_cents" in item:
            item["balance_after"] = item["balance_after_cents"] / 100 if item["balance_after_cents"] is not None else None
        if "user_claimed_amount_cents" in item:
            item["user_claimed_amount"] = (
                item["user_claimed_amount_cents"] / 100
                if item["user_claimed_amount_cents"] is not None
                else None
            )
        if "amount_cents" in item and "user_claimed_amount_cents" in item:
            item["amount_mismatch"] = (
                item["user_claimed_amount_cents"] is not None
                and item["user_claimed_amount_cents"] != item["amount_cents"]
            )
        return item

    return {
        "stats": {
            **stats,
            "revenue": stats["revenue_cents"] / 100,
            "order_debit": stats["order_debit_cents"] / 100,
            "balance_pool": stats["balance_pool_cents"] / 100,
        },
        "users": [money(row, "balance_cents") for row in users],
        "orders": [money(row) for row in orders],
        "payments": [money(row) for row in payments],
        "ledger": [money(row) for row in ledger],
        "api_usage": [money(row) for row in api_usage],
        "admin_audit": [money(row) for row in admin_audit],
        "support_tickets": [dict(row) for row in support_tickets],
        "readiness": readiness_report(),
        "warnings": warnings[:20],
    }


def admin_export_csv(export_type: str) -> tuple[str, bytes]:
    export_type = str(export_type or "").strip()
    spec = ADMIN_EXPORTS.get(export_type)
    if not spec:
        raise ValueError("不支持的导出类型")
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    headers = spec["headers"]
    writer.writerow(headers)
    with db() as conn:
        rows = conn.execute(spec["sql"]).fetchall()
    for row in rows:
        writer.writerow([row[key] if key in row.keys() else "" for key in headers])
    return spec["filename"], ("\ufeff" + output.getvalue()).encode("utf-8")


def admin_database_backup() -> tuple[str, bytes]:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    filename = f"daisy-db-backup-{timestamp}.sqlite3"
    with tempfile.NamedTemporaryFile(prefix="daisy-backup-", suffix=".sqlite3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        source = sqlite3.connect(DB_FILE)
        try:
            target = sqlite3.connect(tmp_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        content = tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return filename, content


class DaisyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def cors_origin(self) -> str:
        origin = self.headers.get("Origin", "").strip().rstrip("/")
        if origin and origin in ALLOWED_CORS_ORIGINS:
            return origin
        return ""

    def end_headers(self) -> None:
        origin = self.cors_origin()
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def current_session(self) -> tuple[str, bool, str | None]:
        auth_header = self.headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token and not token.startswith("daisy_live_"):
                profile = fetch_supabase_profile(token)
                if profile:
                    return ensure_supabase_user(profile), True, None

        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return DEFAULT_USER_ID, False, None
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        morsel = cookie.get(SESSION_COOKIE)
        if not morsel:
            return DEFAULT_USER_ID, False, None
        session_id = morsel.value
        if STATELESS_SESSIONS:
            user_id = verify_stateless_session(session_id)
            return (user_id, True, session_id) if user_id else (DEFAULT_USER_ID, False, None)
        now = int(time.time())
        with db() as conn:
            row = conn.execute("SELECT user_id, expires_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row or row["expires_at"] < now:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                return DEFAULT_USER_ID, False, None
        return row["user_id"], True, session_id

    def session_cookie_header(self, session_id: str, max_age: int = SESSION_TTL_SECONDS) -> str:
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite={COOKIE_SAMESITE}; Max-Age={max_age}{secure}"

    def clear_cookie_header(self) -> str:
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite={COOKIE_SAMESITE}; Max-Age=0{secure}"

    def request_api_key(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            return token if token.startswith("daisy_live_") else ""
        return self.headers.get("X-API-Key", "").strip()

    def client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:80]
        return (self.client_address[0] if self.client_address else "unknown")[:80]

    def send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def send_csv(self, filename: str, content: bytes) -> None:
        safe_name = urllib.parse.quote(filename)
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename={safe_name}; filename*=UTF-8''{safe_name}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_download(self, filename: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        safe_name = urllib.parse.quote(filename)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"attachment; filename={safe_name}; filename*=UTF-8''{safe_name}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_redirect(self, location: str, headers: dict | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()

    def parse_multipart_file(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("请使用 multipart/form-data 上传文件")
        boundary_token = "boundary="
        if boundary_token not in content_type:
            raise ValueError("上传请求缺少 boundary")
        boundary = content_type.split(boundary_token, 1)[1].strip().strip('"').encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("上传内容为空")
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            raise ValueError(f"上传体过大，当前限制 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        body = self.rfile.read(length)
        marker = b"--" + boundary
        for part in body.split(marker):
            if b"Content-Disposition:" not in part or b'name="file"' not in part:
                continue
            header, _, file_data = part.partition(b"\r\n\r\n")
            if not file_data:
                continue
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]
            if file_data.endswith(b"--"):
                file_data = file_data[:-2]
            disposition = header.decode("utf-8", errors="ignore")
            filename = "upload"
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].splitlines()[0].split(";", 1)[0].strip().strip('"')
            return filename, file_data
        raise ValueError("没有找到名为 file 的上传字段")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "bypass_base_url": BYPASS_BASE_URL,
                    "rewrite_model": REWRITE_MODEL,
                    "payment_provider": PAYMENT_PROVIDER,
                    "mock_payments_enabled": MOCK_PAYMENTS_ENABLED,
                    "wechat_login_enabled": False,
                    "supabase_auth_enabled": SUPABASE_AUTH_ENABLED,
                    "supabase_admin_register_enabled": SUPABASE_ADMIN_ENABLED,
                    "billable_auth_required": REQUIRE_AUTH_FOR_BILLABLE,
                    "database": database_status(),
                    "app_version": APP_VERSION,
                    "commit": os.getenv("VERCEL_GIT_COMMIT_SHA", ""),
                },
            )
            return
        if path == "/api/public-config":
            self.send_json(
                200,
                {
                    "payment_provider": PAYMENT_PROVIDER,
                    "mock_payments_enabled": MOCK_PAYMENTS_ENABLED,
                    "manual_payment_enabled": PAYMENT_PROVIDER == "manual_qr",
                    "demo_fallback": DEMO_FALLBACK,
                    "wechat_login_enabled": False,
                    "supabase_auth_enabled": SUPABASE_AUTH_ENABLED,
                    "supabase_admin_register_enabled": SUPABASE_ADMIN_ENABLED,
                    "billable_auth_required": REQUIRE_AUTH_FOR_BILLABLE,
                    "supabase_url": SUPABASE_URL,
                    "supabase_anon_key": SUPABASE_ANON_KEY,
                    "max_chars": 6000,
                    "max_upload_bytes": MAX_UPLOAD_BYTES,
                },
            )
            return
        if path == "/api/wechat/login":
            query = urllib.parse.parse_qs(parsed.query)
            redirect_path = query.get("redirect", ["/"])[0]
            try:
                payload = wechat_login_url(self.headers, redirect_path)
            except ValueError as exc:
                self.send_json(400, {"enabled": False, "error": str(exc)})
                return
            if query.get("mode", ["json"])[0] == "redirect":
                self.send_redirect(payload["auth_url"])
                return
            self.send_json(200, payload)
            return
        if path == "/api/wechat/callback":
            query = urllib.parse.parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            state = query.get("state", [""])[0]
            redirect_path = "/"
            try:
                if not WECHAT_LOGIN_ENABLED:
                    raise ValueError("微信登录未配置")
                if not code or not state:
                    raise ValueError("微信回调缺少 code 或 state")
                redirect_path = consume_oauth_state("wechat", state)
                user = login_wechat_user(wechat_exchange_code(code))
                session_id = create_session(user["user_id"])
                separator = "&" if "?" in redirect_path else "?"
                self.send_redirect(f"{redirect_path}{separator}login=wechat", {"Set-Cookie": self.session_cookie_header(session_id)})
            except Exception as exc:
                separator = "&" if "?" in redirect_path else "?"
                self.send_redirect(f"{redirect_path}{separator}wechat_error={urllib.parse.quote(str(exc))}")
            return
        user_id, authenticated, _ = self.current_session()
        if path == "/api/me":
            self.send_json(200, user_snapshot(user_id, authenticated=True) if authenticated else guest_snapshot())
            return
        if path == "/api/orders":
            if not authenticated:
                self.send_json(401, {"error": "请先登录后查看订单"})
                return
            self.send_json(200, {"orders": recent_orders(user_id)})
            return
        if path.startswith("/api/orders/") and path.endswith("/download"):
            if not authenticated:
                self.send_json(401, {"error": "请先登录后下载订单结果"})
                return
            order_id = urllib.parse.unquote(path.removeprefix("/api/orders/").removesuffix("/download")).strip("/")
            try:
                filename, content = order_result_download(user_id, order_id)
            except ValueError as exc:
                self.send_json(404, {"error": str(exc)})
                return
            content_type = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if filename.lower().endswith(".docx")
                else "text/plain; charset=utf-8"
            )
            self.send_download(filename, content, content_type)
            return
        if path.startswith("/api/orders/"):
            if not authenticated:
                self.send_json(401, {"error": "请先登录后查看订单"})
                return
            order_id = urllib.parse.unquote(path.rsplit("/", 1)[-1]).strip()
            try:
                self.send_json(200, {"order": order_detail(user_id, order_id)})
            except ValueError as exc:
                self.send_json(404, {"error": str(exc)})
            return
        if path == "/api/payments":
            if not authenticated:
                self.send_json(401, {"error": "请先登录后查看充值记录"})
                return
            self.send_json(200, {"payments": recent_payments(user_id)})
            return
        if path == "/api/support-tickets":
            self.send_json(200, {"tickets": recent_support_tickets(user_id, authenticated)})
            return
        if path == "/api/api-keys":
            if not authenticated:
                self.send_json(401, {"error": "请先登录后管理 API Key"})
                return
            self.send_json(200, {"keys": list_api_keys(user_id)})
            return
        if path == "/api/admin/summary":
            if not authenticated:
                self.send_json(401, {"error": "请先登录管理员账号"})
                return
            if not is_admin_user(user_id):
                self.send_json(403, {"error": "没有后台权限"})
                return
            try:
                self.send_json(200, admin_summary())
            except Exception as exc:
                self.send_json(500, {"error": str(exc), "where": "admin_summary"})
            return
        if path == "/api/admin/export":
            if not authenticated:
                self.send_json(401, {"error": "请先登录管理员账号"})
                return
            if not is_admin_user(user_id):
                self.send_json(403, {"error": "没有后台权限"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                filename, content = admin_export_csv(query.get("type", [""])[0])
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_csv(filename, content)
            return
        if path == "/api/admin/backup-db":
            if not authenticated:
                self.send_json(401, {"error": "admin login required"})
                return
            if not is_admin_user(user_id):
                self.send_json(403, {"error": "admin permission required"})
                return
            filename, content = admin_database_backup()
            self.send_download(filename, content, "application/vnd.sqlite3")
            return
        super().do_GET()

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            user_id, authenticated, session_id = self.current_session()
            if path == "/api/inspect-file":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后上传文件"})
                    return
                filename, data = self.parse_multipart_file()
                self.send_json(200, inspect_file(filename, data))
                return
            if path == "/api/extract-file":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后上传文件"})
                    return
                filename, data = self.parse_multipart_file()
                self.send_json(200, extract_file(filename, data))
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if path.startswith("/api/admin/"):
                if not authenticated:
                    self.send_json(401, {"error": "请先登录管理员账号"})
                    return
                if not is_admin_user(user_id):
                    self.send_json(403, {"error": "没有后台权限"})
                    return
                if path == "/api/admin/adjust-balance":
                    amount_cents = parse_money_cents(payload.get("amount"), "调整金额", allow_negative=True)
                    self.send_json(
                        200,
                        admin_adjust_balance(
                            user_id,
                            str(payload.get("email", "")),
                            amount_cents,
                            str(payload.get("note", "")),
                            self.client_ip(),
                        ),
                    )
                    return
                if path == "/api/admin/confirm-payment":
                    self.send_json(
                        200,
                        admin_confirm_payment(
                            user_id,
                            str(payload.get("payment_id", "")),
                            str(payload.get("provider_trade_no", "")),
                            str(payload.get("note", "")),
                            self.client_ip(),
                        ),
                    )
                    return
                if path == "/api/admin/cancel-payment":
                    self.send_json(
                        200,
                        admin_cancel_payment(
                            user_id,
                            str(payload.get("payment_id", "")),
                            str(payload.get("note", "")),
                            self.client_ip(),
                        ),
                    )
                    return
                if path == "/api/admin/cleanup-results":
                    self.send_json(200, admin_cleanup_order_results(user_id, self.client_ip()))
                    return
                if path == "/api/admin/update-ticket":
                    self.send_json(
                        200,
                        admin_update_ticket(
                            user_id,
                            str(payload.get("ticket_id", "")),
                            str(payload.get("status", "")),
                            str(payload.get("note", "")),
                            self.client_ip(),
                        ),
                    )
                    return
            if path == "/api/register":
                user = register_user(
                    str(payload.get("email", "")),
                    str(payload.get("password", "")),
                    bool(payload.get("accept_terms")),
                    bool(payload.get("require_supabase_admin")),
                )
                new_session_id = create_session(user["user_id"])
                self.send_json(200, user_snapshot(user["user_id"], authenticated=True), {"Set-Cookie": self.session_cookie_header(new_session_id)})
                return
            if path == "/api/support-tickets":
                self.send_json(200, create_support_ticket(user_id, authenticated, payload))
                return
            if path.startswith("/api/orders/") and path.endswith("/delete-result"):
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后删除订单结果"})
                    return
                order_id = urllib.parse.unquote(path.removeprefix("/api/orders/").removesuffix("/delete-result")).strip("/")
                self.send_json(200, delete_order_result(user_id, order_id))
                return
            if path == "/api/login":
                email = str(payload.get("email", "")).strip().lower()
                ip = self.client_ip()
                if LOGIN_MAX_FAILED_ATTEMPTS > 0 and login_failure_count(email, ip) >= LOGIN_MAX_FAILED_ATTEMPTS:
                    self.send_json(429, {"error": "登录失败次数过多，请稍后再试"})
                    return
                try:
                    user = login_user(email, str(payload.get("password", "")), bool(payload.get("require_supabase_admin")))
                except ValueError:
                    log_login_attempt(email, ip, False)
                    raise
                log_login_attempt(email, ip, True)
                new_session_id = create_session(user["user_id"])
                self.send_json(200, user_snapshot(user["user_id"], authenticated=True), {"Set-Cookie": self.session_cookie_header(new_session_id)})
                return
            if path == "/api/logout":
                destroy_session(session_id)
                self.send_json(200, user_snapshot(DEFAULT_USER_ID, authenticated=False), {"Set-Cookie": self.clear_cookie_header()})
                return
            if path == "/api/change-password":
                if not authenticated:
                    self.send_json(401, {"error": "login required"})
                    return
                self.send_json(
                    200,
                    change_password(
                        user_id,
                        str(payload.get("current_password", "")),
                        str(payload.get("new_password", "")),
                        session_id,
                    ),
                )
                return
            if path == "/api/api-keys/create":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后创建 API Key"})
                    return
                self.send_json(200, create_api_key(user_id, str(payload.get("name", ""))))
                return
            if path == "/api/api-keys/revoke":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后撤销 API Key"})
                    return
                self.send_json(200, revoke_api_key(user_id, str(payload.get("id", ""))))
                return
            if path == "/api/optimize":
                api_key = self.request_api_key()
                if api_key:
                    api_identity = authenticate_api_key(api_key)
                    if not api_identity:
                        self.send_json(401, {"error": "API Key 无效或已撤销"})
                        return
                    chars, estimated_amount = api_payload_usage(payload)
                    if API_RATE_LIMIT_PER_HOUR > 0 and api_usage_count(api_identity["id"]) >= API_RATE_LIMIT_PER_HOUR:
                        log_api_usage(
                            api_identity["id"],
                            api_identity["user_id"],
                            "rate_limited",
                            chars=chars,
                            amount_cents=estimated_amount,
                            error="rate limit exceeded",
                        )
                        self.send_json(429, {"error": f"API Key 每小时最多调用 {API_RATE_LIMIT_PER_HOUR} 次"})
                        return
                    user_id = api_identity["user_id"]
                    try:
                        result = optimize(payload, user_id=user_id)
                        log_api_usage(
                            api_identity["id"],
                            user_id,
                            "success",
                            chars=int(result.get("chars", chars)),
                            amount_cents=int(result.get("amount_cents", estimated_amount)),
                            order_id=str(result.get("order_id", "")) or None,
                        )
                        self.send_json(200, result)
                    except Exception as exc:
                        log_api_usage(
                            api_identity["id"],
                            user_id,
                            "failed",
                            chars=chars,
                            amount_cents=estimated_amount,
                            error=str(exc),
                        )
                        raise
                    return
                if REQUIRE_AUTH_FOR_BILLABLE and not authenticated:
                    self.send_json(401, {"error": "请先登录后提交订单"})
                    return
                self.send_json(200, optimize(payload, user_id=user_id))
                return
            if path == "/api/payments/create":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后创建充值单"})
                    return
                amount_cents = parse_money_cents(payload.get("amount"), "充值金额")
                self.send_json(200, create_payment(amount_cents, user_id=user_id))
                return
            if path == "/api/payments/notify-paid":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后提交付款凭证"})
                    return
                self.send_json(
                    200,
                    submit_payment_notice(
                        user_id,
                        str(payload.get("payment_id", "")),
                        str(payload.get("user_trade_no", "")),
                        str(payload.get("note", "")),
                        parse_money_cents(payload.get("claimed_amount"), "实际付款金额"),
                    ),
                )
                return
            if path == "/api/payments/mock-confirm":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后确认支付"})
                    return
                if not MOCK_PAYMENTS_ENABLED:
                    raise ValueError("演示支付确认已关闭，请使用支付平台 webhook 确认到账")
                self.send_json(
                    200,
                    confirm_payment(
                        str(payload.get("payment_id", "")),
                        int(payload.get("amount_cents", 0)),
                        str(payload.get("provider_trade_no", "")),
                        str(payload.get("signature", "")),
                    ),
                )
                return
            if path == "/api/payments/webhook":
                self.send_json(
                    200,
                    confirm_payment(
                        str(payload.get("payment_id", "")),
                        int(payload.get("amount_cents", 0)),
                        str(payload.get("provider_trade_no", "")),
                        str(payload.get("signature", "")),
                    ),
                )
                return
            if path == "/api/recharge":
                if not authenticated:
                    self.send_json(401, {"error": "请先登录后充值"})
                    return
                if not MOCK_PAYMENTS_ENABLED:
                    raise ValueError("演示充值接口已关闭，请使用支付平台 webhook 入账")
                amount_cents = parse_money_cents(payload.get("amount"), "充值金额")
                self.send_json(200, recharge(amount_cents, user_id=user_id))
                return
            self.send_json(404, {"error": "Not found"})
        except (ValueError, urllib.error.HTTPError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def main() -> None:
    init_db()
    cleanup_result = cleanup_old_order_results()
    if cleanup_result["enabled"] and cleanup_result["deleted"]:
        print(
            f"Cleaned {cleanup_result['deleted']} order results "
            f"older than {cleanup_result['retention_days']} days"
        )
    port = int(os.getenv("PORT", "9910"))
    server = ThreadingHTTPServer(("0.0.0.0", port), DaisyHandler)
    print(f"雏菊AIGC running at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
