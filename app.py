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
        thread = threading.Thread(target=lambda l=loop, a=accounts[-1]: l.run_until_complete(start_account(a)), daemon=True)
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

# ================= FUNSTATE RESPONSE PARSER (FULLY FIXED) =================
def normalize_text(text):
    """Normalize unicode lookalike / decorative chars to plain ASCII."""
    replacements = {
        # Cyrillic lookalikes
        'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x',
        'А': 'A', 'Е': 'E', 'О': 'O', 'Р': 'P', 'С': 'C', 'Х': 'X',
        'М': 'M', 'В': 'B', 'К': 'K', 'Т': 'T',
        'ѕ': 's',
        # Greek lookalikes
        'α': 'a', 'β': 'b', 'γ': 'y', 'δ': 'd', 'ε': 'e', 'ζ': 'z',
        'η': 'n', 'θ': 'th', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'm',
        'ν': 'n', 'ξ': 'x', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
        'τ': 't', 'υ': 'u', 'φ': 'f', 'χ': 'x', 'ψ': 'ps', 'ω': 'o',
        'Α': 'A', 'Β': 'B', 'Γ': 'G', 'Δ': 'D', 'Ε': 'E', 'Ζ': 'Z',
        'Η': 'H', 'Θ': 'Th', 'Ι': 'I', 'Κ': 'K', 'Λ': 'L', 'Μ': 'M',
        'Ν': 'N', 'Ξ': 'X', 'Ο': 'O', 'Π': 'P', 'Ρ': 'R', 'Σ': 'S',
        'Τ': 'T', 'Υ': 'U', 'Φ': 'F', 'Χ': 'X', 'Ψ': 'Ps', 'Ω': 'O',
        # Latin extended / IPA
        'ɑ': 'a', 'ɐ': 'a', 'ɒ': 'a',
        'ƅ': 'b', 'ƃ': 'b',
        'ç': 'c', 'ć': 'c', 'č': 'c',
        'ď': 'd', 'đ': 'd',
        'è': 'e', 'é': 'e', 'ê': 'e', 'ë': 'e', 'ě': 'e',
        '℮': 'e',
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
        # Small caps
        'ᴀ': 'a', 'ʙ': 'b', 'ᴄ': 'c', 'ᴅ': 'd', 'ᴇ': 'e', 'ꜰ': 'f',
        'ɢ': 'g', 'ʜ': 'h', 'ɪ': 'i', 'ᴊ': 'j', 'ᴋ': 'k', 'ʟ': 'l',
        'ᴍ': 'm', 'ɴ': 'n', 'ᴏ': 'o', 'ᴘ': 'p', 'ǫ': 'q', 'ʀ': 'r',
        'ꜱ': 's', 'ᴛ': 't', 'ᴜ': 'u', 'ᴠ': 'v', 'ᴡ': 'w', 'x': 'x',
        'ʏ': 'y', 'ᴢ': 'z',
        # Roman numeral lookalikes used in bot text
        'ⅼ': 'l', 'ⅽ': 'c', 'ⅾ': 'd', 'ⅿ': 'm',
        'Ⅼ': 'L', 'Ⅽ': 'C', 'Ⅾ': 'D', 'Ⅿ': 'M',
        # Greek digamma (used as F-lookalike)
        'Ϝ': 'F', 'ϝ': 'f',
        # Superscript/subscript digits
        '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
        '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
        '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
        '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
        # Fullwidth ASCII
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
        # Misc symbols
        'ᖴ': 'F', 'ℕ': 'N', 'ℤ': 'Z', 'ℂ': 'C', 'ℝ': 'R',
        '℃': 'C', 'ℓ': 'l', 'ℱ': 'F', 'ℋ': 'H', 'ℐ': 'I',
        'ℒ': 'L', 'ℳ': 'M', 'ℛ': 'R', 'ᴵ': 'I', 'ᴰ': 'D',
        'ＩＤ': 'ID',
    }
    result = text
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

