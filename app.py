import os
import json
import re
import asyncio
import sqlite3
import secrets
import threading
import time
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
from telethon import TelegramClient, events, tl
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENVIRONMENT =================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
PERMANENT_KEY = "felix_unlimited_2024"
DEVELOPER_TAG = "@rajfflive"
CACHE_EXPIRE_SECONDS = int(os.environ.get("CACHE_EXPIRE_SECONDS", 86400))
MAX_RESULTS = 4

GROUP_MAIN_NAME = "USERSXINFO CHEATING GC"
GROUP_OTHER_NAME = "TGTOINFO"
BOT_USERNAME = "usersXinfo0bot"
FUNSTATE_BOT_USERNAME = "Funstate_7bot"

GROUP_MAIN = None
GROUP_OTHER = None
BOT_ID = None
FUNSTATE_BOT_ID = None
FUNSTATE_BOT_ENTITY = None
SPECIAL_COMMANDS = ["upiinfo", "fam", "family", "pan", "tg", "leak"]

# ================= DATABASE =================
DB_FILE = "felix_api.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS accounts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT,
                  api_id INTEGER,
                  api_hash TEXT,
                  session_string TEXT,
                  active INTEGER DEFAULT 1,
                  last_used TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys
                 (key TEXT PRIMARY KEY,
                  name TEXT,
                  owner TEXT,
                  created_at TIMESTAMP,
                  expiry_days INTEGER,
                  daily_limit INTEGER,
                  unlimited INTEGER DEFAULT 0,
                  active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key TEXT,
                  command TEXT,
                  value TEXT,
                  response TEXT,
                  success INTEGER,
                  timestamp TIMESTAMP,
                  account_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_usage
                 (key TEXT,
                  date TEXT,
                  count INTEGER,
                  PRIMARY KEY (key, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  command TEXT,
                  value TEXT,
                  success INTEGER,
                  timestamp TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS global_settings
                 (key TEXT PRIMARY KEY,
                  value TEXT)''')
    for cmd in SPECIAL_COMMANDS:
        c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES (?, '1')", (f"cmd_{cmd}_enabled",))
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('funstate_enabled', '1')")
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('group_main_enabled', '1')")
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('delete_delay', '10')")
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('public_key', '')")
    conn.commit()
    conn.close()
init_db()

# ---------- DB HELPERS ----------
def get_global_setting(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM global_settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_global_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO global_settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()

def get_active_accounts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, api_id, api_hash, session_string FROM accounts WHERE active=1 ORDER BY last_used NULLS FIRST")
    rows = c.fetchall()
    conn.close()
    return rows

def update_account_last_used(account_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE accounts SET last_used = ? WHERE id = ?", (datetime.now(timezone.utc).isoformat(), account_id))
    conn.commit()
    conn.close()

def add_account(name, api_id, api_hash, session_string):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO accounts (name, api_id, api_hash, session_string) VALUES (?,?,?,?)",
              (name, api_id, api_hash, session_string))
    conn.commit()
    conn.close()

def delete_account(account_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()

def toggle_account(account_id, active):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE accounts SET active = ? WHERE id = ?", (1 if active else 0, account_id))
    conn.commit()
    conn.close()

def get_all_accounts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, api_id, active FROM accounts")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "api_id": r[2], "active": bool(r[3])} for r in rows]

def add_api_key(key, name, owner, expiry_days, daily_limit, unlimited=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO api_keys (key, name, owner, created_at, expiry_days, daily_limit, unlimited, active) VALUES (?,?,?,?,?,?,?,1)",
              (key, name, owner, datetime.now(timezone.utc).isoformat(), expiry_days, daily_limit, unlimited))
    conn.commit()
    conn.close()

def get_api_key_info(api_key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT key, name, owner, created_at, expiry_days, daily_limit, unlimited, active FROM api_keys WHERE key = ?", (api_key,))
    row = c.fetchone()
    conn.close()
    if not row: return None
    key, name, owner, created_at, expiry_days, daily_limit, unlimited, active = row
    if not active: return None
    if expiry_days > 0:
        created_dt = datetime.fromisoformat(created_at)
        if datetime.now(timezone.utc) > created_dt + timedelta(days=expiry_days):
            return None
    if not unlimited and daily_limit > 0:
        today = datetime.now(timezone.utc).date().isoformat()
        conn2 = sqlite3.connect(DB_FILE)
        c2 = conn2.cursor()
        c2.execute("SELECT count FROM daily_usage WHERE key = ? AND date = ?", (api_key, today))
        row2 = c2.fetchone()
        count = row2[0] if row2 else 0
        conn2.close()
        if count >= daily_limit:
            return {"error": "Daily limit exceeded"}
    return {"key": key, "name": name, "owner": owner, "unlimited": unlimited, "daily_limit": daily_limit, "expiry_days": expiry_days}

def increment_daily_usage(api_key):
    today = datetime.now(timezone.utc).date().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO daily_usage (key, date, count) VALUES (?,?,1) ON CONFLICT(key,date) DO UPDATE SET count = count + 1", (api_key, today))
    conn.commit()
    conn.close()

def log_usage(api_key, command, value, response, success, account_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO usage_logs (key, command, value, response, success, timestamp, account_id) VALUES (?,?,?,?,?,?,?)",
              (api_key, command, value, response[:500], 1 if success else 0, datetime.now(timezone.utc).isoformat(), account_id))
    conn.commit()
    conn.close()

def get_usage_logs(limit=50):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT timestamp, key, command, value, response, success FROM usage_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"timestamp": r[0], "key": r[1], "command": r[2], "value": r[3], "response": r[4], "success": bool(r[5])} for r in rows]

def get_all_keys():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT key, name, owner, created_at, expiry_days, daily_limit, unlimited, active FROM api_keys")
    rows = c.fetchall()
    conn.close()
    return [{"key": r[0], "name": r[1], "owner": r[2], "created_at": r[3], "expiry_days": r[4], "daily_limit": r[5], "unlimited": bool(r[6]), "active": bool(r[7])} for r in rows]

def revoke_key(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
    conn.commit()
    conn.close()

def delete_key(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM api_keys WHERE key = ?", (key,))
    conn.commit()
    conn.close()

def add_stats(command, value, success):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO stats (command, value, success, timestamp) VALUES (?,?,?,?)",
              (command, value, 1 if success else 0, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM stats")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM stats WHERE success=1")
    success = c.fetchone()[0]
    fail = total - success
    conn.close()
    return total, success, fail

# ================= CACHE =================
response_cache = {}

def get_cached(cmd, value):
    key = f"{cmd}:{value}"
    if key in response_cache:
        data, timestamp = response_cache[key]
        if time.time() - timestamp < CACHE_EXPIRE_SECONDS:
            return data
        else:
            del response_cache[key]
    return None

def set_cache(cmd, value, data):
    key = f"{cmd}:{value}"
    response_cache[key] = (data, time.time())

def clear_cache():
    global response_cache
    response_cache.clear()

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# ================= TELEGRAM ACCOUNT MANAGER =================
accounts = []
account_clients = {}
telegram_loops = {}
account_index = -1

def get_next_account():
    global account_index
    if not accounts:
        return None
    account_index = (account_index + 1) % len(accounts)
    return accounts[account_index]

def get_all_active_clients():
    """Return list of (account, client, loop) for all connected accounts, starting from next in round-robin order."""
    if not accounts:
        return []
    result = []
    start = (account_index + 1) % len(accounts)
    ordered = accounts[start:] + accounts[:start]
    for acc in ordered:
        client = account_clients.get(acc['id'])
        loop = telegram_loops.get(acc['id'])
        if client and loop:
            result.append((acc, client, loop))
    return result

async def start_account(account_data):
    global GROUP_MAIN, GROUP_OTHER, BOT_ID, FUNSTATE_BOT_ID, FUNSTATE_BOT_ENTITY
    acc_id = account_data['id']
    api_id = account_data['api_id']
    api_hash = account_data['api_hash']
    session = account_data['session_string']
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()
    logger.info(f"✅ Account {account_data['name']} (ID: {acc_id}) connected")
    account_clients[acc_id] = client

    for name, var_name in [(GROUP_MAIN_NAME, 'GROUP_MAIN'), (GROUP_OTHER_NAME, 'GROUP_OTHER')]:
        try:
            entity = await client.get_entity(name)
            if var_name == 'GROUP_MAIN':
                GROUP_MAIN = entity
            else:
                GROUP_OTHER = entity
            logger.info(f"✅ {var_name}: {entity.title} (ID: {entity.id})")
        except:
            async for dialog in client.iter_dialogs():
                if dialog.name == name:
                    if var_name == 'GROUP_MAIN':
                        GROUP_MAIN = dialog.entity
                    else:
                        GROUP_OTHER = dialog.entity
                    logger.info(f"✅ {var_name} via dialog: {dialog.name}")
                    break

    try:
        bot_entity = await client.get_entity(BOT_USERNAME)
        BOT_ID = bot_entity.id
        logger.info(f"✅ Bot ID: {BOT_ID}")
    except:
        logger.warning(f"⚠️ Could not fetch bot {BOT_USERNAME}")

    try:
        funstate_entity = await client.get_entity(FUNSTATE_BOT_USERNAME)
        FUNSTATE_BOT_ID = funstate_entity.id
        FUNSTATE_BOT_ENTITY = funstate_entity
        logger.info(f"✅ Funstate Bot ID: {FUNSTATE_BOT_ID}")
    except:
        logger.warning(f"⚠️ Could not fetch Funstate bot {FUNSTATE_BOT_USERNAME}")

    if GROUP_OTHER:
        try:
            await client.send_message(GROUP_OTHER, "Started ✅")
            logger.info(f"📢 Sent startup to {GROUP_OTHER_NAME}")
        except Exception as e:
            logger.error(f"Startup message failed: {e}")

    @client.on(events.NewMessage)
    async def handler(event):
        if event.sender_id == BOT_ID or event.sender_id == FUNSTATE_BOT_ID:
            logger.info(f"📩 Bot message seen: {event.raw_text[:50]}...")

    await client.run_until_disconnected()

def init_accounts():
    global accounts
    rows = get_active_accounts()
    for row in rows:
        acc_id, name, api_id, api_hash, session_str = row
        accounts.append({"id": acc_id, "name": name, "api_id": api_id, "api_hash": api_hash, "session_string": session_str})
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        telegram_loops[acc_id] = loop
        thread = threading.Thread(target=lambda: loop.run_until_complete(start_account(accounts[-1])), daemon=True)
        thread.start()
        time.sleep(2)
    if not accounts:
        i = 1
        while True:
            name = os.environ.get(f"ACCOUNT{i}_NAME")
            api_id = os.environ.get(f"ACCOUNT{i}_API_ID")
            api_hash = os.environ.get(f"ACCOUNT{i}_API_HASH")
            session_str = os.environ.get(f"ACCOUNT{i}_SESSION")
            if not all([name, api_id, api_hash, session_str]):
                break
            add_account(name, int(api_id), api_hash, session_str)
            logger.info(f"Added {name} from env")
            i += 1
        if i > 1:
            init_accounts()

# ================= JSON EXTRACTION =================
def extract_json_objects(text, limit=MAX_RESULTS):
    objects = []
    i = 0
    while i < len(text) and len(objects) < limit:
        if text[i] == '{':
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j+1]
                        try:
                            obj = json.loads(candidate)
                            objects.append(obj)
                            i = j
                            break
                        except:
                            pass
                j += 1
        i += 1
    return objects

def clean_object(obj):
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if k.lower() in ('tag', 'developer'):
                new_obj[k] = DEVELOPER_TAG
            else:
                new_obj[k] = clean_object(v)
        return new_obj
    elif isinstance(obj, list):
        return [clean_object(item) for item in obj]
    else:
        return obj

def finalize_response(data):
    if data is None:
        return None
    if isinstance(data, dict):
        if 'data' in data and isinstance(data['data'], list):
            data['data'] = data['data'][:MAX_RESULTS]
        cleaned = clean_object(data)
        cleaned['developer'] = DEVELOPER_TAG
        cleaned['tag'] = DEVELOPER_TAG
        return cleaned
    elif isinstance(data, list):
        limited = data[:MAX_RESULTS]
        cleaned_list = []
        for item in limited:
            cleaned = clean_object(item)
            cleaned['developer'] = DEVELOPER_TAG
            cleaned['tag'] = DEVELOPER_TAG
            cleaned_list.append(cleaned)
        return cleaned_list
    else:
        return data

# ================= FUNSTATE RESPONSE PARSER (FIXED) =================
def normalize_text(text):
    """
    Normalize unicode lookalike / decorative chars to plain ASCII.
    Extended to cover all common Funstate bot output characters.
    """
    replacements = {
        # ---- Cyrillic lookalikes ----
        'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x',
        'А': 'A', 'Е': 'E', 'О': 'O', 'Р': 'P', 'С': 'C', 'Х': 'X',
        'М': 'M', 'В': 'B', 'К': 'K', 'Т': 'T',
        'ѕ': 's',  # Cyrillic DZE (used as 's')
        # ---- Greek lookalikes ----
        'α': 'a', 'β': 'b', 'γ': 'y', 'δ': 'd', 'ε': 'e', 'ζ': 'z',
        'η': 'n', 'θ': 'th', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm',
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
        'τ': 't', 'υ': 'u', 'φ': 'f', 'χ': 'x', 'ψ': 'ps', 'ω': 'o',
        'Α': 'A', 'Β': 'B', 'Γ': 'G', 'Δ': 'D', 'Ε': 'E', 'Ζ': 'Z',
        'Η': 'H', 'Θ': 'Th', 'Ι': 'I', 'Κ': 'K', 'Λ': 'L', 'Μ': 'M',
        'Ν': 'N', 'Ξ': 'X', 'Ο': 'O', 'Π': 'P', 'Ρ': 'R', 'Σ': 'S',
        'Τ': 'T', 'Υ': 'U', 'Φ': 'F', 'Χ': 'X', 'Ψ': 'Ps', 'Ω': 'O',
        # ---- Latin extended / IPA / Unicode letter forms ----
        'ɑ': 'a', 'ɐ': 'a', 'ɒ': 'a',
        'ƅ': 'b', 'ƃ': 'b',
        'ç': 'c', 'ć': 'c', 'č': 'c',
        'ď': 'd', 'đ': 'd',
        'è': 'e', 'é': 'e', 'ê': 'e', 'ë': 'e', 'ě': 'e',
        '℮': 'e',   # ESTIMATED SIGN (used as 'e' in Funstate)
        'ƒ': 'f', 'Ƒ': 'F',
        'ĝ': 'g', 'ğ': 'g', 'ġ': 'g', 'ģ': 'g', 'ɡ': 'g', 'ᵍ': 'g',
        'ĥ': 'h', 'ħ': 'h',
        'ì': 'i', 'í': 'i', 'î': 'i', 'ï': 'i', 'ĩ': 'i', 'ī': 'i',
        'ĵ': 'j',
        'ķ': 'k', 'қ': 'k', 'ĸ': 'k',
        'ĺ': 'l', 'ļ': 'l', 'ľ': 'l', 'ŀ': 'l', 'ł': 'l',
        'ṁ': 'm',
        'ñ': 'n', 'ń': 'n', 'ņ': 'n', 'ň': 'n', 'ŋ': 'n',
        'ò': 'o', 'ó': 'o', 'ô': 'o', 'õ': 'o', 'ö': 'o', 'ø': 'o',
        'ṗ': 'p',
        'ŕ': 'r', 'ŗ': 'r', 'ř': 'r',
        'ś': 's', 'ŝ': 's', 'ş': 's', 'š': 's',
        'ţ': 't', 'ť': 't', 'ŧ': 't',
        'ù': 'u', 'ú': 'u', 'û': 'u', 'ü': 'u', 'ũ': 'u', 'ū': 'u',
        'ŵ': 'w',
        'ý': 'y', 'ÿ': 'y',
        'ź': 'z', 'ż': 'z', 'ž': 'z',
        # ---- Small caps / modifier letters ----
        'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd', 'ᴇ': 'e', 'ꜰ': 'f',
        'ɢ': 'g', 'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j', 'ᴋ': 'k', 'ʟ': 'l',
        'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o', 'ᴘ': 'p', 'ǫ': 'q', 'ʀ': 'r',
        'ꜱ': 's', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'x': 'x',
        'ʏ': 'y', 'ᴢ': 'z',
        # ---- Superscript / subscript digits and letters ----
        '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        # ---- Fullwidth ASCII (Ａ-Ｚ, ａ-ｚ, ０-９) ----
        'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
        'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
        'Ｋ': 'K', 'Ｌ': 'L', 'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O',
        'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R', 'Ｓ': 'S', 'Ｔ': 'T',
        'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X', 'Ｙ': 'Y', 'Ｚ': 'Z',
        'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e',
        'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j',
        'ｋ': 'k', 'ｌ': 'l', 'ｍ': 'm', 'ｎ': 'n', 'ｏ': 'o',
        'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r', 'ｓ': 's', 'ｔ': 't',
        'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x', 'ｙ': 'y', 'ｚ': 'z',
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        # ---- Misc symbols used by Funstate ----
        'ᖴ': 'F',   # Canadian syllabics F (used as F in Funstate)
        'ᴍ': 'M',
        'ɢ': 'g',
        'ℕ': 'N',
        'ℤ': 'Z',
        'ℂ': 'C',
        'ℝ': 'R',
        '℃': 'C',
        'ℓ': 'l',
        'ℱ': 'F',
        'ℋ': 'H',
        'ℐ': 'I',
        'ℒ': 'L',
        'ℳ': 'M',
        'ℛ': 'R',
        'ᴵ': 'I', 'ᴰ': 'D',
        # ---- Unicode multi-char replacements (do these as string replace) ----
        'ＩＤ': 'ID',
    }
    result = text
    # Apply longer replacements first to avoid partial matches
    for src in sorted(replacements, key=len, reverse=True):
        result = result.replace(src, replacements[src])
    return result

def extract_urls_from_message(msg):
    """Extract all URLs and button URLs from a single Telethon message."""
    urls = []
    text = msg.text or ""

    if msg.entities:
        for entity in msg.entities:
            if hasattr(entity, 'url') and entity.url:
                urls.append(('entity_texturl', entity.url))
            elif isinstance(entity, tl.types.MessageEntityUrl):
                url = text[entity.offset:entity.offset + entity.length]
                urls.append(('entity_url', url))

    if msg.reply_markup and hasattr(msg.reply_markup, 'rows'):
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if hasattr(btn, 'url') and btn.url:
                    label = btn.text if hasattr(btn, 'text') else ''
                    urls.append(('button', btn.url, label))

    for m in re.finditer(r'https?://[^\s<>\]\)]+', text):
        urls.append(('regex', m.group()))
    for m in re.finditer(r't\.me/[a-zA-Z0-9_+/]+', text):
        u = m.group()
        if not u.startswith('http'):
            u = 'https://' + u
        urls.append(('regex_tme', u))

    return urls

def parse_funstate_response(messages):
    """
    Parse a list of Telethon Message objects from Funstate bot.
    Returns a clean structured dict — no raw text, no sticker links,
    no Funstate bot URLs.
    """
    result = {}
    raw_text_parts = []
    bio_link = None

    for msg in messages:
        raw_text_parts.append(msg.raw_text or "")

    combined_raw = "\n".join(raw_text_parts)
    combined_norm = normalize_text(combined_raw)

    # ---- Display name ("This is NAME") ----
    name_match = re.search(r'this\s+is\s+(.+?)(?:\n|$)', combined_norm, re.IGNORECASE)
    if name_match:
        result['name'] = name_match.group(1).strip()

    # ---- Collect and classify URLs ----
    seen_urls = set()
    button_urls = []
    sticker_set = set()
    tme_links = []

    for msg in messages:
        for url_entry in extract_urls_from_message(msg):
            url = url_entry[1] if len(url_entry) > 1 else ''
            label = url_entry[2] if len(url_entry) > 2 else ''

            url = url.strip().rstrip('.,;)')
            if not url:
                continue

            # FIX: skip Funstate bot URLs BEFORE adding to seen_urls
            if FUNSTATE_BOT_USERNAME.lower() in url.lower():
                continue

            if url in seen_urls:
                continue
            seen_urls.add(url)

            if url_entry[0] == 'button':
                button_urls.append((url, label))

            is_sticker = 'addstickers' in url or '/addstickers/' in url
            is_tme = 't.me/' in url or url.startswith('@')

            if is_sticker:
                if not url.startswith('http'):
                    url = 'https://' + url
                sticker_set.add(url)
            elif is_tme:
                tme_links.append(url)

    # ---- Bio link detection (channel/bio link from buttons or text) ----
    for url, label in button_urls:
        norm_label = normalize_text(label).lower()
        if 'channel' in norm_label or 'chan' in norm_label:
            if 'addstickers' not in url:
                bio_link = url
                break

    if not bio_link:
        for line in combined_norm.split('\n'):
            line_norm = line.lower()
            if re.search(r'ch[a@][nn][e][l]', line_norm) or 'channel' in line_norm:
                tme_in_line = re.findall(r'(?:https?://)?t\.me/[a-zA-Z0-9_]+', line)
                if tme_in_line:
                    u = tme_in_line[0]
                    if not u.startswith('http'):
                        u = 'https://' + u
                    if 'addstickers' not in u:
                        bio_link = u
                        break
                at_in_line = re.findall(r'@[a-zA-Z0-9_]+', line)
                if at_in_line:
                    bio_link = 'https://t.me/' + at_in_line[0].lstrip('@')
                    break

    if not bio_link:
        for url, label in button_urls:
            if 'addstickers' not in url and 't.me/' in url:
                bio_link = url
                break

    if not bio_link and tme_links:
        bio_link = tme_links[0]

    # ---- ID extraction ----
    id_match = re.search(r'(?:ID|iD)[:\s]*(\d{5,})', combined_norm, re.IGNORECASE)
    if id_match:
        result['id'] = id_match.group(1)

    # ---- Username extraction ----
    usernames = []
    uname_section_match = re.search(r'usernames?:?\s*\n?\s*\|?\s*(.+?)(?:\n\n|\n[^\|@])', combined_norm, re.IGNORECASE | re.DOTALL)
    if uname_section_match:
        uname_text = uname_section_match.group(1)
        usernames = list(set(re.findall(r'@[a-zA-Z0-9_]+', uname_text)))
    all_at = re.findall(r'@[a-zA-Z0-9_]{4,}', combined_raw)
    for u in all_at:
        if u not in usernames and u.lower() not in ('@' + FUNSTATE_BOT_USERNAME.lower(), '@' + BOT_USERNAME.lower()):
            usernames.append(u)
    usernames = list(set(usernames))
    if usernames:
        result['usernames'] = usernames

    # ---- Name history ----
    name_history = []
    for line in combined_raw.split('\n'):
        if '├' in line and '➜' in line:
            parts = line.split('➜', 1)
            if len(parts) == 2:
                date_part = parts[0].replace('├', '').strip()
                date_part = re.sub(r'\s+', ' ', date_part).strip()
                name_part = parts[1].strip()
                if date_part and name_part:
                    name_history.append({"date": date_part, "name": name_part})
    if name_history:
        result['name_history'] = name_history

    # ---- Stats (uses normalized text for reliable regex matching) ----
    stats = {}
    norm = combined_norm

    div_match = re.search(r'divers\w*\s+([\d.]+%)', norm, re.IGNORECASE)
    if div_match:
        stats['message_diversity'] = div_match.group(1)

    from_match = re.search(r'from\s+([\d/]+)\s+to\s+([\d/]+)', norm, re.IGNORECASE)
    if from_match:
        stats['from_date'] = from_match.group(1)
        stats['to_date'] = from_match.group(2)

    msg_match = re.search(r'(\d+)\s+mess\w*\s+in\s+(\d+)\s+gro\w+', norm, re.IGNORECASE)
    if msg_match:
        stats['total_messages'] = int(msg_match.group(1))
        stats['total_groups'] = int(msg_match.group(2))

    replies_match = re.search(r'([\d.]+%)\s+repli\w*\s+([\d.]+%)\s+med\w+', norm, re.IGNORECASE)
    if replies_match:
        stats['replies_percent'] = replies_match.group(1)
        stats['media_percent'] = replies_match.group(2)

    circles_match = re.search(r'circ\w*:\s*(\d+)[^\n]*?voice:\s*(\d+)', norm, re.IGNORECASE)
    if circles_match:
        stats['circles'] = int(circles_match.group(1))
        stats['voice'] = int(circles_match.group(2))

    fav_match = re.search(r'favor\w*\s+gro\w+:\s*(.+?)(?:\n|$)', norm, re.IGNORECASE)
    if fav_match:
        stats['favorite_group'] = fav_match.group(1).strip()

    looking_match = re.search(r'were?\s+look\w*\s+for:\s*(\d+)', norm, re.IGNORECASE)
    if looking_match:
        stats['were_looking_for'] = int(looking_match.group(1))

    admin_match = re.search(r'admin\s+in\s+gro\w+:\s*(\d+)', norm, re.IGNORECASE)
    if admin_match:
        stats['admin_in_groups'] = int(admin_match.group(1))

    sticker_count_match = re.search(r'stickersets?:\s*(\d+)', norm, re.IGNORECASE)
    if sticker_count_match:
        stats['stickersets_count'] = int(sticker_count_match.group(1))

    if stats:
        result['stats'] = stats

    # ---- Channel display name from text ----
    for line in combined_raw.split('\n'):
        line_norm = normalize_text(line).lower()
        if 'channel' in line_norm:
            colon_idx = line.find(':')
            if colon_idx != -1:
                channel_name_raw = line[colon_idx + 1:].strip()
                if channel_name_raw and 'SET UP' not in channel_name_raw.upper():
                    result['channel'] = channel_name_raw
                    break

    if bio_link:
        result['bio_link'] = bio_link

    # NOTE: 'raw', 'all_links', and 'sticker_pack_links' intentionally omitted
    return result

# ================= QUERY FUNCTIONS =================
async def query_funstate_bot_async(client, value):
    """
    Send plain value to Funstate bot, collect ALL reply messages and parse them.
    """
    try:
        funstate_entity = await client.get_entity(FUNSTATE_BOT_USERNAME)
        funstate_bot_id = funstate_entity.id
    except Exception as e:
        logger.error(f"Could not resolve Funstate bot entity: {e}")
        return {"error": f"Could not resolve Funstate bot: {e}"}

    try:
        sent = await client.send_message(funstate_entity, value)
    except Exception as e:
        logger.error(f"Send to Funstate error: {e}")
        return {"error": str(e)}

    sent_id = sent.id
    logger.info(f"📤 Sent '{value}' to Funstate (msg_id={sent_id})")

    # Initial delay — bot sometimes takes 3-5s to process IDs
    await asyncio.sleep(4)

    all_messages = []
    seen_ids = set()
    for attempt in range(15):          # 15 attempts × 3s = 45s max
        async for msg in client.iter_messages(funstate_entity, min_id=sent_id, limit=30):
            if msg.sender_id == funstate_bot_id and msg.id not in seen_ids:
                all_messages.append(msg)
                seen_ids.add(msg.id)
        if all_messages:
            logger.info(f"📩 Got {len(all_messages)} Funstate reply(s) on attempt {attempt+1}")
            # Wait 3 more seconds to catch any additional messages bot might send
            await asyncio.sleep(3)
            async for msg in client.iter_messages(funstate_entity, min_id=sent_id, limit=30):
                if msg.sender_id == funstate_bot_id and msg.id not in seen_ids:
                    all_messages.append(msg)
                    seen_ids.add(msg.id)
            break
        logger.info(f"⏳ Waiting for Funstate reply... attempt {attempt+1}/15")
        await asyncio.sleep(3)

    if not all_messages:
        return {"error": "Funstate bot did not respond"}

    all_messages.sort(key=lambda m: m.date)
    parsed = parse_funstate_response(all_messages)
    return parsed

async def query_main_bot_async(client, command_text, group):
    try:
        sent = await client.send_message(group.id, command_text)
    except Exception as e:
        logger.error(f"Send error: {e}")
        return {"error": str(e)}
    msg_id = sent.id
    logger.info(f"📤 Sent {command_text} (msg_id: {msg_id}) to group {group.title}")

    bot_replies = []
    for attempt in range(20):
        await asyncio.sleep(1.5)
        async for msg in client.iter_messages(group.id, limit=200):
            if msg.sender_id == BOT_ID and msg.reply_to_msg_id == msg_id:
                bot_replies.append(msg)
                logger.info(f"📩 Found reply (attempt {attempt+1})")
            elif msg.sender_id == BOT_ID and command_text.split()[1] in msg.raw_text:
                bot_replies.append(msg)
                logger.info(f"📩 Found fallback reply (attempt {attempt+1})")
        if bot_replies:
            break

    if not bot_replies:
        await client.delete_messages(group.id, [msg_id])
        return {"error": "Bot did not respond"}

    seen = set()
    unique_replies = []
    for msg in bot_replies:
        if msg.id not in seen:
            seen.add(msg.id)
            unique_replies.append(msg)
    unique_replies.sort(key=lambda m: m.date)

    combined = "".join([m.raw_text for m in unique_replies])
    objects = extract_json_objects(combined)
    if not objects:
        start = combined.find('{')
        end = combined.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                single = json.loads(combined[start:end+1])
                objects = [single]
            except:
                pass
    if not objects:
        await client.delete_messages(group.id, [msg_id] + [m.id for m in unique_replies])
        return {"error": "No valid JSON found"}

    to_delete = [msg_id] + [m.id for m in unique_replies]
    await client.delete_messages(group.id, to_delete)
    logger.info(f"🗑️ Deleted {len(to_delete)} messages")
    if len(objects) == 1:
        return finalize_response(objects[0])
    else:
        return finalize_response(objects)

# ================= MAIN QUERY FUNCTION =================
def query_bot_sync(command_text, group_type, bot_type="main"):
    """
    Query with round-robin across ALL active accounts.
    If one account fails (InvalidPeer, timeout, etc.), automatically tries the next.
    """
    if bot_type == "funstate":
        if get_global_setting('funstate_enabled') != '1':
            return {"error": "Funstate commands are disabled by admin"}
    else:
        if group_type == "main" and get_global_setting('group_main_enabled') != '1':
            return {"error": "Users X Info group (main) is disabled by admin"}

    active_clients = get_all_active_clients()
    if not active_clients:
        return {"error": "No active Telegram accounts"}

    group = None
    if bot_type != "funstate":
        group = GROUP_MAIN if group_type == "main" else GROUP_OTHER
        if group is None:
            return {"error": f"Group '{group_type}' not found / not joined yet"}

    last_error = "All accounts failed"

    for acc, client, loop in active_clients:
        acc_id = acc['id']
        logger.info(f"🔄 Trying account '{acc['name']}' (ID: {acc_id}) for '{command_text[:40]}'")

        if bot_type == "funstate":
            async def do_funstate(c=client):
                return await query_funstate_bot_async(c, command_text)
            coro = do_funstate()
        else:
            async def do_main(c=client, g=group):
                return await query_main_bot_async(c, command_text, g)
            coro = do_main()

        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            result = future.result(timeout=65)
        except asyncio.TimeoutError:
            last_error = "Request timed out"
            logger.warning(f"⏱️ Account '{acc['name']}' timed out — trying next")
            continue
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ Account '{acc['name']}' exception: {e} — trying next")
            continue

        if isinstance(result, dict) and 'error' in result:
            err_msg = result['error']
            if any(kw in err_msg for kw in ['Peer', 'peer', 'invalid', 'Invalid', 'flood', 'Flood', 'banned', 'Banned']):
                last_error = err_msg
                logger.warning(f"⚠️ Account '{acc['name']}' peer/flood error: {err_msg} — trying next")
                continue

        global account_index
        try:
            account_index = accounts.index(acc)
        except ValueError:
            pass

        parts = command_text.split()
        cmd_name = parts[0] if parts else 'unknown'
        val_name = parts[1] if len(parts) > 1 else ''
        success = not (isinstance(result, dict) and 'error' in result)
        add_stats(cmd_name, val_name, success)
        update_account_last_used(acc_id)
        return result

    add_stats('all_failed', '', False)
    return {"error": last_error, "developer": DEVELOPER_TAG}

# ================= AUTH =================
def admin_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if not api_key:
            return jsonify({"error": "API key required"}), 401
        if api_key == PERMANENT_KEY:
            request.api_key = PERMANENT_KEY
            request.is_permanent = True
            return f(*args, **kwargs)
        key_info = get_api_key_info(api_key)
        if not key_info:
            return jsonify({"error": "Invalid/expired/inactive key"}), 401
        if "error" in key_info:
            return jsonify({"error": key_info["error"]}), 403
        increment_daily_usage(api_key)
        request.api_key = api_key
        request.is_permanent = False
        return f(*args, **kwargs)
    return decorated

# ================= API ENDPOINTS =================
ALL_COMMANDS = ["num", "veh", "vnum", "upiinfo", "fam", "insta", "ip", "email", "tg", "ifsc", "adhar", "imei", "pak", "family", "gst", "bomber", "pan", "leak", "funstate", "names"]

for cmd in ALL_COMMANDS:
    def make_endpoint(cmd):
        @require_api_key
        def endpoint(value):
            cached = get_cached(cmd, value)
            if cached is not None:
                cached = finalize_response(cached)
                log_usage(request.api_key, cmd, value, json.dumps(cached), True, None)
                add_stats(cmd, value, True)
                return jsonify(cached)

            if cmd in ("funstate", "names"):
                if get_global_setting('funstate_enabled') != '1':
                    return jsonify({"error": "Funstate commands are disabled by admin"})
                result = query_bot_sync(value, None, bot_type="funstate")
            else:
                if cmd in SPECIAL_COMMANDS and get_global_setting(f"cmd_{cmd}_enabled") == '0':
                    return jsonify({"error": f"Command /{cmd} is disabled by admin"})
                group_type = "main" if cmd in SPECIAL_COMMANDS else "other"
                result = query_bot_sync(f"/{cmd} {value}", group_type)

            if cmd in ("funstate", "names"):
                if isinstance(result, dict):
                    result['developer'] = DEVELOPER_TAG
                    result['tag'] = DEVELOPER_TAG
                if "error" not in result:
                    set_cache(cmd, value, result)
                log_usage(request.api_key, cmd, value, json.dumps(result), 'error' not in result, None)
                return jsonify(result)
            else:
                if isinstance(result, list):
                    finalized = [finalize_response(item) for item in result]
                else:
                    finalized = finalize_response(result)
                if "error" not in finalized:
                    set_cache(cmd, value, finalized)
                log_usage(request.api_key, cmd, value, json.dumps(finalized), 'error' not in finalized, None)
                return jsonify(finalized)
        return endpoint
    app.add_url_rule(f'/{cmd}/<value>', f'api_{cmd}', make_endpoint(cmd), methods=['GET'])

@app.route('/statu', methods=['GET'])
@require_api_key
def statu_endpoint():
    result = query_bot_sync("/statu", "other")
    result = finalize_response(result)
    log_usage(request.api_key, "statu", "", json.dumps(result), 'error' not in result, None)
    add_stats("statu", "", 'error' not in result)
    return jsonify(result)

# ================= ADMIN PANEL =================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Felix API - Admin Panel</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
        .sidebar { position: fixed; left: 0; top: 0; bottom: 0; width: 220px; background: #1e293b; padding: 20px 0; border-right: 1px solid #334155; z-index: 100; }
        .sidebar-logo { padding: 10px 20px 24px; font-size: 18px; font-weight: 700; color: #38bdf8; border-bottom: 1px solid #334155; margin-bottom: 10px; }
        .sidebar-logo span { color: #94a3b8; font-size: 12px; display: block; font-weight: 400; margin-top: 2px; }
        .nav-item { display: flex; align-items: center; gap: 10px; padding: 11px 20px; cursor: pointer; color: #94a3b8; font-size: 14px; transition: all .15s; border-left: 3px solid transparent; }
        .nav-item:hover { background: #334155; color: #e2e8f0; }
        .nav-item.active { background: #1e3a5f; color: #38bdf8; border-left-color: #38bdf8; }
        .nav-item svg { flex-shrink: 0; }
        .main { margin-left: 220px; padding: 30px; }
        .panel { display: none; }
        .panel.active { display: block; }
        h2 { font-size: 20px; font-weight: 600; color: #f1f5f9; margin-bottom: 20px; }
        h3 { font-size: 15px; font-weight: 600; color: #cbd5e1; margin-bottom: 14px; margin-top: 20px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .stat-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px 20px; }
        .stat-label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
        .stat-value { font-size: 26px; font-weight: 700; color: #f1f5f9; }
        .stat-sub { font-size: 12px; color: #64748b; margin-top: 4px; }
        input, textarea, select { width: 100%; padding: 9px 12px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; font-size: 14px; margin-bottom: 10px; outline: none; transition: border .15s; }
        input:focus, textarea:focus { border-color: #38bdf8; }
        textarea { min-height: 80px; resize: vertical; }
        .btn { display: inline-flex; align-items: center; gap: 6px; padding: 9px 18px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: opacity .15s; }
        .btn-primary { background: #0ea5e9; color: white; }
        .btn-success { background: #10b981; color: white; }
        .btn-danger { background: #ef4444; color: white; }
        .btn-warn { background: #f59e0b; color: white; }
        .btn:hover { opacity: .85; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { background: #0f172a; padding: 10px 12px; text-align: left; color: #64748b; font-weight: 500; border-bottom: 1px solid #334155; }
        td { padding: 10px 12px; border-bottom: 1px solid #1e293b; vertical-align: middle; }
        tr:hover td { background: #1e293b55; }
        code { background: #0f172a; padding: 2px 6px; border-radius: 4px; font-size: 12px; color: #38bdf8; border: 1px solid #334155; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 600; }
        .badge-green { background: #064e3b; color: #34d399; }
        .badge-red { background: #450a0a; color: #f87171; }
        .badge-blue { background: #0c2a4a; color: #38bdf8; }
        .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #334155; }
        .toggle-row:last-child { border-bottom: none; }
        .log-row { cursor: pointer; }
        .log-row:hover td { background: #1e3a5f55 !important; }
        .modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.7); z-index:999; align-items:center; justify-content:center; }
        .modal-overlay.open { display:flex; }
        .modal-box { background:#1e293b; border:1px solid #334155; border-radius:14px; padding:24px; width:90%; max-width:760px; max-height:85vh; display:flex; flex-direction:column; }
        .modal-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
        .modal-title { font-size:16px; font-weight:600; color:#f1f5f9; }
        .modal-close { background:none; border:none; color:#94a3b8; font-size:22px; cursor:pointer; line-height:1; }
        .modal-close:hover { color:#f1f5f9; }
        .modal-body { overflow-y:auto; flex:1; }
        .modal-meta { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; font-size:12px; color:#64748b; }
        .modal-meta span { background:#0f172a; padding:3px 8px; border-radius:6px; border:1px solid #334155; }
        pre.json-view { background:#0f172a; border:1px solid #334155; border-radius:8px; padding:14px; font-size:12px; color:#a5f3fc; white-space:pre-wrap; word-break:break-all; margin:0; }
        .toggle-label { font-size: 14px; color: #cbd5e1; }
        .toggle-desc { font-size: 12px; color: #64748b; margin-top: 2px; }
        .switch { position: relative; width: 44px; height: 24px; flex-shrink: 0; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; inset: 0; background: #334155; border-radius: 24px; transition: .25s; }
        .slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: .25s; }
        input:checked + .slider { background: #0ea5e9; }
        input:checked + .slider:before { transform: translateX(20px); }
        .section-divider { border: none; border-top: 1px solid #334155; margin: 24px 0; }
        .top-bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
        .top-bar-title { font-size: 22px; font-weight: 700; color: #f1f5f9; }
        .logout-btn { font-size: 13px; color: #ef4444; text-decoration: none; padding: 7px 14px; border: 1px solid #ef444433; border-radius: 8px; }
        .logout-btn:hover { background: #ef444415; }
        .alert { padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px; }
        .alert-info { background: #0c2a4a; color: #38bdf8; border: 1px solid #1e40af33; }
        a.action-link { color: #38bdf8; text-decoration: none; margin-right: 8px; font-size: 13px; }
        a.action-link:hover { text-decoration: underline; }
        a.action-danger { color: #ef4444; }
        .perm-key-box { display: flex; align-items: center; gap: 10px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px; font-size: 13px; }
        .perm-key-box code { background: none; border: none; padding: 0; font-size: 13px; }
        @media(max-width:768px){
            .sidebar{display:none;}
            .main{margin-left:0;padding:16px;}
            .grid-2{grid-template-columns:1fr;}
        }
    </style>
</head>
<body>
<div class="sidebar">
    <div class="sidebar-logo">Felix API <span>Admin Panel</span></div>
    <div class="nav-item active" onclick="showTab('dashboard')">
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        Dashboard
    </div>
    <div class="nav-item" onclick="showTab('keys')">
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
        API Keys
    </div>
    <div class="nav-item" onclick="showTab('accounts')">
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        Accounts
    </div>
    <div class="nav-item" onclick="showTab('settings')">
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
        Settings
    </div>
    <div class="nav-item" onclick="showTab('logs')">
        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        Logs
    </div>
</div>

<div class="main">
    <div class="top-bar">
        <div class="top-bar-title" id="page-title">Dashboard</div>
        <a href="/admin/logout" class="logout-btn">Logout</a>
    </div>

    <!-- DASHBOARD -->
    <div id="dashboard" class="panel active">
        <div class="grid-2" style="grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px;">
            <div class="stat-card">
                <div class="stat-label">Total Requests</div>
                <div class="stat-value">{{ stats.total }}</div>
                <div class="stat-sub">All time</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Success</div>
                <div class="stat-value" style="color:#34d399">{{ stats.success }}</div>
                <div class="stat-sub">{{ "%.1f"|format(stats.success / stats.total * 100 if stats.total else 0) }}% success rate</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Failed</div>
                <div class="stat-value" style="color:#f87171">{{ stats.fail }}</div>
                <div class="stat-sub">Errors &amp; timeouts</div>
            </div>
        </div>
        <div class="grid-2" style="margin-bottom:20px;">
            <div class="stat-card">
                <div class="stat-label">Active Accounts</div>
                <div class="stat-value">{{ accounts|length }}</div>
                <div class="stat-sub">Telegram sessions</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">API Keys</div>
                <div class="stat-value">{{ keys|length }}</div>
                <div class="stat-sub">{{ cache_size }} cache entries</div>
            </div>
        </div>
        <div class="card">
            <h3>System Info</h3>
            <table>
                <tr><td style="color:#64748b;width:160px;">Developer</td><td><code>{{ developer }}</code></td></tr>
                <tr><td style="color:#64748b;">Main Group</td><td>{{ group_main_name }} <span class="badge {% if group_main_enabled == '1' %}badge-green{% else %}badge-red{% endif %}">{% if group_main_enabled == '1' %}ON{% else %}OFF{% endif %}</span></td></tr>
                <tr><td style="color:#64748b;">Other Group</td><td>{{ group_other_name }}</td></tr>
                <tr><td style="color:#64748b;">Bot ID</td><td>{{ bot_id or 'Not connected' }}</td></tr>
                <tr><td style="color:#64748b;">Funstate Bot ID</td><td>{{ funstate_bot_id or 'Not connected' }} <span class="badge {% if funstate_enabled == '1' %}badge-green{% else %}badge-red{% endif %}">{% if funstate_enabled == '1' %}ON{% else %}OFF{% endif %}</span></td></tr>
                <tr><td style="color:#64748b;">Permanent Key</td><td><code>{{ permanent_key }}</code></td></tr>
            </table>
        </div>
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="margin:0;">Recent Logs</h3>
                <a href="#" onclick="showTab('logs')" class="action-link">View all →</a>
            </div>
            <table>
                <tr><th>Time</th><th>Command</th><th>Value</th><th>Status</th></tr>
                {% for log in logs[:10] %}
                <tr>
                    <td style="color:#64748b;font-size:12px;">{{ log.timestamp[11:19] }}</td>
                    <td><code>{{ log.command }}</code></td>
                    <td style="font-size:12px;">{{ log.value[:30] }}</td>
                    <td><span class="badge {% if log.success %}badge-green{% else %}badge-red{% endif %}">{% if log.success %}✓{% else %}✗{% endif %}</span></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>

    <!-- API KEYS -->
    <div id="keys" class="panel">
        <div class="card">
            <h2>Generate API Key</h2>
            <form method="POST" action="/admin/create_key" style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:10px;align-items:end;">
                <div>
                    <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">Key Name</label>
                    <input type="text" name="name" placeholder="e.g. User123" required style="margin:0">
                </div>
                <div>
                    <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">Expiry Days (0=forever)</label>
                    <input type="number" name="expiry_days" value="30" style="margin:0">
                </div>
                <div>
                    <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">Daily Limit (0=unlimited)</label>
                    <input type="number" name="daily_limit" value="100" style="margin:0">
                </div>
                <button type="submit" class="btn btn-success">Generate</button>
            </form>
        </div>
        <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
                <h2 style="margin:0;">Active API Keys</h2>
                <a href="/admin/clear_cache" class="btn btn-warn" style="font-size:12px;padding:6px 12px;">Clear Cache ({{ cache_size }})</a>
            </div>
            <table>
                <tr><th>Key</th><th>Name</th><th>Expiry</th><th>Daily Limit</th><th>Status</th><th>Actions</th></tr>
                {% for k in keys %}
                <tr>
                    <td><code>{{ k.key[:16] }}…</code></td>
                    <td>{{ k.name }}</td>
                    <td>{{ k.expiry_days ~ 'd' if k.expiry_days > 0 else '∞ Forever' }}</td>
                    <td>{{ k.daily_limit if k.daily_limit > 0 else '∞ Unlimited' }}</td>
                    <td><span class="badge {% if k.active %}badge-green{% else %}badge-red{% endif %}">{% if k.active %}Active{% else %}Revoked{% endif %}</span></td>
                    <td>
                        <a href="/admin/revoke/{{ k.key }}" class="action-link action-danger">Revoke</a>
                        <a href="/admin/delete/{{ k.key }}" class="action-link action-danger" onclick="return confirm('Delete this key?')">Delete</a>
                    </td>
                </tr>
                {% endfor %}
            </table>
            <div style="margin-top:16px;padding-top:16px;border-top:1px solid #334155;">
                <div class="alert alert-info">Permanent Key (never expires): <code>{{ permanent_key }}</code></div>
            </div>
        </div>
    </div>

    <!-- ACCOUNTS -->
    <div id="accounts" class="panel">
        <div class="card">
            <h2>Add Telegram Account</h2>
            <form method="POST" action="/admin/add_account">
                <div class="grid-2">
                    <div>
                        <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">Account Name</label>
                        <input type="text" name="name" placeholder="My Account" required>
                    </div>
                    <div>
                        <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">API ID</label>
                        <input type="number" name="api_id" placeholder="12345678" required>
                    </div>
                </div>
                <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">API Hash</label>
                <input type="text" name="api_hash" placeholder="abcdef1234..." required>
                <label style="font-size:12px;color:#64748b;display:block;margin-bottom:4px;">Session String</label>
                <textarea name="session_string" placeholder="Paste Telethon StringSession here..." required></textarea>
                <button type="submit" class="btn btn-success">Add Account</button>
            </form>
        </div>
        <div class="card">
            <h2>Connected Accounts</h2>
            <table>
                <tr><th>ID</th><th>Name</th><th>API ID</th><th>Status</th><th>Actions</th></tr>
                {% for acc in accounts %}
                <tr>
                    <td>{{ acc.id }}</td>
                    <td>{{ acc.name }}</td>
                    <td><code>{{ acc.api_id }}</code></td>
                    <td><span class="badge {% if acc.active %}badge-green{% else %}badge-red{% endif %}">{% if acc.active %}Active{% else %}Disabled{% endif %}</span></td>
                    <td>
                        <a href="/admin/toggle_account/{{ acc.id }}" class="action-link">{{ 'Disable' if acc.active else 'Enable' }}</a>
                        <a href="/admin/delete_account/{{ acc.id }}" class="action-link action-danger" onclick="return confirm('Delete account?')">Delete</a>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>

    <!-- SETTINGS -->
    <div id="settings" class="panel">
        <form method="POST" action="/admin/toggle_command">
            <div class="card">
                <h2>Groups &amp; Bots</h2>
                <div class="toggle-row">
                    <div>
                        <div class="toggle-label">Users X Info Group (Special Commands)</div>
                        <div class="toggle-desc">GROUP_MAIN — used for /upiinfo /fam /family /pan /tg /leak</div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" name="group_main" value="1" {% if group_main_enabled == '1' %}checked{% endif %}>
                        <span class="slider"></span>
                    </label>
                </div>
                <div class="toggle-row">
                    <div>
                        <div class="toggle-label">Funstate Bot (/funstate &amp; /names)</div>
                        <div class="toggle-desc">Enable Funstate user lookup endpoint</div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" name="funstate" value="1" {% if funstate_enabled == '1' %}checked{% endif %}>
                        <span class="slider"></span>
                    </label>
                </div>
            </div>
            <div class="card">
                <h2>Special Commands</h2>
                <p style="font-size:13px;color:#64748b;margin-bottom:16px;">These run in the Users X Info group. Disabling a command returns an error to API callers.</p>
                {% for cmd in special_commands %}
                <div class="toggle-row">
                    <div>
                        <div class="toggle-label">/<b>{{ cmd }}</b></div>
                    </div>
                    <label class="switch">
                        <input type="checkbox" name="{{ cmd }}" value="1" {% if cmd_status[cmd] == '1' %}checked{% endif %}>
                        <span class="slider"></span>
                    </label>
                </div>
                {% endfor %}
            </div>
            <div class="card">
                <h2>Delete Delay</h2>
                <p style="font-size:13px;color:#64748b;margin-bottom:12px;">Seconds to wait before deleting bot messages from group.</p>
                <div style="display:flex;gap:10px;align-items:center;">
                    <input type="number" name="delete_delay" value="{{ delete_delay }}" min="5" max="120" style="max-width:120px;margin:0;">
                    <span style="color:#64748b;font-size:13px;">seconds</span>
                </div>
            </div>
            <div class="card">
                <h2>Public Search Panel</h2>
                <p style="font-size:13px;color:#64748b;margin-bottom:12px;">API key used by the public search page at <code>/search</code>. Leave blank to disable public search.</p>
                <div style="display:flex;gap:10px;align-items:center;">
                    <input type="text" name="public_key" value="{{ public_key }}" placeholder="Paste an API key or leave blank" style="margin:0;flex:1;">
                    <a href="/search" target="_blank" class="btn btn-primary" style="white-space:nowrap;">Open Search →</a>
                </div>
            </div>
            <button type="submit" class="btn btn-primary" style="margin-top:4px;">Save All Settings</button>
        </form>
    </div>

    <!-- LOGS -->
    <div id="logs" class="panel">
        <div class="card">
            <h2>Usage Logs (last 100)</h2>
            <table>
                <tr><th>Time</th><th>Key</th><th>Command</th><th>Value</th><th>Response</th><th>Status</th></tr>
                {% for log in logs %}
                <tr class="log-row" onclick="openLog(this)"
                    data-time="{{ log.timestamp[:19].replace('T',' ') }}"
                    data-key="{{ log.key }}"
                    data-cmd="{{ log.command }}"
                    data-val="{{ log.value }}"
                    data-resp="{{ log.response | replace('"', '&quot;') }}"
                    data-ok="{{ '1' if log.success else '0' }}">
                    <td style="color:#64748b;font-size:12px;">{{ log.timestamp[:19].replace('T',' ') }}</td>
                    <td><code>{{ log.key[:8] }}…</code></td>
                    <td><code>{{ log.command }}</code></td>
                    <td style="font-size:12px;">{{ log.value }}</td>
                    <td style="font-size:11px;color:#94a3b8;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ log.response[:60] }}{% if log.response|length > 60 %}…{% endif %}</td>
                    <td><span class="badge {% if log.success %}badge-green{% else %}badge-red{% endif %}">{% if log.success %}✓ OK{% else %}✗ Err{% endif %}</span></td>
                </tr>
                {% endfor %}
            </table>
            <p style="font-size:12px;color:#475569;margin-top:10px;">💡 Click any row to view full response</p>
        </div>
    </div>
</div>

<!-- Log Detail Modal -->
<div class="modal-overlay" id="logModal" onclick="if(event.target===this)closeLog()">
    <div class="modal-box">
        <div class="modal-header">
            <span class="modal-title">Log Detail</span>
            <button class="modal-close" onclick="closeLog()">×</button>
        </div>
        <div class="modal-body">
            <div class="modal-meta" id="modalMeta"></div>
            <pre class="json-view" id="modalResp"></pre>
        </div>
    </div>
</div>

<script>
const titles = {dashboard:'Dashboard',keys:'API Keys',accounts:'Accounts',settings:'Settings',logs:'Logs'};
function showTab(tab) {
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById(tab).classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => {
        if(n.getAttribute('onclick') && n.getAttribute('onclick').includes(tab)) n.classList.add('active');
    });
    document.getElementById('page-title').textContent = titles[tab] || tab;
}
function openLog(row) {
    const time = row.dataset.time;
    const key = row.dataset.key;
    const cmd = row.dataset.cmd;
    const val = row.dataset.val;
    const resp = row.dataset.resp;
    const ok = row.dataset.ok === '1';
    document.getElementById('modalMeta').innerHTML =
        `<span>🕐 ${time}</span><span>🔑 ${key.slice(0,12)}…</span><span>📌 /${cmd}</span><span>🔍 ${val || '—'}</span><span class="${ok?'':''}">` +
        (ok ? '✅ Success' : '❌ Failed') + '</span>';
    let pretty = resp;
    try { pretty = JSON.stringify(JSON.parse(resp), null, 2); } catch(e) {}
    document.getElementById('modalResp').textContent = pretty;
    document.getElementById('logModal').classList.add('open');
}
function closeLog() {
    document.getElementById('logModal').classList.remove('open');
}
document.addEventListener('keydown', e => { if(e.key==='Escape') closeLog(); });
</script>
</body>
</html>
"""

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = ''
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
        error = 'Wrong password!'
    return f'''
<!DOCTYPE html>
<html>
<head>
<title>Felix Admin Login</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#0f172a;display:flex;align-items:center;justify-content:center;min-height:100vh;}}
.box{{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:36px 40px;width:340px;}}
h2{{color:#f1f5f9;font-size:22px;font-weight:700;margin-bottom:6px;}}
p{{color:#64748b;font-size:13px;margin-bottom:24px;}}
label{{display:block;font-size:12px;color:#94a3b8;margin-bottom:6px;}}
input{{width:100%;padding:10px 14px;background:#0f172a;border:1px solid #334155;border-radius:8px;color:#e2e8f0;font-size:14px;outline:none;margin-bottom:16px;}}
input:focus{{border-color:#38bdf8;}}
button{{width:100%;padding:11px;background:#0ea5e9;color:white;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;}}
button:hover{{background:#0284c7;}}
.err{{color:#ef4444;font-size:13px;margin-bottom:12px;background:#450a0a;padding:8px 12px;border-radius:6px;}}
</style>
</head>
<body>
<div class="box">
<h2>Felix API</h2>
<p>Admin Panel Login</p>
{"<div class='err'>" + error + "</div>" if error else ""}
<form method="post">
<label>Password</label>
<input type="password" name="password" placeholder="Enter admin password" required autofocus>
<button type="submit">Login →</button>
</form>
</div>
</body>
</html>
'''

@app.route('/admin/dashboard')
@admin_login_required
def admin_dashboard():
    keys = get_all_keys()
    accs = get_all_accounts()
    logs = get_usage_logs(100)
    total, success, fail = get_stats()
    stats = {"total": total, "success": success, "fail": fail}
    cmd_status = {}
    for cmd in SPECIAL_COMMANDS:
        cmd_status[cmd] = get_global_setting(f"cmd_{cmd}_enabled") or '1'
    funstate_enabled = get_global_setting('funstate_enabled') or '1'
    group_main_enabled = get_global_setting('group_main_enabled') or '1'
    delete_delay = get_global_setting('delete_delay') or '10'
    public_key = get_global_setting('public_key') or ''
    return render_template_string(ADMIN_HTML,
                                 keys=keys,
                                 accounts=accs,
                                 logs=logs,
                                 permanent_key=PERMANENT_KEY,
                                 developer=DEVELOPER_TAG,
                                 cache_size=len(response_cache),
                                 group_main_name=GROUP_MAIN_NAME,
                                 group_other_name=GROUP_OTHER_NAME,
                                 bot_id=BOT_ID,
                                 funstate_bot_id=FUNSTATE_BOT_ID,
                                 stats=stats,
                                 special_commands=SPECIAL_COMMANDS,
                                 cmd_status=cmd_status,
                                 funstate_enabled=funstate_enabled,
                                 group_main_enabled=group_main_enabled,
                                 delete_delay=delete_delay,
                                 public_key=public_key)

@app.route('/admin/toggle_command', methods=['POST'])
@admin_login_required
def admin_toggle_command():
    for cmd in SPECIAL_COMMANDS:
        val = '1' if request.form.get(cmd) == '1' else '0'
        set_global_setting(f"cmd_{cmd}_enabled", val)
    funstate_val = '1' if request.form.get('funstate') == '1' else '0'
    set_global_setting('funstate_enabled', funstate_val)
    group_main_val = '1' if request.form.get('group_main') == '1' else '0'
    set_global_setting('group_main_enabled', group_main_val)
    delete_delay = request.form.get('delete_delay', '10')
    set_global_setting('delete_delay', delete_delay)
    public_key_val = request.form.get('public_key', '').strip()
    set_global_setting('public_key', public_key_val)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/clear_cache')
@admin_login_required
def admin_clear_cache():
    clear_cache()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/create_key', methods=['POST'])
@admin_login_required
def admin_create_key():
    name = request.form.get('name')
    expiry_days = int(request.form.get('expiry_days', 30))
    daily_limit = int(request.form.get('daily_limit', 100))
    if not name:
        return "Name required", 400
    new_key = secrets.token_hex(16)
    add_api_key(new_key, name, name, expiry_days, daily_limit)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/revoke/<key>')
@admin_login_required
def admin_revoke_key(key):
    revoke_key(key)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete/<key>')
@admin_login_required
def admin_delete_key(key):
    delete_key(key)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/add_account', methods=['POST'])
@admin_login_required
def admin_add_account():
    name = request.form.get('name')
    api_id = int(request.form.get('api_id'))
    api_hash = request.form.get('api_hash')
    session_string = request.form.get('session_string')
    if not all([name, api_id, api_hash, session_string]):
        return "All fields required", 400
    add_account(name, api_id, api_hash, session_string)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM accounts WHERE name=? AND api_id=? AND active=1 ORDER BY id DESC LIMIT 1", (name, api_id))
    row = c.fetchone()
    conn.close()
    if row:
        new_acc = {"id": row[0], "name": name, "api_id": api_id, "api_hash": api_hash, "session_string": session_string}
        accounts.append(new_acc)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        telegram_loops[new_acc["id"]] = loop
        thread = threading.Thread(target=lambda: loop.run_until_complete(start_account(new_acc)), daemon=True)
        thread.start()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_account/<int:acc_id>')
@admin_login_required
def admin_toggle_account(acc_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT active FROM accounts WHERE id=?", (acc_id,))
    row = c.fetchone()
    if row:
        toggle_account(acc_id, 0 if row[0] else 1)
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_account/<int:acc_id>')
@admin_login_required
def admin_delete_account(acc_id):
    delete_account(acc_id)
    global accounts
    accounts = [a for a in accounts if a['id'] != acc_id]
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/search')
def public_search():
    pub_key = get_global_setting('public_key') or ''
    if not pub_key:
        return '<h2 style="font-family:sans-serif;text-align:center;margin-top:80px;color:#ef4444;">Public search is disabled. Admin needs to configure a public key first.</h2>', 403
    return render_template_string(PUBLIC_SEARCH_HTML, pub_key=pub_key)

PUBLIC_SEARCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Felix API — Search</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',Arial,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:40px 16px;}
h1{color:#38bdf8;font-size:28px;font-weight:700;margin-bottom:6px;}
.sub{color:#64748b;font-size:13px;margin-bottom:32px;}
.search-box{display:flex;gap:10px;width:100%;max-width:540px;margin-bottom:30px;}
input[type=text]{flex:1;padding:12px 16px;background:#1e293b;border:1px solid #334155;border-radius:10px;color:#e2e8f0;font-size:15px;outline:none;transition:border .15s;}
input[type=text]:focus{border-color:#38bdf8;}
button{padding:12px 22px;background:#0ea5e9;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;transition:opacity .15s;}
button:hover{opacity:.85;}
#result{width:100%;max-width:540px;}
.card{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:22px;margin-bottom:16px;}
.label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px;}
.value{font-size:15px;color:#f1f5f9;font-weight:500;}
.value a{color:#38bdf8;text-decoration:none;}
.value a:hover{text-decoration:underline;}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600;background:#0c2a4a;color:#38bdf8;margin:2px 2px 0 0;}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px;}
.stat-item{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:10px 12px;}
.stat-num{font-size:20px;font-weight:700;color:#f1f5f9;}
.stat-lbl{font-size:11px;color:#64748b;margin-top:2px;}
.history-item{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid #1e293b;align-items:flex-start;}
.history-item:last-child{border-bottom:none;}
.hist-date{font-size:11px;color:#64748b;white-space:nowrap;margin-top:2px;min-width:78px;}
.hist-name{font-size:14px;color:#e2e8f0;}
.spinner{text-align:center;padding:30px;color:#64748b;}
.error-box{background:#450a0a;border:1px solid #7f1d1d;border-radius:10px;padding:16px;color:#fca5a5;text-align:center;}
.section-title{font-size:13px;font-weight:600;color:#94a3b8;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #334155;}
</style>
</head>
<body>
<h1>🔍 Felix Search</h1>
<p class="sub">Search Telegram user info — powered by @rajfflive</p>
<div class="search-box">
  <input type="text" id="q" placeholder="Enter username or user ID…" onkeydown="if(event.key==='Enter')search()">
  <button onclick="search()">Search</button>
</div>
<div id="result"></div>
<script>
const PUB_KEY = "{{ pub_key }}";
async function search() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const out = document.getElementById('result');
  out.innerHTML = '<div class="spinner">⏳ Searching… this may take up to 30 seconds</div>';
  try {
    const res = await fetch(`/funstate/${encodeURIComponent(q)}?api_key=${PUB_KEY}`);
    const data = await res.json();
    if (data.error) { out.innerHTML = `<div class="error-box">❌ ${data.error}</div>`; return; }
    out.innerHTML = renderResult(data);
  } catch(e) {
    out.innerHTML = `<div class="error-box">❌ Network error: ${e.message}</div>`;
  }
}
function renderResult(d) {
  let html = '';

  // Identity card
  html += '<div class="card">';
  html += '<div class="section-title">👤 Identity</div>';
  if (d.name) html += row('Display Name', escHtml(d.name));
  if (d.id) html += row('User ID', `<code style="color:#38bdf8">${escHtml(d.id)}</code>`);
  if (d.usernames && d.usernames.length) {
    const badges = d.usernames.map(u => `<span class="badge">${escHtml(u)}</span>`).join('');
    html += row('Usernames', badges);
  }
  if (d.channel) html += row('Channel', escHtml(d.channel));
  if (d.bio_link) html += row('Bio Link', `<a href="${escHtml(d.bio_link)}" target="_blank">${escHtml(d.bio_link)}</a>`);
  html += '</div>';

  // Stats card
  if (d.stats && Object.keys(d.stats).length) {
    const s = d.stats;
    html += '<div class="card">';
    html += '<div class="section-title">📊 Activity Stats</div>';
    html += '<div class="stat-grid">';
    if (s.total_messages !== undefined) html += statBox(s.total_messages, 'Messages');
    if (s.total_groups !== undefined) html += statBox(s.total_groups, 'Groups');
    if (s.replies_percent) html += statBox(s.replies_percent, 'Replies');
    if (s.media_percent) html += statBox(s.media_percent, 'Media');
    if (s.circles !== undefined) html += statBox(s.circles, 'Circles');
    if (s.voice !== undefined) html += statBox(s.voice, 'Voice Msgs');
    if (s.admin_in_groups !== undefined) html += statBox(s.admin_in_groups, 'Admin In');
    if (s.were_looking_for !== undefined) html += statBox(s.were_looking_for, 'Looking For');
    if (s.stickersets_count !== undefined) html += statBox(s.stickersets_count, 'Sticker Sets');
    html += '</div>';
    if (s.message_diversity) html += `<div style="margin-top:12px;">${row('Diversity', s.message_diversity)}</div>`;
    if (s.from_date && s.to_date) html += row('Active Period', `${s.from_date} → ${s.to_date}`);
    if (s.favorite_group) html += row('Favorite Group', escHtml(s.favorite_group));
    html += '</div>';
  }

  // Name history
  if (d.name_history && d.name_history.length) {
    html += '<div class="card">';
    html += '<div class="section-title">📝 Name History</div>';
    d.name_history.forEach(h => {
      html += `<div class="history-item"><span class="hist-date">${escHtml(h.date)}</span><span class="hist-name">→ ${escHtml(h.name)}</span></div>`;
    });
    html += '</div>';
  }

  return html;
}
function row(label, val) {
  return `<div style="margin-bottom:10px;"><div class="label">${label}</div><div class="value">${val}</div></div>`;
}
function statBox(val, lbl) {
  return `<div class="stat-item"><div class="stat-num">${val}</div><div class="stat-lbl">${lbl}</div></div>`;
}
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>"""

@app.route('/')
def home():
    return '''<!DOCTYPE html>
<html>
<head><title>Felix API</title>
<style>body{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;}
.box{text-align:center;} h1{color:#38bdf8;font-size:32px;} p{color:#64748b;margin:8px 0;}
a{color:#0ea5e9;text-decoration:none;padding:10px 24px;background:#1e293b;border:1px solid #334155;border-radius:8px;display:inline-block;margin-top:16px;}
</style></head>
<body><div class="box">
<h1>Felix API</h1>
<p>Telegram OSINT API powered by @rajfflive</p>
<p>Usage: <code>/command/value?api_key=YOUR_KEY</code></p>
<a href="/admin/login">Admin Panel →</a>
</div></body></html>'''

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "accounts": len(accounts),
        "group_main_connected": GROUP_MAIN is not None,
        "group_other_connected": GROUP_OTHER is not None,
        "funstate_bot_connected": FUNSTATE_BOT_ENTITY is not None
    })

if __name__ == "__main__":
    init_accounts()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
