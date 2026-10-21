import os
import asyncio
import sqlite3
import math
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import re
import random
import html
from datetime import datetime
from pathlib import Path

# ============================================================
# HTTP SESSION & POOLING (MUKAMMAL ALOQA TIZIMI)
# ============================================================
HTTP_SESSION = requests.Session()
_http_retries = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
)
_http_adapter = HTTPAdapter(max_retries=_http_retries, pool_connections=30, pool_maxsize=60)
HTTP_SESSION.mount("http://", _http_adapter)
HTTP_SESSION.mount("https://", _http_adapter)

import telebot
from telebot import types
from dotenv import load_dotenv

from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.errors import UserAlreadyParticipantError

# ============================================================
# SOZLAMALAR
# ============================================================

load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8657770745:AAHxznnwFSgmHgA1lUEP35Bnuy3L9pEKPOo").strip() or "8657770745:AAHxznnwFSgmHgA1lUEP35Bnuy3L9pEKPOo"
ADMIN_ID = int(os.getenv("ADMIN_ID", "8191930658") or 8191930658)
DB_FILE = os.getenv("DB_FILE", "smm_bot.db")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# AI Agent fallback modeli.
# Groq ishlamasa OpenAI shu model orqali reja tuzadi.
OPENAI_AGENT_MODEL = os.getenv(
    "OPENAI_AGENT_MODEL",
    "gpt-6-luna"
).strip()

API_ID = 38883376
API_HASH = "67909afdf0fe6c23e6af29985f645297"

DEFAULT_USERBOT_SESSION = "1ApWapzMBuzwsTu81tRBCeZQ7gu4ERhma5D66ZBdVtl-uPoSGrYlyE4NyG9qwDJ989u913j0h6dnWxdoucwSVNvWNAC1g-oz5woPA7ZBpcRsu4txKXWxvU3ZLIGUEDOym-ZmK4QvIgMtrRMYTwuk5EGkdoYKt5iVK58eChFUCVJ_2tU7wCar4KkSlcB2s4UvYDFEzrTA42XkjbjKlP1w0f0c6aFuOvBgeriW4imvLDVKS0mISyLEaOGr1ef2btu4RzNgRpVtjxm3VV9zgr1cxerWl7w2rKivejMqORdVlK7bDYzxzODG9f5wb9Y86YENGHvwokxW4SocsrdbStZu4OlhymivInJQ="

raw_session = os.getenv("USERBOT_SESSION", "").strip()
if raw_session and len(raw_session) > 100 and raw_session.startswith("1"):
    USERBOT_SESSION = raw_session
else:
    USERBOT_SESSION = DEFAULT_USERBOT_SESSION

def log_debug(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="", flush=True)
    try:
        with open("debug.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN .env faylida topilmadi")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

def is_admin(user):
    """Faqat @xua202 va uning ID si (8191930658) uchun admin ruxsatini beradi."""
    if not user:
        return False
    if isinstance(user, int):
        return user == 8191930658 or (ADMIN_ID and user == ADMIN_ID)
    u_id = getattr(user, "id", None) or 0
    u_name = (getattr(user, "username", None) or "").lower()
    return u_id == 8191930658 or (ADMIN_ID and u_id == ADMIN_ID) or u_name == "xua202"


# Foydalanuvchi holatlari
user_state = {}

# AI -> Telethon bridge uchun asosiy asyncio loop
MAIN_LOOP = None
userbot = None

# Postlar bir marta buyurtma qilinishi uchun in-memory kesh va lock
ORDERED_POSTS_CACHE = set()
PROCESSING_POSTS = set()

def clean_channel_id(cid):
    if cid is None:
        return ""
    s = str(cid).strip()
    if s.startswith("-100"):
        s = s[4:]
    elif s.startswith("-"):
        s = s[1:]
    if s.startswith("@"):
        s = s[1:]
    return s

def normalize_channel_id(cid):
    clean = clean_channel_id(cid)
    if clean.isdigit():
        return -int(f"100{clean}")
    try:
        return int(cid)
    except Exception:
        return str(cid).strip().lstrip("@")

def extract_invite_hash(link_or_text):
    if not link_or_text:
        return None
    s = str(link_or_text).strip()
    m = re.search(r"(?:t\.me/|telegram\.me/|telegram\.dog/)?(?:\+|joinchat/)([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    if s.startswith("+") and len(s) > 2:
        return s[1:].strip()
    return None

def join_userbot_to_channel(channel_id, username=None, invite_link=None):
    """Kanal qo'shilganda userbotni darhol ushbu kanalga a'zo qiladi (ommaviy yoki maxfiy)."""
    global MAIN_LOOP, userbot
    log_debug(f"JOIN_USERBOT_TO_CHANNEL: cid={channel_id}, username={username}")
    if not MAIN_LOOP or not userbot:
        log_debug("⚠️ Userbot yoki MAIN_LOOP faol emas, kanalga qo'shilish kechiktirildi.")
        return

    async def _async_join():
        try:
            # 1. Agar invite link yoki hash berilgan bo'lsa
            invite_hash = extract_invite_hash(invite_link or username or str(channel_id))
            if invite_hash:
                log_debug(f"🔗 Userbot maxfiy kanalga invite hash orqali qo'shilmoqda: {invite_hash}")
                try:
                    await userbot(ImportChatInviteRequest(invite_hash))
                    log_debug(f"✅ USERBOT MAXFIY KANALGA QO'SHILDI (hash: {invite_hash})")
                    return
                except UserAlreadyParticipantError:
                    log_debug(f"✅ Userbot allaqachon maxfiy kanalga a'zo: {invite_hash}")
                    return
                except Exception as e:
                    err = repr(e)
                    if "already" in err.lower() or "participant" in err.lower():
                        log_debug(f"✅ Userbot allaqachon maxfiy kanalga a'zo: {invite_hash}")
                        return
                    log_debug(f"⚠️ ImportChatInviteRequest xatosi ({invite_hash}): {e}")

            # 2. Public kanal username bo'lsa
            entity = None
            if username:
                try:
                    clean_u = str(username).strip().lstrip("@")
                    if clean_u.lower() != "joinchat":
                        entity = await userbot.get_entity(clean_u)
                except Exception as e:
                    log_debug(f"⚠️ Entity username orqali topilmadi: {e}")

            # 3. Channel ID orqali entity qidirish (agar userbot allaqachon kanal ichida bo'lsa)
            if not entity and channel_id:
                try:
                    clean_id = clean_channel_id(channel_id)
                    if clean_id.isdigit():
                        for test_id in [-int(f"100{clean_id}"), int(clean_id), -int(clean_id)]:
                            try:
                                entity = await userbot.get_entity(test_id)
                                if entity:
                                    break
                            except Exception:
                                pass
                except Exception as e:
                    log_debug(f"⚠️ Entity channel_id orqali topilmadi: {e}")

            if entity:
                try:
                    await userbot(JoinChannelRequest(entity))
                    log_debug(f"✅ USERBOT YANGI KANALGA QO'SHILDI: {username or channel_id}")
                except Exception as e:
                    err = repr(e)
                    if "already" in err.lower() or "participant" in err.lower():
                        log_debug(f"✅ Userbot allaqachon kanalga a'zo: {username or channel_id}")
                    else:
                        log_debug(f"⚠️ JoinChannelRequest xatosi ({username or channel_id}): {e}")
            else:
                log_debug(f"ℹ️ Kanal entity aniqlanmadi: {username or channel_id}")
        except Exception as e:
            log_debug(f"⚠️ Userbot kanalga qo'shilish xatosi ({username or channel_id}): {e}")

    try:
        asyncio.run_coroutine_threadsafe(_async_join(), MAIN_LOOP)
    except Exception as e:
        log_debug(f"❌ join_userbot_to_channel threadsafe xatosi: {e}")



# ============================================================
# DATABASE & PLATFORM FILTERS
# ============================================================

# Instagram, TikTok va YouTube xizmatlarini to'liq chiqarib tashlash uchun SQL filtr
EXCLUDED_PLATFORMS_SQL = """(
    LOWER(category) LIKE '%instagram%'
    OR LOWER(name) LIKE '%instagram%'
    OR LOWER(category) LIKE '%insta%'
    OR LOWER(name) LIKE '%insta%'
    OR LOWER(category) LIKE '%tiktok%'
    OR LOWER(name) LIKE '%tiktok%'
    OR LOWER(category) LIKE '%tik tok%'
    OR LOWER(name) LIKE '%tik tok%'
    OR LOWER(category) LIKE '%youtube%'
    OR LOWER(name) LIKE '%youtube%'
    OR LOWER(category) LIKE '%yutube%'
    OR LOWER(name) LIKE '%yutube%'
)"""

_tables_initialized = False

def init_core_tables(conn):
    """Barcha jadvallarni va indekslarni xavfsiz yaratadi."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            banned INTEGER DEFAULT 0,
            created_at TEXT,
            total_deposited REAL DEFAULT 0,
            role TEXT DEFAULT 'user'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_id INTEGER,
            external_id TEXT,
            name TEXT,
            category TEXT,
            price REAL DEFAULT 0,
            min_qty INTEGER DEFAULT 1,
            max_qty INTEGER DEFAULT 1000000,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            admin_available INTEGER DEFAULT 0,
            FOREIGN KEY(api_id) REFERENCES apis(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            service_id INTEGER,
            external_order_id TEXT,
            link TEXT,
            quantity INTEGER,
            price REAL,
            status TEXT DEFAULT 'Pending',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            method TEXT DEFAULT '',
            status TEXT DEFAULT 'Pending',
            comment TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            data TEXT DEFAULT '{}',
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            title TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT,
            invite_link TEXT DEFAULT '',
            UNIQUE(user_id, channel_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_id INTEGER DEFAULT 0,
            details TEXT DEFAULT '',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            channel_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            min_qty INTEGER,
            max_qty INTEGER,
            UNIQUE(user_id, channel_id, service_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            service_id INTEGER NOT NULL,
            link TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            external_order_id TEXT DEFAULT '',
            price REAL DEFAULT 0,
            status TEXT DEFAULT 'Pending',
            error TEXT DEFAULT '',
            created_at TEXT,
            UNIQUE(owner_id, channel_id, message_id, service_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS userbot_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            text TEXT NOT NULL,
            run_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_post_groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            active INTEGER DEFAULT 1,
            last_post_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_services_category ON services(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_services_sort ON services(sort_order, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_user ON channels(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_services_user ON channel_services(user_id, channel_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_orders_owner ON auto_orders(owner_id, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_orders_post ON auto_orders(channel_id, message_id, service_id)")
    except Exception:
        pass

    # --------------------------------------------------------
    # MIGRATSIYALAR (Faqat tizim ishga tushganda 1 marta bajariladi)
    # --------------------------------------------------------
    try:
        columns = conn.execute("PRAGMA table_info(services)").fetchall()
        column_names = {row["name"] for row in columns}
        if "admin_available" not in column_names:
            conn.execute("ALTER TABLE services ADD COLUMN admin_available INTEGER DEFAULT 0")
            conn.execute(
                f"""
                UPDATE services
                SET admin_available = 1
                WHERE active = 1
                  AND (
                      LOWER(category) LIKE '%telegram%'
                      OR LOWER(category) LIKE '%reaksiya%'
                      OR LOWER(category) LIKE '%ko%rish%'
                      OR LOWER(category) LIKE '%prosmotr%'
                      OR LOWER(category) LIKE '%ulash%'
                      OR LOWER(category) LIKE '%tekin%'
                      OR LOWER(name) LIKE '%telegram%'
                      OR LOWER(name) LIKE '%reaksiya%'
                      OR LOWER(name) LIKE '%ko%rish%'
                      OR LOWER(name) LIKE '%prosmotr%'
                      OR LOWER(name) LIKE '%ulash%'
                      OR LOWER(name) LIKE '%view%'
                      OR LOWER(name) LIKE '%share%'
                      OR LOWER(name) LIKE '%bonus%'
                      OR price = 0
                  )
                  AND NOT {EXCLUDED_PLATFORMS_SQL}
                """
            )
            conn.execute(f"UPDATE services SET admin_available = 0 WHERE {EXCLUDED_PLATFORMS_SQL}")
            conn.commit()
    except Exception as e:
        log_debug(f"⚠️ admin_available migration xatosi: {e}")

    try:
        ch_columns = conn.execute("PRAGMA table_info(channels)").fetchall()
        ch_column_names = {row["name"] for row in ch_columns}
        if "invite_link" not in ch_column_names:
            conn.execute("ALTER TABLE channels ADD COLUMN invite_link TEXT DEFAULT ''")
            conn.commit()
    except Exception as e:
        log_debug(f"⚠️ channels invite_link migration xatosi: {e}")

    try:
        cs_cols = {row["name"] for row in conn.execute("PRAGMA table_info(channel_services)").fetchall()}
        if "user_id" not in cs_cols:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_services_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    channel_id INTEGER NOT NULL,
                    service_id INTEGER NOT NULL,
                    active INTEGER DEFAULT 1,
                    created_at TEXT,
                    min_qty INTEGER,
                    max_qty INTEGER,
                    UNIQUE(user_id, channel_id, service_id)
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO channel_services_new (id, user_id, channel_id, service_id, active, created_at, min_qty, max_qty)
                SELECT cs.id,
                       COALESCE((SELECT c.user_id FROM channels c WHERE c.channel_id = cs.channel_id LIMIT 1), 8191930658),
                       cs.channel_id, cs.service_id, cs.active, cs.created_at, cs.min_qty, cs.max_qty
                FROM channel_services cs
            """)
            conn.execute("DROP TABLE channel_services")
            conn.execute("ALTER TABLE channel_services_new RENAME TO channel_services")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_services_user ON channel_services(user_id, channel_id)")
            conn.commit()
    except Exception as e:
        log_debug(f"⚠️ channel_services user_id migration xatosi: {e}")

    try:
        ao_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='auto_orders'").fetchone()
        if ao_sql and "UNIQUE(channel_id, message_id)" in ao_sql[0]:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_orders_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    service_id INTEGER NOT NULL,
                    link TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    external_order_id TEXT DEFAULT '',
                    price REAL DEFAULT 0,
                    status TEXT DEFAULT 'Pending',
                    error TEXT DEFAULT '',
                    created_at TEXT,
                    UNIQUE(owner_id, channel_id, message_id, service_id)
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO auto_orders_new (id, channel_id, message_id, owner_id, service_id, link, quantity, external_order_id, price, status, error, created_at)
                SELECT id, channel_id, message_id, owner_id, service_id, link, quantity, external_order_id, price, status, error, created_at
                FROM auto_orders
            """)
            conn.execute("DROP TABLE auto_orders")
            conn.execute("ALTER TABLE auto_orders_new RENAME TO auto_orders")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_orders_owner ON auto_orders(owner_id, id DESC)")
            conn.commit()
    except Exception as e:
        log_debug(f"⚠️ auto_orders migration xatosi: {e}")

    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_orders_post ON auto_orders(channel_id, message_id, service_id)")
        conn.commit()
    except Exception:
        pass
    conn.commit()


def save_local_db_backup(reason="Avtomatik"):
    """Ma'lumotlar bazasining lokal zaxira nusxasini serverning o'zida saqlaydi (foydalanuvchiga yubormaydi)."""
    try:
        if not os.path.exists(DB_FILE):
            return
        import shutil
        bak_file = f"{DB_FILE}.bak"
        shutil.copy2(DB_FILE, bak_file)
    except Exception as e:
        log_debug(f"⚠️ save_local_db_backup xatosi: {e}")


def db():
    """Tezkor va xavfsiz SQLite ulanishi (WAL rejimida)."""
    global _tables_initialized
    conn = sqlite3.connect(DB_FILE, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")

    if not _tables_initialized:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA temp_store = MEMORY")
            init_core_tables(conn)
            _tables_initialized = True
        except Exception as e:
            log_debug(f"⚠️ init_core_tables xatosi: {e}")

    return conn


def is_post_already_ordered(channel_id, message_id, service_id=None):
    """
    Kanal posti uchun buyurtma bajarilganmi yoki yo'qligini tekshiradi.
    service_id berilsa - shu xizmat bo'yicha tekshiradi.
    service_id None bo'lsa - kanal postiga umumiy biror buyurtma berilganmi tekshiradi.
    """
    clean_cid = clean_channel_id(channel_id)
    try:
        mid = int(message_id)
    except Exception:
        return False

    if service_id is not None:
        try:
            sid = int(service_id)
        except Exception:
            sid = None
        if (clean_cid, mid, sid) in ORDERED_POSTS_CACHE:
            return True

    target_cids = [str(channel_id), clean_cid, f"-100{clean_cid}", f"-{clean_cid}"]
    if clean_cid.isdigit():
        target_cids.append(int(clean_cid))
        target_cids.append(-int(f"100{clean_cid}"))
        target_cids.append(-int(clean_cid))

    placeholders = ",".join("?" for _ in target_cids)
    conn = db()
    try:
        if service_id is not None:
            row = conn.execute(
                f"""
                SELECT id, external_order_id, status FROM auto_orders
                WHERE channel_id IN ({placeholders})
                  AND message_id = ?
                  AND service_id = ?
                  AND (
                      (external_order_id IS NOT NULL AND TRIM(external_order_id) != '')
                      OR LOWER(COALESCE(status, '')) IN ('pending', 'processing', 'completed', 'in progress', 'in_progress', 'success')
                  )
                LIMIT 1
                """,
                target_cids + [mid, int(service_id)]
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT id, external_order_id, status FROM auto_orders
                WHERE channel_id IN ({placeholders})
                  AND message_id = ?
                  AND (
                      (external_order_id IS NOT NULL AND TRIM(external_order_id) != '')
                      OR LOWER(COALESCE(status, '')) IN ('pending', 'processing', 'completed', 'in progress', 'in_progress', 'success')
                  )
                LIMIT 1
                """,
                target_cids + [mid]
            ).fetchone()

        if row:
            if service_id is not None:
                ORDERED_POSTS_CACHE.add((clean_cid, mid, int(service_id)))
            return True
        return False
    except Exception as e:
        log_debug(f"⚠️ is_post_already_ordered xatosi: {e}")
        return False
    finally:
        conn.close()


def init_ordered_posts_cache():
    """Bot ishga tushganda bazadagi mavjud buyurtmalarni in-memory keshga yuklaydi."""
    global ORDERED_POSTS_CACHE
    try:
        conn = db()
        rows = conn.execute(
            """
            SELECT channel_id, message_id, service_id
            FROM auto_orders
            WHERE (external_order_id IS NOT NULL AND TRIM(external_order_id) != '')
               OR LOWER(COALESCE(status, '')) IN ('pending', 'processing', 'completed', 'in progress', 'in_progress', 'success')
            """
        ).fetchall()
        for r in rows:
            cid = clean_channel_id(r["channel_id"])
            try:
                mid = int(r["message_id"] or 0)
                sid = int(r["service_id"] or 0)
            except Exception:
                continue
            if cid and mid and sid:
                ORDERED_POSTS_CACHE.add((cid, mid, sid))
        conn.close()
        log_debug(f"📦 ORDERED_POSTS_CACHE yuklandi: {len(ORDERED_POSTS_CACHE)} ta mavjud buyurtma")
    except Exception as e:
        log_debug(f"⚠️ init_ordered_posts_cache xatosi: {e}")


def get_user(user):
    conn = db()

    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user.id,)
    ).fetchone()

    if not row:
        conn.execute(
            """
            INSERT INTO users
            (user_id, username, first_name, balance, banned, created_at)
            VALUES (?, ?, ?, 0, 0, ?)
            """,
            (
                user.id,
                user.username or "",
                user.first_name or "",
                datetime.now().isoformat()
            )
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user.id,)
        ).fetchone()

    conn.close()
    return row



# ============================================================
# ADMIN SECURITY / PAYMENT ALERTS
# ============================================================

def admin_alert(text):
    """
    Admin'ga xavfsiz alert yuboradi.
    Bot ishlashiga xalaqit bermasligi uchun exception yutiladi.
    """
    try:
        if not ADMIN_ID:
            return False

        bot.send_message(
            ADMIN_ID,
            text,
            parse_mode="HTML"
        )
        return True

    except Exception as e:
        print("⚠️ ADMIN ALERT XATOSI:", repr(e))
        return False


def get_user_display(user_id):
    """
    User haqida maxfiy bo'lmagan qisqa ma'lumot.
    """
    try:
        conn = db()

        try:
            row = conn.execute(
                """
                SELECT user_id, username, first_name
                FROM users
                WHERE user_id = ?
                LIMIT 1
                """,
                (int(user_id),)
            ).fetchone()

        finally:
            conn.close()

        if not row:
            return (
                f"🆔 ID: <code>{int(user_id)}</code>\n"
                "👤 Username: noma'lum"
            )

        username = (
            f"@{str(row['username']).lstrip('@')}"
            if row["username"]
            else "noma'lum"
        )

        first_name = str(
            row["first_name"] or "Noma'lum"
        )

        import html

        return (
            f"🆔 ID: <code>{int(row['user_id'])}</code>\n"
            f"👤 Username: <b>{html.escape(username)}</b>\n"
            f"📝 Ism: <b>{html.escape(first_name)}</b>"
        )

    except Exception as e:
        print("⚠️ USER DISPLAY XATOSI:", repr(e))

        return (
            f"🆔 ID: <code>{int(user_id)}</code>\n"
            "👤 Ma'lumot olinmadi"
        )


def admin_balance_alert(
    user_id,
    amount,
    new_balance,
    stars,
    charge_id
):
    """
    Muvaffaqiyatli balans to'ldirilganda admin'ga alert.
    API key yoki boshqa secret yuborilmaydi.
    """

    try:
        user_text = get_user_display(user_id)

        charge_short = str(
            charge_id or "noma'lum"
        )

        # To'lov identifikatorini to'liq oshkor qilmaslik.
        if len(charge_short) > 12:
            charge_short = (
                charge_short[:6]
                + "..."
                + charge_short[-4:]
            )

        text = (
            "💰 <b>BALANS TO'LDIRILDI</b>\n\n"
            f"{user_text}\n\n"
            f"⭐ Stars: <b>{int(stars):,}</b>\n"
            f"💵 Qo'shilgan: <b>{float(amount):,.2f}</b> so'm\n"
            f"💰 Yangi balans: <b>{float(new_balance):,.2f}</b> so'm\n"
            f"🧾 To'lov ID: <code>{charge_short}</code>\n"
            f"🕐 Vaqt: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        )

        admin_alert(text)

    except Exception as e:
        print(
            "⚠️ BALANCE ADMIN ALERT XATOSI:",
            repr(e)
        )


def admin_suspicious_activity_alert(
    sender,
    channel_title,
    channel_username,
    message_id,
    indicators
):
    """
    Shubhali faollikni faqat indikator sifatida admin'ga bildiradi.
    Bu funksiya akkauntni scam/spam deb yakuniy baholamaydi.
    """

    try:
        if not sender:
            return

        flags = [
            str(x).strip()
            for x in (indicators or [])
            if str(x).strip()
        ]

        if not flags:
            return

        import html

        username = getattr(
            sender,
            "username",
            None
        )

        sender_id = getattr(
            sender,
            "id",
            None
        )

        sender_id_text = (
            str(sender_id)
            if sender_id is not None
            else "noma'lum"
        )

        first_name = (
            getattr(sender, "first_name", None)
            or ""
        )

        last_name = (
            getattr(sender, "last_name", None)
            or ""
        )

        full_name = (
            f"{first_name} {last_name}"
        ).strip() or "Noma'lum"

        if username:
            account = (
                f"@{str(username).lstrip('@')}"
            )
        else:
            account = "username yo'q"

        channel_text = (
            f"@{str(channel_username).lstrip('@')}"
            if channel_username
            else channel_title or "Noma'lum kanal"
        )

        lines = "\n".join(
            f"• {html.escape(flag)}"
            for flag in flags[:8]
        )

        text = (
            "⚠️ <b>SHUBHALI FAOLLIK INDIKATORI</b>\n\n"
            f"👤 Akkaunt: <b>{html.escape(account)}</b>\n"
            f"🆔 User ID: <code>{sender_id_text}</code>\n"
            f"📝 Ism: <b>{html.escape(full_name)}</b>\n\n"
            f"📢 Kanal: <b>{html.escape(channel_text)}</b>\n"
            f"🆔 Post ID: <code>{int(message_id)}</code>\n\n"
            "🔎 <b>Indikatorlar:</b>\n"
            f"{lines}\n\n"
            "ℹ️ Bu avtomatik indikatorlar xolos; "
            "akkaunt spam/firibgar ekanini yakuniy tasdiqlamaydi."
        )

        admin_alert(text)

    except Exception as e:
        print(
            "⚠️ SUSPICIOUS ALERT XATOSI:",
            repr(e)
        )



def get_balance(user_id):
    conn = db()

    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    return float(row["balance"] if row else 0)


# ============================================================
# FSM
# ============================================================

def set_state(user_id, state, **data):
    user_state[user_id] = {
        "state": state,
        **data
    }


def get_state(user_id):
    return user_state.get(user_id, {})


def clear_state(user_id):
    user_state.pop(user_id, None)


# ============================================================
# PASTKI DOIMIY REPLY MENYU
# ============================================================

def main_reply_keyboard(user_id):
    """
    Barcha foydalanuvchilar uchun qulay asosiy ReplyKeyboard.
    Admin panel faqat @xua202 (ADMIN_ID) uchun ko'rinadi.
    """
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
    )

    # 1-qator — hamma uchun
    kb.row(
        "📦 Avto buyurtma ulash"
    )

    # 2-qator
    kb.row(
        "💵 Pul kiritish",
        "👤 Kabinet"
    )

    # 3-qator
    kb.row(
        "🆘 Support",
        "📕 Qo'llanma"
    )

    # Faqat @xua202 uchun admin panel tugmasi
    if user_id == ADMIN_ID or user_id == 8191930658:
        kb.row("⚙️ Admin panel")

    return kb


# ============================================================
# INLINE ASOSIY MENYU
# ============================================================

def main_inline_keyboard(user_id):
    return types.InlineKeyboardMarkup()

# ============================================================
# START CONFIG DB & FILE MANAGER
# ============================================================