# ===== FIX: parse_funstate_response — full rewrite of extraction logic =====
def parse_funstate_response(messages):
    """
    Parse a list of Telethon Message objects from Funstate bot.
    FIX: Captures all fields including usernames_total, name_history_total,
         and correctly handles the (N of M) count lines.
    """
    result = {}
    raw_text_parts = []

    for msg in messages:
        raw_text_parts.append(msg.raw_text or "")

    combined_raw = "\n".join(raw_text_parts)
    combined_norm = normalize_text(combined_raw)

    # ---- Display name ("This is NAME") ----
    name_match = re.search(r'this\s+is\s+(.+?)(?:\n|$)', combined_norm, re.IGNORECASE)
    if name_match:
        # Use raw text for name to preserve original styling
        name_match_raw = re.search(r'(?:this\s+is|This\s+is)\s+(.+?)(?:\n|$)', combined_raw, re.IGNORECASE)
        result['name'] = (name_match_raw.group(1) if name_match_raw else name_match.group(1)).strip()

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

    # ---- Bio link detection ----
    bio_link = None
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

    # ---- FIX: Username extraction — handle (N of M) header line + | @user... line ----
    usernames = []
    usernames_shown = 0
    usernames_total = 0

    # Try to extract "usernames: (3 of 4)" count
    uname_count_match = re.search(
        r'usernames?\s*[:\(]?\s*\(?\s*(\d+)\s+of\s+(\d+)',
        combined_norm, re.IGNORECASE
    )
    if uname_count_match:
        usernames_shown = int(uname_count_match.group(1))
        usernames_total = int(uname_count_match.group(2))

    # Find the line(s) with pipe-separated @usernames (| @Felix_Bhai | @Mikey_bhai1 ...)
    # Search in raw text to preserve @ signs
    pipe_lines = re.findall(r'\|(?:\s*@[a-zA-Z0-9_]+)+[^\n]*', combined_raw)
    for pl in pipe_lines:
        found = re.findall(r'@[a-zA-Z0-9_]{3,}', pl)
        for u in found:
            if u not in usernames:
                usernames.append(u)

    # Fallback: extract all @usernames from raw text
    all_at = re.findall(r'@[a-zA-Z0-9_]{3,}', combined_raw)
    bot_filters = {
        '@' + FUNSTATE_BOT_USERNAME.lower(),
        '@' + BOT_USERNAME.lower(),
        '@' + FUNSTATE_BOT_USERNAME,
        '@' + BOT_USERNAME
    }
    for u in all_at:
        if u not in usernames and u.lower() not in {f.lower() for f in bot_filters}:
            usernames.append(u)

    if usernames:
        result['usernames'] = usernames
    if usernames_total > 0:
        result['usernames_shown'] = usernames_shown
        result['usernames_total'] = usernames_total

    # ---- FIX: Name history — extract (N of M) count + all ├ ➜ lines ----
    name_history = []
    name_history_shown = 0
    name_history_total = 0

    # Extract "first name / last name: (3 of 15)" count
    hist_count_match = re.search(
        r'(?:first\s*nam|name\s*hist|last\s*nam)[^\n(]*\(\s*(\d+)\s+of\s+(\d+)',
        combined_norm, re.IGNORECASE
    )
    if hist_count_match:
        name_history_shown = int(hist_count_match.group(1))
        name_history_total = int(hist_count_match.group(2))

    # Parse ├ date ➜ name lines from raw text (preserves emoji / unicode names)
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
    if name_history_total > 0:
        result['name_history_shown'] = name_history_shown
        result['name_history_total'] = name_history_total

    # ---- Stats — line-by-line for maximum robustness ----
    stats = {}
    norm_lines = combined_norm.split('\n')

    for line in norm_lines:
        line = line.strip()
        if not line:
            continue
        ll = line.lower()

        # "Message diversity 52.10%"
        if not stats.get('message_diversity') and 'divers' in ll:
            m = re.search(r'([\d.]+%)', line)
            if m:
                stats['message_diversity'] = m.group(1)

        # "From 10/19/2025 to 6/23/2026"
        if not stats.get('from_date') and 'from' in ll and 'to' in ll:
            m = re.search(r'from\s+([\d/]+)\s+to\s+([\d/]+)', line, re.IGNORECASE)
            if m:
                stats['from_date'] = m.group(1)
                stats['to_date'] = m.group(2)

        # "4482 messages in 8 groups"
        if not stats.get('total_messages') and 'mess' in ll and 'in' in ll and 'gro' in ll:
            m = re.search(r'(\d+)\s+\w+\s+in\s+(\d+)', line, re.IGNORECASE)
            if m:
                stats['total_messages'] = int(m.group(1))
                stats['total_groups'] = int(m.group(2))

        # "39.67% replies 13.03% media"
        if not stats.get('replies_percent') and ('rep' in ll or 'med' in ll):
            pcts = re.findall(r'[\d.]+%', line)
            if len(pcts) >= 2:
                stats['replies_percent'] = pcts[0]
                stats['media_percent'] = pcts[1]

        # "Circles: 0, voice: 0"
        if not stats.get('circles') and 'circ' in ll:
            mc = re.search(r'circ\w*:\s*(\d+)', line, re.IGNORECASE)
            if mc:
                stats['circles'] = int(mc.group(1))
        if stats.get('circles') is None and 'voice' in ll:
            mv = re.search(r'voice:\s*(\d+)', line, re.IGNORECASE)
            if mv:
                stats['voice'] = int(mv.group(1))
        if 'circ' in ll and 'voice' in ll:
            mc = re.search(r'circ\w*:\s*(\d+)', line, re.IGNORECASE)
            mv = re.search(r'voice:\s*(\d+)', line, re.IGNORECASE)
            if mc:
                stats['circles'] = int(mc.group(1))
            if mv:
                stats['voice'] = int(mv.group(1))

        # "Favorite group: Abdul Dev Official Community"
        if not stats.get('favorite_group') and ('favor' in ll or 'fav' in ll) and 'gro' in ll:
            ci = line.find(':')
            if ci != -1:
                val = line[ci+1:].strip()
                if val:
                    stats['favorite_group'] = val

        # "Were looking for: 1"
        if not stats.get('were_looking_for') and 'look' in ll and 'for' in ll:
            m = re.search(r':\s*(\d+)', line)
            if m:
                stats['were_looking_for'] = int(m.group(1))

        # "Admin in groups: 2"
        if not stats.get('admin_in_groups') and 'admin' in ll and 'gro' in ll:
            m = re.search(r':\s*(\d+)', line)
            if m:
                stats['admin_in_groups'] = int(m.group(1))

        # "Stickersets: 11"
        if not stats.get('stickersets_count') and 'sticker' in ll:
            m = re.search(r'stickersets?\s*:\s*(\d+)', line, re.IGNORECASE)
            if m:
                stats['stickersets_count'] = int(m.group(1))

    if sticker_set:
        stats['sticker_links'] = list(sticker_set)

    if stats:
        result['stats'] = stats

    # ---- Channel display name from raw text ----
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

    return result

