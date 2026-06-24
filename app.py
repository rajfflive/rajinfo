import os
import json
import asyncio
import sqlite3
import secrets
import threading
import time
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= ENVIRONMENT =================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
PERMANENT_KEY = "felix_unlimited_2024"
DEVELOPER_TAG = "@rajfflive"
CACHE_EXPIRE_SECONDS = int(os.environ.get("CACHE_EXPIRE_SECONDS", 86400))
MAX_RESULTS = 10
DELETE_DELAY = 10

GROUP_MAIN_NAME = "USERSXINFO CHEATING GC"
GROUP_OTHER_NAME = "TGTOINFO"
BOT_USERNAME = "usersXinfo0bot"
FUNSTATE_BOT_USERNAME = "Funstate_7bot"

GROUP_MAIN = None
GROUP_OTHER = None
BOT_ID = None
FUNSTATE_BOT_ID = None
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
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('vip_commands_enabled', '1')")
    c.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES ('delete_delay', ?)", (str(DELETE_DELAY),))
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
account_index = 0

def get_next_account():
    global account_index
    if not accounts:
        return None
    account_index = (account_index + 1) % len(accounts)
    return accounts[account_index]

async def start_account(account_data):
    global GROUP_MAIN, GROUP_OTHER, BOT_ID, FUNSTATE_BOT_ID
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

# ================= JSON EXTRACTION (for main bot) =================
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

# ================= QUERY FUNCTIONS =================
async def query_main_bot_async(client, command_text, group):
    """Send command to the main bot in a group and capture reply."""
    sent = await client.send_message(group, command_text)
    msg_id = sent.id
    logger.info(f"📤 Sent {command_text} to group {group.title}")

    bot_replies = []
    for attempt in range(20):
        await asyncio.sleep(1.5)
        async for msg in client.iter_messages(group, limit=200):
            if msg.sender_id == BOT_ID and (msg.reply_to_msg_id == msg_id or command_text in msg.raw_text):
                bot_replies.append(msg)
                logger.info(f"📩 Found reply (attempt {attempt+1})")
        if bot_replies:
            break

    if not bot_replies:
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
    if objects:
        return objects
    else:
        return {"info": combined}

async def query_funstate_bot_async(client, value):
    """Send plain value directly to @Funstate_7bot and capture its reply."""
    sent = await client.send_message(FUNSTATE_BOT_USERNAME, value)
    msg_id = sent.id
    logger.info(f"📤 Sent plain '{value}' directly to Funstate bot")

    bot_replies = []
    for attempt in range(20):
        await asyncio.sleep(1.5)
        # Fetch messages from the bot's chat (the bot is the chat)
        async for msg in client.iter_messages(FUNSTATE_BOT_USERNAME, limit=200):
            if msg.sender_id == FUNSTATE_BOT_ID and (msg.reply_to_msg_id == msg_id or value in msg.raw_text):
                bot_replies.append(msg)
                logger.info(f"📩 Found reply (attempt {attempt+1})")
        if bot_replies:
            break

    if not bot_replies:
        return {"error": "Funstate bot did not respond"}

    seen = set()
    unique_replies = []
    for msg in bot_replies:
        if msg.id not in seen:
            seen.add(msg.id)
            unique_replies.append(msg)
    unique_replies.sort(key=lambda m: m.date)

    combined = "".join([m.raw_text for m in unique_replies])
    # Return the full raw text as info (could contain newlines, emojis)
    return {"info": combined}

# ================= MAIN QUERY FUNCTION =================
def query_bot_sync(command_text, group_type, bot_type="main"):
    account = get_next_account()
    if not account:
        return {"error": "No active Telegram accounts"}
    acc_id = account['id']
    client = account_clients.get(acc_id)
    if client is None:
        return {"error": "Account not ready"}
    loop = telegram_loops.get(acc_id)
    if loop is None:
        return {"error": "Event loop not found"}

    if bot_type == "funstate":
        if get_global_setting('vip_commands_enabled') != '1':
            return {"error": "VIP commands are disabled by admin"}

        async def do_query():
            try:
                return await query_funstate_bot_async(client, command_text)
            except Exception as e:
                logger.error(f"Funstate query error: {e}")
                return {"error": str(e)}
    else:
        group = GROUP_MAIN if group_type == "main" else GROUP_OTHER
        if group is None:
            return {"error": f"Group '{group_type}' not found"}

        async def do_query():
            try:
                return await query_main_bot_async(client, command_text, group)
            except Exception as e:
                logger.error(f"Query error: {e}")
                return {"error": str(e)}

    future = asyncio.run_coroutine_threadsafe(do_query(), loop)
    try:
        result = future.result(timeout=50)
        success = 'error' not in result
        add_stats(command_text.split()[0] if command_text else 'unknown', command_text.split()[1] if len(command_text.split()) > 1 else '', success)
        return result
    except asyncio.TimeoutError:
        add_stats('timeout', '', False)
        return {"error": "Request timed out"}
    except Exception as e:
        add_stats('exception', '', False)
        return {"error": str(e)}

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
                result = query_bot_sync(value, None, bot_type="funstate")
            else:
                group_type = "main" if cmd in SPECIAL_COMMANDS else "other"
                result = query_bot_sync(f"/{cmd} {value}", group_type)
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