def get_start_config():
    """Bazada va faylda saqlangan /start konfiguratsiyasini yuklaydi."""
    conn = db()
    try:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'start_config'"
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                pass
    except Exception as e:
        log_debug(f"⚠️ get_start_config db error: {e}")
    finally:
        conn.close()

    start_cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_config.json")
    if os.path.exists(start_cfg_file):
        try:
            with open(start_cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_start_config(cfg):
    """Start konfiguratsiyasini ham DB ga, ham start_config.json fayliga saqlaydi."""
    cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
    conn = db()
    try:
        conn.execute(
            """
            INSERT INTO system_settings (key, value)
            VALUES ('start_config', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (cfg_json,)
        )
        conn.commit()
    except Exception as e:
        log_debug(f"⚠️ save_start_config db error: {e}")
    finally:
        conn.close()

    try:
        start_cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_config.json")
        with open(start_cfg_file, "w", encoding="utf-8") as f:
            f.write(cfg_json)
    except Exception as e:
        log_debug(f"⚠️ save_start_config file error: {e}")

def delete_start_config():
    """Start konfiguratsiyasini DB va fayldan tozalaydi."""
    conn = db()
    try:
        conn.execute("DELETE FROM system_settings WHERE key = 'start_config'")
        conn.commit()
    except Exception as e:
        log_debug(f"⚠️ delete_start_config db error: {e}")
    finally:
        conn.close()

    start_cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_config.json")
    if os.path.exists(start_cfg_file):
        try:
            os.remove(start_cfg_file)
        except Exception:
            pass

def send_start_screen(chat_id, user=None):
    from datetime import datetime
    import html
    import json

    first_name = "Do'stim"
    user_id = 0
    username_str = ""
    if user:
        if hasattr(user, "from_user") and user.from_user and getattr(user.from_user, "first_name", None):
            first_name = user.from_user.first_name
            user_id = user.from_user.id
            username_str = getattr(user.from_user, "username", "") or ""
        elif hasattr(user, "first_name") and user.first_name:
            first_name = user.first_name
            user_id = getattr(user, "id", 0)
            username_str = getattr(user, "username", "") or ""

    first_name = html.escape(first_name or "Do'stim")

    try:
        bot_info = bot.get_me()
        bot_username = bot_info.username or "bot"
    except Exception:
        bot_username = "bot"

    from datetime import timezone, timedelta
    tz_uz = timezone(timedelta(hours=5))
    now = datetime.now(tz_uz).strftime("%Y-%m-%d %H:%M:%S")

    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
    )

    keyboard.row("📦 Avto buyurtma ulash")
    keyboard.row("💵 Pul kiritish", "👤 Kabinet")
    keyboard.row("🆘 Support", "📕 Qo'llanma")

    if is_admin(user) or user_id == ADMIN_ID or user_id == 8191930658:
        keyboard.row("⚙️ Admin panel")

    cfg = get_start_config()
    if cfg:
        try:
            def _format_vars(s):
                if not s:
                    return ""
                formatted = (
                    s.replace("{first_name}", first_name)
                    .replace("{name}", first_name)
                    .replace("{username}", f"@{username_str}" if username_str else first_name)
                    .replace("{user_id}", str(user_id))
                    .replace("{id}", str(user_id))
                    .replace("{bot_username}", f"@{bot_username}")
                    .replace("{now}", str(now))
                )
                formatted = re.sub(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", str(now), formatted)
                formatted = re.sub(r"@[A-Za-z0-9_]+bot", f"@{bot_username}", formatted, flags=re.IGNORECASE)
                formatted = re.sub(r"(👋?\s*Salom!?,?\s*(?:<b>)?)[^<@\n]+((?:<\/b>)?\s*(?:<b>)?@)", rf"\g<1>{first_name} \g<2>", formatted)
                return formatted

            m_type = cfg.get("type")
            if m_type == "photo" and cfg.get("file_id"):
                caption = _format_vars(cfg.get("caption", ""))
                return bot.send_photo(
                    chat_id,
                    photo=cfg["file_id"],
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    reply_markup=keyboard
                )
            elif m_type == "video" and cfg.get("file_id"):
                caption = _format_vars(cfg.get("caption", ""))
                return bot.send_video(
                    chat_id,
                    video=cfg["file_id"],
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    reply_markup=keyboard
                )
            elif m_type == "animation" and cfg.get("file_id"):
                caption = _format_vars(cfg.get("caption", ""))
                return bot.send_animation(
                    chat_id,
                    animation=cfg["file_id"],
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    reply_markup=keyboard
                )
            elif m_type == "text" and cfg.get("text"):
                body = _format_vars(cfg.get("text", ""))
                return bot.send_message(
                    chat_id,
                    body,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
        except Exception as e:
            print("❌ Custom start screen error:", e)

    default_text = (
        f"👋 Salom! <b>{first_name}</b> "
        f"<a href=\"https://t.me/{bot_username}\">@{bot_username}</a> "
        "ga xush kelibsiz!\n\n"
        "<blockquote>📈 Ushbu bot orqali kanallaringizga avto buyurtmalar ulashingiz "
        "va SMM xizmatlaridan qulay foydalanishingiz mumkin.</blockquote>\n\n"
        "<i>📌 Botdan foydalanish uchun quyidagi menyulardan birini tanlang.</i>\n\n"
        "🙌 Bizni tanlaganingizdan xursandmiz!\n"
        f"{now}"
    )

    return bot.send_message(
        chat_id,
        default_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


def main_menu_text(message=None):
    import html
    from datetime import datetime

    first_name = "Do'stim"
    if message:
        if hasattr(message, "from_user") and message.from_user and getattr(message.from_user, "first_name", None):
            first_name = message.from_user.first_name
        elif hasattr(message, "first_name") and message.first_name:
            first_name = message.first_name

    first_name = html.escape(first_name or "Do'stim")

    try:
        bot_info = bot.get_me()
        bot_username = bot_info.username or "bot"
    except Exception:
        bot_username = "bot"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return (
        f'👋 Salom! <b>{first_name}</b> '
        f'<a href="https://t.me/{bot_username}">@{bot_username}</a> '
        "ga xush kelibsiz!\n\n"
        "<blockquote>📈 Bot sizning buyurtmalaringizni avtomatlashtirishga yordam beradi.</blockquote>\n\n"
        "<i>📌 Botdan foydalanishni tushunmasangiz Qo'llanma tugmasini bosing.</i>\n\n"
        "🙌 Bizni tanlaganingizdan xursandmiz!\n"
        f"{now}"
    )



def send_main_menu(chat_id, user_id):
    try:
        user = bot.get_chat(user_id)
    except Exception:
        user = None
    send_start_screen(chat_id, user)


# ============================================================
# /START
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    clear_state(message.from_user.id)
    send_start_screen(message.chat.id, message.from_user)


@bot.message_handler(
    func=lambda m: m.text in ["🏠 Asosiy menyu", "🏠 Bosh menyu"]
)
def reply_home(message):
    clear_state(message.from_user.id)
    send_start_screen(message.chat.id, message.from_user)


@bot.message_handler(
    func=lambda m: m.text == "📦 Buyurtma berish"
)
def reply_order(message):
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "⚠️ <b>Ushbu bo'lim olib tashlangan.</b>",
        parse_mode="HTML",
        reply_markup=main_reply_keyboard(message.from_user.id)
    )


@bot.message_handler(
    func=lambda m: m.text == "💰 Balans"
)
def reply_balance(message):
    clear_state(message.from_user.id)

    show_balance(
        message.chat.id,
        message.from_user.id
    )


@bot.message_handler(
    func=lambda m: m.text == "📦 Buyurtmalarim"
)
def reply_orders(message):
    clear_state(message.from_user.id)

    show_orders(
        message.chat.id,
        message.from_user.id
    )


@bot.message_handler(
    func=lambda m: m.text == "👤 Kabinet"
)
def reply_cabinet(message):
    clear_state(message.from_user.id)

    show_cabinet(
        message.chat.id,
        message.from_user.id
    )


@bot.message_handler(
    func=lambda m: m.text == "🆘 Support"
)
def reply_support(message):
    clear_state(message.from_user.id)

    show_support(
        message.chat.id
    )


@bot.message_handler(
    func=lambda m: m.text == "📘 Qo'llanma"
)
def reply_guide(message):
    clear_state(message.from_user.id)

    show_guide(
        message.chat.id
    )


@bot.message_handler(func=lambda m: m.text == "⚙️ Admin panel")
def reply_admin_panel_button(message):
    if not is_admin(message.from_user):
        bot.send_message(message.chat.id, "❌ Sizda admin paneliga kirish uchun ruxsat yo'q.")
        return
    admin_command(message)


@bot.message_handler(
    func=lambda m: m.text == "📦 Avto buyurtma ulash"
)
def reply_auto(message):
    clear_state(message.from_user.id)

    send_auto_menu(
        message.chat.id
    )


# ============================================================
# ORDER — INLINE
# ============================================================

def order_keyboard():
    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "📂 Kategoriyalar",
            callback_data="order_categories"
        )
    )


    return kb


def send_order_menu(chat_id):
    bot.send_message(
        chat_id,
        "📦 <b>Buyurtma berish</b>\n\n"
        "👇 Kerakli bo'limni tanlang:",
        reply_markup=order_keyboard()
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "order_menu"
)
def order_menu_callback(call):
    bot.answer_callback_query(call.id)

    bot.edit_message_text(
        "📦 <b>Buyurtma berish</b>\n\n"
        "👇 Kerakli bo'limni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=order_keyboard()
    )


# ============================================================
# KATEGORIYALAR — REAL DB
# ============================================================

CATEGORY_PER_PAGE = 5
SERVICE_PER_PAGE = 10


def get_db_categories():
    """Barcha faol Telegram xizmat kategoriyalari (Instagram, TikTok, YouTube filtrlanadi)."""

    conn = db()

    try:
        rows = conn.execute(f"""
            SELECT category, COUNT(*) AS total
            FROM services
            WHERE active = 1
              AND category IS NOT NULL
              AND TRIM(category) != ''
              AND NOT {EXCLUDED_PLATFORMS_SQL}
            GROUP BY category
            ORDER BY
                CASE
                    WHEN LOWER(category) LIKE '%tekin%' OR LOWER(category) LIKE '%bonus%' THEN 0
                    WHEN LOWER(category) LIKE '%telegram%' THEN 1
                    ELSE 2
                END,
                MIN(sort_order),
                category
        """).fetchall()

        return [
            {
                "name": row["category"],
                "total": int(row["total"] or 0)
            }
            for row in rows
        ]

    finally:
        conn.close()


def categories_keyboard(page=0):
    categories = get_db_categories()

    total = len(categories)
    pages = max(1, math.ceil(total / CATEGORY_PER_PAGE))

    page = max(0, min(page, pages - 1))

    start = page * CATEGORY_PER_PAGE
    end = start + CATEGORY_PER_PAGE

    kb = types.InlineKeyboardMarkup(row_width=1)

    for index in range(start, min(end, total)):
        category = categories[index]

        kb.add(
            types.InlineKeyboardButton(
                f"📂 {category['name']} ({category['total']})",
                callback_data=f"category:{index}"
            )
        )

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=f"catpage:{page - 1}"
            )
        )

    nav.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{pages}",
            callback_data="noop"
        )
    )

    if page < pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=f"catpage:{page + 1}"
            )
        )

    kb.row(*nav)


    return kb


def render_categories(call, page=0):
    categories = get_db_categories()

    total = len(categories)
    pages = max(1, math.ceil(total / CATEGORY_PER_PAGE))

    page = max(0, min(page, pages - 1))

    bot.edit_message_text(
        "📦 <b>Buyurtma berish</b>\n\n"
        f"📊 Jami: <b>{total}</b> ta kategoriya\n"
        f"📄 Sahifa: <b>{page + 1}/{pages}</b>\n\n"
        "👇 Kerakli kategoriyani tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=categories_keyboard(page)
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "order_categories"
)
def order_categories_callback(call):
    bot.answer_callback_query(call.id)
    render_categories(call, 0)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("catpage:")
)
def category_page_callback(call):
    bot.answer_callback_query(call.id)

    try:
        page = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        page = 0

    render_categories(call, page)


# ============================================================
# KATEGORIYA → REAL XIZMATLAR
# ============================================================

def get_category_by_index(index):
    categories = get_db_categories()

    if index < 0 or index >= len(categories):
        return None

    return categories[index]["name"]


def get_services_by_category(category, page=0):
    conn = db()

    try:
        offset = page * SERVICE_PER_PAGE

        rows = conn.execute(f"""
            SELECT
                id,
                name,
                category,
                price,
                min_qty,
                max_qty
            FROM services
            WHERE active = 1
              AND category = ?
              AND NOT {EXCLUDED_PLATFORMS_SQL}
            ORDER BY
                COALESCE(price, 0) ASC,
                sort_order ASC,
                id ASC
            LIMIT ? OFFSET ?
        """, (
            category,
            SERVICE_PER_PAGE,
            offset
        )).fetchall()

        count_row = conn.execute(f"""
            SELECT COUNT(*) AS total
            FROM services
            WHERE active = 1
              AND category = ?
              AND NOT {EXCLUDED_PLATFORMS_SQL}
        """, (category,)).fetchone()

        total = int(count_row["total"] or 0)

        return rows, total

    finally:
        conn.close()


def services_keyboard(category, page=0):
    rows, total = get_services_by_category(
        category,
        page
    )

    pages = max(1, math.ceil(total / SERVICE_PER_PAGE))

    page = max(0, min(page, pages - 1))

    kb = types.InlineKeyboardMarkup(row_width=1)

    for row in rows:
        kb.add(
            types.InlineKeyboardButton(
                f"🛠 {row['name']}",
                callback_data=f"service:{row['id']}"
            )
        )

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=f"servicepage:{page - 1}"
            )
        )

    nav.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{pages}",
            callback_data="noop"
        )
    )

    if page < pages - 1:
        nav.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=f"servicepage:{page + 1}"
            )
        )

    kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="order_categories"
        )
    )

    return kb


def render_services(call, category, page=0):
    rows, total = get_services_by_category(
        category,
        page
    )

    pages = max(1, math.ceil(total / SERVICE_PER_PAGE))
    page = max(0, min(page, pages - 1))

    bot.edit_message_text(
        f"📂 <b>{category}</b>\n\n"
        f"🛠 Jami: <b>{total}</b> ta xizmat\n"
        f"📄 Sahifa: <b>{page + 1}/{pages}</b>\n\n"
        "👇 Kerakli xizmatni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=services_keyboard(
            category,
            page
        )
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("category:")
)
def category_callback(call):
    bot.answer_callback_query(call.id)

    try:
        index = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return

    category = get_category_by_index(index)

    if not category:
        bot.answer_callback_query(
            call.id,
            "❌ Kategoriya topilmadi!",
            show_alert=True
        )
        return

    render_services(
        call,
        category,
        0
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("servicepage:")
)
def service_page_callback(call):
    bot.answer_callback_query(call.id)

    try:
        page = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return

    # Hozirgi kategoriya xabar matnidan olinadi.
    text = call.message.text or ""

    category = None

    if text.startswith("📂 "):
        category = text.split("\n", 1)[0][3:].strip()

    if not category:
        bot.answer_callback_query(
            call.id,
            "❌ Kategoriyani aniqlab bo'lmadi!",
            show_alert=True
        )
        return

    render_services(
        call,
        category,
        page
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("service:")
)
def service_callback(call):
    bot.answer_callback_query(call.id)

    try:
        service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        return

    conn = db()

    try:
        row = conn.execute("""
            SELECT
                id,
                name,
                category,
                price,
                min_qty,
                max_qty
            FROM services
            WHERE id = ?
              AND active = 1
        """, (service_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi yoki faol emas!",
            show_alert=True
        )
        return

    price = float(row["price"] or 0)
    min_qty = int(row["min_qty"] or 0)
    max_qty = int(row["max_qty"] or 0)

    bot.edit_message_text(
        "🛠 <b>Xizmat ma'lumotlari</b>\n\n"
        f"📌 <b>{row['name']}</b>\n\n"
        f"📂 Kategoriya: <b>{row['category']}</b>\n"
        f"💰 Narx: <b>{price:,.2f}</b> so'm\n"
        f"🔢 Minimal: <b>{min_qty:,}</b>\n"
        f"🔢 Maksimal: <b>{max_qty:,}</b>\n\n"
        "⚠️ Buyurtma berish bosqichi keyingi qismda ulanadi.",
        call.message.chat.id,
        call.message.message_id
    )

# ============================================================
# AUTO ORDER — REPLY KEYBOARD
# ============================================================

def auto_order_reply_keyboard():
    """
    Auto Order bo'limining pastki doimiy menyusi.
    Bu yerda InlineKeyboard emas, ReplyKeyboard ishlatiladi.
    """

    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
    )

    # 1-qator
    kb.row(
        "➕ Yangi kanal",
        "📌 Kanallarim"
    )

    # 2-qator
    kb.row("⬅️ Orqaga")

    return kb


def auto_keyboard():
    """Auto Order bo'limi uchun inline klaviatura."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "➕ Yangi kanal",
            callback_data="auto_add"
        ),
        types.InlineKeyboardButton(
            "📌 Kanallarim",
            callback_data="auto_channels"
        ),
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="home_menu"
        )
    )
    return kb


def send_auto_menu(chat_id):
    """
    Auto Order asosiy menyusini ochadi.
    Faqat qisqa matn va pastki ReplyKeyboard chiqadi.
    """

    bot.send_message(
        chat_id,
        "📌 Kanal uchun avto buyurtma bo'limidasiz.",
        reply_markup=auto_order_reply_keyboard()
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "auto_menu"
)
def auto_menu_callback(call):
    """
    Asosiy menyudagi Inline '📦 Avto buyurtma ulash'
    tugmasidan kelganda Auto Order ReplyKeyboard menyusini ochadi.
    """

    # Auto order ochiq hamma uchun

    bot.answer_callback_query(call.id)

    send_auto_menu(call.message.chat.id)


# ============================================================
# AUTO ORDER — REPLY KEYBOARD TUGMALARI
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "📦 Avto buyurtma ulash"
)
def auto_order_reply_entry(message):
    """
    Pastki asosiy menyudagi '📦 Avto buyurtma ulash'
    tugmasidan Auto Order bo'limiga kirish.
    """

    # Auto order ochiq hamma uchun

    send_auto_menu(message.chat.id)


@bot.message_handler(
    func=lambda message: message.text == "➕ Yangi kanal"
)
def auto_add_reply(message):
    """
    ReplyKeyboard'dagi "➕ Yangi kanal" tugmasi.
    Mavjud kanal ulash jarayonini boshlaydi.
    """

    set_state(
        message.from_user.id,
        "waiting_channel"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data="auto_cancel"
        )
    )

    bot.send_message(
        message.chat.id,
        "➕ <b>Yangi kanal ulash</b>\n\n"
        "📌 Ommaviy kanalingizdan istalgan postni shu yerga <b>forward</b> qiling yoki kanal username/havolasini (<code>@kanal</code>) yuboring.\n\n"
        "⚠️ <b>Diqqat:</b> Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi. Maxfiy (yopiq) kanallar qabul qilinmaydi!",
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.message_handler(
    func=lambda message: message.text == "📌 Kanallarim"
)
def auto_channels_reply(message):
    """
    Ulangan kanallarni Inline tugmalar ko'rinishida chiqaradi.
    """

    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT id, username, title, active
            FROM channels
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (message.from_user.id,)
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup(row_width=1)

    for row in rows:
        username = row["username"]

        if username:
            channel_text = f"📡 @{str(username).lstrip('@')}"
        else:
            channel_text = f"📡 {row['title'] or 'Nomaʼlum kanal'}"

        kb.add(
            types.InlineKeyboardButton(
                channel_text,
                callback_data=f"auto_channel:{row['id']}"
            )
        )

    text = (
        "📋 <b>Avtomatik buyurtmalar uchun ulangan kanallar "
        "ro'yxati</b>"
    )

    if not rows:
        text += "\n\n📭 Hozircha hech qanday kanal ulanmagan."

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.message_handler(
    func=lambda message: message.text in [
        "⬅️ Orqaga", "🔙 Orqaga", "↩️ Orqaga", "Orqaga", "orqaga",
        "⬅️ orqaga", "🔙 orqaga", "↩️ orqaga"
    ]
)
def auto_order_reply_back(message):
    """
    Auto Order va barcha menyulardagi 'Orqaga'
    tugmasi /start kabi to'liq bosh menyuga qaytaradi.
    """
    clear_state(message.from_user.id)
    send_start_screen(message.chat.id, message.from_user)




# ============================================================
# AUTO — YANGI KANAL
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_add"
)
def auto_add_callback(call):
    bot.answer_callback_query(call.id)

    set_state(
        call.from_user.id,
        "waiting_channel"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data="auto_cancel"
        )
    )

    bot.edit_message_text(
        "➕ <b>Yangi kanal ulash</b>\n\n"
        "📌 Ommaviy kanalingizdan istalgan postni shu yerga <b>forward</b> qiling yoki kanal username/havolasini (<code>@kanal</code>) yuboring.\n\n"
        "⚠️ <b>Diqqat:</b> Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi. Maxfiy (yopiq) kanallar qabul qilinmaydi!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# ADMIN FORWARD ORQALI /START XABARINI SOZLASH
# ============================================================

def is_forwarded_message(message):
    return bool(
        getattr(message, "forward_date", None)
        or getattr(message, "forward_from_chat", None)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_sender_name", None)
        or getattr(message, "forward_origin", None)
    )

@bot.message_handler(commands=["setstart"])
def admin_set_start_cmd(message):
    if not is_admin(message.from_user):
        return
    set_state(message.from_user.id, "waiting_setstart")
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data="cancel_setstart"
        )
    )
    bot.reply_to(
        message,
        "📌 <b>Yangi /start xabarini o'rnatish:</b>\n\n"
        "Istalgan kanal yoki chatdan xabarni (matn, rasm yoki video) <b>FORWARD</b> qilib yuboring "
        "yoki to'g'ridan-to'g'ri yangi matn/rasm/video yuboring!\n"
        "Bot uni darhol bazaga doimiy saqlab, yangi /start xabari qilib sozlaydi. 🚀",
        parse_mode="HTML",
        reply_markup=kb
    )

@bot.message_handler(commands=["resetstart"])
def admin_reset_start_cmd(message):
    if not is_admin(message.from_user):
        return
    clear_state(message.from_user.id)
    delete_start_config()
    bot.reply_to(
        message,
        "🔄 <b>/start xabari standart holatiga qaytarildi!</b>",
        parse_mode="HTML"
    )
    send_start_screen(message.chat.id, message.from_user)

@bot.message_handler(
    content_types=["text", "photo", "video", "animation"],
    func=lambda message: (
        is_admin(message.from_user)
        and get_state(message.from_user.id).get("state") == "waiting_setstart"
    )
)
def handle_admin_start_forward(message):
    clear_state(message.from_user.id)

    cfg = {}
    if message.photo:
        file_id = message.photo[-1].file_id
        caption = getattr(message, "html_caption", None) or message.caption or ""
        cfg = {"type": "photo", "file_id": file_id, "caption": caption}
    elif message.video:
        file_id = message.video.file_id
        caption = getattr(message, "html_caption", None) or message.caption or ""
        cfg = {"type": "video", "file_id": file_id, "caption": caption}
    elif message.animation:
        file_id = message.animation.file_id
        caption = getattr(message, "html_caption", None) or message.caption or ""
        cfg = {"type": "animation", "file_id": file_id, "caption": caption}
    else:
        text = getattr(message, "html_text", None) or message.text or ""
        cfg = {"type": "text", "text": text}

    try:
        save_start_config(cfg)

        bot.reply_to(
            message,
            "✅ <b>Qabul qilindi! /start xabari bazaga muvaffaqiyatli saqlandi!</b>\n\n"
            "👇 <i>Quyida yangi /start ko'rinishi namoyish etilmoqda:</i>",
            parse_mode="HTML"
        )
        send_start_screen(message.chat.id, message.from_user)
    except Exception as e:
        bot.reply_to(message, f"❌ Xatolik yuz berdi: {e}")



# ============================================================
# FORWARD QABUL QILISH
# ============================================================

@bot.message_handler(
    content_types=["text", "photo", "video", "document"],
    func=lambda message: (
        get_state(message.from_user.id).get("state") == "waiting_channel"
        or (getattr(message, "forward_from_chat", None) is not None and getattr(message.forward_from_chat, "type", None) == "channel")
        or (hasattr(message, "forward_origin") and getattr(getattr(message, "forward_origin", None), "type", None) == "channel")
        or bool(message.text and extract_invite_hash(message.text))
    )
)
def receive_channel(message):

    forwarded_chat = getattr(
        message,
        "forward_from_chat",
        None
    )

    if not forwarded_chat and hasattr(message, "forward_origin"):
        origin = getattr(message, "forward_origin", None)
        if getattr(origin, "type", None) == "channel" and getattr(origin, "chat", None):
            forwarded_chat = origin.chat

    channel_id = None
    channel_title = "Noma'lum"
    username = None

    if forwarded_chat:
        if forwarded_chat.type != "channel":
            bot.reply_to(
                message,
                "❌ Faqat ommaviy kanal postini forward qiling yoki kanal usernamesini (@kanal) yuboring."
            )
            return

        username = getattr(forwarded_chat, "username", None)
        if not username:
            bot.reply_to(
                message,
                "❌ <b>Maxfiy (yopiq) kanallar qabul qilinmaydi!</b>\n\n"
                "Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi.\n\n"
                "📌 Kanal sozlamalaridan ommaviy havola (masalan: <code>@kanal</code>) o'rnating va postni qayta forward qiling."
            )
            return

        channel_id = forwarded_chat.id
        channel_title = forwarded_chat.title or f"@{username}"

    elif message.text:
        txt = message.text.strip()

        # 1. Maxfiy kanal taklif havolasi (https://t.me/+... yoki https://t.me/joinchat/...) rad etiladi
        invite_hash = extract_invite_hash(txt)
        if invite_hash:
            bot.reply_to(
                message,
                "❌ <b>Maxfiy (yopiq) kanallar qabul qilinmaydi!</b>\n\n"
                "Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi.\n\n"
                "📌 Kanal sozlamalaridan ommaviy havola (masalan: <code>@kanal</code>) o'rnating va kanal usernamesini yuboring."
            )
            return

        # 2. Public kanal username (@kanal yoki t.me/kanal)
        m_u = re.search(r"(?:t\.me/|@)([a-zA-Z0-9_]{4,})", txt)
        if m_u and m_u.group(1).lower() != "joinchat":
            raw_u = m_u.group(1)
            try:
                ch_obj = bot.get_chat(f"@{raw_u}")
                if getattr(ch_obj, "type", "") != "channel":
                    bot.reply_to(message, "❌ Ko'rsatilgan havola kanal emas!")
                    return
                channel_id = ch_obj.id
                channel_title = ch_obj.title or f"@{raw_u}"
                username = ch_obj.username or raw_u
            except Exception:
                # Agar bot get_chat qila olmasa, userbot orqali entity qidiramiz
                if userbot and MAIN_LOOP:
                    try:
                        async def _res():
                            ent = await userbot.get_entity(raw_u)
                            return ent.id, getattr(ent, "title", None), getattr(ent, "username", None)
                        fut = asyncio.run_coroutine_threadsafe(_res(), MAIN_LOOP)
                        r_id, r_title, r_un = fut.result(timeout=4)
                        if r_id:
                            channel_id = -int(f"100{clean_channel_id(r_id)}")
                            channel_title = r_title or f"@{raw_u}"
                            username = r_un or raw_u
                    except Exception:
                        pass
                if not username:
                    username = raw_u
                    channel_id = f"@{raw_u}"
                    channel_title = f"@{raw_u}"

        elif txt.startswith("-100") or txt.isdigit():
            try:
                ch_obj = bot.get_chat(int(txt))
                if not getattr(ch_obj, "username", None):
                    bot.reply_to(
                        message,
                        "❌ <b>Maxfiy (yopiq) kanallar qabul qilinmaydi!</b>\n\n"
                        "Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi.\n\n"
                        "📌 Kanal sozlamalaridan ommaviy havola (masalan: <code>@kanal</code>) o'rnating."
                    )
                    return
                channel_id = ch_obj.id
                channel_title = ch_obj.title or str(channel_id)
                username = ch_obj.username
            except Exception:
                bot.reply_to(
                    message,
                    "❌ <b>Maxfiy (yopiq) kanallar qabul qilinmaydi!</b>\n\n"
                    "Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi.\n\n"
                    "📌 Kanal usernamesini (masalan: <code>@kanal</code>) yuboring."
                )
                return

    # Faqat ommaviy kanal (username bo'lishi shart)
    if not username:
        bot.reply_to(
            message,
            "❌ <b>Maxfiy (yopiq) kanallar qabul qilinmaydi!</b>\n\n"
            "Bot faqat <b>ommaviy (ochiq)</b> kanallar bilan ishlaydi.\n\n"
            "📌 Ommaviy kanal postini <b>forward</b> qiling yoki kanal usernamesini (<code>@kanal</code>) yuboring."
        )
        return

    # --------------------------------------------------------
    # KANAL ADMINLIGINI TEKSHIRISH
    # --------------------------------------------------------

    if channel_id and not is_admin(message.from_user):
        try:
            member = bot.get_chat_member(
                channel_id,
                message.from_user.id
            )

            if member.status not in ("administrator", "creator"):
                bot.reply_to(
                    message,
                    "❌ <b>Siz bu kanal administratori emassiz.</b>\n\n"
                    "Avto buyurtma ulash uchun kanal egasi yoki "
                    "administrator bo'lishingiz kerak."
                )
                return

        except Exception as e:
            print(
                f"ℹ️ Kanal adminligi bot orqali tekshirilmadi: {e}"
            )

    # Agar channel_id hali aniqlanmagan bo'lsa, userbot orqali aniqlashga urinamiz
    if (not channel_id or str(channel_id).startswith("@")) and username and userbot and MAIN_LOOP:
        try:
            async def _resolve_cid():
                ent = await userbot.get_entity(username)
                return ent.id, getattr(ent, "title", None)
            fut = asyncio.run_coroutine_threadsafe(_resolve_cid(), MAIN_LOOP)
            res_id, res_title = fut.result(timeout=4)
            if res_id:
                channel_id = -int(f"100{clean_channel_id(res_id)}")
                if res_title:
                    channel_title = res_title
        except Exception as e:
            print(f"ℹ️ Userbot orqali channel_id aniqlash xatosi: {e}")

    # Agar hali ham channel_id bo'lmasa, vaqtinchalik belgilaymiz
    if not channel_id:
        channel_id = f"@{username}"

    # --------------------------------------------------------
    # KANALNI DB GA SAQLASH
    # --------------------------------------------------------

    conn = db()

    try:
        clean_u = str(username).strip().lstrip("@")
        existing = conn.execute(
            """
            SELECT id
            FROM channels
            WHERE user_id = ?
              AND (channel_id = ? OR (username != '' AND username = ?))
            """,
            (
                message.from_user.id,
                str(channel_id),
                clean_u
            )
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE channels
                SET username = ?,
                    title = ?,
                    channel_id = ?,
                    active = 1
                WHERE id = ?
                """,
                (
                    clean_u,
                    channel_title,
                    str(channel_id),
                    existing["id"]
                )
            )

            channel_db_id = existing["id"]

        else:
            cursor = conn.execute(
                """
                INSERT INTO channels
                (
                    user_id,
                    channel_id,
                    username,
                    title,
                    active,
                    created_at
                )
                VALUES (?, ?, ?, ?, 1, datetime('now'))
                """,
                (
                    message.from_user.id,
                    str(channel_id),
                    clean_u,
                    channel_title
                )
            )

            channel_db_id = cursor.lastrowid

        conn.commit()

        # Userbotni darhol yangi ommaviy kanalga qo'shamiz
        join_userbot_to_channel(channel_id, clean_u)

    except Exception as e:
        conn.rollback()

        print(
            f"❌ Kanalni DB ga saqlash xatosi: {e}"
        )

        bot.reply_to(
            message,
            "❌ Kanalni saqlashda xatolik yuz berdi."
        )

        return

    finally:
        conn.close()

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    set_state(
        message.from_user.id,
        "channel_received",
        channel_id=channel_id,
        channel_db_id=channel_db_id,
        username=clean_u,
        title=channel_title
    )

    # --------------------------------------------------------
    # KANAL SOZLAMALARI
    # --------------------------------------------------------

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📋 Ro'yxat",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Kanalni o'chirish",
            callback_data="auto_delete"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_menu"
        )
    )

    bot.send_message(
        message.chat.id,
        "✅ <b>Ommaviy kanal muvaffaqiyatli ulandi!</b>\n\n"
        f"📌 <b>{channel_title}</b>\n"
        f"🔗 @{clean_u}\n"
        f"🆔 <code>{channel_id}</code>\n\n"
        "👇 Kanal uchun kerakli amalni tanlang:",
        reply_markup=kb
    )


# ============================================================
# AUTO — KANALLARIM
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_channels"
)
def auto_channels_callback(call):

    bot.answer_callback_query(call.id)

    user_id = call.from_user.id

    conn = db()

    try:
        channels = conn.execute(
            """
            SELECT
                id,
                channel_id,
                username,
                title,
                active
            FROM channels
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,)
        ).fetchall()

    finally:
        conn.close()

    # --------------------------------------------------------
    # KANAL YO'Q
    # --------------------------------------------------------

    if not channels:
        kb = types.InlineKeyboardMarkup()

        kb.add(
            types.InlineKeyboardButton(
                "➕ Yangi kanal",
                callback_data="auto_add"
            )
        )

        kb.add(
            types.InlineKeyboardButton(
                "⬅️ Orqaga",
                callback_data="auto_menu"
            )
        )

        bot.edit_message_text(
            "📌 <b>Kanallarim</b>\n\n"
            "📭 Hozircha hech qanday kanal ulanmagan.\n\n"
            "👇 Kanal ulash uchun tugmani bosing:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=kb
        )

        return

    # --------------------------------------------------------
    # KANALLAR RO'YXATI
    # --------------------------------------------------------

    kb = types.InlineKeyboardMarkup()

    for channel in channels:

        title = (
            channel["title"]
            or channel["username"]
            or str(channel["channel_id"])
        )

        if channel["active"]:
            status = "🟢"
        else:
            status = "🔴"

        kb.add(
            types.InlineKeyboardButton(
                f"{status} {title}",
                callback_data=f"auto_channel:{channel['id']}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "➕ Yangi kanal",
            callback_data="auto_add"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_menu"
        )
    )

    bot.edit_message_text(
        "📌 <b>Kanallarim</b>\n\n"
        f"📊 Ulangan kanallar: <b>{len(channels)}</b>\n\n"
        "👇 Kanalni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — KANAL TANLASH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_channel:")
)
def auto_channel_select_callback(call):

    bot.answer_callback_query(call.id)

    try:
        channel_db_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Kanal topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT
                id,
                channel_id,
                username,
                title,
                active,
                invite_link
            FROM channels
            WHERE id = ?
              AND user_id = ?
            """,
            (
                channel_db_id,
                call.from_user.id
            )
        ).fetchone()

    finally:
        conn.close()

    if not channel:
        bot.answer_callback_query(
            call.id,
            "❌ Kanal topilmadi.",
            show_alert=True
        )
        return

    # Kanalni state'da saqlaymiz
    set_state(
        call.from_user.id,
        "channel_received",
        channel_id=channel["channel_id"],
        channel_db_id=channel["id"],
        username=channel["username"],
        title=channel["title"]
    )

    title = (
        channel["title"]
        or channel["username"]
        or "Noma'lum kanal"
    )

    username = channel["username"]

    if username:
        channel_line = f"🔗 @{username}"
    else:
        channel_line = f"🆔 <code>{channel['channel_id']}</code>"
        inv_val = channel["invite_link"] if "invite_link" in channel.keys() else ""
        if inv_val:
            channel_line += f"\n🔗 {inv_val}"

    status = (
        "🟢 Faol"
        if channel["active"]
        else "🔴 O'chirilgan"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            f"Holati: {'✅' if channel['active'] else '❌'}",
            callback_data=f"auto_toggle:{channel['id']}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        ),
        types.InlineKeyboardButton(
            "📋 Ro'yxat",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Kanalni o'chirish",
            callback_data="auto_delete"
        )
    )

    bot.edit_message_text(
        f"<b>📋 @{str(username).lstrip('@')} kanalining sozlamalari:</b>\n\n"
        "<b>➡️ Quyidagi tugmalar orqali sozlashingiz mumkin.</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — KANAL HOLATI TOGGLE
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_toggle:")
)
def auto_toggle_callback(call):
    try:
        channel_db_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Kanal topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT id, username, title, active
            FROM channels
            WHERE id = ?
              AND user_id = ?
            """,
            (channel_db_id, call.from_user.id)
        ).fetchone()

        if not channel:
            bot.answer_callback_query(
                call.id,
                "❌ Kanal topilmadi.",
                show_alert=True
            )
            return

        new_status = 0 if channel["active"] else 1

        conn.execute(
            """
            UPDATE channels
            SET active = ?
            WHERE id = ?
              AND user_id = ?
            """,
            (new_status, channel_db_id, call.from_user.id)
        )
        conn.commit()

    finally:
        conn.close()

    username = channel["username"]

    if username:
        channel_name = f"@{str(username).lstrip('@')}"
    else:
        channel_name = channel["title"] or "Noma'lum kanal"

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            f"Holati: {'✅' if new_status else '❌'}",
            callback_data=f"auto_toggle:{channel_db_id}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        ),
        types.InlineKeyboardButton(
            "📋 Ro'yxat",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Kanalni o'chirish",
            callback_data="auto_delete"
        )
    )

    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )

    bot.answer_callback_query(
        call.id,
        "✅ Kanal yoqildi." if new_status else "❌ Kanal o'chirildi."
    )