# ================= QUERY FUNCTIONS =================
async def query_funstate_bot_async(client, value):
    """
    FIX: Send value to Funstate bot and collect ALL reply messages with improved timing.
    Polls multiple times after first response to catch multi-message replies.
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

    # Initial delay — bot sometimes takes 3-5s
    await asyncio.sleep(4)

    all_messages = []
    seen_ids = set()

    # Poll until we get first response (max 15 attempts × 2s = 30s)
    for attempt in range(15):
        async for msg in client.iter_messages(funstate_entity, min_id=sent_id, limit=50):
            if msg.sender_id == funstate_bot_id and msg.id not in seen_ids:
                all_messages.append(msg)
                seen_ids.add(msg.id)
        if all_messages:
            logger.info(f"📩 Got {len(all_messages)} Funstate reply(s) on attempt {attempt+1}")
            break
        logger.info(f"⏳ Waiting for Funstate reply... attempt {attempt+1}/15")
        await asyncio.sleep(2)

    if not all_messages:
        return {"error": "Funstate bot did not respond"}

    # FIX: After first messages found, do 3 more rounds of polling to catch
    # any additional messages the bot may still be sending (multi-message responses)
    for extra_round in range(3):
        await asyncio.sleep(2)
        prev_count = len(all_messages)
        async for msg in client.iter_messages(funstate_entity, min_id=sent_id, limit=50):
            if msg.sender_id == funstate_bot_id and msg.id not in seen_ids:
                all_messages.append(msg)
                seen_ids.add(msg.id)
        new_count = len(all_messages)
        logger.info(f"🔄 Extra poll {extra_round+1}/3: {new_count - prev_count} new messages (total: {new_count})")
        # If no new messages in last 2 polls, stop early
        if new_count == prev_count and extra_round >= 1:
            break

    all_messages.sort(key=lambda m: m.date)
    logger.info(f"✅ Final message count from Funstate: {len(all_messages)}")
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
            result = future.result(timeout=70)
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
            t_start = time.time()
            cached = get_cached(cmd, value)
            if cached is not None:
                cached = finalize_response(cached)
                cached['time_taken'] = "0.00s (cached)"
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

            elapsed = f"{time.time() - t_start:.2f}s"

            if cmd in ("funstate", "names"):
                if isinstance(result, dict):
                    result['developer'] = DEVELOPER_TAG
                    result['tag'] = DEVELOPER_TAG
                    result['time_taken'] = elapsed
                if "error" not in result:
                    set_cache(cmd, value, result)
                log_usage(request.api_key, cmd, value, json.dumps(result), 'error' not in result, None)
                return jsonify(result)
            else:
                if isinstance(result, list):
                    finalized = [finalize_response(item) for item in result]
                    for item in finalized:
                        if isinstance(item, dict):
                            item['time_taken'] = elapsed
                else:
                    finalized = finalize_response(result)
                    if isinstance(finalized, dict):
                        finalized['time_taken'] = elapsed
                if isinstance(finalized, dict) and "error" not in finalized:
                    set_cache(cmd, value, finalized)
                elif isinstance(finalized, list):
                    set_cache(cmd, value, finalized)
                log_usage(request.api_key, cmd, value, json.dumps(finalized), 'error' not in str(finalized), None)
                return jsonify(finalized)
        return endpoint
    app.add_url_rule(f'/{cmd}/<value>', f'api_{cmd}', make_endpoint(cmd), methods=['GET'])

@app.route('/statu', methods=['GET'])
@require_api_key
def statu_endpoint():
    t_start = time.time()
    result = query_bot_sync("/statu", "other")
    result = finalize_response(result)
    if isinstance(result, dict):
        result['time_taken'] = f"{time.time() - t_start:.2f}s"
    log_usage(request.api_key, "statu", "", json.dumps(result), 'error' not in result, None)
    add_stats("statu", "", 'error' not in result)
    return jsonify(result)

# ================= PUBLIC SEARCH PANEL =================
PUBLIC_SEARCH_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>rajfflive — Search</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:#060d1a;--surface:#0d1b2e;--card:#0f2137;--border:#1a3050;
  --accent:#00c2ff;--accent2:#7c3aed;--text:#e8f4ff;--muted:#6b8aaa;
  --dim:#3a5570;--green:#00d97e;--red:#ff4d6d;--yellow:#ffb830;
  --glow:0 0 20px rgba(0,194,255,.15);
}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;background-image:radial-gradient(ellipse at 20% 0%,rgba(0,194,255,.06) 0%,transparent 60%),radial-gradient(ellipse at 80% 100%,rgba(124,58,237,.06) 0%,transparent 60%);}
/* HEADER */
.header{background:rgba(13,27,46,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;}
.logo{display:flex;align-items:center;gap:10px;}
.logo-icon{width:32px;height:32px;background:linear-gradient(135deg,var(--accent),var(--accent2));border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:900;color:#fff;letter-spacing:-1px;}
.logo-text{font-size:18px;font-weight:700;color:var(--text);}
.logo-sub{font-size:11px;color:var(--muted);margin-top:1px;}
.header-right{display:flex;align-items:center;gap:10px;}
.key-pill{display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--border);border-radius:99px;padding:6px 14px;cursor:pointer;transition:border-color .2s;}
.key-pill:hover{border-color:var(--accent);}
.key-pill.set{border-color:rgba(0,217,126,.4);background:rgba(0,217,126,.05);}
.key-pill-dot{width:7px;height:7px;border-radius:50%;background:var(--dim);flex-shrink:0;}
.key-pill.set .key-pill-dot{background:var(--green);}
.key-pill-text{font-size:12px;color:var(--muted);font-family:monospace;}
.key-pill.set .key-pill-text{color:var(--green);}
/* CONTAINER */
.container{max-width:960px;margin:0 auto;padding:28px 20px;}
/* SEARCH CARD */
.search-card{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:28px;margin-bottom:22px;box-shadow:var(--glow);}
.search-row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;}
.field{flex:1;min-width:130px;}
.field label{display:block;font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;font-weight:600;}
select,input[type=text]{width:100%;padding:11px 14px;background:var(--surface);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:14px;outline:none;transition:border .2s,box-shadow .2s;-webkit-appearance:none;}
select:focus,input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(0,194,255,.1);}
.search-btn{padding:11px 28px;background:linear-gradient(135deg,var(--accent),#0080ff);border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;white-space:nowrap;transition:opacity .2s,transform .1s;letter-spacing:.3px;}
.search-btn:hover{opacity:.9;}
.search-btn:active{transform:scale(.97);}
.search-btn:disabled{opacity:.4;cursor:not-allowed;transform:none;}
/* COMMANDS GRID */
.cmds-section{margin-bottom:22px;}
.cmds-title{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;font-weight:600;}
.cmds-grid{display:flex;flex-wrap:wrap;gap:8px;}
.cmd-chip{padding:6px 14px;background:var(--surface);border:1px solid var(--border);border-radius:99px;font-size:12px;color:var(--muted);cursor:pointer;transition:all .15s;font-family:monospace;font-weight:600;}
.cmd-chip:hover,.cmd-chip.active{background:rgba(0,194,255,.1);border-color:var(--accent);color:var(--accent);}
/* RESULT BOX */
.result-card{background:var(--card);border:1px solid var(--border);border-radius:20px;overflow:hidden;margin-bottom:22px;}
.result-bar{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;}
.status-row{display:flex;align-items:center;gap:10px;}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;}
.dot-green{background:var(--green);box-shadow:0 0 8px var(--green);}
.dot-red{background:var(--red);box-shadow:0 0 8px var(--red);}
.dot-yellow{background:var(--yellow);box-shadow:0 0 8px var(--yellow);animation:pulse 1s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.status-text{font-size:13px;color:var(--muted);}
.time-badge{font-size:11px;padding:3px 8px;background:var(--surface);border:1px solid var(--border);border-radius:99px;color:var(--dim);font-family:monospace;}
.right-actions{display:flex;align-items:center;gap:8px;}
.vbtn{padding:5px 13px;border-radius:7px;font-size:12px;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);font-weight:500;transition:all .15s;}
.vbtn.active{background:rgba(0,194,255,.12);color:var(--accent);border-color:rgba(0,194,255,.4);}
.copy-btn{padding:5px 12px;border-radius:7px;font-size:12px;cursor:pointer;border:1px solid var(--border);background:transparent;color:var(--muted);transition:all .15s;}
.copy-btn:hover{border-color:var(--accent);color:var(--accent);}
pre.json-out{padding:22px;font-size:12.5px;line-height:1.75;overflow-x:auto;white-space:pre-wrap;word-break:break-all;color:#7dd3fc;font-family:'Cascadia Code','Fira Code','Consolas',monospace;max-height:520px;overflow-y:auto;}
/* CARDS VIEW */
.info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;padding:20px;}
.info-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;transition:border-color .15s;}
.info-card:hover{border-color:rgba(0,194,255,.25);}
.info-label{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin-bottom:5px;font-weight:600;}
.info-value{font-size:14px;color:var(--text);word-break:break-all;line-height:1.4;}
.tag-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
.tag{background:rgba(0,194,255,.1);color:var(--accent);border:1px solid rgba(0,194,255,.25);padding:4px 10px;border-radius:99px;font-size:12px;font-family:monospace;cursor:pointer;}
.tag:hover{background:rgba(0,194,255,.2);}
.hist-table{width:100%;border-collapse:collapse;font-size:13px;}
.hist-table tr:hover td{background:rgba(0,194,255,.04);}
.hist-table td{padding:9px 22px;border-bottom:1px solid var(--border);}
.hist-table td:first-child{color:var(--dim);width:120px;font-family:monospace;font-size:12px;}
.hist-section{padding:0 20px 22px;}
.hist-title{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin-bottom:12px;font-weight:600;}
/* EMPTY STATE */
.empty-state{padding:64px 20px;text-align:center;}
.empty-icon{font-size:48px;margin-bottom:16px;opacity:.6;}
.empty-title{font-size:17px;font-weight:600;color:var(--muted);margin-bottom:6px;}
.empty-sub{font-size:13px;color:var(--dim);}
/* KEY MODAL */
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.8);backdrop-filter:blur(4px);z-index:500;align-items:center;justify-content:center;}
.overlay.open{display:flex;}
.modal{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:32px;width:90%;max-width:460px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.modal-title{font-size:18px;font-weight:700;margin-bottom:6px;}
.modal-sub{font-size:13px;color:var(--muted);margin-bottom:24px;line-height:1.5;}
.key-input-wrap{position:relative;margin-bottom:14px;}
.key-input-wrap input{padding-right:44px;font-family:monospace;font-size:13px;}
.eye-btn{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--muted);cursor:pointer;font-size:16px;line-height:1;}
.key-actions{display:flex;gap:8px;margin-bottom:20px;}
.ka-btn{flex:1;padding:9px;border:1px solid var(--border);background:var(--surface);border-radius:8px;color:var(--muted);cursor:pointer;font-size:12px;transition:all .15s;font-weight:500;}
.ka-btn:hover{border-color:var(--accent);color:var(--accent);}
.ka-btn.danger:hover{border-color:var(--red);color:var(--red);}
.modal-footer{display:flex;gap:10px;justify-content:flex-end;}
.btn-cancel{background:transparent;border:1px solid var(--border);color:var(--text);padding:10px 20px;border-radius:9px;cursor:pointer;font-size:14px;font-weight:500;}
.btn-save{background:linear-gradient(135deg,var(--accent),#0080ff);border:none;color:#fff;padding:10px 24px;border-radius:9px;cursor:pointer;font-size:14px;font-weight:700;}
.btn-save:hover{opacity:.9;}
/* TOAST */
.toast{position:fixed;bottom:24px;right:24px;background:#1a2f45;border:1px solid var(--border);border-radius:12px;padding:12px 18px;font-size:13px;color:var(--text);z-index:999;opacity:0;transform:translateY(10px);transition:all .3s;pointer-events:none;}
.toast.show{opacity:1;transform:translateY(0);}
@media(max-width:600px){
  .search-row{flex-direction:column;}
  .search-btn{width:100%;}
  .header{padding:12px 16px;}
  .container{padding:16px 12px;}
}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-icon">F</div>
    <div>
      <div class="logo-text">rajfflive</div>
      <div class="logo-sub">Search Panel</div>
    </div>
  </div>
  <div class="header-right">
    <div class="key-pill" id="keyPill" onclick="openKeyModal()">
      <div class="key-pill-dot"></div>
      <span class="key-pill-text" id="keyPillText">Set API Key</span>
    </div>
  </div>
</div>

<div class="container">

  <!-- COMMAND CHIPS -->
  <div class="cmds-section">
    <div class="cmds-title">Available Commands — click to select</div>
    <div class="cmds-grid" id="cmdChips"></div>
  </div>

  <!-- SEARCH ROW -->
  <div class="search-card">
    <div class="search-row">
      <div class="field" style="max-width:170px;">
        <label>Command</label>
        <select id="cmdSelect" onchange="syncChip(this.value)">
          <option value="funstate">funstate</option>
          <option value="names">names</option>
          <option value="num">num</option>
          <option value="veh">veh</option>
          <option value="vnum">vnum</option>
          <option value="upiinfo">upiinfo</option>
          <option value="fam">fam</option>
          <option value="insta">insta</option>
          <option value="ip">ip</option>
          <option value="email">email</option>
          <option value="tg">tg</option>
          <option value="ifsc">ifsc</option>
          <option value="adhar">adhar</option>
          <option value="imei">imei</option>
          <option value="pak">pak</option>
          <option value="family">family</option>
          <option value="gst">gst</option>
          <option value="pan">pan</option>
          <option value="leak">leak</option>
        </select>
      </div>
      <div class="field">
        <label>Value</label>
        <input type="text" id="val" placeholder="@username, phone number, IP...">
      </div>
      <button class="search-btn" id="searchBtn" onclick="doSearch()">Search</button>
    </div>
  </div>

  <!-- RESULT -->
  <div class="result-card" id="resultCard" style="display:none;">
    <div class="result-bar">
      <div class="status-row">
        <div class="dot dot-yellow" id="statusDot"></div>
        <span class="status-text" id="statusText">Searching…</span>
        <span class="time-badge" id="timeBadge" style="display:none;"></span>
      </div>
      <div class="right-actions">
        <button class="copy-btn" onclick="copyResult()">Copy JSON</button>
        <button class="vbtn active" id="vRaw" onclick="switchView('raw')">JSON</button>
        <button class="vbtn" id="vCard" onclick="switchView('card')">Cards</button>
      </div>
    </div>
    <div id="rawView"><pre class="json-out" id="jsonOut"></pre></div>
    <div id="cardView" style="display:none;"></div>
  </div>

  <!-- EMPTY STATE -->
  <div class="empty-state" id="emptyState">
    <div class="empty-icon">🔍</div>
    <div class="empty-title">Ready to Search</div>
    <div class="empty-sub">Select a command, enter a value and hit Search.<br>Your API key is saved locally in the browser.</div>
  </div>

</div>

<!-- KEY MODAL -->
<div class="overlay" id="keyOverlay" onclick="if(event.target===this)closeKeyModal()">
  <div class="modal">
    <div class="modal-title">API Key</div>
    <div class="modal-sub">Your key is stored in the browser (localStorage) and only sent as a query parameter when you search.</div>
    <div class="key-input-wrap">
      <input type="password" id="keyInput" placeholder="Paste your API key here…">
      <button class="eye-btn" onclick="toggleEye()" id="eyeBtn">👁</button>
    </div>
    <div class="key-actions">
      <button class="ka-btn" onclick="pasteKey()">Paste</button>
      <button class="ka-btn" onclick="copyKey()">Copy</button>
      <button class="ka-btn danger" onclick="clearKey()">Clear Key</button>
    </div>
    <div class="modal-footer">
      <button class="btn-cancel" onclick="closeKeyModal()">Cancel</button>
      <button class="btn-save" onclick="saveKey()">Save Key</button>
    </div>
  </div>
</div>

<!-- TOAST -->
<div class="toast" id="toast"></div>

<script>
const COMMANDS = [
  {v:'funstate', label:'funstate', hint:'Telegram user lookup (full stats)'},
  {v:'names',    label:'names',    hint:'Alias for funstate'},
  {v:'num',      label:'num',      hint:'Phone number lookup'},
  {v:'veh',      label:'veh',      hint:'Vehicle info'},
  {v:'vnum',     label:'vnum',     hint:'Vehicle number'},
  {v:'upiinfo',  label:'upiinfo',  hint:'UPI ID lookup'},
  {v:'tg',       label:'tg',       hint:'Telegram ID/username lookup'},
  {v:'email',    label:'email',    hint:'Email lookup'},
  {v:'ip',       label:'ip',       hint:'IP address info'},
  {v:'adhar',    label:'adhar',    hint:'Aadhaar lookup'},
  {v:'pan',      label:'pan',      hint:'PAN card lookup'},
  {v:'ifsc',     label:'ifsc',     hint:'Bank IFSC lookup'},
  {v:'imei',     label:'imei',     hint:'IMEI lookup'},
  {v:'gst',      label:'gst',      hint:'GST number lookup'},
  {v:'insta',    label:'insta',    hint:'Instagram lookup'},
  {v:'pak',      label:'pak',      hint:'Pakistan lookup'},
  {v:'fam',      label:'fam',      hint:'Family lookup'},
  {v:'family',   label:'family',   hint:'Family lookup'},
  {v:'leak',     label:'leak',     hint:'Data breach check'},
];

let currentData = null;
let currentView = 'raw';

// Build chips
const chipsEl = document.getElementById('cmdChips');
COMMANDS.forEach(c => {
  const el = document.createElement('div');
  el.className = 'cmd-chip' + (c.v === 'funstate' ? ' active' : '');
  el.textContent = c.label;
  el.title = c.hint;
  el.onclick = () => selectCmd(c.v);
  el.id = 'chip_' + c.v;
  chipsEl.appendChild(el);
});

function selectCmd(v){
  document.querySelectorAll('.cmd-chip').forEach(c => c.classList.remove('active'));
  const el = document.getElementById('chip_' + v);
  if(el) el.classList.add('active');
  document.getElementById('cmdSelect').value = v;
  document.getElementById('val').focus();
}
function syncChip(v){
  document.querySelectorAll('.cmd-chip').forEach(c => c.classList.remove('active'));
  const el = document.getElementById('chip_' + v);
  if(el) el.classList.add('active');
}

// KEY MANAGEMENT
function updateKeyPill(){
  const k = localStorage.getItem('felixApiKey');
  const pill = document.getElementById('keyPill');
  const txt  = document.getElementById('keyPillText');
  if(k){ pill.classList.add('set'); txt.textContent = '••••' + k.slice(-6); }
  else  { pill.classList.remove('set'); txt.textContent = 'Set API Key'; }
}
function openKeyModal(){
  const k = localStorage.getItem('felixApiKey') || '';
  document.getElementById('keyInput').value = k;
  document.getElementById('keyInput').type = 'password';
  document.getElementById('eyeBtn').textContent = '👁';
  document.getElementById('keyOverlay').classList.add('open');
  setTimeout(() => document.getElementById('keyInput').focus(), 80);
}
function closeKeyModal(){ document.getElementById('keyOverlay').classList.remove('open'); }
function saveKey(){
  const k = document.getElementById('keyInput').value.trim();
  if(k){ localStorage.setItem('felixApiKey', k); updateKeyPill(); showToast('Key saved ✓'); }
  closeKeyModal();
}
function toggleEye(){
  const inp = document.getElementById('keyInput');
  const btn = document.getElementById('eyeBtn');
  if(inp.type === 'password'){ inp.type = 'text'; btn.textContent = '🙈'; }
  else { inp.type = 'password'; btn.textContent = '👁'; }
}
async function pasteKey(){
  try{
    const t = await navigator.clipboard.readText();
    document.getElementById('keyInput').value = t.trim();
    showToast('Pasted from clipboard');
  } catch(e){ showToast('Clipboard not available'); }
}
function copyKey(){
  const k = document.getElementById('keyInput').value.trim();
  if(!k){ showToast('No key to copy'); return; }
  navigator.clipboard.writeText(k).then(() => showToast('Key copied ✓')).catch(() => showToast('Copy failed'));
}
function clearKey(){
  localStorage.removeItem('felixApiKey');
  document.getElementById('keyInput').value = '';
  updateKeyPill();
  showToast('Key cleared');
  closeKeyModal();
}
updateKeyPill();

// SEARCH
document.getElementById('val').addEventListener('keydown', e => { if(e.key==='Enter') doSearch(); });

async function doSearch(){
  const apiKey = localStorage.getItem('felixApiKey');
  if(!apiKey){ openKeyModal(); return; }
  const cmd = document.getElementById('cmdSelect').value;
  const val = document.getElementById('val').value.trim();
  if(!val){ document.getElementById('val').focus(); return; }

  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('resultCard').style.display = 'block';
  document.getElementById('statusDot').className = 'dot dot-yellow';
  document.getElementById('statusText').textContent = 'Searching…';
  document.getElementById('timeBadge').style.display = 'none';
  document.getElementById('jsonOut').textContent = '';
  document.getElementById('cardView').innerHTML = '';
  document.getElementById('searchBtn').disabled = true;
  currentData = null;

  const t0 = Date.now();
  try{
    const url = '/' + cmd + '/' + encodeURIComponent(val) + '?api_key=' + encodeURIComponent(apiKey);
    const resp = await fetch(url);
    const elapsed = ((Date.now() - t0) / 1000).toFixed(2);
    const data = await resp.json();
    currentData = data;

    const tb = document.getElementById('timeBadge');
    tb.textContent = (data.time_taken || elapsed + 's');
    tb.style.display = '';

    if(data.error){
      document.getElementById('statusDot').className = 'dot dot-red';
      document.getElementById('statusText').textContent = 'Error';
    } else {
      document.getElementById('statusDot').className = 'dot dot-green';
      document.getElementById('statusText').textContent = 'Success — ' + cmd + ' / ' + val;
      renderCards(data);
    }
    document.getElementById('jsonOut').textContent = JSON.stringify(data, null, 2);
  } catch(e){
    document.getElementById('statusDot').className = 'dot dot-red';
    document.getElementById('statusText').textContent = 'Network error';
    document.getElementById('jsonOut').textContent = String(e);
  }
  document.getElementById('searchBtn').disabled = false;
}

function copyResult(){
  if(!currentData){ showToast('Nothing to copy'); return; }
  navigator.clipboard.writeText(JSON.stringify(currentData, null, 2))
    .then(() => showToast('JSON copied ✓'))
    .catch(() => showToast('Copy failed'));
}

function switchView(v){
  currentView = v;
  document.getElementById('rawView').style.display = v==='raw' ? '' : 'none';
  document.getElementById('cardView').style.display = v==='card' ? '' : 'none';
  document.getElementById('vRaw').className = 'vbtn' + (v==='raw' ? ' active' : '');
  document.getElementById('vCard').className = 'vbtn' + (v==='card' ? ' active' : '');
}

function renderCards(data){
  const cv = document.getElementById('cardView');
  if(!data || typeof data !== 'object'){ cv.innerHTML=''; return; }
  const skip = new Set(['developer','tag','name_history','usernames','stats','sticker_links','name_history_shown','name_history_total','usernames_shown','usernames_total','time_taken']);
  let h = '<div class="info-grid">';
  if(data.name) h += ic('Display Name', esc(data.name));
  if(data.id)   h += ic('Telegram ID', data.id);
  if(data.time_taken) h += ic('Response Time', '<span style="color:var(--accent);font-family:monospace;">' + esc(data.time_taken) + '</span>');
  if(data.channel) h += ic('Channel', esc(data.channel));
  if(data.bio_link) h += ic('Bio / Channel Link', '<a href="' + esc(data.bio_link) + '" target="_blank" style="color:var(--accent);">' + esc(data.bio_link) + '</a>');
  if(data.usernames && data.usernames.length){
    const tot = data.usernames_total ? ' <span style="color:var(--dim);font-size:11px;">(' + data.usernames_shown + ' of ' + data.usernames_total + ')</span>' : '';
    let tags = data.usernames.map(u => '<span class="tag" onclick="navigator.clipboard.writeText(\''+esc(u)+'\')" title="Click to copy">' + esc(u) + '</span>').join('');
    h += '<div class="info-card" style="grid-column:1/-1"><div class="info-label">Usernames' + tot + '</div><div class="tag-list">' + tags + '</div></div>';
  }
  if(data.stats && typeof data.stats === 'object'){
    const s = data.stats;
    if(s.message_diversity) h += ic('Msg Diversity', s.message_diversity);
    if(s.total_messages)    h += ic('Messages', s.total_messages + ' in ' + (s.total_groups||'?') + ' groups');
    if(s.from_date)         h += ic('Active Period', s.from_date + ' → ' + (s.to_date||'?'));
    if(s.replies_percent)   h += ic('Replies / Media', s.replies_percent + ' / ' + (s.media_percent||'?'));
    if(s.circles !== undefined) h += ic('Circles / Voice', s.circles + ' / ' + (s.voice !== undefined ? s.voice : '?'));
    if(s.favorite_group)    h += ic('Favorite Group', esc(s.favorite_group));
    if(s.were_looking_for !== undefined) h += ic('Were Looking For', s.were_looking_for);
    if(s.stickersets_count !== undefined) h += ic('Sticker Sets', s.stickersets_count);
    if(s.admin_in_groups !== undefined)   h += ic('Admin In Groups', s.admin_in_groups);
  }
  for(const [k,v] of Object.entries(data)){
    if(skip.has(k)||k==='name'||k==='id'||k==='channel'||k==='bio_link'||k==='usernames') continue;
    if(typeof v === 'object') continue;
    h += ic(k, esc(String(v)));
  }
  h += '</div>';
  if(data.name_history && data.name_history.length){
    const tot = data.name_history_total ? ' <span style="color:var(--dim);font-size:11px;">(' + data.name_history_shown + ' of ' + data.name_history_total + ' shown)</span>' : '';
    h += '<div class="hist-section"><div class="hist-title">Name / Last Name History' + tot + '</div><table class="hist-table">';
    data.name_history.forEach(r => { h += '<tr><td>' + esc(r.date) + '</td><td>' + esc(r.name) + '</td></tr>'; });
    h += '</table></div>';
  }
  cv.innerHTML = h;
}

function ic(label, value){
  return '<div class="info-card"><div class="info-label">' + label + '</div><div class="info-value">' + value + '</div></div>';
}
function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

document.addEventListener('keydown', e => { if(e.key==='Escape') closeKeyModal(); });
</script>
</body>
</html>
"""

