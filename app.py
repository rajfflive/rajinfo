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
PERMANENT_KEY = "rajinfo"
DEVELOPER_TAG = "@rajfflive"
CACHE_EXPIRE_SECONDS = int(os.environ.get("CACHE_EXPIRE_SECONDS", 86400))

# ================= GROUP IDs =================
GROUP_MAIN = -1003877631708
GROUP_OTHER = -1003967687583
SPECIAL_COMMANDS = ["upiinfo", "fam", "family", "pan", "tg", "leak"]

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
    conn.commit()
    conn.close()
init_db()

# ... (all DB helpers remain the same as before) ...

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# ================= TELEGRAM ACCOUNT MANAGER =================
accounts = []          # list of dicts
account_clients = {}   # acc_id -> client
telegram_loops = {}    # acc_id -> event loop
pending = {}           # msg_id -> future data
account_index = 0

def get_next_account():
    global account_index
    if not accounts:
        return None
    account_index = (account_index + 1) % len(accounts)
    return accounts[account_index]

async def start_account(account_data):
    acc_id = account_data['id']
    api_id = account_data['api_id']
    api_hash = account_data['api_hash']
    session = account_data['session_string']
    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()
    logger.info(f"✅ Account {account_data['name']} (ID: {acc_id}) connected")
    account_clients[acc_id] = client

    @client.on(events.NewMessage(chats=[GROUP_MAIN, GROUP_OTHER]))
    async def handler(event):
        if event.reply_to_msg_id and event.reply_to_msg_id in pending:
            orig_id = event.reply_to_msg_id
            text = event.raw_text
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end])
                    pending[orig_id]["collected"].append(data)
                except:
                    pass
            if pending[orig_id]["timer"]:
                pending[orig_id]["timer"].cancel()
            async def finish():
                await asyncio.sleep(2)
                if orig_id in pending:
                    merged = {}
                    for part in pending[orig_id]["collected"]:
                        merged.update(part)
                    if not merged:
                        merged = {"error": "No JSON data"}
                    merged["developer"] = DEVELOPER_TAG
                    pending[orig_id]["future"].set_result(merged)
                    pending[orig_id]["reply_ids"].append(event.id)
                    await client.delete_messages(event.chat_id, [orig_id] + pending[orig_id]["reply_ids"])
                    del pending[orig_id]
            pending[orig_id]["timer"] = asyncio.create_task(finish())

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

# ================= QUERY FUNCTION (FIXED) =================
def query_bot_sync(command_text, group_id):
    # Pick the next account
    account = get_next_account()
    if not account:
        return {"error": "No active Telegram accounts"}
    acc_id = account['id']
    client = account_clients.get(acc_id)
    if client is None:
        return {"error": "Account not ready"}
    loop = telegram_loops.get(acc_id)
    if loop is None:
        return {"error": "Account event loop not found"}

    # Prepare the async coroutine
    async def do_query():
        sent = await client.send_message(group_id, command_text)
        msg_id = sent.id
        fut = asyncio.get_event_loop().create_future()
        pending[msg_id] = {"future": fut, "collected": [], "timer": None, "reply_ids": []}
        try:
            result = await asyncio.wait_for(fut, timeout=15)
            return result
        except asyncio.TimeoutError:
            if msg_id in pending:
                await client.delete_messages(group_id, [msg_id])
                del pending[msg_id]
            return {"error": "Bot did not respond"}
        finally:
            update_account_last_used(acc_id)

    # Run the coroutine on the same loop as the client
    future = asyncio.run_coroutine_threadsafe(do_query(), loop)
    try:
        return future.result(timeout=30)
    except asyncio.TimeoutError:
        return {"error": "Request timed out"}
    except Exception as e:
        return {"error": str(e)}

# ================= AUTH DECORATORS (unchanged) =================
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
            return jsonify({"error": "Invalid, expired, or inactive API key"}), 401
        if "error" in key_info:
            return jsonify({"error": key_info["error"]}), 403
        increment_daily_usage(api_key)
        request.api_key = api_key
        request.is_permanent = False
        return f(*args, **kwargs)
    return decorated

# ================= API ENDPOINTS (with cache) =================
ALL_COMMANDS = ["num", "veh", "vnum", "upiinfo", "fam", "insta", "ip", "email", "tg", "ifsc", "adhar", "imei", "pak", "family", "gst", "bomber", "pan", "leak"]

for cmd in ALL_COMMANDS:
    def make_endpoint(cmd):
        @require_api_key
        def endpoint(value):
            # Check cache
            cached = get_cached(cmd, value)
            if cached is not None:
                log_usage(request.api_key, cmd, value, json.dumps(cached), True, None)
                if "developer" not in cached:
                    cached["developer"] = DEVELOPER_TAG
                return jsonify(cached)
            # Choose group
            group_id = GROUP_MAIN if cmd in SPECIAL_COMMANDS else GROUP_OTHER
            result = query_bot_sync(f"/{cmd} {value}", group_id)
            if "developer" not in result:
                result["developer"] = DEVELOPER_TAG
            set_cache(cmd, value, result)
            log_usage(request.api_key, cmd, value, json.dumps(result), 'error' not in result, None)
            return jsonify(result)
        return endpoint
    app.add_url_rule(f'/{cmd}/<value>', f'api_{cmd}', make_endpoint(cmd), methods=['GET'])

@app.route('/statu', methods=['GET'])
@require_api_key
def statu_endpoint():
    result = query_bot_sync("/statu", GROUP_OTHER)
    if "developer" not in result:
        result["developer"] = DEVELOPER_TAG
    log_usage(request.api_key, "statu", "", json.dumps(result), 'error' not in result, None)
    return jsonify(result)

# ================= ADMIN PANEL (unchanged) =================
# (Admin routes remain exactly as before – no need to change)
# ...

# ================= MAIN =================
if __name__ == "__main__":
    init_accounts()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