# ============================================================
# AUTO — XIZMATLAR
# ============================================================

AUTO_SERVICE_PAGE_SIZE = 10


def get_auto_services(page=0):
    """Auto Order uchun admin tomonidan mavjud qilingan xizmatlar (Instagram, TikTok, YouTube filtrlanadi)."""

    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0

    conn = db()

    try:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM services
            WHERE active = 1
              AND COALESCE(admin_available, 0) = 1
              AND NOT {EXCLUDED_PLATFORMS_SQL}
            """
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT
                id,
                name,
                category,
                price,
                min_qty,
                max_qty
            FROM services
            WHERE active = 1
              AND COALESCE(admin_available, 0) = 1
              AND NOT {EXCLUDED_PLATFORMS_SQL}
            ORDER BY
                CASE
                    WHEN COALESCE(price, 0) = 0 THEN 0
                    ELSE 1
                END,
                COALESCE(price, 0) ASC,
                sort_order ASC,
                id ASC
            LIMIT ? OFFSET ?
            """,
            (
                AUTO_SERVICE_PAGE_SIZE,
                page * AUTO_SERVICE_PAGE_SIZE
            )
        ).fetchall()

        return rows, int(total or 0)

    finally:
        conn.close()


def auto_service_keyboard(page=0):
    rows, total = get_auto_services(page)

    kb = types.InlineKeyboardMarkup(row_width=1)

    for row in rows:
        name = row["name"] or "Noma'lum xizmat"

        # Xizmat nomi uzun bo'lsa qisqartiriladi,
        # narx esa faqat raqam ko'rinishida chiqadi.
        price = float(row["price"] or 0)

        if price.is_integer():
            price_text = f"{int(price):,}".replace(",", " ")
        else:
            price_text = f"{price:,.2f}".replace(",", " ")

        short_name = name[:45] + ("..." if len(name) > 45 else "")
        kb.add(
            types.InlineKeyboardButton(
                f"🛠 {short_name} — {price_text}",
                callback_data=f"auto_service:{row['id']}"
            )
        )

    total_pages = max(
        1,
        (total + AUTO_SERVICE_PAGE_SIZE - 1)
        // AUTO_SERVICE_PAGE_SIZE
    )

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=f"auto_service_page:{page - 1}"
            )
        )

    nav.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop"
        )
    )

    if page + 1 < total_pages:
        nav.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=f"auto_service_page:{page + 1}"
            )
        )

    kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_channel_settings"
        )
    )

    return kb


# ============================================================
# AUTO — XIZMAT QO'SHISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_add_service"
)
def auto_add_service_callback(call):
    bot.answer_callback_query(call.id)

    state = get_state(call.from_user.id)

    if not state.get("channel_id"):
        bot.edit_message_text(
            "❌ Kanal topilmadi.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    bot.edit_message_text(
        "➕ <b>Xizmat qo'shish</b>\n\n"
        "👇 Kanal uchun xizmatni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=auto_service_keyboard(0)
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_service_page:")
)
def auto_service_page_callback(call):
    bot.answer_callback_query(call.id)

    try:
        page = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        page = 0

    bot.edit_message_text(
        "➕ <b>Xizmat qo'shish</b>\n\n"
        "👇 Kanal uchun xizmatni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=auto_service_keyboard(page)
    )


# ============================================================
# AUTO — QO'SHILGAN XIZMATLAR RO'YXATI
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_service_list"
)
def auto_service_list_callback(call):
    bot.answer_callback_query(call.id)

    state = get_state(call.from_user.id)
    channel_id = state.get("channel_id")

    if not channel_id:
        bot.edit_message_text(
            "❌ Kanal topilmadi.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    conn = db()

    try:
        clean_cid = clean_channel_id(channel_id)
        cid_variants = [str(channel_id), f"-100{clean_cid}", f"-{clean_cid}", clean_cid]
        if state.get("username"):
            cid_variants.append(f"@{state['username'].lstrip('@')}")
        placeholders = ",".join("?" for _ in cid_variants)

        rows = conn.execute(
            f"""
            SELECT
                cs.id AS channel_service_id,
                cs.service_id,
                cs.min_qty,
                cs.max_qty,
                s.name,
                s.category,
                COALESCE(s.price, 0) AS price
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.user_id = ?
              AND cs.channel_id IN ({placeholders})
              AND cs.active = 1
            ORDER BY
                COALESCE(s.price, 0) ASC,
                cs.id DESC
            """,
            [call.from_user.id] + cid_variants
        ).fetchall()

    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup(row_width=1)

    if not rows:
        text = (
            "⚠️ Ushbu kanal uchun hech qanday avto xizmatlar "
            "qo'shilmagan"
        )

        kb.add(
            types.InlineKeyboardButton(
                "➕ Qo'shish",
                callback_data="auto_add_service"
            )
        )

    else:
        text = "📋 <b>Qo'shilgan xizmatlar ro'yxati:</b>"

        for row in rows:
            kb.add(
                types.InlineKeyboardButton(
                    (
                        f"🆓 {row['name'][:42]} — 0 so'm"
                        if float(row["price"] or 0) == 0
                        else (
                            f"💰 {row['name'][:42]} — "
                            f"{float(row['price']):,.0f} so'm"
                        )
                    ),
                    callback_data=(
                        f"auto_bound_service:"
                        f"{row['channel_service_id']}"
                    )
                )
            )

        kb.add(
            types.InlineKeyboardButton(
                "➕ Xizmat qo'shish",
                callback_data="auto_add_service"
            )
        )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — QO'SHILGAN XIZMAT MA'LUMOTI
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_bound_service:")
)
def auto_bound_service_callback(call):
    bot.answer_callback_query(call.id)

    try:
        channel_service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                cs.id,
                cs.channel_id,
                cs.service_id,
                cs.min_qty,
                cs.max_qty,
                s.name,
                s.category
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.id = ?
              AND cs.user_id = ?
              AND cs.active = 1
            """,
            (channel_service_id, call.from_user.id)
        ).fetchone()

    finally:
        conn.close()

    if not row:
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    text = (
        f"🛠 <b>{row['name']}</b>\n\n"
        f"📌 Xizmat nomi: <b>{row['name']}</b>\n"
        f"📂 Kategoriya: {row['category']}\n"
        f"🔢 Min: <b>{row['min_qty']}</b>\n"
        f"🔢 Max: <b>{row['max_qty']}</b>"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "🗑 O'chirish",
            callback_data=(
                f"auto_bound_delete:{channel_service_id}"
            )
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_service_list"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — XIZMAT O'CHIRISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "auto_bound_delete:"
    )
)
def auto_bound_delete_callback(call):

    bot.answer_callback_query(call.id)

    try:
        channel_service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                cs.id,
                cs.channel_id,
                s.name
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.id = ?
              AND cs.active = 1
              AND cs.user_id = ?
            """,
            (
                channel_service_id,
                call.from_user.id
            )
        ).fetchone()

    finally:
        conn.close()

    if not row:
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "✅ Ha, o'chirish",
            callback_data=(
                f"auto_bound_delete_yes:{channel_service_id}"
            )
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data=(
                f"auto_bound_delete_cancel:{channel_service_id}"
            )
        )
    )

    bot.edit_message_text(
        "⚠️ <b>Xizmatni o'chirish</b>\n\n"
        f"🛠 Xizmat: <b>{row['name']}</b>\n\n"
        "Ushbu xizmat faqat shu kanaldan o'chiriladi.\n\n"
        "Davom etasizmi?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "auto_bound_delete_yes:"
    )
)
def auto_bound_delete_yes_callback(call):

    try:
        channel_service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                cs.id,
                cs.channel_id,
                s.name
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.id = ?
              AND cs.active = 1
              AND cs.user_id = ?
            """,
            (
                channel_service_id,
                call.from_user.id
            )
        ).fetchone()

        if not row:
            bot.answer_callback_query(
                call.id,
                "❌ Xizmat topilmadi.",
                show_alert=True
            )
            return

        conn.execute(
            """
            UPDATE channel_services
            SET active = 0
            WHERE id = ? AND user_id = ?
            """,
            (channel_service_id, call.from_user.id)
        )

        conn.commit()

        channel_id = row["channel_id"]
        service_name = row["name"]

    except Exception as e:
        conn.rollback()

        print(
            f"❌ Xizmatni o'chirish xatosi: {e}"
        )

        bot.answer_callback_query(
            call.id,
            "❌ O'chirishda xatolik yuz berdi.",
            show_alert=True
        )
        return

    finally:
        conn.close()

    bot.answer_callback_query(
        call.id,
        "✅ Xizmat o'chirildi."
    )

    # Kanalni state'da qayta saqlaymiz
    state = get_state(call.from_user.id)

    set_state(
        call.from_user.id,
        "channel_received",
        channel_id=channel_id,
        channel_db_id=state.get("channel_db_id"),
        username=state.get("username"),
        title=state.get("title")
    )

    # Xizmatlar ro'yxatiga qaytamiz
    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT
                cs.id AS channel_service_id,
                cs.min_qty,
                cs.max_qty,
                s.name,
                s.category
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.user_id = ?
              AND cs.channel_id = ?
              AND cs.active = 1
            ORDER BY cs.id DESC
            """,
            (call.from_user.id, channel_id)
        ).fetchall()

    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup(row_width=1)

    if rows:
        for row_item in rows:
            kb.add(
                types.InlineKeyboardButton(
                    f"🛠 {row_item['name'][:55]}",
                    callback_data=(
                        f"auto_bound_service:"
                        f"{row_item['channel_service_id']}"
                    )
                )
            )

        text = (
            "📋 <b>Xizmatlar ro'yxati</b>\n\n"
            f"✅ <b>{service_name}</b> o'chirildi.\n\n"
            "👇 Qolgan xizmatlardan birini tanlang:"
        )

    else:
        text = (
            "📋 <b>Xizmatlar ro'yxati</b>\n\n"
            f"✅ <b>{service_name}</b> o'chirildi.\n\n"
            "📭 Bu kanal uchun boshqa xizmat qolmadi."
        )

    kb.add(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_channel_settings"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "auto_bound_delete_cancel:"
    )
)
def auto_bound_delete_cancel_callback(call):

    bot.answer_callback_query(
        call.id,
        "❌ Bekor qilindi."
    )

    try:
        channel_service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                cs.id,
                cs.channel_id,
                cs.min_qty,
                cs.max_qty,
                s.name,
                s.category
            FROM channel_services cs
            JOIN services s
                ON s.id = cs.service_id
            WHERE cs.id = ?
              AND cs.active = 1
              AND cs.user_id = ?
            """,
            (
                channel_service_id,
                call.from_user.id
            )
        ).fetchone()

    finally:
        conn.close()

    if not row:
        bot.edit_message_text(
            "❌ Xizmat topilmadi.",
            call.message.chat.id,
            call.message.message_id
        )
        return

    text = (
        f"🛠 <b>{row['name']}</b>\n\n"
        f"📌 Xizmat nomi: <b>{row['name']}</b>\n"
        f"📂 Kategoriya: {row['category']}\n"
        f"🔢 Min: <b>{row['min_qty']}</b>\n"
        f"🔢 Max: <b>{row['max_qty']}</b>"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "🗑 O'chirish",
            callback_data=(
                f"auto_bound_delete:{channel_service_id}"
            )
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_service_list"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — XIZMAT TANLASH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_service:")
)
def auto_service_callback(call):
    bot.answer_callback_query(call.id)

    try:
        service_id = int(
            call.data.split(":")[1]
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat topilmadi.",
            show_alert=True
        )
        return

    state = get_state(call.from_user.id)
    channel_id = state.get("channel_id")

    if not channel_id:
        bot.answer_callback_query(
            call.id,
            "❌ Kanal topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        service = conn.execute(
            """
            SELECT
                id,
                name,
                category,
                price,
                min_qty,
                max_qty
            FROM services
            WHERE id = ?
              AND active = 1
            """,
            (service_id,)
        ).fetchone()

        if not service:
            bot.answer_callback_query(
                call.id,
                "❌ Xizmat topilmadi.",
                show_alert=True
            )
            return

        existing = conn.execute(
            """
            SELECT id
            FROM channel_services
            WHERE user_id = ?
              AND channel_id = ?
              AND service_id = ?
              AND active = 1
            """,
            (call.from_user.id, channel_id, service_id)
        ).fetchone()

    finally:
        conn.close()

    if existing:
        bot.answer_callback_query(
            call.id,
            "⚠️ Bu xizmat kanalga allaqachon qo'shilgan.",
            show_alert=True
        )
        return

    state["service_id"] = service_id
    state["service_name"] = service["name"]
    state["service_min"] = service["min_qty"]
    state["service_max"] = service["max_qty"]
    state["service_category"] = service["category"]
    state["service_price"] = service["price"]

    service_state = dict(state)
    service_state.pop("state", None)

    set_state(
        call.from_user.id,
        "waiting_auto_min",
        **service_state
    )

    bot.edit_message_text(
        "🛠 <b>Xizmat tanlandi</b>\n\n"
        f"📌 <b>{service['name']}</b>\n\n"
        f"📂 Kategoriya: {service['category']}\n"
        f"🔢 Ruxsat etilgan min: <b>{service['min_qty']}</b>\n"
        f"🔢 Ruxsat etilgan max: <b>{service['max_qty']}</b>\n\n"
        "✏️ Endi <b>Min</b> miqdorni yuboring.\n\n"
        f"Masalan: <code>{service['min_qty']}</code>",
        call.message.chat.id,
        call.message.message_id
    )

# ============================================================
# AUTO — MIN MIQDOR
# ============================================================

@bot.message_handler(
    func=lambda message: (
        get_state(message.from_user.id).get("state")
        == "waiting_auto_min"
    )
)
def receive_auto_min(message):
    text = (message.text or "").strip()

    if not text.isdigit():
        bot.send_message(
            message.chat.id,
            "❌ Faqat raqam yuboring.\n\n"
            "Masalan: <code>100</code>"
        )
        return

    min_qty = int(text)

    state = get_state(message.from_user.id)

    service_min = int(
        state.get("service_min") or 0
    )

    service_max = int(
        state.get("service_max") or 0
    )

    if min_qty < service_min:
        bot.send_message(
            message.chat.id,
            f"❌ Min miqdor juda kichik.\n\n"
            f"Minimal qiymat: <b>{service_min}</b>"
        )
        return

    if service_max and min_qty > service_max:
        bot.send_message(
            message.chat.id,
            f"❌ Min miqdor maksimal qiymatdan katta.\n\n"
            f"Maksimal qiymat: <b>{service_max}</b>"
        )
        return

    state["auto_min"] = min_qty

    next_state = dict(state)
    next_state.pop("state", None)

    set_state(
        message.from_user.id,
        "waiting_auto_max",
        **next_state
    )

    bot.send_message(
        message.chat.id,
        "✅ Min qabul qilindi.\n\n"
        f"🔢 Min: <b>{min_qty}</b>\n\n"
        "✏️ Endi <b>Max</b> miqdorni yuboring.\n\n"
        f"Masalan: <code>{service_max}</code>"
    )


# ============================================================
# AUTO — MAX MIQDOR
# ============================================================

@bot.message_handler(
    func=lambda message: (
        get_state(message.from_user.id).get("state")
        == "waiting_auto_max"
    )
)
def receive_auto_max(message):
    text = (message.text or "").strip()

    if not text.isdigit():
        bot.send_message(
            message.chat.id,
            "❌ Faqat raqam yuboring.\n\n"
            "Masalan: <code>1000</code>"
        )
        return

    max_qty = int(text)

    state = get_state(message.from_user.id)

    min_qty = int(
        state.get("auto_min") or 0
    )

    service_max = int(
        state.get("service_max") or 0
    )

    if max_qty < min_qty:
        bot.send_message(
            message.chat.id,
            f"❌ Max Min'dan kichik bo'lishi mumkin emas.\n\n"
            f"Min: <b>{min_qty}</b>"
        )
        return

    if service_max and max_qty > service_max:
        bot.send_message(
            message.chat.id,
            f"❌ Max miqdor xizmat limitidan katta.\n\n"
            f"Maksimal qiymat: <b>{service_max}</b>"
        )
        return

    channel_id = state.get("channel_id")
    service_id = state.get("service_id")

    if not channel_id or not service_id:
        clear_state(message.from_user.id)

        bot.send_message(
            message.chat.id,
            "❌ Kanal yoki xizmat ma'lumoti topilmadi."
        )
        return

    user_id = message.from_user.id
    conn = db()

    try:
        existing = conn.execute(
            """
            SELECT id
            FROM channel_services
            WHERE user_id = ?
              AND channel_id = ?
              AND service_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, channel_id, service_id)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE channel_services
                SET
                    min_qty = ?,
                    max_qty = ?,
                    active = 1
                WHERE id = ? AND user_id = ?
                """,
                (
                    min_qty,
                    max_qty,
                    existing["id"],
                    user_id
                )
            )
        else:
            conn.execute(
                """
                INSERT INTO channel_services
                (
                    user_id,
                    channel_id,
                    service_id,
                    min_qty,
                    max_qty,
                    active,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
                """,
                (
                    user_id,
                    channel_id,
                    service_id,
                    min_qty,
                    max_qty
                )
            )

        conn.commit()

    finally:
        conn.close()

    service_name = (
        state.get("service_name")
        or "Noma'lum xizmat"
    )

    # Kanal ma'lumotlarini saqlab qolamiz.
    # Aks holda xizmat qo'shilgandan keyin
    # auto_service_list / auto_add_service kanalni topa olmaydi.
    set_state(
        message.from_user.id,
        "channel_received",
        channel_id=state.get("channel_id"),
        channel_db_id=state.get("channel_db_id"),
        username=state.get("username"),
        title=state.get("title")
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "📋 Xizmatlar ro'yxati",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "➕ Yana xizmat qo'shish",
            callback_data="auto_add_service"
        )
    )

    bot.send_message(
        message.chat.id,
        "✅ <b>Xizmat muvaffaqiyatli qo'shildi!</b>\n\n"
        f"🛠 Xizmat: <b>{service_name}</b>\n"
        f"🔢 Min: <b>{min_qty}</b>\n"
        f"🔢 Max: <b>{max_qty}</b>",
        reply_markup=kb
    )


# ============================================================
# AUTO — KANAL SOZLAMALARIGA QAYTISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_channel_settings"
)
def auto_channel_settings_callback(call):
    bot.answer_callback_query(call.id)

    state = get_state(call.from_user.id)

    channel_db_id = state.get("channel_db_id")

    if not channel_db_id:
        bot.edit_message_text(
            "❌ Kanal topilmadi.",
            call.message.chat.id,
            call.message.message_id
        )
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT id, username, title, active
            FROM channels
            WHERE id = ?
              AND user_id = ?
            """,
            (channel_db_id, call.from_user.id)
        ).fetchone()
    finally:
        conn.close()

    if not channel:
        bot.edit_message_text(
            "❌ Kanal topilmadi.",
            call.message.chat.id,
            call.message.message_id
        )
        return

    username = channel["username"]

    if username:
        channel_name = f"@{str(username).lstrip('@')}"
    else:
        channel_name = channel["title"] or "Noma'lum kanal"

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            f"Holati: {'✅' if channel['active'] else '❌'}",
            callback_data=f"auto_toggle:{channel['id']}"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        ),
        types.InlineKeyboardButton(
            "📋 Ro'yxat",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Kanalni o'chirish",
            callback_data="auto_delete"
        )
    )

    bot.edit_message_text(
        f"<b>📋 {channel_name} kanalining sozlamalari:</b>\n\n"
        "<b>➡️ Quyidagi tugmalar orqali sozlashingiz mumkin.</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )




# ============================================================
# AUTO — KANALNI O'CHIRISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_delete"
)
def auto_delete_callback(call):
    bot.answer_callback_query(call.id)

    user_id = call.from_user.id
    state = get_state(user_id)

    channel_id = state.get("channel_id")

    if not channel_id:
        bot.edit_message_text(
            "❌ Kanal ma'lumotlari topilmadi.\n\n"
            "Iltimos, kanalni qaytadan tanlang.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT id, channel_id, username, title
            FROM channels
            WHERE id = ? AND user_id = ?
            """,
            (
                state.get("channel_db_id"),
                user_id
            )
        ).fetchone()

        if not channel:
            channel = conn.execute(
                """
                SELECT id, channel_id, username, title
                FROM channels
                WHERE channel_id = ? AND user_id = ?
                """,
                (
                    channel_id,
                    user_id
                )
            ).fetchone()

    finally:
        conn.close()

    if not channel:
        bot.edit_message_text(
            "❌ Bu kanal sizga tegishli emas yoki topilmadi.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    title = channel["title"] or channel["username"] or str(channel["channel_id"])

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Ha, o'chirish",
            callback_data=f"auto_delete_yes:{channel['id']}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data=f"auto_delete_cancel:{channel['id']}"
        )
    )

    bot.edit_message_text(
        f"⚠️ <b>Kanalni o'chirish</b>\n\n"
        f"📌 Kanal: <b>{title}</b>\n\n"
        "Kanal sozlamalari va unga biriktirilgan "
        "avtomatik xizmatlar o'chiriladi.\n\n"
        "Davom etasizmi?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# AUTO — KANALNI O'CHIRISHNI TASDIQLASH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_delete_yes:")
)
def auto_delete_yes_callback(call):
    bot.answer_callback_query(call.id)

    user_id = call.from_user.id

    try:
        channel_db_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Noto'g'ri kanal.",
            show_alert=True
        )
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT id, channel_id, username, title
            FROM channels
            WHERE id = ? AND user_id = ?
            """,
            (
                channel_db_id,
                user_id
            )
        ).fetchone()

        if not channel:
            bot.edit_message_text(
                "❌ Kanal topilmadi yoki sizga tegishli emas.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=auto_keyboard()
            )
            return

        channel_id = channel["channel_id"]
        title = channel["title"] or channel["username"] or str(channel_id)

        # Avval kanalga biriktirilgan xizmatlarni o'chiramiz
        clean_cid = clean_channel_id(channel_id)
        cid_variants = [str(channel_id), f"-100{clean_cid}", f"-{clean_cid}", clean_cid]
        if channel["username"]:
            cid_variants.append(f"@{channel['username'].lstrip('@')}")
        ph = ",".join("?" for _ in cid_variants)
        conn.execute(
            f"""
            DELETE FROM channel_services
            WHERE user_id = ? AND channel_id IN ({ph})
            """,
            [user_id] + cid_variants
        )

        # Keyin kanalning o'zini o'chiramiz
        conn.execute(
            """
            DELETE FROM channels
            WHERE id = ? AND user_id = ?
            """,
            (
                channel_db_id,
                user_id
            )
        )

        conn.commit()

    except Exception as e:
        conn.rollback()

        bot.edit_message_text(
            f"❌ Kanalni o'chirishda xatolik:\n\n"
            f"<code>{e}</code>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    finally:
        conn.close()

    clear_state(user_id)

    bot.edit_message_text(
        f"✅ <b>Kanal o'chirildi</b>\n\n"
        f"📌 {title}\n\n"
        "Kanalga biriktirilgan avtomatik xizmatlar ham o'chirildi.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=auto_keyboard()
    )


# ============================================================
# AUTO — KANALNI O'CHIRISHNI BEKOR QILISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("auto_delete_cancel:")
)
def auto_delete_cancel_callback(call):
    bot.answer_callback_query(call.id)

    user_id = call.from_user.id

    try:
        channel_db_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    conn = db()

    try:
        channel = conn.execute(
            """
            SELECT id, channel_id, username, title
            FROM channels
            WHERE id = ? AND user_id = ?
            """,
            (
                channel_db_id,
                user_id
            )
        ).fetchone()
    finally:
        conn.close()

    if not channel:
        bot.edit_message_text(
            "❌ Kanal topilmadi.",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=auto_keyboard()
        )
        return

    set_state(
        user_id,
        "channel_received",
        channel_id=channel["channel_id"],
        channel_db_id=channel["id"],
        username=channel["username"],
        title=channel["title"]
    )

    title = channel["title"] or channel["username"] or "Noma'lum kanal"

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "➕ Xizmat qo'shish",
            callback_data="auto_add_service"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📋 Ro'yxat",
            callback_data="auto_service_list"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 Kanalni o'chirish",
            callback_data="auto_delete"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "⬅️ Orqaga",
            callback_data="auto_channels"
        )
    )

    bot.edit_message_text(
        f"📌 <b>{title}</b> kanalining sozlamalari\n\n"
        "👇 Kerakli amalni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# BEKOR QILISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "auto_cancel"
)
def auto_cancel_callback(call):
    bot.answer_callback_query(call.id)

    clear_state(call.from_user.id)

    bot.edit_message_text(
        "❌ Amal bekor qilindi.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=auto_keyboard()
    )


# ============================================================
# BALANS
# ============================================================

def show_balance(chat_id, user_id):
    balance = get_balance(user_id)

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "💵 Pul kiritish",
            callback_data="deposit"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
                        "🏠 Asosiy menyu",
            callback_data="home_menu"
        )
    )

    bot.send_message(
        chat_id,
        "💰 <b>Balans</b>\n\n"
        f"💵 Hisobingiz: <b>{balance:,.2f}</b> so'm",
        reply_markup=kb
    )


# ============================================================
# BUYURTMALARIM
# ============================================================

def show_orders(chat_id, user_id):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    if not rows:
        text = (
            "📦 <b>Buyurtmalarim</b>\n\n"
            "📭 Hozircha buyurtmalar mavjud emas."
        )
    else:
        parts = [
            "📦 <b>Buyurtmalarim</b>\n"
        ]

        for row in rows:
            service_name = (
                row["service_name"]
                if "service_name" in row.keys()
                else "Noma'lum xizmat"
            )

            quantity = row["quantity"] or 0
            price = float(row["price"] or 0)
            status = row["status"] or "unknown"

            parts.append(
                f"🆔 <b>#{row['id']}</b>\n"
                f"🛠 {service_name}\n"
                f"📊 Miqdor: <b>{quantity}</b>\n"
                f"💰 Narx: <b>{price:,.2f}</b> so'm\n"
                f"📌 Status: <b>{status}</b>\n"
            )

        text = "\n".join(parts)

    kb = types.InlineKeyboardMarkup()


    bot.send_message(
        chat_id,
        text,
        reply_markup=kb
    )


# ============================================================
# KABINET
# ============================================================

def show_cabinet(chat_id, user_id):
    balance = get_balance(user_id)

    conn = db()

    row = conn.execute(
        """
        SELECT total_deposited
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    total_deposited = float(
        row["total_deposited"]
        if row and row["total_deposited"] is not None
        else 0
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "💵 Pul kiritish",
            callback_data="deposit"
        )
    )


    bot.send_message(
        chat_id,
        "👤 <b>Kabinet</b>\n\n"
        f"📋 <b>Kabinet ID: {user_id}</b>\n"
        f"💵 <b>Hisobingiz:</b> <code>{format(balance, ',.2f').replace(',', ' ')} so'm</code>\n"
        f"🛍 <b>Kiritgan pullaringiz:</b> <code>{format(total_deposited, ',.2f').replace(',', ' ')} so'm</code>",
        reply_markup=kb
    )


# ============================================================
# SUPPORT
# ============================================================

def show_support(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.row(
        types.InlineKeyboardButton(
            "📞 Admin",
            url="https://t.me/xua202"
        ),
        types.InlineKeyboardButton(
            "👨‍💻 Dasturchi",
            url="https://t.me/xua202"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "💬 Online murojaat",
            url="https://t.me/xua202"
        ),
        types.InlineKeyboardButton(
            "👥 Yordam chat",
            url="https://t.me/xua202"
        )
    )


    bot.send_message(
        chat_id,
        "🆘 <b>SUPPORT – Qo‘llab-quvvatlash xizmati</b>\n\n"
        "Savol yoki muammo bo‘lsa, quyidagi "
        "tugmalardan foydalaning.",
        reply_markup=kb
    )


# ============================================================
# QO‘LLANMA
# ============================================================

def show_guide(chat_id):
    text = (
        "<b>📕 QO‘LLANMA</b>\n\n"

        "<b>👋 BOTDAN FOYDALANISH</b>\n\n"

        "<b>🛍 Avto buyurtma ulash</b> — "
        "kanalingizni ulang va avtomatik buyurtmani sozlang.\n\n"

        "<b>💵 Pul kiritish</b> — "
        "hisobingizni to‘ldiring.\n\n"

        "<b>💼 Kabinet</b> — "
        "balans va hisobingiz haqidagi ma’lumotlarni ko‘ring.\n\n"

        "<b>📌 Kanallarim</b> — "
        "ulangan kanallaringizni boshqaring.\n\n"

        "<b>🆘 Support</b> — "
        "muammo yoki savol bo‘lsa, yordam oling.\n\n"

        "<b>⚠️ Eslatma:</b>\n"
        "Buyurtma berishdan oldin <b>balansingiz yetarli</b> "
        "ekanini tekshiring va kanal ma’lumotlarini to‘g‘ri kiriting.\n\n"

        "<b>🚀 Kerakli bo‘limni tanlang va ko‘rsatmalarga amal qiling!</b>"
    )

    bot.send_message(
        chat_id,
        text,
        parse_mode="HTML"
    )

# ============================================================
# TELEGRAM STARS / PUL KIRITISH
# ============================================================

def stars_setting(key, default):
    conn = db()
    try:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?",
            (key,)
        ).fetchone()

        if row and row["value"] not in (None, ""):
            return row["value"]

        conn.execute(
            """
            INSERT OR IGNORE INTO system_settings(key, value)
            VALUES (?, ?)
            """,
            (key, str(default))
        )
        conn.commit()

        return str(default)

    finally:
        conn.close()


def set_stars_setting(key, value):
    conn = db()
    try:
        conn.execute(
            """
            INSERT INTO system_settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (key, str(value))
        )
        conn.commit()
    finally:
        conn.close()


def get_stars_config():
    return {
        "rate": float(stars_setting("stars_rate", "200")),
        "min": 1,
        "max": 10000
    }
def stars_settings_keyboard():
    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "💱 Kursni o'zgartirish",
            callback_data="stars_set_rate"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Orqaga",
            callback_data="admin_settings"
        )
    )

    return kb
def show_stars_settings(chat_id, message_id=None):
    cfg = get_stars_config()

    text = (
        "⭐ <b>Telegram Stars sozlamalari</b>\n\n"
        f"💱 1 ⭐ = <b>{cfg['rate']:g}</b> so'm\n"
        f"⬇️ Minimum: <b>{cfg['min']}</b> ⭐\n"
        f"⬆️ Maksimum: <b>{cfg['max']}</b> ⭐\n\n"
        "💡 Foydalanuvchi Stars orqali to'lov qiladi "
        "va balansiga avtomatik ravishda so'm qo'shiladi."
    )

    kb = stars_settings_keyboard()

    if message_id is not None:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=kb
        )