@app.route('/search')
def public_search():
    public_key = get_global_setting('public_key') or ''
    # Allow access if public_key is set in settings OR if user provides own key via browser localStorage (JS side)
    return render_template_string(PUBLIC_SEARCH_HTML)

# ================= ADMIN PANEL =================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>rajfflive - Admin Panel</title>
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
    <div class="sidebar-logo">rajfflive <span>Admin Panel</span></div>
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
                <tr><td style="color:#64748b;">Search Panel</td><td><a href="/search" target="_blank" style="color:#38bdf8;">/search</a> {% if public_key %}<span class="badge badge-green">Key Set</span>{% else %}<span class="badge badge-red">No Key</span>{% endif %}</td></tr>
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
                    <td>
                        <code id="key_{{ loop.index }}">{{ k.key[:20] }}…</code>
                        <button onclick="copyFullKey('{{ k.key }}', this)" style="margin-left:6px;background:none;border:1px solid #334155;border-radius:4px;color:#64748b;font-size:10px;padding:2px 7px;cursor:pointer;" title="Copy full key">Copy</button>
                    </td>
                    <td>{{ k.name }}</td>
                    <td>{{ k.expiry_days ~ 'd' if k.expiry_days > 0 else '∞ Forever' }}</td>
                    <td>{{ k.daily_limit if k.daily_limit > 0 else '∞ Unlimited' }}</td>
                    <td><span class="badge {% if k.active %}badge-green{% else %}badge-red{% endif %}">{% if k.active %}Active{% else %}Paused{% endif %}</span></td>
                    <td>
                        <a href="/admin/toggle_key/{{ k.key }}" class="action-link" style="color:{% if k.active %}#f59e0b{% else %}#10b981{% endif %}">{% if k.active %}Pause{% else %}Resume{% endif %}</a>
                        <a href="/admin/revoke/{{ k.key }}" class="action-link action-danger" onclick="return confirm('Permanently revoke this key?')">Revoke</a>
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
                <p style="font-size:13px;color:#64748b;margin-bottom:12px;">
                    Public search is at <code>/search</code> — users can paste their own key in the browser.<br>
                    Optionally set a default key below to pre-fill it for users who visit <code>/search</code>.
                </p>
                <div style="display:flex;gap:10px;align-items:center;">
                    <input type="text" name="public_key" value="{{ public_key }}" placeholder="Optional default key for /search" style="margin:0;flex:1;">
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
function copyFullKey(key, btn){
    navigator.clipboard.writeText(key).then(()=>{
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        btn.style.color = '#34d399';
        btn.style.borderColor = '#34d399';
        setTimeout(()=>{ btn.textContent = orig; btn.style.color=''; btn.style.borderColor=''; }, 1800);
    }).catch(()=>{ alert('Copy failed — key: ' + key); });
}
function openLog(row) {
    const time = row.dataset.time;
    const key = row.dataset.key;
    const cmd = row.dataset.cmd;
    const val = row.dataset.val;
    const resp = row.dataset.resp;
    const ok = row.dataset.ok === '1';
    document.getElementById('modalMeta').innerHTML =
        `<span>🕐 ${time}</span><span>🔑 ${key.slice(0,12)}…</span><span>📌 /${cmd}</span><span>🔍 ${val || '—'}</span><span>` +
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
<title>rajfflive Login</title>
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
<h2>rajfflive</h2>
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

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@app.route('/admin')
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
        return redirect(url_for('admin_dashboard'))
    key = secrets.token_hex(24)
    add_api_key(key, name, "admin", expiry_days, daily_limit)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_key/<key>')
@admin_login_required
def admin_toggle_key(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT active FROM api_keys WHERE key=?", (key,))
    row = c.fetchone()
    if row:
        new_active = 0 if row[0] else 1
        c.execute("UPDATE api_keys SET active=? WHERE key=?", (new_active, key))
        conn.commit()
    conn.close()
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
    api_id = request.form.get('api_id')
    api_hash = request.form.get('api_hash')
    session_string = request.form.get('session_string')
    if all([name, api_id, api_hash, session_string]):
        add_account(name, int(api_id), api_hash, session_string)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/toggle_account/<int:acc_id>')
@admin_login_required
def admin_toggle_account(acc_id):
    accs = get_all_accounts()
    for a in accs:
        if a['id'] == acc_id:
            toggle_account(acc_id, not a['active'])
            break
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_account/<int:acc_id>')
@admin_login_required
def admin_delete_account(acc_id):
    delete_account(acc_id)
    return redirect(url_for('admin_dashboard'))

@app.route('/')
def index():
    return redirect(url_for('public_search'))

# ================= MAIN =================
if __name__ == '__main__':
    init_accounts()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