# ================= ADMIN PANEL (with VIP toggle) =================
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Felix API - Admin</title>
    <style>
        body { font-family: Arial; margin: 0; padding: 20px; background: #f0f2f5; }
        .container { max-width: 1200px; margin: auto; }
        h1 { color: #1a73e8; }
        .tabs { display: flex; gap: 10px; margin: 20px 0; flex-wrap: wrap; }
        .tab { padding: 10px 20px; background: white; border-radius: 5px; cursor: pointer; border: 1px solid #ddd; }
        .tab.active { background: #1a73e8; color: white; border-color: #1a73e8; }
        .panel { background: white; padding: 20px; border-radius: 10px; margin-top: 10px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #f2f2f2; }
        input, textarea { padding: 8px; width: 100%; margin: 5px 0; border: 1px solid #ddd; border-radius: 4px; }
        button { padding: 10px 20px; background: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; }
        .danger { background: #f44336; }
        .success { background: #4CAF50; }
        .clear { background: #ff9800; }
    </style>
</head>
<body>
<div class="container">
    <h1>Felix API - Admin</h1>
    <div class="tabs">
        <div class="tab active" onclick="showTab('keys')">API Keys</div>
        <div class="tab" onclick="showTab('accounts')">Accounts</div>
        <div class="tab" onclick="showTab('logs')">Usage Logs</div>
        <div class="tab" onclick="showTab('status')">Status</div>
        <div class="tab" onclick="showTab('settings')">Settings</div>
        <div style="margin-left:auto;"><a href="/admin/logout" style="color:red;">Logout</a></div>
    </div>
    <div id="keys" class="panel">
        <h2>Generate API Key</h2>
        <form method="POST" action="/admin/create_key">
            <input type="text" name="name" placeholder="Key Name" required>
            <input type="number" name="expiry_days" placeholder="Expiry Days (0=forever)" value="30">
            <input type="number" name="daily_limit" placeholder="Daily Limit (0=unlimited)" value="100">
            <button type="submit" class="success">Generate</button>
        </form>
        <hr>
        <h2>Active API Keys</h2>
        <table>
            <tr><th>Key</th><th>Name</th><th>Expiry</th><th>Daily Limit</th><th>Status</th><th>Actions</th></tr>
            {% for k in keys %}
            <tr>
                <td><code>{{ k.key }}</code></td>
                <td>{{ k.name }}</td>
                <td>{{ k.expiry_days if k.expiry_days > 0 else 'Forever' }}</td>
                <td>{{ k.daily_limit if k.daily_limit > 0 else 'Unlimited' }}</td>
                <td>{{ '✅' if k.active else '❌' }}</td>
                <td>
                    <a href="/admin/revoke/{{ k.key }}" class="danger">Revoke</a>
                    <a href="/admin/delete/{{ k.key }}" class="danger" onclick="return confirm('Delete?')">Delete</a>
                </td>
            </tr>
            {% endfor %}
        </table>
        <p><strong>Permanent Key:</strong> <code>{{ permanent_key }}</code></p>
        <p><a href="/admin/clear_cache" class="clear" style="color:white;padding:5px 10px;border-radius:4px;text-decoration:none;">Clear Cache</a> ({{ cache_size }} entries)</p>
    </div>
    <div id="accounts" class="panel" style="display:none;">
        <h2>Add Account</h2>
        <form method="POST" action="/admin/add_account">
            <input type="text" name="name" placeholder="Account Name" required>
            <input type="number" name="api_id" placeholder="API ID" required>
            <input type="text" name="api_hash" placeholder="API Hash" required>
            <textarea name="session_string" placeholder="Session String" rows="3" required></textarea>
            <button type="submit" class="success">Add Account</button>
        </form>
        <hr>
        <h2>Active Accounts</h2>
        <table>
            <tr><th>ID</th><th>Name</th><th>API ID</th><th>Status</th><th>Actions</th></tr>
            {% for acc in accounts %}
            <tr>
                <td>{{ acc.id }}</td>
                <td>{{ acc.name }}</td>
                <td>{{ acc.api_id }}</td>
                <td>{{ '✅' if acc.active else '❌' }}</td>
                <td>
                    <a href="/admin/toggle_account/{{ acc.id }}">{{ 'Disable' if acc.active else 'Enable' }}</a>
                    <a href="/admin/delete_account/{{ acc.id }}" onclick="return confirm('Delete?')">Delete</a>
                </td>
            </tr>
            {% endfor %}
        </table>
    </div>
    <div id="logs" class="panel" style="display:none;">
        <h2>Usage Logs (last 100)</h2>
        <table>
            <tr><th>Time</th><th>Key</th><th>Command</th><th>Value</th><th>Response (truncated)</th><th>Success</th></tr>
            {% for log in logs %}
            <tr>
                <td>{{ log.timestamp[:19] }}</td>
                <td>{{ log.key[:8] }}...</td>
                <td>{{ log.command }}</td>
                <td>{{ log.value }}</td>
                <td>{{ log.response[:80] }}{% if log.response|length > 80 %}...{% endif %}</td>
                <td>{{ '✅' if log.success else '❌' }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    <div id="status" class="panel" style="display:none;">
        <h2>System Status</h2>
        <p><strong>Active Accounts:</strong> {{ accounts|length }}</p>
        <p><strong>API Keys:</strong> {{ keys|length }}</p>
        <p><strong>Cache Entries:</strong> {{ cache_size }}</p>
        <p><strong>Developer:</strong> {{ developer }}</p>
        <p><strong>Main Group (Special):</strong> {{ group_main_name }}</p>
        <p><strong>Other Group:</strong> {{ group_other_name }}</p>
        <p><strong>Bot ID:</strong> {{ bot_id or 'Not fetched' }}</p>
        <p><strong>Funstate Bot ID:</strong> {{ funstate_bot_id or 'Not fetched' }}</p>
        <hr>
        <h3>API Stats</h3>
        <p><strong>Total Requests:</strong> {{ stats.total }}</p>
        <p><strong>✅ Success:</strong> {{ stats.success }}</p>
        <p><strong>❌ Failed:</strong> {{ stats.fail }}</p>
    </div>
    <div id="settings" class="panel" style="display:none;">
        <h2>Settings</h2>
        <h3>VIP Commands (/funstate, /names)</h3>
        <form method="POST" action="/admin/toggle_vip">
            <label>Enable VIP Commands:</label>
            <select name="vip_enabled">
                <option value="1" {% if vip_enabled == '1' %}selected{% endif %}>ON</option>
                <option value="0" {% if vip_enabled == '0' %}selected{% endif %}>OFF</option>
            </select>
            <button type="submit" class="success">Update</button>
        </form>
        <hr>
        <h3>Delete Delay (seconds)</h3>
        <p>Current delay: {{ delete_delay }}s</p>
        <form method="POST" action="/admin/set_delete_delay">
            <input type="number" name="delete_delay" value="{{ delete_delay }}" min="5" max="60">
            <button type="submit" class="success">Update</button>
        </form>
    </div>
</div>
<script>
function showTab(tab) {
    document.querySelectorAll('.panel').forEach(p => p.style.display = 'none');
    document.getElementById(tab).style.display = 'block';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[onclick*="${tab}"]`).classList.add('active');
}
</script>
</body>
</html>
"""

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return redirect(url_for('admin_dashboard'))
    return '''
    <form method=post style="margin-top:100px;text-align:center;">
        <h2>Admin Login</h2>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Login</button>
    </form>
    '''

@app.route('/admin/dashboard')
@admin_login_required
def admin_dashboard():
    keys = get_all_keys()
    accounts = get_all_accounts()
    logs = get_usage_logs(100)
    total, success, fail = get_stats()
    stats = {"total": total, "success": success, "fail": fail}
    vip_enabled = get_global_setting('vip_commands_enabled') or '1'
    delete_delay = get_global_setting('delete_delay') or str(DELETE_DELAY)
    return render_template_string(ADMIN_HTML,
                                 keys=keys,
                                 accounts=accounts,
                                 logs=logs,
                                 permanent_key=PERMANENT_KEY,
                                 developer=DEVELOPER_TAG,
                                 cache_size=len(response_cache),
                                 group_main_name=GROUP_MAIN_NAME,
                                 group_other_name=GROUP_OTHER_NAME,
                                 bot_id=BOT_ID,
                                 funstate_bot_id=FUNSTATE_BOT_ID,
                                 stats=stats,
                                 vip_enabled=vip_enabled,
                                 delete_delay=delete_delay)

@app.route('/admin/toggle_vip', methods=['POST'])
@admin_login_required
def admin_toggle_vip():
    enabled = request.form.get('vip_enabled', '1')
    set_global_setting('vip_commands_enabled', enabled)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/set_delete_delay', methods=['POST'])
@admin_login_required
def admin_set_delete_delay():
    delay = request.form.get('delete_delay', str(DELETE_DELAY))
    set_global_setting('delete_delay', delay)
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

@app.route('/')
def home():
    return '<h2>Felix API</h2><p>Use /command?api_key=YOUR_KEY</p><p><a href="/admin/login">Admin</a></p>'

@app.route('/health')
def health():
    return jsonify({"status": "ok", "accounts": len(accounts)})

if __name__ == "__main__":
    init_accounts()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