def build_admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.row(
        types.InlineKeyboardButton(
            "📊 Statistika",
            callback_data="admin_stats"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "👥 Foydalanuvchilar ro'yxati",
            callback_data="admin_users"
        ),
        types.InlineKeyboardButton(
            "💰 Depozitlar",
            callback_data="admin_deposits"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🔑 API lar",
            callback_data="admin_apis"
        ),
        types.InlineKeyboardButton(
            "🛠 Xizmatlar",
            callback_data="admin_services"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "⭐ Stars",
            callback_data="stars_settings"
        ),
        types.InlineKeyboardButton(
            "✉️ /start xabari",
            callback_data="admin_start_msg"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🤖 AI yordamchi",
            callback_data="admin_ai"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "🏠 Bosh menyu",
            callback_data="home_menu"
        )
    )

    return kb



def home_menu_callback(call):
    bot.answer_callback_query(call.id)

    clear_state(call.from_user.id)

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    send_start_screen(call.message.chat.id, call.from_user)


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_settings"
)
def admin_settings_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    bot.edit_message_text(
        "⚙️ <b>Admin sozlamalari</b>\n\n"
        "Quyidagi bo'limlardan keraklisini tanlang:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=build_admin_keyboard()
    )


def admin_start_msg_menu(chat_id, message_id=None):
    cfg = get_start_config()
    if cfg:
        m_type = cfg.get("type", "matn")
        status_text = f"🟢 <b>Maxsus xabar faol</b> ({m_type})"
    else:
        status_text = "⚪️ <b>Standart xabar faol</b>"

    text = (
        "✉️ <b>/START XABARINI SOZLASH</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 <b>Holat:</b> {status_text}\n\n"
        "Ushbu bo'lim orqali botning /start xabarini o'zingiz xohlagan matn, rasm yoki video bilan sozlashingiz mumkin.\n"
        "Sozlangan xabar ma'lumotlar bazasida doimiy saqlanadi."
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(
            "📝 Yangi /start xabari o'rnatish",
            callback_data="admin_set_start"
        ),
        types.InlineKeyboardButton(
            "👁 Joriy ko'rinishni sinash",
            callback_data="admin_preview_start"
        ),
        types.InlineKeyboardButton(
            "🔄 Standart holatga qaytarish",
            callback_data="admin_reset_start"
        ),
        types.InlineKeyboardButton(
            "↩️ Admin panel",
            callback_data="admin_settings"
        )
    )

    if message_id:
        try:
            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                parse_mode="HTML",
                reply_markup=kb
            )
            return
        except Exception:
            pass

    bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda call: call.data == "admin_start_msg")
def admin_start_msg_callback(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.from_user):
        return
    admin_start_msg_menu(call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_set_start")
def admin_set_start_callback(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.from_user):
        return
    set_state(call.from_user.id, "waiting_setstart")
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data="cancel_setstart"
        )
    )
    bot.edit_message_text(
        "📝 <b>Yangi /start xabarini yuboring:</b>\n\n"
        "Istalgan kanal yoki chatdan xabarni (matn, rasm yoki video) <b>FORWARD</b> qilib yuboring "
        "yoki to'g'ridan-to'g'ri yangi matn/rasm/video yuboring.\n\n"
        "<i>Bot uni bazaga doimiy saqlab oladi va yangi /start xabari qilib sozlaydi.</i> 🚀",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda call: call.data == "admin_reset_start")
def admin_reset_start_callback(call):
    if not is_admin(call.from_user):
        return
    delete_start_config()
    bot.answer_callback_query(call.id, "✅ /start xabari standart holatiga qaytarildi!", show_alert=True)
    admin_start_msg_menu(call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda call: call.data == "admin_preview_start")
def admin_preview_start_callback(call):
    bot.answer_callback_query(call.id, "👁 Sinov ko'rinishi...")
    if not is_admin(call.from_user):
        return
    send_start_screen(call.message.chat.id, call.from_user)



@bot.message_handler(
    content_types=["document"],
    func=lambda message: is_admin(message.from_user) and message.document and (
        (message.document.file_name and message.document.file_name.lower().endswith(('.db', '.sqlite', '.sqlite3')))
        or (message.caption and '#RESTORE' in message.caption.upper())
    )
)
def handle_db_restore(message):
    try:
        doc = message.document
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        if len(downloaded) < 100 or downloaded[:16] != b'SQLite format 3\x00':
            bot.reply_to(message, "❌ <b>Xatolik:</b> Bu fayl haqiqiy SQLite formatidagi ma'lumotlar bazasi emas!", parse_mode="HTML")
            return

        if os.path.exists(DB_FILE):
            bak_name = f"{DB_FILE}.bak_{int(datetime.now().timestamp())}"
            try:
                import shutil
                shutil.copy2(DB_FILE, bak_name)
            except Exception:
                pass

        with open(DB_FILE, "wb") as f:
            f.write(downloaded)

        bot.reply_to(
            message,
            "✅ <b>Ma'lumotlar bazasi muvaffaqiyatli tiklandi!</b>\n\n"
            "Barcha APIlar, foydalanuvchilar, kanallar va buyurtmalar yangilandi va saqlandi.",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Tiklashda xatolik yuz berdi: {e}")



# ============================================================
# /admin — FAQAT ADMIN
# ============================================================

@bot.message_handler(commands=["admin"])
def admin_command(message):
    if not is_admin(message.from_user):
        bot.send_message(
            message.chat.id,
            "❌ Sizda admin paneliga kirish uchun ruxsat yo'q. Faqat @xua202 kira oladi."
        )
        return

    clear_state(message.from_user.id)

    bot.send_message(
        message.chat.id,
        "⚙️ <b>Admin sozlamalari</b>\n\n"
        "Quyidagi bo'limlardan keraklisini tanlang:",
        parse_mode="HTML",
        reply_markup=build_admin_keyboard()
    )


@bot.callback_query_handler(
    func=lambda call: call.data in ("admin_stats", "admin_stats_refresh")
)
def admin_stats_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    conn = db()

    try:
        # =========================
        # 👥 FOYDALANUVCHILAR
        # =========================
        users_count = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        active_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(banned, 0) = 0"
        ).fetchone()[0]

        banned_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(banned, 0) = 1"
        ).fetchone()[0]

        # Bugun ro'yxatdan o'tganlar
        today_users = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE date(created_at) = date('now', 'localtime')
            """
        ).fetchone()[0]

        # Oxirgi 7 kun
        week_users = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE datetime(created_at) >= datetime('now', '-7 days')
            """
        ).fetchone()[0]

        # =========================
        # 📦 BUYURTMALAR
        # =========================
        orders_count = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

        today_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE date(created_at) = date('now', 'localtime')
            """
        ).fetchone()[0]

        week_orders = conn.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE datetime(created_at) >= datetime('now', '-7 days')
            """
        ).fetchone()[0]

        # =========================
        # 📡 KANALLAR
        # =========================
        channels_count = conn.execute(
            "SELECT COUNT(*) FROM channels"
        ).fetchone()[0]

        active_channels = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE active = 1"
        ).fetchone()[0]

        inactive_channels = conn.execute(
            "SELECT COUNT(*) FROM channels WHERE active = 0"
        ).fetchone()[0]

        # =========================
        # 🛠 XIZMATLAR
        # =========================
        services_count = conn.execute(
            "SELECT COUNT(*) FROM services"
        ).fetchone()[0]

        active_services = conn.execute(
            "SELECT COUNT(*) FROM services WHERE active = 1"
        ).fetchone()[0]

        # =========================
        # 🔑 API
        # =========================
        try:
            apis_count = conn.execute(
                "SELECT COUNT(*) FROM apis"
            ).fetchone()[0]

            try:
                active_apis = conn.execute(
                    "SELECT COUNT(*) FROM apis WHERE active = 1"
                ).fetchone()[0]
            except Exception:
                active_apis = apis_count

        except Exception:
            apis_count = 0
            active_apis = 0

        # =========================
        # 💰 MOLIYA
        # =========================
        balance_sum = conn.execute(
            """
            SELECT COALESCE(SUM(balance), 0)
            FROM users
            """
        ).fetchone()[0] or 0

        deposited_sum = conn.execute(
            """
            SELECT COALESCE(SUM(total_deposited), 0)
            FROM users
            """
        ).fetchone()[0] or 0

        avg_balance = (
            float(balance_sum) / users_count
            if users_count
            else 0
        )

        # =========================
        # 💳 DEPOZITLAR
        # =========================
        try:
            deposits_count = conn.execute(
                "SELECT COUNT(*) FROM deposits"
            ).fetchone()[0]
        except Exception:
            deposits_count = 0

        try:
            successful_deposits = conn.execute(
                """
                SELECT COUNT(*)
                FROM deposits
                WHERE LOWER(COALESCE(status, '')) IN
                ('success', 'successful', 'paid', 'completed', 'completed')
                """
            ).fetchone()[0]
        except Exception:
            successful_deposits = 0

        # =========================
        # ⭐ STARS
        # =========================
        cfg = get_stars_config()

        # =========================
        # 🕐 VAQT
        # =========================
        now = datetime.now().strftime("%H:%M:%S")

    except Exception as e:
        print("❌ ADMIN STATS XATOSI:", repr(e))

        bot.send_message(
            call.message.chat.id,
            "❌ Statistikani olishda xatolik yuz berdi."
        )
        return

    finally:
        conn.close()

    text = (
        "📊 <b>BOT STATISTIKASI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "👥 <b>FOYDALANUVCHILAR</b>\n"
        f"├ 👤 Jami: <b>{users_count:,}</b>\n"
        f"├ 🟢 Faol: <b>{active_users:,}</b>\n"
        f"├ 🔴 Bloklangan: <b>{banned_users:,}</b>\n"
        f"├ 🆕 Bugun: <b>{today_users:,}</b>\n"
        f"└ 📅 7 kunlik: <b>{week_users:,}</b>\n\n"

        "📦 <b>BUYURTMALAR</b>\n"
        f"├ 📦 Jami: <b>{orders_count:,}</b>\n"
        f"├ 🕐 Bugun: <b>{today_orders:,}</b>\n"
        f"└ 📅 7 kunlik: <b>{week_orders:,}</b>\n\n"

        "📡 <b>AVTO BUYURTMA</b>\n"
        f"├ 📡 Kanallar: <b>{channels_count:,}</b>\n"
        f"├ ✅ Faol: <b>{active_channels:,}</b>\n"
        f"└ ⛔ Faol emas: <b>{inactive_channels:,}</b>\n\n"

        "🛠 <b>XIZMATLAR</b>\n"
        f"├ 🛠 Jami: <b>{services_count:,}</b>\n"
        f"└ ✅ Faol: <b>{active_services:,}</b>\n\n"

        "🔑 <b>API TIZIMI</b>\n"
        f"├ 🔑 Jami API: <b>{apis_count:,}</b>\n"
        f"└ 🟢 Faol API: <b>{active_apis:,}</b>\n\n"

        "💰 <b>MOLIYAVIY HOLAT</b>\n"
        f"├ 💰 Umumiy balans: <b>{float(balance_sum):,.2f}</b> so'm\n"
        f"├ 💵 Jami kiritilgan: <b>{float(deposited_sum):,.2f}</b> so'm\n"
        f"├ 👤 O'rtacha balans: <b>{avg_balance:,.2f}</b> so'm\n"
        f"├ 💳 Depozitlar: <b>{deposits_count:,}</b>\n"
        f"└ ✅ Muvaffaqiyatli: <b>{successful_deposits:,}</b>\n\n"

        "⭐ <b>STARS</b>\n"
        f"├ 💱 Kurs: <b>1 ⭐ = {cfg['rate']:g} so'm</b>\n"
        f"├ ⬇️ Minimum: <b>1 ⭐</b>\n"
        f"└ ⬆️ Maksimum: <b>10000 ⭐</b>\n\n"

        "🕐 <b>TIZIM</b>\n"
        f"└ 🔄 Yangilangan: <code>{now}</code>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.row(
        types.InlineKeyboardButton(
            "🔄 Yangilash",
            callback_data="admin_stats_refresh"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            "↩️ Sozlamalar",
            callback_data="admin_settings"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )



# ============================================================
# ADMIN — FOYDALANUVCHILAR
# ============================================================

def admin_users_stats():
    conn = db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        active = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE COALESCE(banned, 0) = 0
            """
        ).fetchone()[0]

        banned = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE COALESCE(banned, 0) = 1
            """
        ).fetchone()[0]

        today = conn.execute(
            """
            SELECT COUNT(*)
            FROM users
            WHERE date(created_at) = date('now', 'localtime')
            """
        ).fetchone()[0]

        balance = conn.execute(
            """
            SELECT COALESCE(SUM(balance), 0)
            FROM users
            """
        ).fetchone()[0] or 0

        return total, active, banned, today, float(balance)

    finally:
        conn.close()


def admin_users_main(chat_id, message_id=None):
    total, active, banned, today, balance = admin_users_stats()

    text = (
        "👥 <b>FOYDALANUVCHILAR</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Jami: <b>{total:,}</b>\n"
        f"🟢 Faol: <b>{active:,}</b>\n"
        f"🔴 Bloklangan: <b>{banned:,}</b>\n"
        f"🆕 Bugun qo'shilgan: <b>{today:,}</b>\n\n"
        f"💰 Umumiy balans: <b>{balance:,.2f}</b> so'm"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.add(
        types.InlineKeyboardButton(
            "📋 Foydalanuvchilar ro'yxati",
            callback_data="admin_users_list:0"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔎 ID / Username orqali qidirish",
            callback_data="admin_user_search"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔄 Yangilash",
            callback_data="admin_users"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Admin panel",
            callback_data="admin_settings"
        )
    )

    if message_id is None:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML",
            reply_markup=kb
        )


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_users"
)
def admin_users_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    admin_users_main(
        call.message.chat.id,
        call.message.message_id
    )


# ============================================================
# ADMIN — FOYDALANUVCHILAR RO'YXATI
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_users_list:")
)
def admin_users_list_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        page = int(call.data.split(":")[1])
    except Exception:
        page = 0

    per_page = 8
    offset = page * per_page

    conn = db()

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT
                user_id,
                username,
                first_name,
                balance,
                banned
            FROM users
            ORDER BY user_id DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset)
        ).fetchall()

    finally:
        conn.close()

    if not rows:
        text = (
            "📋 <b>FOYDALANUVCHILAR RO'YXATI</b>\n\n"
            "📭 Foydalanuvchilar topilmadi."
        )
    else:
        text = (
            "📋 <b>FOYDALANUVCHILAR RO'YXATI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        for row in rows:
            uid = row["user_id"]
            username = row["username"] or ""
            first_name = row["first_name"] or ""
            balance = float(row["balance"] or 0)
            banned = int(row["banned"] or 0)

            name = first_name.strip() or "Noma'lum"

            if username:
                label = f"@{username.lstrip('@')}"
            else:
                label = f"ID: {uid}"

            status = "🔴" if banned else "🟢"

            text += (
                f"{status} <b>{name}</b>\n"
                f"   👤 {label}\n"
                f"   🆔 <code>{uid}</code> | "
                f"💰 {balance:,.2f} so'm\n\n"
            )

    kb = types.InlineKeyboardMarkup(row_width=2)

    for row in rows:
        uid = row["user_id"]
        name = row["first_name"] or "Foydalanuvchi"
        button_name = str(name)[:18]

        kb.add(
            types.InlineKeyboardButton(
                f"👤 {button_name}",
                callback_data=f"admin_user:{uid}"
            )
        )

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️ Oldingi",
                callback_data=f"admin_users_list:{page - 1}"
            )
        )

    if offset + len(rows) < total:
        nav.append(
            types.InlineKeyboardButton(
                "Keyingi ➡️",
                callback_data=f"admin_users_list:{page + 1}"
            )
        )

    if nav:
        kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "🔎 Qidirish",
            callback_data="admin_user_search"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Foydalanuvchilar",
            callback_data="admin_users"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


# ============================================================
# ADMIN — FOYDALANUVCHI QIDIRISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "admin_user_search"
)
def admin_user_search_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    set_state(
        call.from_user.id,
        "admin_user_search"
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "↩️ Bekor qilish",
            callback_data="admin_users"
        )
    )

    bot.edit_message_text(
        "🔎 <b>FOYDALANUVCHI QIDIRISH</b>\n\n"
        "🆔 Telegram ID yoki 👤 username yuboring.\n\n"
        "Masalan:\n"
        "• <code>8191930658</code>\n"
        "• <code>@xua202</code> yoki <code>xua202</code>\n\n"
        "<i>Bekor qilish uchun pastdagi tugmani bosing yoki /cancel yuboring.</i>",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


def show_admin_user(bot_chat_id, message_id, user_id):
    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                user_id,
                username,
                first_name,
                balance,
                banned,
                created_at,
                total_deposited,
                role
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

        orders_count = 0
        try:
            oc = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            if oc:
                orders_count = oc[0] or 0
        except Exception:
            pass

    finally:
        conn.close()

    # Telegram orqali jonli ma'lumotlarni olish (batafsil Telegramda ko'rish uchun)
    tg_name = None
    tg_username = None
    tg_bio = None
    try:
        chat = bot.get_chat(user_id)
        if chat:
            tg_name = f"{chat.first_name or ''} {chat.last_name or ''}".strip()
            tg_username = chat.username
            tg_bio = getattr(chat, "bio", None)
    except Exception:
        pass

    if not row:
        if not tg_name and not tg_username:
            msg_text = "❌ Foydalanuvchi topilmadi (bazada ham, Telegramda ham mavjud emas)."
            if message_id:
                try:
                    bot.edit_message_text(msg_text, bot_chat_id, message_id, parse_mode="HTML")
                except Exception:
                    bot.send_message(bot_chat_id, msg_text, parse_mode="HTML")
            else:
                bot.send_message(bot_chat_id, msg_text, parse_mode="HTML")
            return

        conn = db()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO users
                (user_id, username, first_name, balance, banned, created_at)
                VALUES (?, ?, ?, 0, 0, ?)
                """,
                (user_id, tg_username or "", tg_name or "", datetime.now().isoformat())
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        finally:
            conn.close()

    username = (row["username"] if row else "") or tg_username or ""
    first_name = (row["first_name"] if row else "") or tg_name or "Noma'lum"
    balance = float(row["balance"] or 0) if row else 0.0
    total_deposited = float(row["total_deposited"] or 0) if row else 0.0
    banned = int(row["banned"] or 0) if row else 0
    role = (row["role"] if row else "") or "user"
    created_at = (row["created_at"] if row else "") or "-"

    status = "🔴 Bloklangan" if banned else "🟢 Faol"
    username_text = f"@{username.lstrip('@')}" if username else "Mavjud emas"

    text = (
        "👤 <b>FOYDALANUVCHI BAFASIL PROFILI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Ism:</b> <b>{html.escape(first_name)}</b>\n"
        f"🔗 <b>Username:</b> {username_text}\n"
        f"📊 <b>Holati:</b> {status}\n"
        f"👑 <b>Roli:</b> <code>{role}</code>\n\n"
        f"💰 <b>Balans:</b> <code>{balance:,.2f}</code> so'm\n"
        f"💳 <b>Kiritgan puli:</b> <code>{total_deposited:,.2f}</code> so'm\n"
        f"📦 <b>Jami buyurtmalar:</b> <code>{orders_count}</code> ta\n"
        f"📅 <b>Ro'yxatdan o'tgan:</b> <code>{created_at}</code>\n"
    )

    if tg_name or tg_bio:
        text += "\n📱 <b>Telegram profil ma'lumotlari:</b>\n"
        if tg_name and tg_name != first_name:
            text += f"• TG Ismi: <b>{html.escape(tg_name)}</b>\n"
        if tg_bio:
            text += f"• Bio: <i>{html.escape(tg_bio)}</i>\n"

    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.add(
        types.InlineKeyboardButton(
            "💰 Pul qo'shish",
            callback_data=f"admin_user_add:{user_id}"
        ),
        types.InlineKeyboardButton(
            "💸 Pul ayirish",
            callback_data=f"admin_user_sub:{user_id}"
        )
    )

    if banned:
        kb.add(
            types.InlineKeyboardButton(
                "🔓 Blokdan ochish",
                callback_data=f"admin_user_unban:{user_id}"
            )
        )
    else:
        kb.add(
            types.InlineKeyboardButton(
                "🚫 Bloklash",
                callback_data=f"admin_user_ban:{user_id}"
            )
        )

    # Batafsil Telegramda ko'rish havolasi!
    tg_url = f"https://t.me/{username.lstrip('@')}" if username else f"tg://user?id={user_id}"
    kb.add(
        types.InlineKeyboardButton(
            "✈️ Telegram profilini ochish",
            url=tg_url
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔎 Boshqa qidirish",
            callback_data="admin_user_search"
        ),
        types.InlineKeyboardButton(
            "↩️ Foydalanuvchilar",
            callback_data="admin_users"
        )
    )

    if message_id:
        try:
            bot.edit_message_text(
                text,
                bot_chat_id,
                message_id,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True
            )
        except Exception:
            bot.send_message(
                bot_chat_id,
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True
            )
    else:
        bot.send_message(
            bot_chat_id,
            text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True
        )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_user:")
)
def admin_user_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        user_id = int(call.data.split(":")[1])
    except Exception:
        return

    show_admin_user(
        call.message.chat.id,
        call.message.message_id,
        user_id
    )


# ============================================================
# ADMIN — PUL QO'SHISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_user_add:")
)
def admin_user_add_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        user_id = int(call.data.split(":")[1])
    except Exception:
        return

    set_state(
        call.from_user.id,
        f"admin_user_add:{user_id}"
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "↩️ Bekor qilish",
            callback_data=f"admin_user:{user_id}"
        )
    )

    bot.edit_message_text(
        "💰 <b>PUL QO'SHISH</b>\n\n"
        f"🆔 Foydalanuvchi: <code>{user_id}</code>\n\n"
        "💵 Qancha pul qo'shmoqchisiz?\n"
        "Masalan: <code>10000</code>\n\n"
        "❌ Bekor qilish: /cancel",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


# ============================================================
# ADMIN — PUL AYIRISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_user_sub:")
)
def admin_user_sub_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        user_id = int(call.data.split(":")[1])
    except Exception:
        return

    set_state(
        call.from_user.id,
        f"admin_user_sub:{user_id}"
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "↩️ Bekor qilish",
            callback_data=f"admin_user:{user_id}"
        )
    )

    bot.edit_message_text(
        "💸 <b>PUL AYIRISH</b>\n\n"
        f"🆔 Foydalanuvchi: <code>{user_id}</code>\n\n"
        "💵 Qancha pul ayirmoqchisiz?\n"
        "Masalan: <code>10000</code>\n\n"
        "⚠️ Balansdan ko'p miqdor ayirib bo'lmaydi.\n\n"
        "❌ Bekor qilish: /cancel",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


# ============================================================
# ADMIN — BLOKLASH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_user_ban:")
)
def admin_user_ban_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        user_id = int(call.data.split(":")[1])
    except Exception:
        return

    conn = db()

    try:
        cur = conn.execute(
            """
            UPDATE users
            SET banned = 1
            WHERE user_id = ?
            """,
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        bot.answer_callback_query(
            call.id,
            "❌ Foydalanuvchi topilmadi",
            show_alert=True
        )
        return

    bot.answer_callback_query(
        call.id,
        "🚫 Foydalanuvchi bloklandi"
    )

    show_admin_user(
        call.message.chat.id,
        call.message.message_id,
        user_id
    )


# ============================================================
# ADMIN — BLOKDAN OCHISH
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("admin_user_unban:")
)
def admin_user_unban_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        user_id = int(call.data.split(":")[1])
    except Exception:
        return

    conn = db()

    try:
        cur = conn.execute(
            """
            UPDATE users
            SET banned = 0
            WHERE user_id = ?
            """,
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        bot.answer_callback_query(
            call.id,
            "❌ Foydalanuvchi topilmadi",
            show_alert=True
        )
        return

    bot.answer_callback_query(
        call.id,
        "🔓 Blokdan ochildi"
    )

    show_admin_user(
        call.message.chat.id,
        call.message.message_id,
        user_id
    )


# ============================================================
# ADMIN — PUL / QIDIRUV MESSAGE HANDLER
# ============================================================

@bot.message_handler(
    func=lambda message:
        is_admin(message.from_user)
        and (
            get_state(message.from_user.id).get("state") == "admin_user_search"
            or str(get_state(message.from_user.id).get("state", "")).startswith("admin_user_add:")
            or str(get_state(message.from_user.id).get("state", "")).startswith("admin_user_sub:")
        )
)
def admin_users_input(message):
    state_data = get_state(message.from_user.id)
    state = state_data.get("state", "")
    value = (message.text or "").strip()

    if value.lower() == "/cancel":
        clear_state(message.from_user.id)

        bot.send_message(
            message.chat.id,
            "↩️ Amal bekor qilindi."
        )

        admin_users_main(message.chat.id)
        return

    # --------------------------------------------------------
    # QIDIRUV
    # --------------------------------------------------------
    if state == "admin_user_search":
        search = value.lstrip("@").strip()

        if not search:
            bot.send_message(
                message.chat.id,
                "❌ ID yoki username yuboring."
            )
            return

        conn = db()
        rows = []

        try:
            if search.isdigit():
                rows = conn.execute(
                    """
                    SELECT user_id, username, first_name
                    FROM users
                    WHERE user_id = ?
                    LIMIT 10
                    """,
                    (int(search),)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id, username, first_name
                    FROM users
                    WHERE LOWER(username) = LOWER(?)
                       OR LOWER(username) LIKE LOWER(?)
                    ORDER BY user_id DESC
                    LIMIT 10
                    """,
                    (search, f"%{search}%")
                ).fetchall()
        finally:
            conn.close()

        clear_state(message.from_user.id)

        # Agar bazada topilmasa, Telegram orqali jonli qidirib ko'ramiz
        if not rows:
            tg_chat = None
            try:
                if search.isdigit():
                    tg_chat = bot.get_chat(int(search))
                else:
                    tg_chat = bot.get_chat("@" + search)
            except Exception as e:
                print("⚠️ Telegram get_chat xatosi:", repr(e))

            if tg_chat:
                show_admin_user(
                    message.chat.id,
                    None,
                    tg_chat.id
                )
                return

            bot.send_message(
                message.chat.id,
                f"❌ Foydalanuvchi topilmadi: <code>{html.escape(value)}</code>\n\n"
                "Iltimos, to'g'ri Telegram ID yoki username kiriting.",
                parse_mode="HTML"
            )
            admin_users_main(message.chat.id)
            return

        if len(rows) == 1:
            show_admin_user(
                message.chat.id,
                None,
                rows[0]["user_id"]
            )
            return

        kb = types.InlineKeyboardMarkup()

        for row in rows:
            name = (row["first_name"] or "Foydalanuvchi").strip()
            uname = f" (@{row['username']})" if row["username"] else ""
            btn_text = f"👤 {name}{uname} [{row['user_id']}]"
            if len(btn_text) > 35:
                btn_text = btn_text[:32] + "..."
            kb.add(
                types.InlineKeyboardButton(
                    btn_text,
                    callback_data=f"admin_user:{row['user_id']}"
                )
            )

        kb.add(
            types.InlineKeyboardButton(
                "↩️ Bekor qilish",
                callback_data="admin_users"
            )
        )

        bot.send_message(
            message.chat.id,
            f"🔎 <b>{len(rows)}</b> ta foydalanuvchi topildi.\n"
            "Keraklisini tanlang:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    # --------------------------------------------------------
    # PUL QO'SHISH / AYIRISH
    # --------------------------------------------------------
    try:
        amount = float(value.replace(",", ".").replace(" ", ""))
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Faqat raqam kiriting.\n\n"
            "Masalan: <code>10</code>",
            parse_mode="HTML"
        )
        return

    if amount <= 0:
        bot.send_message(
            message.chat.id,
            "❌ Miqdor 0 dan katta bo'lishi kerak."
        )
        return

    if amount > 1_000_000_000:
        bot.send_message(
            message.chat.id,
            "❌ Juda katta summa."
        )
        return

    if state.startswith("admin_user_add:"):
        user_id = int(state.split(":")[1])

        conn = db()

        try:
            cur = conn.execute(
                """
                UPDATE users
                SET
                    balance = COALESCE(balance, 0) + ?,
                    total_deposited = COALESCE(total_deposited, 0) + ?
                WHERE user_id = ?
                """,
                (amount, amount, user_id)
            )
            conn.commit()
        finally:
            conn.close()

        clear_state(message.from_user.id)

        if cur.rowcount == 0:
            bot.send_message(
                message.chat.id,
                "❌ Foydalanuvchi topilmadi."
            )
            return

        bot.send_message(
            message.chat.id,
            f"✅ <b>{amount:,.2f} so'm</b> qo'shildi.\n"
            f"🆔 ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )

        show_admin_user(
            message.chat.id,
            None,
            user_id
        )
        return

    if state.startswith("admin_user_sub:"):
        user_id = int(state.split(":")[1])

        conn = db()

        try:
            cur = conn.execute(
                """
                UPDATE users
                SET balance = balance - ?
                WHERE user_id = ?
                  AND COALESCE(balance, 0) >= ?
                """,
                (amount, user_id, amount)
            )
            conn.commit()

            if cur.rowcount:
                row = conn.execute(
                    """
                    SELECT balance
                    FROM users
                    WHERE user_id = ?
                    """,
                    (user_id,)
                ).fetchone()
                new_balance = float(row["balance"] or 0)
            else:
                exists = conn.execute(
                    """
                    SELECT balance
                    FROM users
                    WHERE user_id = ?
                    """,
                    (user_id,)
                ).fetchone()

                new_balance = (
                    float(exists["balance"] or 0)
                    if exists
                    else None
                )

        finally:
            conn.close()

        clear_state(message.from_user.id)

        if new_balance is None:
            bot.send_message(
                message.chat.id,
                "❌ Foydalanuvchi topilmadi."
            )
            return

        if not cur.rowcount:
            bot.send_message(
                message.chat.id,
                "❌ Balans yetarli emas.\n\n"
                f"💰 Hozirgi balans: "
                f"<b>{new_balance:,.2f} so'm</b>",
                parse_mode="HTML"
            )

            show_admin_user(
                message.chat.id,
                None,
                user_id
            )
            return

        bot.send_message(
            message.chat.id,
            f"✅ <b>{amount:,.2f} so'm</b> ayirildi.\n"
            f"💰 Yangi balans: <b>{new_balance:,.2f} so'm</b>",
            parse_mode="HTML"
        )

        show_admin_user(
            message.chat.id,
            None,
            user_id
        )
        return


# ============================================================
# ADMIN — DEPOZITLAR
# ============================================================


# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "admin_deposits"
)
def admin_deposits_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    conn = db()

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM deposits"
        ).fetchone()[0]

        paid = conn.execute(
            """
            SELECT COUNT(*)
            FROM deposits
            WHERE LOWER(COALESCE(status, '')) IN
            ('paid', 'success', 'successful', 'completed')
            """
        ).fetchone()[0]

        pending = conn.execute(
            """
            SELECT COUNT(*)
            FROM deposits
            WHERE LOWER(COALESCE(status, '')) = 'pending'
            """
        ).fetchone()[0]

        amount = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM deposits
            WHERE LOWER(COALESCE(status, '')) IN
            ('paid', 'success', 'successful', 'completed')
            """
        ).fetchone()[0] or 0

        stars = conn.execute(
            """
            SELECT COUNT(*)
            FROM deposits
            WHERE method = 'Telegram Stars'
            """
        ).fetchone()[0]

    finally:
        conn.close()

    text = (
        "💰 <b>DEPOZITLAR</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 Jami: <b>{total:,}</b>\n"
        f"✅ Muvaffaqiyatli: <b>{paid:,}</b>\n"
        f"⏳ Kutilmoqda: <b>{pending:,}</b>\n"
        f"⭐ Stars to'lovlari: <b>{stars:,}</b>\n\n"
        f"💵 Muvaffaqiyatli summa: <b>{float(amount):,.2f}</b> so'm"
    )

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "↩️ Admin panel",
            callback_data="admin_settings"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


# ============================================================
# ADMIN — API LAR
# ============================================================


# ============================================================
# ADMIN — API LAR
# ============================================================

def api_panel_text():
    conn = db()

    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM apis"
        ).fetchone()[0]

        active = conn.execute(
            """
            SELECT COUNT(*)
            FROM apis
            WHERE COALESCE(active, 1) = 1
            """
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT id, name, url, active
            FROM apis
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    finally:
        conn.close()

    lines = [
        "🔑 <b>API LAR</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🔑 Jami: <b>{total:,}</b>",
        f"🟢 Faol: <b>{active:,}</b>",
        "",
        "Quyidagi API'ni boshqarish uchun tanlang:"
    ]

    if not rows:
        lines.extend([
            "",
            "📭 Hozircha API mavjud emas."
        ])

    return "\n".join(lines), rows


def api_panel_keyboard(rows):
    kb = types.InlineKeyboardMarkup(row_width=1)

    for row in rows:
        status = "🟢" if row["active"] else "🔴"
        name = row["name"] or "Nomsiz"

        kb.add(
            types.InlineKeyboardButton(
                f"{status} #{row['id']} {name}",
                callback_data=f"api_view:{row['id']}"
            )
        )

    kb.add(
        types.InlineKeyboardButton(
            "➕ Yangi API qo'shish",
            callback_data="api_add_start"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔄 Barcha xizmatlarni qayta yuklash",
            callback_data="api_sync_all"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Admin panel",
            callback_data="admin_settings"
        )
    )

    return kb


def show_api_panel(chat_id, message_id=None):
    text, rows = api_panel_text()
    kb = api_panel_keyboard(rows)

    if message_id:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=kb
        )


@bot.callback_query_handler(
    func=lambda call: call.data == "admin_apis"
)
def admin_apis_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    show_api_panel(
        call.message.chat.id,
        call.message.message_id
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("api_view:")
)
def api_view_callback(call):
    if not is_admin(call.from_user):
        return

    bot.answer_callback_query(call.id)

    try:
        api_id = int(call.data.split(":", 1)[1])
    except Exception:
        return

    api = get_api_by_id(api_id)

    if not api:
        bot.answer_callback_query(
            call.id,
            "❌ API topilmadi.",
            show_alert=True
        )
        return

    status = "🟢 Faol" if api["active"] else "🔴 O'chirilgan"

    url = api["url"] or "-"
    safe_url = str(url).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    conn = db()

    try:
        service_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM services
            WHERE api_id = ?
            """,
            (api_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    text = (
        f"🔌 <b>API #{api_id}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 Nomi: <b>{api['name'] or 'Nomsiz'}</b>\n"
        f"🟢 Holat: <b>{status}</b>\n"
        f"🔗 URL: <code>{safe_url}</code>\n"
        f"🛠 Xizmatlar: <b>{service_count:,}</b>\n"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)

    sync_button = types.InlineKeyboardButton(
        "🔄 Sinxronlash",
        callback_data=f"api_sync:{api_id}"
    )

    toggle_text = (
        "🔴 O'chirish"
        if api["active"]
        else "🟢 Faollashtirish"
    )

    toggle_button = types.InlineKeyboardButton(
        toggle_text,
        callback_data=f"api_toggle:{api_id}"
    )

    kb.row(
        sync_button,
        toggle_button
    )

    kb.add(
        types.InlineKeyboardButton(
            "🗑 API'ni o'chirish",
            callback_data=f"api_delete_confirm:{api_id}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ API LAR",
            callback_data="admin_apis"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("api_toggle:")
)
def api_toggle_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        api_id = int(call.data.split(":", 1)[1])
    except Exception:
        bot.answer_callback_query(call.id, "❌ Noto'g'ri API ID.")
        return

    conn = db()

    try:
        row = conn.execute(
            "SELECT active FROM apis WHERE id = ?",
            (api_id,)
        ).fetchone()

        if not row:
            bot.answer_callback_query(
                call.id,
                "❌ API topilmadi.",
                show_alert=True
            )
            return

        new_status = 0 if row["active"] else 1

        conn.execute(
            """
            UPDATE apis
            SET active = ?
            WHERE id = ?
            """,
            (new_status, api_id)
        )

        conn.commit()

    finally:
        conn.close()

    bot.answer_callback_query(
        call.id,
        "🟢 API faollashtirildi."
        if new_status
        else "🔴 API o'chirildi."
    )

    api_view_callback(call)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("api_sync:")
)
def api_sync_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        api_id = int(call.data.split(":", 1)[1])
    except Exception:
        bot.answer_callback_query(call.id, "❌ Noto'g'ri API ID.")
        return

    bot.answer_callback_query(
        call.id,
        "🔄 Sinxronlash boshlandi..."
    )

    try:
        result = sync_api_services(api_id)

        text = (
            "✅ <b>API SINXRONLANDI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔌 API: <b>#{api_id}</b>\n"
            f"📦 Jami: <b>{result['total']:,}</b>\n"
            f"🆕 Yangi: <b>{result['new']:,}</b>\n"
            f"🔄 Yangilangan: <b>{result['updated']:,}</b>\n"
            f"⏺ O'zgarmagan: <b>{result['unchanged']:,}</b>\n"
            f"⏭ O'tkazib yuborilgan: <b>{result['skipped']:,}</b>"
        )

    except Exception as e:
        print(
            "❌ API SYNC CALLBACK ERROR:",
            repr(e)
        )

        text = (
            "❌ <b>API SINXRONLASHDA XATO</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{str(e)}</code>"
        )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "↩️ API tafsilotlari",
            callback_data=f"api_view:{api_id}"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "🔑 API LAR",
            callback_data="admin_apis"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "api_sync_all"
)
def api_sync_all_callback(call):
    if not is_admin(call.from_user):
        return

    bot.answer_callback_query(
        call.id,
        "🔄 Barcha xizmatlar yuklanmoqda..."
    )

    conn = db()

    try:
        apis = conn.execute(
            """
            SELECT id, name
            FROM apis
            WHERE COALESCE(active, 1) = 1
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    if not apis:
        bot.answer_callback_query(
            call.id,
            "📭 Faol API mavjud emas.",
            show_alert=True
        )
        return

    results = []
    total_new = 0
    total_updated = 0
    total_unchanged = 0
    total_deleted = 0
    total_skipped = 0

    for api in apis:
        try:
            result = sync_api_services(
                int(api["id"]),
                False
            )

            new_count = result.get("new", 0)
            updated_count = result.get("updated", 0)
            unchanged_count = result.get("unchanged", 0)
            deleted_count = result.get("deleted", 0)
            skipped_count = result.get("skipped", 0)

            total_new += new_count
            total_updated += updated_count
            total_unchanged += unchanged_count
            total_deleted += deleted_count
            total_skipped += skipped_count

            results.append(
                f"🔌 <b>#{api['id']} {api['name']}</b>"
                f"\n📦 Jami: {result.get('total', 0):,}"
                f"\n🆕 Yangi: {new_count:,}"
                f"\n🔄 Yangilangan: {updated_count:,}"
                f"\n🗑 O'chirilgan: {deleted_count:,}"
            )

        except Exception as e:
            results.append(
                f"❌ <b>#{api['id']} {api['name']}</b>"
                f"\n<code>{str(e)[:300]}</code>"
            )

    text = (
        "✅ <b>BARCHA XIZMATLAR QAYTA YUKLANDI</b>"
        "\n━━━━━━━━━━━━━━━━━━"
        "\n\n"
        + "\n\n".join(results)
        + "\n\n━━━━━━━━━━━━━━━━━━"
        f"\n🆕 Yangi: <b>{total_new:,}</b>"
        f"\n🔄 Yangilangan: <b>{total_updated:,}</b>"
        f"\n🗑 O'chirilgan: <b>{total_deleted:,}</b>"
        f"\n⏺ O'zgarmagan: <b>{total_unchanged:,}</b>"
        f"\n⏭ O'tkazilgan: <b>{total_skipped:,}</b>"
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "🔑 API LAR",
            callback_data="admin_apis"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("api_delete_confirm:")
)
def api_delete_confirm_callback(call):
    if not is_admin(call.from_user):
        return

    bot.answer_callback_query(call.id)

    try:
        api_id = int(call.data.split(":", 1)[1])
    except Exception:
        return

    api = get_api_by_id(api_id)

    if not api:
        bot.answer_callback_query(
            call.id,
            "❌ API topilmadi.",
            show_alert=True
        )
        return

    conn = db()

    try:
        service_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM services
            WHERE api_id = ?
            """,
            (api_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    text = (
        "⚠️ <b>API'NI O'CHIRISH</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 API: <b>#{api_id} {api['name']}</b>\n"
        f"🛠 Bog'langan xizmatlar: <b>{service_count:,}</b>\n\n"
        "API yozuvi o'chiriladi.\n"
        "Davom etishni tasdiqlaysizmi?"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.row(
        types.InlineKeyboardButton(
            "✅ Ha, o'chirish",
            callback_data=f"api_delete:{api_id}"
        ),
        types.InlineKeyboardButton(
            "❌ Bekor qilish",
            callback_data=f"api_view:{api_id}"
        )
    )

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith("api_delete:")
)
def api_delete_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        api_id = int(call.data.split(":", 1)[1])
    except Exception:
        bot.answer_callback_query(call.id, "❌ Noto'g'ri API ID.")
        return

    conn = db()

    try:
        row = conn.execute(
            "SELECT name FROM apis WHERE id = ?",
            (api_id,)
        ).fetchone()

        if not row:
            bot.answer_callback_query(
                call.id,
                "❌ API topilmadi.",
                show_alert=True
            )
            return

        # API ga tegishli xizmatlarni va ularning
        # kanal bog'lamalarini birinchi bo'lib o'chiramiz.
        service_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM services WHERE api_id = ?",
                (api_id,)
            ).fetchall()
        ]

        if service_ids:
            placeholders = ",".join("?" for _ in service_ids)

            conn.execute(
                f"""
                DELETE FROM channel_services
                WHERE service_id IN ({placeholders})
                """,
                service_ids
            )

            conn.execute(
                f"""
                DELETE FROM services
                WHERE id IN ({placeholders})
                """,
                service_ids
            )

        conn.execute(
            "DELETE FROM apis WHERE id = ?",
            (api_id,)
        )

        conn.commit()

        # Avtomatik lokal zaxira
        save_local_db_backup(reason=f"API #{api_id} o'chirildi")

    finally:
        conn.close()

    bot.answer_callback_query(
        call.id,
        "✅ API o'chirildi."
    )

    show_api_panel(
        call.message.chat.id,
        call.message.message_id
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "api_add_start"
)
def api_add_start_callback(call):
    if not is_admin(call.from_user):
        return

    bot.answer_callback_query(call.id)

    set_state(
        call.from_user.id,
        "api_add_name"
    )

    bot.edit_message_text(
        "➕ <b>YANGI API QO'SHISH</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ API nomini yuboring.\n\n"
        "Masalan:\n"
        "<code>@smm_xizmati_bot</code>\n\n"
        "❌ Bekor qilish: /cancel",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )


@bot.message_handler(
    content_types=["text"],
    func=lambda message: (
        is_admin(message.from_user)
        and get_state(message.from_user.id).get("state")
        in (
            "api_add_name",
            "api_add_url",
            "api_add_key"
        )
    )
)
def api_add_input(message):
    user_id = message.from_user.id
    value = (message.text or "").strip()

    if value == "/cancel":
        clear_state(user_id)

        bot.send_message(
            message.chat.id,
            "❌ API qo'shish bekor qilindi."
        )

        return

    state = get_state(user_id).get("state")

    if state == "api_add_name":
        if len(value) < 2:
            bot.send_message(
                message.chat.id,
                "❌ API nomi juda qisqa."
            )
            return

        set_state(
            user_id,
            "api_add_url",
            api_name=value
        )

        bot.send_message(
            message.chat.id,
            "2️⃣ API URL manzilini yuboring.\n\n"
            "Masalan:\n"
            "<code>https://example.com/api/v2</code>",
            parse_mode="HTML"
        )

        return

    if state == "api_add_url":
        if not (
            value.startswith("http://")
            or value.startswith("https://")
        ):
            bot.send_message(
                message.chat.id,
                "❌ URL http:// yoki https:// bilan boshlanishi kerak."
            )
            return

        current = get_state(user_id)

        set_state(
            user_id,
            "api_add_key",
            api_name=current.get("api_name"),
            api_url=value
        )

        bot.send_message(
            message.chat.id,
            "3️⃣ API keyni yuboring.\n\n"
            "🔐 Key Telegram'da qayta ko'rsatilmaydi."
        )

        return

    if state == "api_add_key":
        current = get_state(user_id)

        name = current.get("api_name")
        url = current.get("api_url")
        api_key = value

        if len(api_key) < 3:
            bot.send_message(
                message.chat.id,
                "❌ API key noto'g'ri yoki juda qisqa."
            )
            return

        conn = db()

        try:
            cur = conn.execute(
                """
                INSERT INTO apis
                    (name, url, api_key, active, created_at)
                VALUES
                    (?, ?, ?, 1, ?)
                """,
                (
                    name,
                    url,
                    api_key,
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            )

            api_id = cur.lastrowid
            conn.commit()

        finally:
            conn.close()

        clear_state(user_id)

        # Yangi API qo'shilganda lokal zaxira
        save_local_db_backup(reason=f"Yangi API #{api_id} ({name}) qo'shildi")

        bot.send_message(
            message.chat.id,
            "✅ <b>YANGI API QO'SHILDI</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔌 API: <b>#{api_id} {name}</b>\n"
            "🟢 Holat: <b>Faol</b>\n\n"
            "🔄 Xizmatlarni sinxronlash uchun API panelidan "
            "«Sinxronlash» tugmasini bosing.",
            parse_mode="HTML"
        )

        return


# ============================================================
# ADMIN — XIZMATLAR
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "admin_services"
)
def admin_services_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    state = get_state(call.from_user.id)

    if state.get("state") in (
        "admin_services_bulk",
        "admin_service_search"
    ):
        clear_state(call.from_user.id)

    kb = types.InlineKeyboardMarkup(row_width=1)

    kb.add(
        types.InlineKeyboardButton(
            "✅ Mavjud xizmatlar",
            callback_data="admin_services_api_list:active:0"
        ),
        types.InlineKeyboardButton(
            "❌ Mavjud emas xizmatlar",
            callback_data="admin_services_api_list:inactive:0"
        ),
        types.InlineKeyboardButton(
            "↩️ Admin panel",
            callback_data="admin_settings"
        )
    )

    bot.edit_message_text(
        "🛠 <b>XIZMATLAR</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Kerakli bo'limni tanlang:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


def get_admin_service_apis(status, page=0):
    """
    Xizmatlar uchun API ro'yxati.

    API tanlangandan keyin faqat shu API'ga
    tegishli xizmatlar ko'rsatiladi.
    """

    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0

    page_size = 8

    conn = db()

    try:
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM apis
            """
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT
                a.id,
                a.name,
                COALESCE(a.active, 0) AS active,
                (
                    SELECT COUNT(*)
                    FROM services s
                    WHERE s.api_id = a.id
                      AND COALESCE(s.admin_available, 0) = ?
                ) AS service_count
            FROM apis a
            ORDER BY a.id ASC
            LIMIT ? OFFSET ?
            """,
            (
                1 if status == "active" else 0,
                page_size,
                page * page_size
            )
        ).fetchall()

        return rows, int(total or 0)

    finally:
        conn.close()


def get_admin_service_rows(status, page=0, api_id=None):
    """
    Faqat tanlangan API ichidagi xizmatlar.

    active:
        admin_available = 1

    inactive:
        admin_available = 0
    """

    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0

    page_size = 10

    conn = db()

    try:
        wanted_status = 1 if status == "active" else 0

        where = [
            "COALESCE(s.admin_available, 0) = ?"
        ]
        params = [wanted_status]

        if api_id is not None:
            where.append("s.api_id = ?")
            params.append(int(api_id))

        where_sql = " AND ".join(where)

        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM services s
            WHERE {where_sql}
            """,
            tuple(params)
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT
                s.id,
                s.api_id,
                s.external_id,
                s.name,
                s.category,
                COALESCE(s.price, 0) AS price,
                s.min_qty,
                s.max_qty,
                COALESCE(s.active, 0) AS active,
                COALESCE(s.admin_available, 0) AS admin_available,
                COALESCE(a.name, 'API #' || s.api_id) AS api_name
            FROM services s
            LEFT JOIN apis a
                ON a.id = s.api_id
            WHERE {where_sql}
            ORDER BY
                CASE
                    WHEN COALESCE(s.price, 0) = 0 THEN 0
                    ELSE 1
                END,
                COALESCE(s.price, 0) ASC,
                s.sort_order ASC,
                s.id ASC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (
                page_size,
                page * page_size
            )
        ).fetchall()

        return rows, int(total or 0)

    finally:
        conn.close()


def get_admin_bulk_selected(user_id, status, api_id=None):
    """
    Tanlangan xizmatlar.
    Tanlov API + status bo'yicha alohida saqlanadi.
    """

    state = get_state(user_id)

    if state.get("state") != "admin_services_bulk":
        return set()

    if state.get("admin_service_status") != status:
        return set()

    saved_api_id = state.get("admin_service_api_id")

    try:
        saved_api_id = int(saved_api_id)
    except (TypeError, ValueError):
        saved_api_id = 0

    if api_id is not None:
        try:
            if saved_api_id != int(api_id):
                return set()
        except (TypeError, ValueError):
            return set()

    selected = state.get("admin_service_selected", [])

    result = set()

    for service_id in selected:
        try:
            result.add(int(service_id))
        except (TypeError, ValueError):
            continue

    return result


def set_admin_bulk_selected(
    user_id,
    status,
    selected,
    api_id=None
):
    """
    Tanlangan xizmatlarni user_state ichida saqlaydi.
    """

    clean = set()

    for service_id in selected:
        try:
            clean.add(int(service_id))
        except (TypeError, ValueError):
            continue

    try:
        clean_api_id = int(api_id) if api_id is not None else 0
    except (TypeError, ValueError):
        clean_api_id = 0

    set_state(
        user_id,
        "admin_services_bulk",
        admin_service_status=status,
        admin_service_api_id=clean_api_id,
        admin_service_selected=sorted(clean)
    )


def admin_services_api_keyboard(
    status,
    page,
    total_pages
):
    kb = types.InlineKeyboardMarkup(row_width=1)

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"admin_services_api_list:"
                    f"{status}:{page - 1}"
                )
            )
        )

    nav.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop"
        )
    )

    if page + 1 < total_pages:
        nav.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"admin_services_api_list:"
                    f"{status}:{page + 1}"
                )
            )
        )

    kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Xizmatlar",
            callback_data="admin_services"
        )
    )

    return kb


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_services_api_list:"
    )
)
def admin_services_api_list_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        _, status, page_text = call.data.split(":", 2)

        if status not in ("active", "inactive"):
            raise ValueError("status")

        page = max(0, int(page_text))

    except (ValueError, IndexError):
        status = "active"
        page = 0

    rows, total = get_admin_service_apis(
        status,
        page
    )

    page_size = 8

    total_pages = max(
        1,
        (total + page_size - 1) // page_size
    )

    if page >= total_pages:
        page = total_pages - 1

        rows, total = get_admin_service_apis(
            status,
            page
        )

    if status == "active":
        title = "✅ <b>MAVJUD XIZMATLAR</b>"
        description = (
            "Avval API'ni tanlang. "
            "Keyin shu API'ning mavjud xizmatlari chiqadi."
        )
    else:
        title = "❌ <b>MAVJUD EMAS XIZMATLAR</b>"
        description = (
            "Avval API'ni tanlang. "
            "Keyin shu API'ning yashirilgan xizmatlari chiqadi."
        )

    text = (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 API'lar: <b>{total}</b> ta\n"
        f"📄 Sahifa: <b>{page + 1}/{total_pages}</b>\n\n"
        f"ℹ️ {description}\n"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)

    if not rows:
        text += "\n📭 API topilmadi."

    for row in rows:
        api_id = int(row["id"])
        api_name = row["name"] or f"API #{api_id}"

        service_count = int(
            row["service_count"] or 0
        )

        api_status = (
            "🟢"
            if int(row["active"] or 0)
            else "🔴"
        )

        kb.add(
            types.InlineKeyboardButton(
                f"{api_status} #{api_id} {api_name} "
                f"({service_count} ta)",
                callback_data=(
                    f"admin_services_api_select:"
                    f"{status}:{api_id}:0"
                )
            )
        )

    nav_kb = admin_services_api_keyboard(
        status,
        page,
        total_pages
    )

    for nav_row in nav_kb.keyboard:
        kb.row(*nav_row)

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_services_api_select:"
    )
)
def admin_services_api_select_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        _, status, api_id_text, page_text = (
            call.data.split(":", 3)
        )

        if status not in ("active", "inactive"):
            raise ValueError("status")

        api_id = int(api_id_text)
        page = max(0, int(page_text))

        if api_id <= 0:
            raise ValueError("api_id")

    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ API ma'lumoti noto'g'ri.",
            show_alert=True
        )
        return

    clear_state(call.from_user.id)

    proxy = type(
        "CallbackProxy",
        (),
        {
            "id": call.id,
            "from_user": call.from_user,
            "data": (
                f"admin_services_list:"
                f"{status}:{api_id}:{page}"
            ),
            "message": call.message,
        }
    )()

    admin_services_list_callback(proxy)


def admin_services_bulk_keyboard(
    status,
    api_id,
    page,
    total_pages,
    selected
):
    kb = types.InlineKeyboardMarkup(row_width=1)

    if selected:
        count = len(selected)

        if status == "active":
            action_text = (
                f"🗑 Tanlanganlarni olib tashlash ({count})"
            )
        else:
            action_text = (
                f"➕ Tanlanganlarni qo'shish ({count})"
            )

        kb.add(
            types.InlineKeyboardButton(
                action_text,
                callback_data=(
                    f"admin_services_bulk_action:"
                    f"{status}:{api_id}"
                )
            )
        )

    nav = []

    if page > 0:
        nav.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"admin_services_list:"
                    f"{status}:{api_id}:{page - 1}"
                )
            )
        )

    nav.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop"
        )
    )

    if page + 1 < total_pages:
        nav.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"admin_services_list:"
                    f"{status}:{api_id}:{page + 1}"
                )
            )
        )

    kb.row(*nav)

    kb.add(
        types.InlineKeyboardButton(
            "🔎 Service ID qidirish",
            callback_data=(
                f"admin_service_search_start:"
                f"{status}:{api_id}:{page}"
            )
        ),
        types.InlineKeyboardButton(
            "🔌 API'lar",
            callback_data=(
                f"admin_services_api_list:"
                f"{status}:0"
            )
        ),
        types.InlineKeyboardButton(
            "↩️ Xizmatlar",
            callback_data="admin_services"
        )
    )

    return kb


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_services_list:"
    )
)
def admin_services_list_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    try:
        _, status, api_id_text, page_text = (
            call.data.split(":", 3)
        )

        if status not in ("active", "inactive"):
            raise ValueError("status")

        api_id = int(api_id_text)
        page = max(0, int(page_text))

        if api_id <= 0:
            raise ValueError("api_id")

    except (ValueError, IndexError):
        status = "active"
        api_id = 0
        page = 0

    state = get_state(call.from_user.id)

    if (
        state.get("state") != "admin_services_bulk"
        or state.get("admin_service_status") != status
        or int(state.get("admin_service_api_id", 0) or 0) != api_id
    ):
        set_admin_bulk_selected(
            call.from_user.id,
            status,
            set(),
            api_id
        )

    rows, total = get_admin_service_rows(
        status,
        page,
        api_id
    )

    page_size = 10

    total_pages = max(
        1,
        (total + page_size - 1) // page_size
    )

    if page >= total_pages:
        page = total_pages - 1

        rows, total = get_admin_service_rows(
            status,
            page,
            api_id
        )

    selected = get_admin_bulk_selected(
        call.from_user.id,
        status,
        api_id
    )

    if status == "active":
        title = "✅ <b>MAVJUD XIZMATLAR</b>"
        description = (
            "Bu API'dagi admin panelda mavjud xizmatlar."
        )
    else:
        title = "❌ <b>MAVJUD EMAS XIZMATLAR</b>"
        description = (
            "Bu API'dagi admin panelda yashirilgan xizmatlar."
        )

    api_name = f"API #{api_id}"

    conn = db()

    try:
        api_row = conn.execute(
            """
            SELECT name, COALESCE(active, 0) AS active
            FROM apis
            WHERE id = ?
            """,
            (api_id,)
        ).fetchone()

        if api_row:
            api_name = (
                f"#{api_id} "
                f"{api_row['name'] or 'Nomaʼlum API'}"
            )
    finally:
        conn.close()

    text = (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 API: <b>{api_name}</b>\n"
        f"📦 Jami: <b>{total:,}</b>\n"
        f"📄 Sahifa: <b>{page + 1}/{total_pages}</b>\n"
        f"ℹ️ {description}\n\n"
    )

    if selected:
        text += (
            f"☑️ Tanlangan: <b>{len(selected)}</b> ta\n"
        )
    else:
        text += "☐ Tanlangan: <b>0</b> ta\n"

    text += (
        "\n💡 Xizmat ustiga bosib tanlang.\n"
        "🔎 Kerakli API Service ID'ni aniq qidirishingiz mumkin."
    )

    kb = types.InlineKeyboardMarkup(row_width=1)

    if not rows:
        text += "\n\n📭 Bu bo'limda xizmat topilmadi."

    for row in rows:
        service_id = int(row["id"])

        external_id = str(
            row["external_id"]
            if row["external_id"] is not None
            else "-"
        )

        name = row["name"] or "Noma'lum xizmat"

        if len(name) > 35:
            name = name[:35].rstrip() + "..."

        price = float(row["price"] or 0)

        if price.is_integer():
            price_text = (
                f"{int(price):,}".replace(",", " ")
            )
        else:
            price_text = (
                f"{price:,.2f}".replace(",", " ")
            )

        prefix = (
            "☑️"
            if service_id in selected
            else "☐"
        )

        kb.add(
            types.InlineKeyboardButton(
                f"{prefix} ID {external_id} | "
                f"{name} — {price_text} so'm",
                callback_data=(
                    f"admin_service_select:"
                    f"{status}:{api_id}:{service_id}:{page}"
                )
            )
        )

    nav_kb = admin_services_bulk_keyboard(
        status,
        api_id,
        page,
        total_pages,
        selected
    )

    for nav_row in nav_kb.keyboard:
        kb.row(*nav_row)

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_service_select:"
    )
)
def admin_service_select_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        _, status, api_id_text, service_id_text, page_text = (
            call.data.split(":", 4)
        )

        if status not in ("active", "inactive"):
            raise ValueError("status")

        api_id = int(api_id_text)
        service_id = int(service_id_text)
        page = max(0, int(page_text))

    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Xizmat ma'lumoti noto'g'ri.",
            show_alert=True
        )
        return

    selected = get_admin_bulk_selected(
        call.from_user.id,
        status,
        api_id
    )

    if service_id in selected:
        selected.remove(service_id)
        message = "☐ Xizmat tanlovdan chiqarildi."
    else:
        selected.add(service_id)
        message = "☑️ Xizmat tanlandi."

    set_admin_bulk_selected(
        call.from_user.id,
        status,
        selected,
        api_id
    )

    bot.answer_callback_query(
        call.id,
        message
    )

    proxy = type(
        "CallbackProxy",
        (),
        {
            "id": call.id,
            "from_user": call.from_user,
            "data": (
                f"admin_services_list:"
                f"{status}:{api_id}:{page}"
            ),
            "message": call.message,
        }
    )()

    admin_services_list_callback(proxy)


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_services_bulk_action:"
    )
)
def admin_services_bulk_action_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        _, status, api_id_text = call.data.split(":", 2)

        if status not in ("active", "inactive"):
            raise ValueError("status")

        api_id = int(api_id_text)

        if api_id <= 0:
            raise ValueError("api_id")

    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Ma'lumot noto'g'ri.",
            show_alert=True
        )
        return

    selected = get_admin_bulk_selected(
        call.from_user.id,
        status,
        api_id
    )

    if not selected:
        bot.answer_callback_query(
            call.id,
            "⚠️ Hech qanday xizmat tanlanmagan.",
            show_alert=True
        )
        return

    placeholders = ",".join(
               "?" for _ in selected
    )

    conn = db()

    try:
        rows = conn.execute(
            f"""
            SELECT
                id,
                api_id,
                name,
                external_id
            FROM services
            WHERE api_id = ?
              AND id IN ({placeholders})
            """,
            (
                api_id,
                *sorted(selected)
            )
        ).fetchall()

        valid_ids = {
            int(row["id"])
            for row in rows
        }

        selected = selected & valid_ids

        if not selected:
            bot.answer_callback_query(
                call.id,
                "❌ Tanlangan xizmatlar topilmadi.",
                show_alert=True
            )
            return

        new_status = (
            0
            if status == "active"
            else 1
        )

        placeholders = ",".join(
            "?" for _ in selected
        )

        if new_status == 1:
            conn.execute(
                f"""
                UPDATE services
                SET admin_available = 1, active = 1
                WHERE api_id = ?
                  AND id IN ({placeholders})
                """,
                (
                    api_id,
                    *sorted(selected)
                )
            )
        else:
            conn.execute(
                f"""
                UPDATE services
                SET admin_available = 0
                WHERE api_id = ?
                  AND id IN ({placeholders})
                """,
                (
                    api_id,
                    *sorted(selected)
                )
            )

        conn.commit()

        changed_count = len(selected)

    finally:
        conn.close()

    clear_state(call.from_user.id)

    if new_status == 1:
        bot.answer_callback_query(
            call.id,
            f"✅ {changed_count} ta xizmat qo'shildi."
        )
    else:
        bot.answer_callback_query(
            call.id,
            f"🗑 {changed_count} ta xizmat olib tashlandi."
        )

    target_status = status

    proxy = type(
        "CallbackProxy",
        (),
        {
            "id": call.id,
            "from_user": call.from_user,
            "data": (
                f"admin_services_list:"
                f"{target_status}:{api_id}:0"
            ),
            "message": call.message,
        }
    )()

    admin_services_list_callback(proxy)


# ============================================================
# ADMIN — SERVICE ID QIDIRUV
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_service_search_start:"
    )
)
def admin_service_search_start_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        _, status, api_id_text, page_text = (
            call.data.split(":", 3)
        )

        if status not in ("active", "inactive"):
            raise ValueError("status")

        api_id = int(api_id_text)
        page = max(0, int(page_text))

        if api_id <= 0:
            raise ValueError("api_id")

    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Qidiruv ma'lumoti noto'g'ri.",
            show_alert=True
        )
        return

    set_state(
        call.from_user.id,
        "admin_service_search",
        admin_service_status=status,
        admin_service_api_id=api_id,
        admin_service_page=page
    )

    bot.answer_callback_query(call.id)

    bot.edit_message_text(
        "🔎 <b>SERVICE ID QIDIRISH</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 API: <b>#{api_id}</b>\n\n"
        "API'dagi Service ID'ni yuboring.\n\n"
        "Masalan:\n"
        "<code>1234</code>\n\n"
        "❌ Bekor qilish: <code>/cancel</code>",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )


@bot.message_handler(
    content_types=["text"],
    func=lambda message: (
        is_admin(message.from_user)
        and get_state(message.from_user.id).get("state")
        == "admin_service_search"
    )
)
def admin_service_search_input(message):
    user_id = message.from_user.id
    value = (message.text or "").strip()

    current = get_state(user_id)

    if value.lower() == "/cancel":
        status = current.get(
            "admin_service_status",
            "active"
        )

        try:
            api_id = int(
                current.get(
                    "admin_service_api_id",
                    0
                )
            )
        except (TypeError, ValueError):
            api_id = 0

        try:
            page = int(
                current.get(
                    "admin_service_page",
                    0
                )
            )
        except (TypeError, ValueError):
            page = 0

        clear_state(user_id)

        proxy = type(
            "CallbackProxy",
            (),
            {
                "id": "search_cancel",
                "from_user": message.from_user,
                "data": (
                    f"admin_services_list:"
                    f"{status}:{api_id}:{page}"
                ),
                "message": type(
                    "MessageProxy",
                    (),
                    {
                        "chat": message.chat,
                        "message_id": getattr(
                            message,
                            "message_id",
                            0
                        )
                    }
                )()
            }
        )()

        bot.send_message(
            message.chat.id,
            "↩️ Qidiruv bekor qilindi."
        )

        # Yangi xabar yuborilgani uchun eski xabarni
        # o'zgartirish emas, yangi ro'yxatni yuboramiz.
        rows, total = get_admin_service_rows(
            status,
            page,
            api_id
        )

        page_size = 10
        total_pages = max(
            1,
            (total + page_size - 1) // page_size
        )

        selected = get_admin_bulk_selected(
            user_id,
            status,
            api_id
        )

        api_name = f"API #{api_id}"

        conn = db()

        try:
            api_row = conn.execute(
                """
                SELECT name
                FROM apis
                WHERE id = ?
                """,
                (api_id,)
            ).fetchone()

            if api_row:
                api_name = (
                    f"#{api_id} "
                    f"{api_row['name'] or 'Nomaʼlum API'}"
                )
        finally:
            conn.close()

        title = (
            "✅ <b>MAVJUD XIZMATLAR</b>"
            if status == "active"
            else "❌ <b>MAVJUD EMAS XIZMATLAR</b>"
        )

        kb = types.InlineKeyboardMarkup(row_width=1)

        for row in rows:
            service_id = int(row["id"])
            external_id = str(
                row["external_id"]
                if row["external_id"] is not None
                else "-"
            )

            name = row["name"] or "Noma'lum xizmat"

            if len(name) > 35:
                name = name[:35].rstrip() + "..."

            price = float(row["price"] or 0)

            if price.is_integer():
                price_text = (
                    f"{int(price):,}".replace(",", " ")
                )
            else:
                price_text = (
                    f"{price:,.2f}".replace(",", " ")
                )

            prefix = (
                "☑️"
                if service_id in selected
                else "☐"
            )

            kb.add(
                types.InlineKeyboardButton(
                    f"{prefix} ID {external_id} | "
                    f"{name} — {price_text} so'm",
                    callback_data=(
                        f"admin_service_select:"
                        f"{status}:{api_id}:"
                        f"{service_id}:{page}"
                    )
                )
            )

        nav_kb = admin_services_bulk_keyboard(
            status,
            api_id,
            page,
            total_pages,
            selected
        )

        for nav_row in nav_kb.keyboard:
            kb.row(*nav_row)

        bot.send_message(
            message.chat.id,
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"🔌 API: <b>{api_name}</b>\n"
            f"📦 Jami: <b>{total:,}</b>\n"
            f"📄 Sahifa: <b>{page + 1}/{total_pages}</b>",
            parse_mode="HTML",
            reply_markup=kb
        )

        return

    # Faqat raqamli Service ID qabul qilamiz.
    if not value.isdigit():
        bot.send_message(
            message.chat.id,
            "❌ Service ID faqat raqam bo'lishi kerak.\n\n"
            "Masalan: <code>1234</code>",
            parse_mode="HTML"
        )
        return

    try:
        external_id = int(value)
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Service ID noto'g'ri."
        )
        return

    try:
        api_id = int(
            current.get(
                "admin_service_api_id",
                0
            )
        )
    except (TypeError, ValueError):
        api_id = 0

    status = current.get(
        "admin_service_status",
        "active"
    )

    try:
        page = int(
            current.get(
                "admin_service_page",
                0
            )
        )
    except (TypeError, ValueError):
        page = 0

    if api_id <= 0:
        clear_state(user_id)

        bot.send_message(
            message.chat.id,
            "❌ API ma'lumoti topilmadi. Qidiruvni qaytadan boshlang."
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                s.id,
                s.api_id,
                s.external_id,
                s.name,
                s.category,
                COALESCE(s.price, 0) AS price,
                s.min_qty,
                s.max_qty,
                COALESCE(s.active, 0) AS active,
                COALESCE(s.admin_available, 0) AS admin_available,
                COALESCE(a.name, 'API #' || s.api_id) AS api_name
            FROM services s
            LEFT JOIN apis a
                ON a.id = s.api_id
            WHERE s.api_id = ?
              AND CAST(s.external_id AS TEXT) = ?
            LIMIT 1
            """,
            (
                api_id,
                str(external_id)
            )
        ).fetchone()

    finally:
        conn.close()

    if not row:
        bot.send_message(
            message.chat.id,
            "❌ <b>TOPILMADI</b>\n\n"
            f"🔌 API: <b>#{api_id}</b>\n"
            f"🆔 Service ID: <code>{external_id}</code>\n\n"
            "Bu Service ID ushbu API'da mavjud emas.",
            parse_mode="HTML"
        )
        return

    actual_available = int(
        row["admin_available"] or 0
    )

    wanted_available = (
        1
        if status == "active"
        else 0
    )

    name = row["name"] or "Noma'lum xizmat"

    price = float(row["price"] or 0)

    if price.is_integer():
        price_text = (
            f"{int(price):,}".replace(",", " ")
        )
    else:
        price_text = (
            f"{price:,.2f}".replace(",", " ")
        )

    current_status_text = (
        "✅ Mavjud"
        if actual_available
        else "❌ Mavjud emas"
    )

    if actual_available:
        action_text = "🗑 O'chirish"
        action_callback = (
            f"admin_service_search_toggle:"
            f"{api_id}:{int(row['id'])}:0"
        )
    else:
        action_text = "➕ Qo'shish"
        action_callback = (
            f"admin_service_search_toggle:"
            f"{api_id}:{int(row['id'])}:1"
        )

    clear_state(user_id)

    kb = types.InlineKeyboardMarkup(row_width=1)

    kb.add(
        types.InlineKeyboardButton(
            action_text,
            callback_data=action_callback
        ),
        types.InlineKeyboardButton(
            "🔎 Yana qidirish",
            callback_data=(
                f"admin_service_search_start:"
                f"{status}:{api_id}:{page}"
            )
        ),
        types.InlineKeyboardButton(
            "↩️ Xizmatlar",
            callback_data=(
                f"admin_services_list:"
                f"{status}:{api_id}:{page}"
            )
        )
    )

    bot.send_message(
        message.chat.id,
        "🔎 <b>QIDIRUV NATIJASI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🔌 API: <b>{row['api_name']}</b> "
        f"(#{api_id})\n"
        f"🆔 API Service ID: "
        f"<code>{row['external_id']}</code>\n"
        f"📦 Xizmat: <b>{name}</b>\n"
        f"💵 Narx: <b>{price_text} so'm</b>\n"
        f"📂 Kategoriya: "
        f"{row['category'] or '-'}\n"
        f"📊 Holat: <b>{current_status_text}</b>",
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data.startswith(
        "admin_service_search_toggle:"
    )
)
def admin_service_search_toggle_callback(call):
    if not is_admin(call.from_user):
        return

    try:
        _, api_id_text, service_id_text, new_status_text = (
            call.data.split(":", 3)
        )

        api_id = int(api_id_text)
        service_id = int(service_id_text)
        new_status = int(new_status_text)

        if api_id <= 0:
            raise ValueError("api_id")

        if service_id <= 0:
            raise ValueError("service_id")

        if new_status not in (0, 1):
            raise ValueError("status")

    except (ValueError, IndexError):
        bot.answer_callback_query(
            call.id,
            "❌ Ma'lumot noto'g'ri.",
            show_alert=True
        )
        return

    conn = db()

    try:
        row = conn.execute(
            """
            SELECT
                id,
                name,
                external_id
            FROM services
            WHERE id = ?
              AND api_id = ?
            """,
            (
                service_id,
                api_id
            )
        ).fetchone()

        if not row:
            bot.answer_callback_query(
                call.id,
                "❌ Xizmat topilmadi.",
                show_alert=True
            )
            return

        if new_status == 1:
            conn.execute(
                """
                UPDATE services
                SET admin_available = 1, active = 1
                WHERE id = ?
                  AND api_id = ?
                """,
                (
                    service_id,
                    api_id
                )
            )
        else:
            conn.execute(
                """
                UPDATE services
                SET admin_available = 0
                WHERE id = ?
                  AND api_id = ?
                """,
                (
                    service_id,
                    api_id
                )
            )

        conn.commit()

    finally:
        conn.close()

    bot.answer_callback_query(
        call.id,
        (
            "✅ Xizmat qo'shildi."
            if new_status == 1
            else "🗑 Xizmat olib tashlandi."
        )
    )

    status = (
        "active"
        if new_status == 1
        else "inactive"
    )

    proxy = type(
        "CallbackProxy",
        (),
        {
            "id": call.id,
            "from_user": call.from_user,
            "data": (
                f"admin_services_list:"
                f"{status}:{api_id}:0"
            ),
            "message": call.message,
        }
    )()

    admin_services_list_callback(proxy)


# ============================================================
# 🤖 AI AGENT 2.0
# ============================================================

def init_ai_tables():
    """AI uchun mavjud DB jadvallarini yaratish/moslashtirish."""
    conn = db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_agent_memory (
                admin_id INTEGER PRIMARY KEY,
                memory TEXT DEFAULT '',
                updated_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                question TEXT,
                plan TEXT,
                result TEXT,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                message_id INTEGER,
                direction TEXT,
                text TEXT,
                created_at TEXT
            )
        """)

        conn.commit()

    finally:
        conn.close()


def save_message_history(
    user_id,
    chat_id,
    message_id,
    direction,
    text
):
    """Foydalanuvchi xabarlarini AI tarixiga saqlaydi."""
    if not text:
        return

    conn = db()

    try:
        conn.execute(
            """
            INSERT INTO message_history
            (
                user_id,
                chat_id,
                message_id,
                direction,
                text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(chat_id),
                int(message_id),
                str(direction),
                str(text)[:4000],
                datetime.now().isoformat()
            )
        )

        conn.commit()

    finally:
        conn.close()


def ai_get_memory(admin_id):
    conn = db()

    try:
        row = conn.execute(
            """
            SELECT memory
            FROM ai_agent_memory
            WHERE admin_id = ?
            """,
            (int(admin_id),)
        ).fetchone()

        return (
            row["memory"]
            if row and row["memory"]
            else ""
        )

    finally:
        conn.close()


def ai_save_memory(admin_id, memory):
    conn = db()

    try:
        now = datetime.now().isoformat()

        conn.execute(
            """
            INSERT INTO ai_agent_memory
            (
                admin_id,
                memory,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(admin_id)
            DO UPDATE SET
                memory = excluded.memory,
                updated_at = excluded.updated_at
            """,
            (
                int(admin_id),
                str(memory)[:12000],
                now
            )
        )

        conn.commit()

    finally:
        conn.close()


def ai_get_recent_history(user_id, limit=20):
    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT
                direction,
                text,
                created_at
            FROM message_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                int(user_id),
                int(limit)
            )
        ).fetchall()

        return list(reversed(rows))

    finally:
        conn.close()


def ai_find_user(query):
    query = str(query or "").strip().lstrip("@")

    if not query:
        return None

    conn = db()

    try:
        if query.isdigit():
            return conn.execute(
                """
                SELECT *
                FROM users
                WHERE user_id = ?
                LIMIT 1
                """,
                (int(query),)
            ).fetchone()

        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE LOWER(username) = LOWER(?)
               OR LOWER(username) LIKE LOWER(?)
            ORDER BY user_id DESC
            LIMIT 1
            """,
            (
                query,
                "%" + query + "%"
            )
        ).fetchone()

    finally:
        conn.close()


def ai_collect_context(question):
    """
    AI uchun xavfsiz, read-only bot konteksti.
    Bu funksiya balansni o'zgartirmaydi,
    ban qilmaydi, buyurtma yubormaydi.
    """

    conn = db()

    try:
        users = conn.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]

        active_users = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM users
            WHERE COALESCE(banned, 0) = 0
            """
        ).fetchone()["c"]

        banned_users = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM users
            WHERE COALESCE(banned, 0) = 1
            """
        ).fetchone()["c"]

        orders = conn.execute(
            "SELECT COUNT(*) AS c FROM orders"
        ).fetchone()["c"]

        pending_orders = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM orders
            WHERE LOWER(COALESCE(status, '')) IN
            ('pending', 'processing', 'in progress')
            """
        ).fetchone()["c"]

        failed_orders = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM orders
            WHERE LOWER(COALESCE(status, '')) IN
            ('failed', 'error', 'canceled', 'cancelled')
            """
        ).fetchone()["c"]

        channels = conn.execute(
            "SELECT COUNT(*) AS c FROM channels"
        ).fetchone()["c"]

        active_channels = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM channels
            WHERE active = 1
            """
        ).fetchone()["c"]

        apis = conn.execute(
            "SELECT COUNT(*) AS c FROM apis"
        ).fetchone()["c"]

        active_apis = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM apis
            WHERE active = 1
            """
        ).fetchone()["c"]

        services = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM services
            WHERE active = 1
            """
        ).fetchone()["c"]

        deposits = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM deposits
            """
        ).fetchone()["c"]

        context = {
            "users": users,
            "active_users": active_users,
            "banned_users": banned_users,
            "orders": orders,
            "pending_orders": pending_orders,
            "failed_orders": failed_orders,
            "channels": channels,
            "active_channels": active_channels,
            "apis": apis,
            "active_apis": active_apis,
            "active_services": services,
            "deposits": deposits,
        }

        # User ID yoki username savolda bo'lsa,
        # tegishli foydalanuvchi ma'lumotini qo'shamiz.
        match = re.search(
            r'(?<!\d)(\d{5,20})(?!\d)|@([A-Za-z0-9_]{3,64})',
            str(question)
        )

        if match:
            target = (
                match.group(1)
                or match.group(2)
            )

            user = ai_find_user(target)

            if user:
                user_data = {
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "first_name": user["first_name"],
                    "balance": user["balance"],
                    "banned": user["banned"],
                    "created_at": user["created_at"],
                    "total_deposited": user["total_deposited"],
                    "role": user["role"],
                }

                context["target_user"] = user_data

                history = ai_get_recent_history(
                    user["user_id"],
                    15
                )

                context["target_user_history"] = [
                    {
                        "direction": row["direction"],
                        "text": row["text"],
                        "created_at": row["created_at"],
                    }
                    for row in history
                ]

        return context

    finally:
        conn.close()


def ai_call_provider(messages):
    """
    Groq -> OpenAI fallback.
    API keylar .env dan olinadi.
    """

    providers = []

    if GROQ_API_KEY:
        providers.append(
            {
                "name": "Groq",
                "key": GROQ_API_KEY,
                "url": (
                    "https://api.groq.com/"
                    "openai/v1/chat/completions"
                ),
                "model": os.getenv(
                    "GROQ_AGENT_MODEL",
                    "openai/gpt-oss-120b"
                ).strip(),
            }
        )

    if OPENAI_API_KEY:
        providers.append(
            {
                "name": "OpenAI",
                "key": OPENAI_API_KEY,
                "url": (
                    "https://api.openai.com/"
                    "v1/chat/completions"
                ),
                "model": OPENAI_AGENT_MODEL,
            }
        )

    if not providers:
        raise RuntimeError(
            "GROQ_API_KEY yoki OPENAI_API_KEY topilmadi."
        )

    last_error = None

    for provider in providers:
        try:
            response = HTTP_SESSION.post(
                provider["url"],
                headers={
                    "Authorization":
                        "Bearer " + provider["key"],
                    "Content-Type":
                        "application/json",
                },
                json={
                    "model": provider["model"],
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 1800,
                },
                timeout=45,
            )

            if response.status_code >= 400:
                last_error = (
                    f"{provider['name']} "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
                continue

            data = response.json()

            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            if content:
                return content.strip()

            last_error = (
                f"{provider['name']} bo'sh javob qaytardi."
            )

        except Exception as e:
            last_error = (
                f"{provider['name']}: {repr(e)}"
            )

    raise RuntimeError(
        last_error or "AI provider ishlamadi."
    )


def ai_agent_execute(admin_id, question):
    """
    AI Agent 2.0.

    AI faqat bot holatini o'qiydi va tahlil qiladi.
    Moliyaviy o'zgartirish, ban, API order yoki
    arbitrary code execution bu qatlamdan bajarilmaydi.
    """

    question = str(question or "").strip()

    if not question:
        return "❌ Savol bo'sh."

    if len(question) > 4000:
        question = question[:4000]

    context = ai_collect_context(question)
    memory = ai_get_memory(admin_id)

    system_prompt = """
Siz CheapReaction Telegram botining administrator AI yordamchisisiz.

Javob tili: o'zbek tili.
Javobni qisqa, aniq va amaliy bering.

Sizga berilgan BOT CONTEXT faqat o'qish uchun.
Hech qachon o'zingizcha:
- pul qo'shmang;
- pul ayirmang;
- balansni o'zgartirmang;
- foydalanuvchini bloklamang yoki blokdan chiqarmang;
- real SMM API order yubormang;
- Telegram xabar yubormang;
- shell/Python kod bajarmang;
- fayl o'chirmang;
- token, API key yoki maxfiy ma'lumotni ko'rsatmang.

Agar administrator shunday amalni so'rasa,
amal bajarilgan deb YOLG'ON aytmang.
Buning o'rniga kerakli amalni aniq tushuntiring
va administrator panelidagi tegishli bo'limdan
bajarish kerakligini ayting.

Agar user ID yoki username topilsa,
uning ko'rsatilgan ma'lumotlarini tahlil qiling.

Agar statistika so'ralsa,
faqat berilgan raqamlardan foydalaning.

BOT CONTEXT:
"""

    prompt = (
        system_prompt
        + "\n"
        + json.dumps(
            context,
            ensure_ascii=False,
            indent=2,
            default=str
        )
        + "\n\nOLD MEMORY:\n"
        + (memory[-6000:] if memory else "Yo'q")
        + "\n\nADMIN QUESTION:\n"
        + question
    )

    result = ai_call_provider(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    "BOT CONTEXT:\n"
                    + json.dumps(
                        context,
                        ensure_ascii=False,
                        indent=2,
                        default=str
                    )
                    + "\n\nOLD MEMORY:\n"
                    + (
                        memory[-6000:]
                        if memory
                        else "Yo'q"
                    )
                    + "\n\nADMIN QUESTION:\n"
                    + question
                ),
            },
        ]
    )

    # Memory'ga maxfiy tokenlar emas,
    # faqat suhbatning qisqa mazmunini saqlaymiz.
    new_memory = (
        "Oxirgi admin savoli: "
        + question[:1500]
        + "\nAI javobi: "
        + result[:3000]
    )

    ai_save_memory(
        admin_id,
        new_memory
    )

    conn = db()

    try:
        conn.execute(
            """
            INSERT INTO ai_agent_runs
            (
                admin_id,
                question,
                plan,
                result,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(admin_id),
                question,
                "READ_ONLY_ANALYSIS",
                result[:10000],
                datetime.now().isoformat()
            )
        )

        conn.commit()

    finally:
        conn.close()

    return result



# ============================================================
# ADMIN — LOG LAR
# ============================================================

@bot.message_handler(commands=["ai"])
def ai_admin_command(message):

    if not is_admin(message.from_user):
        bot.send_message(
            message.chat.id,
            "❌ AI yordamchi faqat administrator uchun."
        )
        return

    question = message.text.partition(" ")[2].strip()

    if not question:
        bot.send_message(
            message.chat.id,
            "🤖 <b>AI AGENT</b>\n\n"
            "Oddiy tilda yozing.\n\n"
            "Masalan:\n"
            "• <code>/ai @abc ni top</code>\n"
            "• <code>/ai @abc ni topib profilini tekshir</code>\n"
            "• <code>/ai @abc ni top, oxirgi 20 ta xabarini ko'r</code>\n"
            "• <code>/ai unga Salom yoz</code>\n"
            "• <code>/ai ertaga 15:00 da unga Salom yoz</code>\n"
            "• <code>/ai chatlarimni ko'rsat</code>\n"
            "• <code>/ai statistika</code>\n\n"
            "🧠 AI kontekstni ham eslab qoladi.",
            parse_mode="HTML"
        )
        return

    wait = bot.send_message(
        message.chat.id,
        "🤖 <b>AI Agent ishlayapti...</b>\n"
        "🧠 Reja tuzilmoqda...",
        parse_mode="HTML"
    )

    try:
        result = ai_agent_execute(
            message.from_user.id,
            question
        )

        bot.edit_message_text(
            result,
            message.chat.id,
            wait.message_id,
            parse_mode="HTML"
        )

    except Exception as e:
        print(
            "❌ AI AGENT HANDLER ERROR:",
            repr(e)
        )

        try:
            bot.edit_message_text(
                "❌ AI Agent xatosi yuz berdi.",
                message.chat.id,
                wait.message_id
            )
        except Exception:
            pass


try:
    init_ai_tables()
    print("🤖 AI tables: OK")
except Exception as e:
    print("❌ AI tables:", repr(e))



# ============================================================
# SMM API — XIZMATLARNI SINXRONLASH
# ============================================================

def get_api_by_id(api_id):
    conn = db()
    try:
        return conn.execute(
            """
            SELECT id, name, url, api_key, active
            FROM apis
            WHERE id = ?
            """,
            (api_id,)
        ).fetchone()
    finally:
        conn.close()


def fetch_api_services(api_id):
    api = get_api_by_id(api_id)

    if not api:
        raise RuntimeError("API topilmadi")

    if not api["active"]:
        raise RuntimeError("API faol emas")

    response = HTTP_SESSION.post(
        api["url"],
        data={
            "key": api["api_key"],
            "action": "services"
        },
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError(
            f"API services noto'g'ri format qaytardi: {data}"
        )

    return api, data


def sync_api_services(api_id, dry_run=False):
    api, services = fetch_api_services(api_id)

    result = {
        "api_id": api_id,
        "api_name": api["name"],
        "total": len(services),
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "deleted": 0
    }

    conn = db()

    try:
        for item in services:
            external_id = str(
                item.get("service", "")
            ).strip()

            name = str(
                item.get("name", "")
            ).strip()

            category = str(
                item.get("category", "")
            ).strip()

            if not external_id or not name:
                result["skipped"] += 1
                continue

            try:
                price = float(item.get("rate") or 0)
            except Exception:
                price = 0

            try:
                min_qty = int(item.get("min") or 1)
            except Exception:
                min_qty = 1

            try:
                max_qty = int(
                    item.get("max") or 1000000
                )
            except Exception:
                max_qty = 1000000

            cat_lower = (category or "").lower()
            name_lower = (name or "").lower()
            is_excluded = any(
                p in cat_lower or p in name_lower
                for p in ["instagram", "insta", "tiktok", "tik tok", "youtube", "yutube"]
            )
            is_avail = 0 if is_excluded else (1 if (
                any(k in cat_lower for k in ["telegram", "reaksiya", "ko'rish", "koʼrish", "prosmotr", "ulash", "tekin", "bonus"])
                or any(k in name_lower for k in ["telegram", "reaksiya", "ko'rish", "koʼrish", "prosmotr", "ulash", "view", "share", "bonus"])
                or price == 0
            ) else 0)

            existing = conn.execute(
                """
                SELECT id, name, category, price,
                       min_qty, max_qty, active,
                       COALESCE(admin_available, 0) AS admin_available
                FROM services
                WHERE api_id = ?
                  AND external_id = ?
                LIMIT 1
                """,
                (api_id, external_id)
            ).fetchone()

            if existing:
                changed = (
                    existing["name"] != name
                    or (existing["category"] or "") != category
                    or float(existing["price"] or 0) != price
                    or int(existing["min_qty"] or 1) != min_qty
                    or int(existing["max_qty"] or 1000000) != max_qty
                    or (is_excluded and existing["admin_available"])
                    or existing["active"] == 0
                )

                if changed:
                    result["updated"] += 1

                    if not dry_run:
                        new_avail = 0 if is_excluded else existing["admin_available"]
                        conn.execute(
                            """
                            UPDATE services
                            SET name = ?,
                                category = ?,
                                price = ?,
                                min_qty = ?,
                                max_qty = ?,
                                admin_available = ?,
                                active = 1
                            WHERE id = ?
                            """,
                            (
                                name,
                                category,
                                price,
                                min_qty,
                                max_qty,
                                new_avail,
                                existing["id"]
                            )
                        )
                else:
                    result["unchanged"] += 1

            else:
                result["new"] += 1

                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO services (
                            api_id,
                            external_id,
                            name,
                            category,
                            price,
                            min_qty,
                            max_qty,
                            active,
                            sort_order,
                            admin_available
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                        """,
                        (
                            api_id,
                            external_id,
                            name,
                            category,
                            price,
                            min_qty,
                            max_qty,
                            is_avail
                        )
                    )

        if not dry_run:
            # Provider'da hozir mavjud bo'lmagan xizmatlarni aniqlash
            current_external_ids = {
                str(item.get("service", "")).strip().strip("'").strip('"')
                for item in services
                if str(item.get("service", "")).strip()
            }

            db_rows = conn.execute(
                """
                SELECT id, external_id
                FROM services
                WHERE api_id = ?
                """,
                (api_id,)
            ).fetchall()

            stale_service_ids = [
                int(row["id"])
                for row in db_rows
                if str(row["external_id"] or "").strip().strip("'").strip('"')
                not in current_external_ids
            ]

            if stale_service_ids:
                placeholders = ",".join(
                    "?" for _ in stale_service_ids
                )

                # Foydalanuvchi ulagan xizmatlar buzilmasligi uchun
                # o'chirib yubormasdan, nofaol holatga o'tkazamiz
                conn.execute(
                    f"""
                    UPDATE services
                    SET active = 0
                    WHERE id IN ({placeholders})
                    """,
                    stale_service_ids
                )

                result["deleted"] = len(stale_service_ids)

            conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return result


# ============================================================
# ADMIN — AI YORDAMCHI
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "admin_ai"
)
def admin_ai_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    set_state(
        call.from_user.id,
        "admin_ai"
    )

    kb = types.InlineKeyboardMarkup()

    kb.row(
        types.InlineKeyboardButton(
            "🧹 AI rejimini yopish",
            callback_data="admin_ai_close"
        )
    )

    text = (
        "🤖 <b>AI Yordamchi</b> — botni boshqarish, tekshirish va tahlil qilishda yordam beradi."
    )
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "stars_settings"
)
def stars_settings_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    show_stars_settings(
        call.message.chat.id,
        call.message.message_id
    )




@bot.callback_query_handler(
    func=lambda call: call.data == "stars_set_rate"
)
def stars_set_rate_callback(call):
    bot.answer_callback_query(call.id)

    if not is_admin(call.from_user):
        return

    set_state(
        call.from_user.id,
        "stars_admin_input",
        stars_setting="rate"
    )

    bot.edit_message_text(
        "💱 <b>Stars kursi</b>\n\n"
        "1 ⭐ nechta so'm bo'lishini yuboring.\n\n"
        "Masalan:\n"
        "<code>20</code>",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )




@bot.message_handler(
    func=lambda message: (
        get_state(message.from_user.id).get("state")
        == "stars_admin_input"
    )
)
def stars_admin_input(message):
    if not is_admin(message.from_user):
        return

    state = get_state(message.from_user.id)
    setting_name = state.get("stars_setting")

    try:
        value = message.text.strip()

        if setting_name == "rate":
            rate = float(value)

            if rate <= 0:
                raise ValueError

            set_stars_setting("stars_rate", rate)

        elif setting_name == "min":
            minimum = int(value)

            if minimum < 1:
                raise ValueError

            maximum = int(
                stars_setting("stars_max", "10000")
            )

            if minimum > maximum:
                bot.send_message(
                    message.chat.id,
                    "❌ Minimum Stars maksimumdan katta bo'lishi mumkin emas."
                )
                return

            set_stars_setting("stars_min", minimum)

        elif setting_name == "max":
            maximum = int(value)

            minimum = int(
                stars_setting("stars_min", "1")
            )

            if maximum < 1:
                raise ValueError

            if maximum < minimum:
                bot.send_message(
                    message.chat.id,
                    "❌ Maksimum Stars minimumdan kichik bo'lishi mumkin emas."
                )
                return

            set_stars_setting("stars_max", maximum)

        else:
            clear_state(message.from_user.id)
            return

        clear_state(message.from_user.id)

        cfg = get_stars_config()

        bot.send_message(
            message.chat.id,
            "✅ <b>Stars sozlamasi saqlandi.</b>\n\n"
            f"💱 1 ⭐ = <b>{cfg['rate']:g}</b> so'm\n"
            f"⬇️ Minimum: <b>{cfg['min']}</b> ⭐\n"
            f"⬆️ Maksimum: <b>{cfg['max']}</b> ⭐",
            parse_mode="HTML",
            reply_markup=stars_settings_keyboard()
        )

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Noto'g'ri qiymat.\n\n"
            "Iltimos, faqat musbat son yuboring."
        )


def send_stars_payment_menu(chat_id, message_id=None):
    cfg = get_stars_config()

    example_stars = 100
    example_sum = example_stars * cfg["rate"]

    text = (
        "💳 <b>Hisobni to'ldirish</b>\n\n"
        "⭐ Telegram Stars orqali balansingizni to'ldiring.\n\n"
        f"💱 <b>Kurs:</b> 1 ⭐ = <code>{cfg['rate']:g} so'm</code>\n\n"
        f"💡 Masalan: <code>{example_stars} ⭐</code> yuborsangiz, "
        f"hisobingizga <code>{example_sum:,.0f} so'm</code> qo'shiladi.\n\n"
        "👇 <b>To'lamoqchi bo'lgan Stars miqdorini yozing:</b>\n\n"
        "✍️ Masalan: <code>100</code>"
    )

    if message_id is not None:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML"
        )
    else:
        bot.send_message(
            chat_id,
            text,
            parse_mode="HTML"
        )

@bot.message_handler(
    func=lambda message: message.text == "💵 Pul kiritish"
)
def deposit_reply(message):
    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "⭐ Telegram Stars",
            callback_data="stars_payment"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📞 Admin orqali",
            url="https://t.me/xua202"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Orqaga",
            callback_data="cabinet_menu"
        )
    )

    bot.send_message(
        message.chat.id,
        "💳 <b>Pul kiritish</b>\n\n"
        "Kerakli to'lov tizimini tanlang:",
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "deposit"
)
def deposit_callback(call):
    bot.answer_callback_query(call.id)

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "⭐ Telegram Stars",
            callback_data="stars_payment"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "📞 Admin orqali",
            url="https://t.me/xua202"
        )
    )

    kb.add(
        types.InlineKeyboardButton(
            "↩️ Orqaga",
            callback_data="cabinet_menu"
        )
    )

    bot.edit_message_text(
        "💳 <b>Pul kiritish</b>\n\n"
        "Kerakli to'lov tizimini tanlang:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "stars_payment"
)
def stars_payment_callback(call):
    bot.answer_callback_query(call.id)

    set_state(
        call.from_user.id,
        "stars_amount"
    )

    send_stars_payment_menu(
        call.message.chat.id,
        call.message.message_id
    )


@bot.callback_query_handler(
    func=lambda call: call.data == "stars_cancel"
)
def stars_cancel_callback(call):
    bot.answer_callback_query(call.id)

    clear_state(call.from_user.id)

    bot.edit_message_text(
        "❌ To'lov bekor qilindi.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )


@bot.message_handler(
    func=lambda message: (
        get_state(message.from_user.id).get("state")
        == "stars_amount"
    )
)
def stars_amount_input(message):
    user_id = message.from_user.id

    try:
        stars = int(message.text.strip())

        if stars <= 0:
            raise ValueError

    except Exception:
        bot.send_message(
            message.chat.id,
            "❌ Stars miqdorini butun son ko'rinishida yuboring.\n\n"
            "Masalan: <code>100</code>",
            parse_mode="HTML"
        )
        return

    cfg = get_stars_config()

    if stars < cfg["min"]:
        bot.send_message(
            message.chat.id,
            f"❌ Minimal to'lov: <b>{cfg['min']}</b> ⭐",
            parse_mode="HTML"
        )
        return

    if stars > cfg["max"]:
        bot.send_message(
            message.chat.id,
            f"❌ Maksimal to'lov: <b>{cfg['max']}</b> ⭐",
            parse_mode="HTML"
        )
        return

    total = stars * cfg["rate"]
    payload = f"stars_deposit:{user_id}:{stars}"

    try:
        # Havola shu yerning o'zida yaratiladi.
        invoice_link = bot.create_invoice_link(
            title="⭐ Telegram Stars",
            description=f"{stars} ⭐ = {total:,.2f} so'm",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                types.LabeledPrice(
                    label=f"{stars} Telegram Stars",
                    amount=stars
                )
            ]
        )

        # Sessiyani saqlash
        set_state(
            user_id,
            "stars_confirm",
            stars=stars,
            total=total,
            invoice_link=invoice_link
        )

        kb = types.InlineKeyboardMarkup()

        kb.add(
            types.InlineKeyboardButton(
                f"⭐ {stars} to'lash",
                url=invoice_link
            )
        )

        kb.add(
            types.InlineKeyboardButton(
                "❌ Bekor qilish",
                callback_data="stars_cancel"
            )
        )

        bot.send_message(
            message.chat.id,
            "💳 <b>To'lovni tasdiqlang</b>\n\n"
            f"⭐ Stars: <b>{stars}</b>\n"
            f"💰 Hisobingizga: <b>{total:,.2f} so'm</b>\n\n"
            f"💱 Kurs: <b>1 ⭐ = {cfg['rate']:,.2f} so'm</b>\n\n"
            "To'lovni davom ettirish uchun quyidagi tugmani bosing:",
            parse_mode="HTML",
            reply_markup=kb
        )

    except Exception as e:
        print("❌ STARS INVOICE LINK XATOSI:", repr(e))

        bot.send_message(
            message.chat.id,
            "❌ To'lov havolasini yaratishda xatolik yuz berdi.\n"
            "Iltimos, qaytadan urinib ko'ring."
        )

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("stars_pay:")
)
def stars_pay_callback(call):
    user_id = call.from_user.id

    # Telegram callback'ni darhol tasdiqlash
    try:
        bot.answer_callback_query(
            call.id,
            "⏳ To'lov oynasi tayyorlanmoqda..."
        )
    except Exception as e:
        print("⚠️ CALLBACK ANSWER XATOSI:", e)

    try:
        stars = int(call.data.split(":", 1)[1])
    except Exception:
        bot.answer_callback_query(
            call.id,
            "❌ To'lov ma'lumotlari noto'g'ri.",
            show_alert=True
        )
        return

    state = get_state(user_id)

    if state.get("state") != "stars_confirm":
        bot.answer_callback_query(
            call.id,
            "❌ To'lov sessiyasi topilmadi. Qaytadan urinib ko'ring.",
            show_alert=True
        )
        return

    saved_stars = int(state.get("stars", 0))

    if saved_stars != stars:
        bot.answer_callback_query(
            call.id,
            "❌ To'lov miqdori mos kelmadi.",
            show_alert=True
        )
        return

    cfg = get_stars_config()

    if stars < cfg["min"] or stars > cfg["max"]:
        clear_state(user_id)

        bot.answer_callback_query(
            call.id,
            "❌ Stars miqdori ruxsat etilgan chegaradan tashqarida.",
            show_alert=True
        )
        return

    total = stars * cfg["rate"]
    payload = f"stars_deposit:{user_id}:{stars}"

    try:
        invoice_link = bot.create_invoice_link(
            title="⭐ Telegram Stars",
            description=f"{stars} ⭐ = {total:,.2f} so'm",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                types.LabeledPrice(
                    label=f"{stars} Telegram Stars",
                    amount=stars
                )
            ]
        )

        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton(
                f"⭐ {stars} to'lash",
                url=invoice_link
            )
        )
        kb.add(
            types.InlineKeyboardButton(
                "❌ Bekor qilish",
                callback_data="stars_cancel"
            )
        )

        bot.edit_message_text(
            "💳 <b>To'lovni tasdiqlang</b>\n\n"
            f"⭐ Stars: <b>{stars}</b>\n"
            f"💰 Hisobingizga: <b>{total:,.2f} so'm</b>\n\n"
            f"💱 Kurs: <b>1 ⭐ = {cfg['rate']:,.2f} so'm</b>\n\n"
            "To'lovni davom ettirish uchun quyidagi tugmani bosing:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=kb
        )

        # Invoice link yaratilgach state saqlanadi.
        # To'lov muvaffaqiyatli bo'lganda payload orqali balans to'ldiriladi.

    except Exception as e:
        print("❌ STARS INVOICE LINK XATOSI:", e)

        bot.answer_callback_query(
            call.id,
            "❌ To'lov havolasini yaratishda xatolik.",
            show_alert=True
        )


@bot.pre_checkout_query_handler(
    func=lambda query: True
)
def stars_pre_checkout(pre_checkout_query):
    try:
        if not pre_checkout_query.invoice_payload.startswith(
            "stars_deposit:"
        ):
            bot.answer_pre_checkout_query(
                pre_checkout_query.id,
                ok=False,
                error_message="❌ Noto'g'ri to'lov."
            )
            return

        bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=True
        )

    except Exception as e:
        print("❌ PRE CHECKOUT XATOSI:", e)


@bot.message_handler(
    content_types=["successful_payment"]
)
def stars_successful_payment(message):
    try:
        payment = message.successful_payment

        payload = payment.invoice_payload

        if not payload.startswith("stars_deposit:"):
            return

        parts = payload.split(":")

        if len(parts) != 3:
            return

        payload_user_id = int(parts[1])
        stars = int(parts[2])

        if payload_user_id != message.from_user.id:
            print("❌ STARS USER ID MOS EMAS")
            return

        cfg = get_stars_config()

        if stars < cfg["min"] or stars > cfg["max"]:
            print("❌ STARS MIQDORI LIMITDAN TASHQARI")
            return

        total = stars * cfg["rate"]

        charge_id = getattr(
            payment,
            "telegram_payment_charge_id",
            ""
        )

        conn = db()

        try:
            duplicate = conn.execute(
                """
                SELECT id
                FROM deposits
                WHERE method = 'Telegram Stars'
                AND comment LIKE ?
                LIMIT 1
                """,
                (f"%{charge_id}%",)
            ).fetchone()

            if duplicate:
                print(
                    "⚠️ DUPLIKAT STARS TO'LOV:",
                    charge_id
                )
                return

            conn.execute(
                """
                UPDATE users
                SET balance = balance + ?,
                    total_deposited = total_deposited + ?
                WHERE user_id = ?
                """,
                (
                    total,
                    total,
                    message.from_user.id
                )
            )

            conn.execute(
                """
                INSERT INTO deposits(
                    user_id,
                    amount,
                    method,
                    status,
                    comment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    message.from_user.id,
                    total,
                    "Telegram Stars",
                    "Paid",
                    f"{stars} Stars | charge_id={charge_id}"
                )
            )

            conn.commit()

        finally:
            conn.close()

        bot.send_message(
            message.chat.id,
            "✅ <b>To'lov muvaffaqiyatli amalga oshirildi!</b>\n\n"
            f"⭐ To'langan: <b>{stars:,}</b> ⭐\n"
            f"💵 Hisobingizga qo'shildi: <b>{total:,.2f}</b> so'm\n\n"
            f"💰 Joriy balans: "
            f"<b>{get_balance(message.from_user.id):,.2f}</b> so'm",
            parse_mode="HTML"
        )

    except Exception as e:
        print("❌ SUCCESSFUL PAYMENT XATOSI:", e)

        bot.send_message(
            message.chat.id,
            "⚠️ To'lov qabul qilindi, lekin balansni "
            "yangilashda texnik xatolik yuz berdi.\n\n"
            "Administratorga murojaat qiling."
        )


# ============================================================
# INLINE ASOSIY MENYU
# ============================================================


@bot.callback_query_handler(
    func=lambda call: call.data == "home_menu"
)
def home_callback(call):
    bot.answer_callback_query(call.id)

    clear_state(call.from_user.id)

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    send_start_screen(call.message.chat.id, call.from_user)


# ============================================================
# INLINE BALANS
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "balance_menu"
)
def balance_callback(call):
    bot.answer_callback_query(call.id)

    balance = get_balance(call.from_user.id)

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "💵 Pul kiritish",
            callback_data="deposit"
        )
    )


    bot.edit_message_text(
        "💰 <b>Balans</b>\n\n"
        f"💵 Hisobingiz: <b>{balance:,.2f}</b> so'm",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# INLINE BUYURTMALARIM
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "orders_menu"
)
def orders_callback(call):
    bot.answer_callback_query(call.id)

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (call.from_user.id,)
    ).fetchall()

    conn.close()

    kb = types.InlineKeyboardMarkup()


    if not rows:
        text = (
            "📦 <b>Buyurtmalarim</b>\n\n"
            "📭 Hozircha buyurtmalar mavjud emas."
        )
    else:
        parts = ["📦 <b>Buyurtmalarim</b>\n"]

        for row in rows:
            quantity = row["quantity"] or 0
            price = float(row["price"] or 0)
            status = row["status"] or "unknown"

            parts.append(
                f"🆔 <b>#{row['id']}</b>\n"
                f"📊 Miqdor: <b>{quantity}</b>\n"
                f"💰 Narx: <b>{price:,.2f}</b> so'm\n"
                f"📌 Status: <b>{status}</b>\n"
            )

        text = "\n".join(parts)

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# INLINE KABINET
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "cabinet_menu"
)
def cabinet_callback(call):
    bot.answer_callback_query(call.id)

    clear_state(call.from_user.id)

    balance = get_balance(call.from_user.id)

    conn = db()

    row = conn.execute(
        """
        SELECT total_deposited
        FROM users
        WHERE user_id = ?
        """,
        (call.from_user.id,)
    ).fetchone()

    conn.close()

    total_deposited = float(
        row["total_deposited"]
        if row and row["total_deposited"] is not None
        else 0
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "💵 Pul kiritish",
            callback_data="deposit"
        )
    )


    bot.edit_message_text(
        "👤 <b>Kabinet</b>\n\n"
        f"📋 <b>Kabinet ID:</b> <code>{format(call.from_user.id, ',').replace(',', ' ')}</code>\n"
        f"💵 <b>Hisobingiz:</b> <code>{format(balance, ',.2f').replace(',', ' ')} so'm</code>\n"
        f"🛍 <b>Kiritgan pullaringiz:</b> <code>{format(total_deposited, ',.2f').replace(',', ' ')} so'm</code>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# INLINE SUPPORT
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "support_menu"
)
def support_callback(call):
    bot.answer_callback_query(call.id)

    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.row(
        types.InlineKeyboardButton(
            "📞 Admin",
            url="https://t.me/xua202"
        ),
        types.InlineKeyboardButton(
            "👨‍💻 Dasturchi",
            url="https://t.me/xua202"
        )
    )


    bot.edit_message_text(
        "🆘 <b>SUPPORT</b>\n\n"
        "Administrator bilan bog‘lanish uchun "
        "pastdagi tugmani bosing.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )


# ============================================================
# INLINE QO‘LLANMA
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "guide_menu"
)
def guide_callback(call):
    bot.answer_callback_query(call.id)

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "📹 Videoni ko‘rish",
            url="https://t.me/CheapSMM"
        )
    )


    bot.edit_message_text(
        "📕 <b>Qo‘llanma</b>\n\n"
        "Botdan foydalanish bo‘yicha video "
        "qo‘llanmalardan foydalanishingiz mumkin.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb
    )




# ============================================================
# NO-OP
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "noop"
)
def noop_callback(call):
    bot.answer_callback_query(call.id)



# ============================================================
# ============================================================
# ============================================================
# TELETHON AUTO ORDER EVENT PROCESSOR
# ============================================================

def clean_channel_id(cid):
    if cid is None:
        return ""
    s = str(cid).strip()
    if s.startswith("-100"):
        s = s[4:]
    elif s.startswith("-"):
        s = s[1:]
    return s

def get_active_channel_ids():
    """
    DB'dagi active=1 bo'lgan Auto Order kanallarini oladi.
    ID lar har xil formatda (-100, ijobiy, string, username) mos kelishini ta'minlaydi.
    """
    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT channel_id, username
            FROM channels
            WHERE active = 1
            """
        ).fetchall()

        result = set()
        for row in rows:
            cid = row["channel_id"]
            if cid:
                try:
                    result.add(int(cid))
                except Exception:
                    pass
                cleaned = clean_channel_id(cid)
                if cleaned.isdigit():
                    result.add(int(cleaned))
                    result.add(-int(f"100{cleaned}"))
                    result.add(-int(cleaned))
                result.add(str(cid).strip())
                result.add(cleaned)
            u = (row["username"] or "").strip().lstrip("@").lower()
            if u:
                result.add(u)
                result.add(f"@{u}")

        return result

    finally:
        conn.close()


async def process_channel_post(
    channel_id,
    message_id,
    title,
    username
):
    """
    Telethon va Bot API orqali kelgan kanal postlarini qayta ishlaydi.
    Har bir post uchun aniq 1 marta buyurtma beriladi (qayta-qayta kelgan taqdirda dublikat yubormaydi).
    """

    try:
        channel_id_str = str(channel_id).strip()
        channel_id_clean = clean_channel_id(channel_id)
        message_id = int(message_id)

        post_key = (channel_id_clean, message_id)
        if post_key in PROCESSING_POSTS:
            log_debug(f"⏳ POST HOZIRDA QAYTA ISHLANMOQDA (o'tkazib yuborildi): {post_key}")
            return

        PROCESSING_POSTS.add(post_key)
        try:
            title = (
                str(title or "").strip()
                or "Noma'lum kanal"
            )

            username = (
                str(username or "").strip().lstrip("@")
                or None
            )

            active_channels = get_active_channel_ids()

            is_active = (
                channel_id in active_channels
                or channel_id_str in active_channels
                or channel_id_clean in active_channels
                or (int(channel_id_clean) in active_channels if channel_id_clean.isdigit() else False)
                or (-int(f"100{channel_id_clean}") in active_channels if channel_id_clean.isdigit() else False)
                or (username and username.lower() in active_channels)
                or (username and f"@{username.lower()}" in active_channels)
            )

            if not is_active:
                print(
                    "ℹ️ TELETHON EVENT: kanal Auto Order uchun faol emas:",
                    channel_id
                )
                return

            print()
            print("============================================")
            print("📨 YANGI KANAL POSTI")
            print("📌 Kanal:", title)
            print("🆔 Channel ID:", channel_id)
            print(
                "🔗 Username:",
                "@" + username if username else "maxfiy"
            )
            print("🆔 Message ID:", message_id)

            # ============================================
            # AUTO ORDER XIZMATLARINI OLISH
            # ============================================

            conn = db()

            try:
                target_ids = [
                    str(channel_id),
                    f"-100{channel_id_clean}",
                    f"-{channel_id_clean}",
                    channel_id_clean
                ]
                if channel_id_clean.isdigit():
                    target_ids.append(int(channel_id_clean))
                    target_ids.append(-int(f"100{channel_id_clean}"))
                    target_ids.append(-int(channel_id_clean))
                if username:
                    clean_un = str(username).strip().lstrip("@")
                    target_ids.append(f"@{clean_un}")
                    target_ids.append(clean_un)

                placeholders = ",".join("?" for _ in target_ids)

                service_rows = conn.execute(
                    f"""
                    SELECT
                        cs.id AS channel_service_id,
                        cs.service_id,
                        cs.min_qty,
                        cs.max_qty,
                        s.name,
                        s.category,
                        s.api_id,
                        s.price,
                        s.external_id,
                        a.name AS api_name,
                        cs.user_id AS owner_id
                    FROM channel_services cs
                    JOIN services s
                        ON s.id = cs.service_id
                    JOIN apis a
                        ON a.id = s.api_id
                       AND a.active = 1
                    JOIN channels ch
                        ON ch.user_id = cs.user_id
                       AND (ch.channel_id = cs.channel_id OR ch.channel_id IN ({placeholders}))
                    WHERE (cs.channel_id IN ({placeholders}))
                      AND cs.active = 1
                      AND s.active = 1
                      AND ch.active = 1
                    GROUP BY cs.id
                    ORDER BY cs.id ASC
                    """,
                    target_ids + target_ids
                ).fetchall()

            finally:
                conn.close()

            print()
            print("🧪 AUTO ORDER SERVICE TEKSHIRUVI")
            print("--------------------------------------------")
            print(
                "🛠 Ulangan faol xizmatlar:",
                len(service_rows)
            )

            if not service_rows:
                print(
                    "⚠️ Bu kanal uchun faol xizmat topilmadi."
                )
                return

            # DEDUPLIKATSIYA: Ushbu post uchun allaqachon bajarilgan xizmatlarni chiqarib tashlash
            pending_services = []
            for service in service_rows:
                sid = int(service["service_id"])
                if is_post_already_ordered(channel_id, message_id, sid):
                    log_debug(f"⏭️ POST ALLAQACHON BUYURTMA QILINGAN (o'tkazib yuborildi): post={message_id}, svc={sid} ({service['name']})")
                    continue
                pending_services.append(service)

            if not pending_services:
                log_debug(f"ℹ️ Kanal {channel_id} post {message_id} uchun barcha buyurtmalar allaqachon bajarilgan, qayta yuborilmaydi.")
                return

            for service in pending_services:
                print(
                    "   ├─ Service:",
                    service["service_id"]
                )
                print(
                    "   ├─ Nomi:",
                    service["name"]
                )
                print(
                    "   ├─ Min:",
                    service["min_qty"]
                )
                print(
                    "   ├─ Max:",
                    service["max_qty"]
                )
                print(
                    "   └─ API:",
                    service["api_name"] or "Noma'lum",
                    "(#%s)" % service["api_id"]
                )

            if not username:
                conn_u = db()
                try:
                    ch_u = conn_u.execute(
                        "SELECT username FROM channels WHERE (channel_id = ? OR channel_id = ? OR channel_id = ?) AND active = 1 AND username != '' LIMIT 1",
                        (str(channel_id), str(channel_id_clean), f"-100{channel_id_clean}")
                    ).fetchone()
                    if ch_u and ch_u["username"]:
                        username = ch_u["username"].strip().lstrip("@")
                finally:
                    conn_u.close()

            if not username:
                log_debug("⚠️ TELETHON EVENT: Kanal maxfiy (username yo'q). Faqat ommaviy kanallar qabul qilinadi.")
                return

            post_link = f"https://t.me/{username}/{message_id}"

            log_debug(
                f"🔗 Post link: {post_link} | Bajariladigan yangi xizmatlar: {len(pending_services)}"
            )

            # ============================================
            # AUTO ORDER — AVTOMATIK QUANTITY PREVIEW
            # ============================================

            if post_link and pending_services:

                print()
                print("🤖 AVTOMATIK BUYURTMA HISOBI")
                print("--------------------------------------------")

                for service in pending_services:

                    min_qty = int(
                        service["min_qty"] or 0
                    )

                    max_qty = int(
                        service["max_qty"] or 0
                    )

                    if min_qty <= 0:
                        print(
                            "⚠️ Service #%s: min quantity noto'g'ri"
                            % service["service_id"]
                        )
                        continue

                    if max_qty <= 0:
                        print(
                            "⚠️ Service #%s: max quantity noto'g'ri"
                            % service["service_id"]
                        )
                        continue

                    if max_qty < min_qty:
                        print(
                            "⚠️ Service #%s: max < min"
                            % service["service_id"]
                        )
                        continue

                    auto_quantity = random.randint(
                        min_qty,
                        max_qty
                    )

                    price_per_1000 = float(
                        service["price"] or 0
                    )

                    estimated_price = (
                        auto_quantity / 1000
                    ) * price_per_1000

                    print()
                    print(
                        "🛠 Xizmat:",
                        service["name"]
                    )

                    print(
                        "🆔 Service ID:",
                        service["service_id"]
                    )

                    print(
                        "🔢 Min:",
                        min_qty
                    )

                    print(
                        "🔢 Max:",
                        max_qty
                    )

                    print(
                        "🎯 Avtomatik quantity:",
                        auto_quantity
                    )

                    print(
                        "💰 Taxminiy narx:",
                        f"{estimated_price:,.2f}",
                        "so'm"
                    )

                    print(
                        "🔗 Link:",
                        post_link
                    )

                    owner_id = service["owner_id"]

                    if not owner_id:
                        print("❌ Kanal egasi topilmadi:", channel_id)
                        continue

                    log_debug(f"🚀 REAL API ORDER YUBORILMOQDA: svc={service['service_id']}, qty={auto_quantity}, link={post_link}")

                    try:
                        await process_real_auto_order(
                            channel_id=channel_id,
                            message_id=message_id,
                            owner_id=int(owner_id),
                            service=service,
                            link=post_link,
                            quantity=auto_quantity
                        )
                    except Exception as s_err:
                        log_debug(f"❌ Xizmat #{service.get('service_id')} buyurtma berish xatosi: {s_err}")

                print("--------------------------------------------")

            else:

                print(
                    "⚠️ Post link yoki faol xizmat mavjud emas."
                )

            print("--------------------------------------------")

            print("============================================")

        finally:
            PROCESSING_POSTS.discard(post_key)

    except Exception as e:

        print(
            "❌ TELETHON AUTO ORDER EVENT XATOSI:",
            repr(e)
        )


@bot.channel_post_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'animation'])
def bot_channel_post_listener(message):
    """
    Agar kanalga bot admin qilingan bo'lsa, Telegram Bot API orqali kelgan
    yangi postni darhol qabul qiladi va Auto Order buyurtmasini beradi.
    """
    try:
        ch_id = getattr(message.chat, "id", None)
        msg_id = getattr(message, "message_id", None)
        title = getattr(message.chat, "title", None) or "Noma'lum kanal"
        username = getattr(message.chat, "username", None)
        if not ch_id or not msg_id:
            return
        log_debug(f"📡 BOT API CHANNEL POST QABUL QILINDI: {title} (@{username}) | ch={ch_id} | Msg: {msg_id}")
        if MAIN_LOOP:
            asyncio.run_coroutine_threadsafe(
                process_channel_post(ch_id, msg_id, title, username),
                MAIN_LOOP
            )
    except Exception as e:
        log_debug(f"❌ BOT CHANNEL POST XATOSI: {e}")


async def start_userbot():
    """
    Telethon userbotni to'g'ridan-to'g'ri ishga tushiradi.

    Auto Order uchun yangi kanal postlari:
        Telethon -> events.NewMessage
        -> process_channel_post()
    """

    global userbot

    if not API_ID or not API_HASH:
        log_debug("⚠️ USERBOT: API_ID/API_HASH topilmadi.")
        return

    from telethon.sessions import StringSession
    if USERBOT_SESSION and len(USERBOT_SESSION) > 60:
        sess = StringSession(USERBOT_SESSION)
    else:
        sess = USERBOT_SESSION or 'cheap_userbot'

    log_debug(f"USERBOT INIT: sess_len={len(USERBOT_SESSION)}")

    userbot = TelegramClient(
        sess,
        API_ID,
        API_HASH
    )

    @userbot.on(events.NewMessage)
    async def new_channel_post(event):
        try:
            log_debug(
                f"🔔 TELETHON EVENT: chat_id={event.chat_id} is_channel={event.is_channel} msg_id={event.id}"
            )

            if not event.is_channel:
                log_debug("⏭️ EVENT CHANNEL EMAS")
                return

            channel_id = int(event.chat_id)
            channel_id_raw = str(channel_id).strip()
            channel_id_clean = clean_channel_id(channel_id)
            active_channels = get_active_channel_ids()

            is_active = (
                channel_id in active_channels
                or channel_id_raw in active_channels
                or channel_id_clean in active_channels
                or (int(channel_id_clean) in active_channels if channel_id_clean.isdigit() else False)
                or (-int(f"100{channel_id_clean}") in active_channels if channel_id_clean.isdigit() else False)
            )

            chat = getattr(event, "chat", None)
            if not is_active:
                try:
                    if chat is None:
                        chat = await event.get_chat()
                    u_test = (getattr(chat, "username", None) or "").strip().lstrip("@").lower()
                    if u_test and (u_test in active_channels or f"@{u_test}" in active_channels):
                        is_active = True
                except Exception:
                    pass

            if not is_active:
                log_debug(f"⏭️ KANAL FAOL EMAS: {channel_id}")
                return

            if chat is None:
                try:
                    chat = await event.get_chat()
                except Exception:
                    pass

            title = (
                getattr(chat, "title", None)
                or "Noma'lum kanal"
            )

            username = getattr(
                chat,
                "username",
                None
            )

            if not username:
                try:
                    conn = db()
                    db_ch = conn.execute(
                        "SELECT username, title FROM channels WHERE (channel_id = ? OR channel_id = ? OR channel_id = ?) AND active = 1 AND username != '' LIMIT 1",
                        (channel_id, channel_id_raw, channel_id_clean)
                    ).fetchone()
                    conn.close()
                    if db_ch:
                        if db_ch["username"]:
                            username = db_ch["username"].strip().lstrip("@")
                        if (not title or title == "Noma'lum kanal") and db_ch["title"]:
                            title = db_ch["title"]
                except Exception:
                    pass

            log_debug(f"📨 YANGI KANAL POSTI: {title} (@{username}) | ch_id={channel_id} | msg_id={event.id}")

            await process_channel_post(
                channel_id,
                event.id,
                title,
                username
            )

        except Exception as e:
            log_debug(f"❌ TELETHON USERBOT EVENT XATOSI: {e}")

    connected = False
    for attempt in range(1, 6):
        try:
            log_debug(f"USERBOT CONNECTING (urinish {attempt}/5)...")
            await asyncio.wait_for(userbot.connect(), timeout=25)
            is_auth = await userbot.is_user_authorized()
            log_debug(f"USERBOT CONNECTED: auth={is_auth}")
            if not is_auth:
                log_debug("⚠️ USERBOT: Session avtorizatsiyadan o'tmagan. Userbot to'xtatildi.")
                await asyncio.sleep(300)
                return
            connected = True
            break
        except Exception as e:
            log_debug(f"⚠️ USERBOT CONNECT urinish {attempt} xatosi: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

    if not connected:
        log_debug("❌ USERBOT: 5 ta urinishdan so'ng ham ulanib bo'lmadi.")
        await asyncio.sleep(300)
        return

    # ============================================================
    # FAOL KANALLARGA USERBOTNI ULASH
    # ============================================================
    log_debug("🔗 Faol kanallar tekshirilmoqda...")

    conn = db()
    try:
        rows = conn.execute("""
            SELECT channel_id, username, title
            FROM channels
            WHERE active = 1 AND username != ''
        """).fetchall()
    finally:
        conn.close()

    for row in rows:
        channel_id = row["channel_id"]
        username = (row["username"] or "").strip().lstrip("@")
        title = row["title"] or "Noma'lum kanal"

        if not username:
            continue

        try:
            entity = None

            # Ommaviy kanal username orqali topamiz
            try:
                entity = await userbot.get_entity(username)
            except Exception as e:
                log_debug(f"⚠️ Kanal entity topilmadi: @{username}: {e}")

            # Username ishlamasa ID orqali urinib ko'ramiz
            if entity is None and channel_id:
                clean_id = clean_channel_id(channel_id)
                if clean_id.isdigit():
                    for test_id in [-int(f"100{clean_id}"), int(clean_id), -int(clean_id)]:
                        try:
                            entity = await userbot.get_entity(test_id)
                            if entity:
                                break
                        except Exception:
                            pass

            if entity is None:
                log_debug(f"❌ Kanal ulanmagan: {title} | @{username}")
                continue

            # Public kanalga qo'shilish
            try:
                await userbot(JoinChannelRequest(entity))
                log_debug(f"✅ USERBOT KANALGA QO'SHILDI: {title} | @{username}")
            except Exception as join_error:
                err = repr(join_error)

                if (
                    "already" in err.lower()
                    or "participant" in err.lower()
                ):
                    log_debug(f"✅ USERBOT ALLAQACHON A'ZO: {title} | @{username}")
                else:
                    log_debug(f"⚠️ KANALGA QO'SHILISH IMKONI BO'LMADI: {title} | {join_error}")

        except Exception as e:
            log_debug(f"❌ FAOL KANAL TEKSHIRUVIDA XATO: {title}: {e}")

    me = await userbot.get_me()
    bot_info = getattr(me, 'username', None) or getattr(me, 'first_name', None) or getattr(me, 'id', "Noma'lum")
    log_debug(f"✅ TELETHON USERBOT ISHLADI: {bot_info} (ID: {getattr(me, 'id', None)}) | Channels: {len(get_active_channel_ids())}")

    log_debug(f"👀 TELETHON KUZATUV BOSHLANDI (kanallar soni: {len(get_active_channel_ids())})")

    while True:
        try:
            await userbot.run_until_disconnected()
            log_debug("⚠️ TELETHON: ulanish uzildi, 5 soniyadan keyin qayta ulanadi...")
        except Exception as e:
            log_debug(f"❌ TELETHON CONNECTION XATOSI: {repr(e)}")
            print("🔄 TELETHON: 5 soniyadan keyin qayta ulanadi...")

        await asyncio.sleep(5)

        try:
            if not userbot.is_connected():
                await userbot.connect()

            if not await userbot.is_user_authorized():
                print("❌ TELETHON: session avtorizatsiyasi yo'q.")
                print("⚠️ Userbot qayta login talab qilishi mumkin.")
                await asyncio.sleep(10)
                continue

            print("✅ TELETHON: qayta ulandi.")

        except Exception as reconnect_error:
            print(
                "❌ TELETHON RECONNECT XATOSI:",
                repr(reconnect_error)
            )
            await asyncio.sleep(5)



# ============================================================
# 🚀 REAL SMM AUTO ORDER
# ============================================================

def get_auto_order_api(service_id):
    conn = db()
    try:
        return conn.execute(
            """
            SELECT
                s.id,
                s.external_id,
                s.name,
                s.api_id,
                s.price,
                s.min_qty,
                s.max_qty,
                a.name AS api_name,
                a.url,
                a.api_key,
                a.active AS api_active
            FROM services s
            LEFT JOIN apis a
                ON a.id = s.api_id
            WHERE s.id = ?
            """,
            (service_id,)
        ).fetchone()
    finally:
        conn.close()


def create_auto_order_record(
    channel_id,
    message_id,
    owner_id,
    service_id,
    link,
    quantity,
    price
):
    conn = db()

    try:
        norm_cid = normalize_channel_id(channel_id)
        clean_cid = clean_channel_id(channel_id)
        mid = int(message_id)
        sid = int(service_id)

        target_cids = [str(channel_id), clean_cid, f"-100{clean_cid}", f"-{clean_cid}"]
        if clean_cid.isdigit():
            target_cids.append(int(clean_cid))
            target_cids.append(-int(f"100{clean_cid}"))
            target_cids.append(-int(clean_cid))

        placeholders = ",".join("?" for _ in target_cids)

        existing = conn.execute(
            f"""
            SELECT *
            FROM auto_orders
            WHERE owner_id = ?
              AND channel_id IN ({placeholders})
              AND message_id = ?
              AND service_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            [owner_id] + target_cids + [mid, sid]
        ).fetchone()

        if existing:
            return existing

        now = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO auto_orders
            (
                channel_id,
                message_id,
                owner_id,
                service_id,
                link,
                quantity,
                external_order_id,
                price,
                status,
                error,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, 'Pending', '', ?)
            """,
            (
                norm_cid,
                mid,
                owner_id,
                sid,
                link,
                quantity,
                price,
                now
            )
        )

        conn.commit()

        if cur.lastrowid:
            return conn.execute(
                """
                SELECT *
                FROM auto_orders
                WHERE id = ?
                """,
                (cur.lastrowid,)
            ).fetchone()
        else:
            return conn.execute(
                f"""
                SELECT *
                FROM auto_orders
                WHERE owner_id = ?
                  AND channel_id IN ({placeholders})
                  AND message_id = ?
                  AND service_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                [owner_id] + target_cids + [mid, sid]
            ).fetchone()

    finally:
        conn.close()


def update_auto_order_success(
    internal_id,
    external_order_id,
    price=0
):
    conn = db()

    try:
        conn.execute(
            """
            UPDATE auto_orders
            SET
                external_order_id = ?,
                price = ?,
                status = 'Pending',
                error = ''
            WHERE id = ?
            """,
            (
                str(external_order_id),
                float(price or 0),
                internal_id
            )
        )

        conn.commit()

        row = conn.execute(
            "SELECT channel_id, message_id, service_id FROM auto_orders WHERE id = ?",
            (internal_id,)
        ).fetchone()
        if row:
            cid = clean_channel_id(row["channel_id"])
            try:
                mid = int(row["message_id"])
                sid = int(row["service_id"])
                ORDERED_POSTS_CACHE.add((cid, mid, sid))
            except Exception:
                pass

    finally:
        conn.close()


def update_auto_order_error(
    internal_id,
    error
):
    conn = db()

    try:
        conn.execute(
            """
            UPDATE auto_orders
            SET
                status = 'Error',
                error = ?
            WHERE id = ?
            """,
            (
                str(error)[:1000],
                internal_id
            )
        )

        conn.commit()

    finally:
        conn.close()


def send_auto_order_message(
    user_id,
    text
):
    if not user_id:
        return

    try:
        bot.send_message(
            int(user_id),
            text
        )

    except Exception as e:
        print(
            "❌ AUTO ORDER USERGA XABAR XATOSI:",
            repr(e)
        )


def send_real_auto_order(
    service,
    link,
    quantity,
    internal_id
):
    api_url = (
        str(service["url"] or "").strip()
    )

    api_key = (
        str(service["api_key"] or "").strip()
    )

    external_service_id = (
        str(service["external_id"] or "").strip().strip("'").strip('"')
    )

    if not api_url:
        raise RuntimeError(
            "API URL mavjud emas"
        )

    if not api_key:
        raise RuntimeError(
            "API key mavjud emas"
        )

    if not external_service_id:
        raise RuntimeError(
            "External service ID mavjud emas"
        )

    if not service["api_active"]:
        raise RuntimeError(
            "API faol emas"
        )

    payload = {
        "key": api_key,
        "action": "add",
        "service": external_service_id,
        "link": link,
        "quantity": int(quantity)
    }

    print()
    print("🚀 REAL API ORDER")
    print("--------------------------------------------")
    print(
        "🛠 Service:",
        service["name"]
    )
    print(
        "🆔 External service:",
        external_service_id
    )
    print(
        "🔢 Quantity:",
        quantity
    )
    print(
        "🔗 Link:",
        link
    )
    print(
        "🌐 API:",
        service["api_name"]
    )

    response = HTTP_SESSION.post(
        api_url,
        data=payload,
        timeout=30
    )

    response.raise_for_status()

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "API JSON format qaytarmadi: "
            + response.text[:500]
        )

    print(
        "📥 API RESPONSE:",
        data
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"API noto'g'ri javob qaytardi: {data}"
        )

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    external_order_id = (
        data.get("order")
        or data.get("order_id")
        or data.get("id")
    )

    if not external_order_id:
        raise RuntimeError(
            f"API order ID qaytarmadi: {data}"
        )

    api_price = (
        data.get("charge")
        or data.get("price")
        or 0
    )

    update_auto_order_success(
        internal_id,
        external_order_id,
        api_price
    )

    print(
        "✅ REAL ORDER YARATILDI:",
        external_order_id
    )

    return str(external_order_id)



def reserve_auto_order_balance(user_id, amount):
    """
    Avto-buyurtma uchun balansni atomik tekshiradi va yechadi.
    Balans yetarli bo'lmasa False qaytaradi.
    """
    amount = float(amount or 0)

    if amount <= 0:
        return True

    conn = db()

    try:
        cur = conn.execute(
            """
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ?
              AND COALESCE(balance, 0) >= ?
            """,
            (
                amount,
                int(user_id),
                amount
            )
        )

        conn.commit()

        if cur.rowcount == 1:
            print(
                "💰 AUTO ORDER BALANS YECHILDI:",
                int(user_id),
                amount
            )
            return True

        print(
            "❌ AUTO ORDER BALANS YETARLI EMAS:",
            int(user_id),
            amount
        )
        return False

    finally:
        conn.close()


def refund_auto_order_balance(user_id, amount):
    """
    API buyurtmasi xato bo'lsa rezerv qilingan balansni qaytaradi.
    """
    amount = float(amount or 0)

    if amount <= 0:
        return

    conn = db()

    try:
        conn.execute(
            """
            UPDATE users
            SET balance = COALESCE(balance, 0) + ?
            WHERE user_id = ?
            """,
            (
                amount,
                int(user_id)
            )
        )

        conn.commit()

        print(
            "↩️ AUTO ORDER BALANS QAYTARILDI:",
            int(user_id),
            amount
        )

    finally:
        conn.close()


async def process_real_auto_order(
    channel_id,
    message_id,
    owner_id,
    service,
    link,
    quantity
):
    service_id = int(
        service["service_id"]
    )

    estimated_price = (
        float(quantity) / 1000
    ) * float(
        service["price"] or 0
    )

    estimated_price = round(
        estimated_price,
        2
    )

    # Bir xil post + service uchun oldingi orderni tekshirish
    clean_cid = clean_channel_id(channel_id)
    mid = int(message_id)
    sid = service_id

    # 1. In-memory kesh tekshiruvi
    if (clean_cid, mid, sid) in ORDERED_POSTS_CACHE:
        log_debug(f"⏭️ AUTO ORDER ALLAQACHON MAVJUD (kesh): ch={clean_cid}, msg={mid}, svc={sid}")
        return

    # 2. Bazadan barcha ID formatlari bilan tekshirish
    target_cids = [str(channel_id), clean_cid, f"-100{clean_cid}", f"-{clean_cid}"]
    if clean_cid.isdigit():
        target_cids.append(int(clean_cid))
        target_cids.append(-int(f"100{clean_cid}"))
        target_cids.append(-int(clean_cid))

    placeholders = ",".join("?" for _ in target_cids)

    conn = db()
    try:
        existing = conn.execute(
            f"""
            SELECT *
            FROM auto_orders
            WHERE owner_id = ?
              AND channel_id IN ({placeholders})
              AND message_id = ?
              AND service_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            [owner_id] + target_cids + [mid, sid]
        ).fetchone()

    finally:
        conn.close()

    if existing:
        if existing["external_order_id"]:
            log_debug(f"⏭️ AUTO ORDER ALLAQACHON YARATILGAN: #{existing['external_order_id']}")
            ORDERED_POSTS_CACHE.add((clean_cid, mid, sid))
            return

        if str(existing["status"] or "").lower() in ("pending", "processing", "completed", "in progress", "in_progress", "success"):
            log_debug(f"⏭️ AUTO ORDER ALLAQACHON JARAYONDA: id={existing['id']}, status={existing['status']}")
            ORDERED_POSTS_CACHE.add((clean_cid, mid, sid))
            return

    # ========================================================
    # 💰 BALANS TEKSHIRUVI
    # ========================================================

    if estimated_price > 0:

        current_balance = get_balance(
            owner_id
        )

        print(
            "💰 AUTO ORDER BALANCE:",
            int(owner_id),
            current_balance,
            "/ kerak:",
            estimated_price
        )

        # Avval oddiy tekshiruv
        if current_balance < estimated_price:
            print(
                "⛔ AUTO ORDER TO'XTATILDI: BALANS YETARLI EMAS"
            )

            send_auto_order_message(
                owner_id,
                (
                    "⚠️ <b>Avtomatik buyurtma berilmadi</b>\n\n"
                    f"🛠 Xizmat: {service['name']}\n"
                    f"🔢 Miqdor: <b>{quantity}</b>\n"
                    f"💰 Kerakli balans: <b>{estimated_price:,.2f} so'm</b>\n"
                    f"💳 Sizning balansingiz: <b>{current_balance:,.2f} so'm</b>\n\n"
                    "❗ Balans yetarli emas."
                )
            )

            return

        # Atomik yechish:
        # parallel buyurtmalarda ham bitta balansni
        # ikki marta ishlatib yubormaydi.
        reserved = reserve_auto_order_balance(
            owner_id,
            estimated_price
        )

        if not reserved:
            latest_balance = get_balance(
                owner_id
            )

            print(
                "⛔ AUTO ORDER TO'XTATILDI: ATOMIK BALANS TEKSHIRUVI"
            )

            send_auto_order_message(
                owner_id,
                (
                    "⚠️ <b>Avtomatik buyurtma berilmadi</b>\n\n"
                    f"💳 Hozirgi balans: <b>{latest_balance:,.2f} so'm</b>\n"
                    f"💰 Kerak: <b>{estimated_price:,.2f} so'm</b>\n\n"
                    "❗ Balans yetarli emas."
                )
            )

            return

    else:
        estimated_price = 0

    # ========================================================
    # 🧾 AUTO ORDER DB RECORD
    # ========================================================

    record = create_auto_order_record(
        channel_id,
        message_id,
        owner_id,
        service_id,
        link,
        quantity,
        estimated_price
    )

    if not record:
        if estimated_price > 0:
            refund_auto_order_balance(
                owner_id,
                estimated_price
            )

        raise RuntimeError(
            "Auto order DB record yaratilmadi"
        )

    internal_id = int(
        record["id"]
    )

    try:
        api = get_auto_order_api(
            service_id
        )

        if not api:
            raise RuntimeError(
                "Service API topilmadi"
            )

        # ====================================================
        # 🚀 FAQAT BALANS TASDIQLANGANDAN KEYIN API
        # ====================================================

        external_order_id = send_real_auto_order(
            api,
            link,
            quantity,
            internal_id
        )

        ORDERED_POSTS_CACHE.add((clean_cid, mid, service_id))

        log_debug(f"✅ REAL AUTO ORDER YARATILDI: #{external_order_id} | svc={service_id} | qty={quantity}")

        send_auto_order_message(
            owner_id,
            (
                "✅ <b>Buyurtma berildi!</b>\n\n"
                f"🔢 Buyurtma: <code>#{external_order_id}</code>\n"
                f"🛠 Xizmat: {service['name']}\n"
                f"🔢 Miqdor: <b>{quantity}</b>\n"
                f"💰 Yechildi: <b>{estimated_price:,.2f} so'm</b>\n"
                f"🔗 <a href=\"{link}\">Post</a>\n\n"
                "⏳ Buyurtma bajarilishi kutilmoqda."
            )
        )

    except Exception as e:
        error_text = str(e)

        # API xato bersa, oldindan yechilgan pul qaytariladi.
        if estimated_price > 0:
            refund_auto_order_balance(
                owner_id,
                estimated_price
            )

        update_auto_order_error(
            internal_id,
            error_text
        )

        log_debug(f"❌ REAL AUTO ORDER XATOSI: {repr(e)}")

        send_auto_order_message(
            owner_id,
            (
                "❌ <b>Avtomatik buyurtma berilmadi</b>\n\n"
                f"🛠 Xizmat: {service['name']}\n"
                f"🔢 Miqdor: <b>{quantity}</b>\n"
                f"💰 Rezerv qaytarildi: <b>{estimated_price:,.2f} so'm</b>\n"
                f"❗ Xato: <code>{error_text[:500]}</code>"
            )
        )


def get_pending_auto_orders():
    conn = db()

    try:
        return conn.execute(
            """
            SELECT
                ao.*,
                s.name AS service_name,
                s.api_id,
                s.external_id AS service_external_id,
                a.url AS api_url,
                a.api_key,
                a.name AS api_name,
                a.active AS api_active
            FROM auto_orders ao
            JOIN services s
                ON s.id = ao.service_id
            JOIN apis a
                ON a.id = s.api_id
            WHERE ao.external_order_id IS NOT NULL
              AND TRIM(ao.external_order_id) != ''
              AND LOWER(
                    COALESCE(ao.status, 'pending')
                  ) NOT IN (
                    'completed',
                    'complete',
                    'success',
                    'successful',
                    'canceled',
                    'cancelled',
                    'error'
                  )
            ORDER BY ao.id ASC
            LIMIT 50
            """
        ).fetchall()

    finally:
        conn.close()


def update_auto_order_status(
    internal_id,
    status
):
    conn = db()

    try:
        conn.execute(
            """
            UPDATE auto_orders
            SET status = ?
            WHERE id = ?
            """,
            (
                str(status),
                internal_id
            )
        )

        conn.commit()

    finally:
        conn.close()


def check_real_auto_order_status(row):
    if not row["api_active"]:
        raise RuntimeError(
            "API faol emas"
        )

    response = HTTP_SESSION.post(
        row["api_url"],
        data={
            "key": row["api_key"],
            "action": "status",
            "order": str(
                row["external_order_id"]
            )
        },
        timeout=30
    )

    response.raise_for_status()

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "Status API JSON qaytarmadi"
        )

    print(
        "📊 ORDER STATUS:",
        row["external_order_id"],
        data
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Status noto'g'ri format: {data}"
        )

    if data.get("error"):
        raise RuntimeError(
            str(data["error"])
        )

    status = str(
        data.get("status")
        or data.get("state")
        or ""
    ).strip()

    return status


async def auto_order_status_worker():
    """
    Real API order statuslarini tekshiradi.
    Completed bo'lganda kanal egasiga bir marta xabar yuboradi.
    """

    while True:
        try:
            rows = get_pending_auto_orders()

            for row in rows:
                try:
                    status = await asyncio.to_thread(
                        check_real_auto_order_status,
                        row
                    )

                    if not status:
                        continue

                    normalized = status.lower()

                    if normalized in (
                        "completed",
                        "complete",
                        "success",
                        "successful"
                    ):
                        update_auto_order_status(
                            row["id"],
                            "Completed"
                        )

                        await asyncio.to_thread(
                            send_auto_order_message,
                            row["owner_id"],
                            (
                                "✅ <b>Buyurtma bajarildi!</b>\n\n"
                                f"🔢 Buyurtma: <code>#{row['external_order_id']}</code>\n"
                                f"🛠 Xizmat: {row['service_name']}\n"
                                f"🔢 Miqdor: <b>{row['quantity']}</b>\n"
                                f"🔗 <a href=\"{row['link']}\">Post</a>"
                            )
                        )

                        print(
                            "🎉 AUTO ORDER COMPLETED:",
                            row["external_order_id"]
                        )

                    elif normalized in (
                        "canceled",
                        "cancelled"
                    ):
                        update_auto_order_status(
                            row["id"],
                            "Canceled"
                        )

                        await asyncio.to_thread(
                            send_auto_order_message,
                            row["owner_id"],
                            (
                                "⚠️ <b>Buyurtma bekor qilindi</b>\n\n"
                                f"🔢 Buyurtma: <code>#{row['external_order_id']}</code>\n"
                                f"🛠 Xizmat: {row['service_name']}"
                            )
                        )

                except Exception as e:
                    print(
                        "❌ ORDER STATUS XATOSI |",
                        row["external_order_id"],
                        repr(e)
                    )

        except Exception as e:
            print(
                "❌ AUTO ORDER STATUS WORKER XATOSI:",
                repr(e)
            )

        await asyncio.sleep(60)



# ============================================================
# 🤖 ADMIN — AI ODDIY XABAR
# ============================================================

@bot.message_handler(
    content_types=["text"],
    func=lambda message: (
        is_admin(message.from_user)
        and not (message.text or "").startswith("/")
        and get_state(message.from_user.id).get("state") == "admin_ai"
    )
)
def ai_plain_admin_message(message):
    print("🤖 AI PLAIN XABAR KELDI:", repr(message.text), "STATE:", get_state(message.from_user.id))
    question = (message.text or "").strip()

    if not question:
        return

    try:
        wait = bot.send_message(
            message.chat.id,
            "🤖 AI tahlil qilmoqda..."
        )

        result = ai_agent_execute(
            message.from_user.id,
            question
        )

        bot.edit_message_text(
            result,
            message.chat.id,
            wait.message_id,
            parse_mode="HTML"
        )

    except Exception as e:
        print("❌ AI PLAIN HANDLER:", repr(e))
        bot.send_message(
            message.chat.id,
            "❌ AI xatolik berdi. Termux logini tekshiring."
        )


# ============================================================
# ODDIY FOYDALANUVCHILAR UCHUN ODDIY XABAR (AI O'CHIRILGAN)
# ============================================================

EXCLUDED_BUTTONS = {
    "📦 Buyurtma berish", "📦 Avto buyurtma ulash",
    "💵 Pul kiritish", "👤 Kabinet", "🆘 Support", "📕 Qo'llanma",
    "💰 Balans", "🏠 Asosiy menyu", "🏠 Bosh menyu",
    "➕ Yangi kanal", "📌 Kanallarim", "⬅️ Orqaga", "❌ Bekor qilish",
    "⚙️ Admin panel"
}

@bot.message_handler(
    content_types=["text"],
    func=lambda message: (
        not (message.text or "").startswith("/")
        and message.text not in EXCLUDED_BUTTONS
        and not get_state(message.from_user.id).get("state")
    )
)
def default_user_message(message):
    try:
        save_message_history(
            message.from_user.id,
            message.chat.id,
            message.message_id,
            "in",
            (message.text or "").strip()
        )
    except Exception:
        pass

    try:
        bot.send_message(
            message.chat.id,
            "📌 Iltimos, kerakli bo'limni tanlash uchun pastdagi menyu tugmalaridan foydalaning.",
            reply_markup=main_reply_keyboard(message.from_user.id)
        )
    except Exception:
        pass



# ============================================================
# ISHGA TUSHIRISH VA WORKERLAR
# ============================================================

async def api_auto_sync_worker():
    """API xizmatlarini avtomatik ravishda yangilab turadi."""
    while True:
        try:
            conn = db()
            apis = conn.execute(
                "SELECT id, name FROM apis WHERE active = 1"
            ).fetchall()
            conn.close()

            for api in apis:
                try:
                    result = await asyncio.to_thread(
                        sync_api_services,
                        api["id"],
                        False
                    )

                    print(
                        f"🔄 API AUTO SYNC | "
                        f"#{api['id']} {api['name']} | "
                        f"yangi: {result.get('new', 0)} | "
                        f"yangilangan: {result.get('updated', 0)} | "
                        f"o'zgarmagan: {result.get('unchanged', 0)}"
                    )

                except Exception as e:
                    print(
                        f"❌ API AUTO SYNC XATOSI | "
                        f"#{api['id']} {api['name']}: {e}"
                    )

        except Exception as e:
            print(f"❌ AUTO SYNC WORKER XATOSI: {e}")

        await asyncio.sleep(600)


async def userbot_schedule_worker():
    """Har 10 soniyada rejalashtirilgan userbot xabarlarini tekshiradi."""
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = db()
            try:
                rows = conn.execute(
                    """
                    SELECT id, target, text, run_at
                    FROM userbot_schedules
                    WHERE status = 'pending'
                      AND run_at <= ?
                    ORDER BY id ASC
                    LIMIT 10
                    """,
                    (now,)
                ).fetchall()
            finally:
                conn.close()

            for row in rows:
                try:
                    if 'userbot' in globals() and userbot and userbot.is_connected():
                        target = row["target"]
                        if isinstance(target, str) and (target.lstrip("-").isdigit()):
                            target = int(target)
                        await userbot.send_message(target, row["text"])
                        conn = db()
                        conn.execute(
                            "UPDATE userbot_schedules SET status = 'sent' WHERE id = ?",
                            (row["id"],)
                        )
                        conn.commit()
                        conn.close()
                        print(f"✅ USERBOT SCHEDULE YUBORILDI: {row['id']}")
                    else:
                        break
                except Exception as send_err:
                    print(f"❌ USERBOT SCHEDULE ERROR #{row['id']}: {send_err}")
                    conn = db()
                    conn.execute(
                        "UPDATE userbot_schedules SET status = 'error' WHERE id = ?",
                        (row["id"],)
                    )
                    conn.commit()
                    conn.close()
        except Exception as e:
            print(f"❌ USERBOT SCHEDULE WORKER XATOSI: {e}")

        await asyncio.sleep(10)


# ============================================================
# 🌟 AUTO GROUP FACT POSTER (5 DAQIQA)
# ============================================================

FALLBACK_FACTS = [
    "🧠 <b>BILASIZMI?</b>\n\nInson miyasi tunda kunduzgiga qaraganda faolroq ishlaydi. Uyqu vaqtida u xotiralarni tartibga soladi va yangi g'oyalarni shakllantiradi.\n\n#qiziqarli #fakt #miya",
    "🌌 <b>BILASIZMI?</b>\n\nKoinotda Yerdagi barcha qum zarralaridan ko'ra ko'proq yulduzlar mavjud. Somon yo'li galaktikasining o'zida 100 milliarddan ortiq yulduz bor!\n\n#koinot #fakt #fazo",
    "🌊 <b>BILASIZMI?</b>\n\nOkeanlarning 80 foizdan ortig'i insoniyat tomonidan hali o'rganilmagan. Biz Oy yuzasini okean tubidan yaxshiroq bilamiz.\n\n#tabiat #okean #dunyo",
    "⚡ <b>BILASIZMI?</b>\n\nBitta chaqmoq chaqnashi taxminan 100 000 dona nonni bir zumda qovurishga yetadigan elektr energiyasini ishlab chiqaradi!\n\n#fizika #tabiat #energiya",
    "🍯 <b>BILASIZMI?</b>\n\nAsal hech qachon buzilmaydi. Qadimgi Misr piramidalaridan topilgan 3000 yillik asal hali ham iste'molga yaroqli holatda saqlangan!\n\n#tarix #asal #mojiza",
    "🦅 <b>BILASIZMI?</b>\n\nBurgutlar 3 kilometr uzoqlikdagi kichik quyonni bemalol ko'ra oladi. Ularning ko'rish qobiliyati insonnikidan 8 barobar kuchliroqdir.\n\n#hayvonot #qushlar #fakt",
    "⏱ <b>BILASIZMI?</b>\n\nYorug'lik Quyoshdan Yerga yetib kelishi uchun roppa-rosa 8 daqiqa 20 soniya vaqt sarflaydi. Demak, biz Quyoshni doim 8 daqiqa oldingi holatida ko'ramiz!\n\n#quyosh #fizika #vaqt",
    "🐬 <b>BILASIZMI?</b>\n\nDelfinlar uxlaganda ularning miyasining faqat bitta yarmi uxlaydi, ikkinchi yarmi esa nafas olish va xavfni sezish uchun uyg'oq turadi.\n\n#tabiat #delfin #hayot",
    "📱 <b>BILASIZMI?</b>\n\nBugungi kunda har qanday oddiy smartfon 1969-yilda odamni Oyga uchirgan NASA ning barcha superkompyuterlaridan millionlab marta kuchliroqdir!\n\n#texnologiya #tarix #smartfon",
    "🌲 <b>BILASIZMI?</b>\n\nBitta katta eman daraxti bir yilda taxminan 4 nafar odam uchun bir yillik toza kislorod ishlab chiqaradi.\n\n#tabiat #ekologiya #daraxt"
]

def generate_interesting_fact():
    if GROQ_API_KEY:
        try:
            prompt = (
                "Telegram guruhi uchun o'zbek tilida 1 ta juda qiziqarli, ajoyib va hayratlanarli fakt yoz. "
                "Format:\n"
                "🧠 <b>BILASIZMI?</b>\n\n"
                "[Fakt matni 2-3 qatorda]\n\n"
                "#qiziqarli #fakt\n"
                "Faqat shu formatda qaytar."
            )
            res = HTTP_SESSION.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "qwen/qwen3.8-27b",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.85
                },
                timeout=10
            )
            fact = res.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if fact and len(fact) > 20:
                return fact
        except Exception as e:
            print("⚠️ GROQ FAKT XATOSI:", e)
    return random.choice(FALLBACK_FACTS)


def register_group_for_posts(chat_id, title):
    try:
        conn = db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auto_post_groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                active INTEGER DEFAULT 1,
                last_post_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO auto_post_groups (chat_id, title, active)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET title = ?, active = 1
        """, (chat_id, title, title))
        conn.commit()
        conn.close()
        print(f"✅ GURUH RO'YXATGA OLINDI: '{title}' ({chat_id})")
    except Exception as e:
        print("❌ register_group_for_posts xatosi:", e)


@bot.my_chat_member_handler()
def handle_group_member_event(update):
    try:
        chat = update.chat
        if chat.type in ["group", "supergroup"]:
            register_group_for_posts(chat.id, chat.title)
            fact = generate_interesting_fact()
            bot.send_message(
                chat.id,
                f"🎉 <b>Salom, {html.escape(chat.title or 'Guruh')} ahli!</b>\n\n"
                f"Bot guruhga muvaffaqiyatli ulandi. Endi har 5 daqiqada ajoyib va qiziqarli faktlar ulashib boraman! 🚀\n\n"
                f"{fact}",
                parse_mode="HTML"
            )
    except Exception as e:
        print("❌ handle_group_member_event xatosi:", e)


@bot.message_handler(func=lambda m: m.chat.type in ["group", "supergroup"])
def handle_group_messages(m):
    try:
        register_group_for_posts(m.chat.id, m.chat.title)
        if m.text and (m.text.startswith(("/start", "/fakt", "/post", "/id")) or "@" in m.text):
            fact = generate_interesting_fact()
            bot.reply_to(
                m,
                f"💡 <b>Qiziqarli fakt:</b>\n\n{fact}",
                parse_mode="HTML"
            )
    except Exception as e:
        print("❌ handle_group_messages xatosi:", e)


async def auto_group_post_worker():
    while True:
        await asyncio.sleep(300)  # Har 5 daqiqada
        try:
            conn = db()
            try:
                rows = conn.execute("SELECT chat_id, title FROM auto_post_groups WHERE active = 1").fetchall()
            finally:
                conn.close()

            if rows:
                fact = generate_interesting_fact()
                for row in rows:
                    chat_id = row["chat_id"]
                    title = row["title"]
                    try:
                        bot.send_message(chat_id, fact, parse_mode="HTML")
                        print(f"📢 5-DAQIQA POSTI YUBORILDI: {title} ({chat_id})")
                    except Exception as send_err:
                        print(f"⚠️ Guruhga yuborishda xatolik {title}: {send_err}")

            render_url = os.getenv("RENDER_EXTERNAL_URL", "https://cheap-bot-gahc.onrender.com")
            try:
                HTTP_SESSION.get(render_url, timeout=10)
            except Exception:
                pass

        except Exception as e:
            print("❌ AUTO GROUP POST WORKER XATOSI:", e)


async def keep_alive_worker():
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://cheap-bot-gahc.onrender.com")
    ping_count = 0
    while True:
        await asyncio.sleep(480)
        ping_count += 1
        try:
            r = HTTP_SESSION.get(render_url, timeout=10)
            print(f"💓 KEEP-ALIVE PING: {r.status_code}")
        except Exception as e:
            print("⚠️ KEEP-ALIVE PING XATOSI:", e)

        # Har 2 soatda avtomatik DB lokal zaxirasi
        if ping_count % 15 == 0:
            try:
                save_local_db_backup(reason="Rejali avtomatik 2 soatlik zaxira")
            except Exception:
                pass


async def bot_polling_worker():
    while True:
        try:
            print("🚀 TeleBot polling boshlandi...")
            await asyncio.to_thread(
                bot.infinity_polling,
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )
        except Exception as e:
            print(f"⚠️ TeleBot polling to'xtadi: {e}, 3 soniyada qayta uriniladi...")
            await asyncio.sleep(3)


async def supervised_worker(coro_fn, name):
    """Fonda ishlovchi workerlarni nazorat qiluvchi va kutilmagan xatolikda qayta ishga tushiruvchi supervisor."""
    while True:
        try:
            await coro_fn()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log_debug(f"⚠️ SUPERVISOR [{name}] xatosi: {e}. 5 soniyada qayta ishga tushadi...")
            import traceback
            log_debug(f"TRACEBACK: {traceback.format_exc()}")
            await asyncio.sleep(5)


async def run_all():
    global MAIN_LOOP

    MAIN_LOOP = asyncio.get_running_loop()

    # Bazadagi mavjud buyurtmalarni in-memory deduplikatsiya keshiga yuklash
    init_ordered_posts_cache()

    try:
        bot.set_chat_menu_button(menu_button=types.MenuButtonDefault())
    except Exception:
        pass

    await asyncio.gather(
        supervised_worker(bot_polling_worker, "bot_polling"),
        supervised_worker(start_userbot, "userbot"),
        supervised_worker(api_auto_sync_worker, "api_auto_sync"),
        supervised_worker(userbot_schedule_worker, "userbot_schedule"),
        supervised_worker(auto_order_status_worker, "auto_order_status"),
        supervised_worker(keep_alive_worker, "keep_alive"),
        supervised_worker(auto_group_post_worker, "auto_group_post"),
        return_exceptions=True
    )


if __name__ == "__main__":
    print("============================================")
    print("🤖 CheapReaction | Avto")
    print("🐍 Python:", __import__("sys").version.split()[0])
    print("💾 DB:", DB_FILE)
    print("============================================")
    asyncio.run(run_all())

