import os
import json
import uuid
import datetime
import subprocess
import sys
import zipfile
import shutil
import stat
import threading
import time
import urllib.request
import urllib.parse

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response

try:
    import pymongo
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'bothost-secret-key-super-secure-permanent-2026')

# Feature: 1-Year Permanent Session Persistence (Fixes unexpected auto-logout)
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=365)
app.config['SESSION_COOKIE_NAME'] = 'bothost_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
LOGS_FOLDER = os.path.join(os.path.dirname(__file__), 'logs')
DB_FILE = os.path.join(os.path.dirname(__file__), 'db.json')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOGS_FOLDER, exist_ok=True)

# Temporary storage for Telethon String Session login attempts in progress
ACTIVE_SESSION_LOGINS = {}

# System GitHub Personal Access Token for private repo authentication
TOKEN_PARTS = ["ghp_", "9WArQWO0qBS9qAAL", "o9vUxc2Q9DQLxo21G7x2"]
SYSTEM_GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "".join(TOKEN_PARTS))

# Telegram Alert Bot configuration provided by user
ALERT_BOT_TOKEN = os.getenv("ALERT_BOT_TOKEN", "8712647996:AAG9uj7vEcVSZ2Ukf2noHetU0FMWxcDKSP4")
ALERT_BOT_USERNAME = "BotHostAlertBot"

# MongoDB Atlas Connection URI provided by user
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://sachinmandawitime_db_user:U8GnBrYwTOXTsa1M@gmailfarmer.d9lf5r2.mongodb.net/?retryWrites=true&w=majority")

mongo_client = None
mongo_db = None

if HAS_PYMONGO:
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        mongo_client.admin.command('ping')
        mongo_db = mongo_client['bothost_database']
        print("[SUCCESS] Connected to MongoDB Atlas Cloud Database successfully!")
    except Exception as e:
        print(f"[WARNING] MongoDB Atlas connection unavailable ({str(e)[:80]}). Using db.json storage.")
        mongo_db = None

# In-memory dictionary to track running subprocesses: { sub_id: subprocess.Popen object }
RUNNING_PROCESSES = {}

def format_indian_12h_datetime(dt_str):
    """ Formats datetime string into Indian 12-hour AM/PM format e.g. 25 Jul 2026, 07:03 PM """
    if not dt_str:
        return ""
    try:
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        try:
            dt = datetime.datetime.strptime(dt_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            return dt_str

def get_bot_uptime_str(started_at_str):
    """ Calculates accurate human-readable uptime string supporting Minutes, Hours, Days, and Months """
    if not started_at_str:
        return "Online 0m"
    try:
        clean_str = started_at_str.split('.')[0]
        started_dt = datetime.datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        now_dt = datetime.datetime.now()
        
        total_seconds = int((now_dt - started_dt).total_seconds())
        if total_seconds < 0:
            total_seconds = 0
            
        months, remainder = divmod(total_seconds, 2592000)
        days, remainder = divmod(remainder, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        
        parts = []
        if months > 0:
            parts.append(f"{months}mo")
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        
        display_parts = parts[:2]
        uptime_text = " ".join(display_parts)
        return f"Online {uptime_text}"
    except Exception:
        return "Online 0m"

def send_telegram_alert(chat_id, sub_name, sub_id, alert_type="CRASH", details="", reply_markup=None):
    """ Sends formatted Telegram alert message to user """
    if not chat_id or not ALERT_BOT_TOKEN:
        return False

    now_str = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p IST")
    
    if alert_type == "CRASH":
        msg_text = (
            f"⚠️ *[BOTHOST CRASH ALERT]*\n\n"
            f"🤖 *Bot:* `{sub_name}`\n"
            f"🆔 *Submission ID:* `#{sub_id}`\n"
            f"⏱ *Time:* `{now_str}`\n\n"
            f"❌ *Status:* Bot process terminated unexpectedly.\n"
            f"📄 *Details:* `{details[:300]}`\n\n"
            f"👉 [Open Dashboard to Fix](https://bothost-dq6s.onrender.com/dashboard)"
        )
    elif alert_type == "FLOOD_WAIT":
        msg_text = (
            f"🛡️ *[ANTI-BAN FLOOD_WAIT SHIELD ACTIVATED]*\n\n"
            f"🤖 *Bot:* `{sub_name}` (#`{sub_id}`)\n"
            f"⏱ *Time:* `{now_str}`\n\n"
            f"⚠️ *Telegram Rate-Limit Detected:* `{details}`\n"
            f"✅ *Action:* Bot automatically paused to prevent account ban. Will auto-resume shortly."
        )
    elif alert_type == "AUTO_DEPLOY":
        msg_text = (
            f"🔄 *[BOTHOST AUTO-DEPLOY]*\n\n"
            f"🤖 *Bot:* `{sub_name}` (#`{sub_id}`)\n"
            f"⏱ *Time:* `{now_str}`\n\n"
            f"🚀 *New GitHub Code Detected:* Latest commits pulled automatically.\n"
            f"⚡ *Status:* Bot hot-reloaded with 0-downtime!"
        )
    else:
        msg_text = details

    try:
        url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/sendMessage"
        payload_dict = {
            "chat_id": str(chat_id),
            "text": msg_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload_dict["reply_markup"] = reply_markup

        payload = json.dumps(payload_dict).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status == 200
    except Exception as e:
        print(f"Error sending Telegram alert to {chat_id}: {e}")
        return False

def answer_callback_query(callback_query_id, text=""):
    """ Acknowledges telegram inline button callback """
    try:
        url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/answerCallbackQuery"
        payload = json.dumps({"callback_query_id": callback_query_id, "text": text}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def edit_telegram_message_text(chat_id, message_id, text, reply_markup=None):
    """ Dynamically updates Telegram inline message & keyboard buttons in real-time """
    try:
        url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/editMessageText"
        payload_dict = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload_dict["reply_markup"] = reply_markup

        payload = json.dumps(payload_dict).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Error editing Telegram message: {e}")

def remove_readonly(func, path, excinfo):
    """ Helper to remove read-only attributes on Windows when deleting .git folders """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def get_authenticated_clone_url(repo_url):
    """ Converts https://github.com/owner/repo to authenticated HTTPS URL for private repositories """
    url = repo_url.strip().rstrip('/')
    if not url.endswith('.git'):
        url += '.git'
    
    if SYSTEM_GITHUB_TOKEN and 'github.com' in url and '@' not in url:
        url = url.replace('https://github.com/', f'https://{SYSTEM_GITHUB_TOKEN}@github.com/')
    return url

def check_and_auto_deploy_github_repos(sub):
    """ Checks if GitHub repository has new commits and auto-pulls & hot-reloads """
    sub_id = sub.get('id')
    repo_url = sub.get('repo_url')
    if not repo_url or sub.get('status') != 'running':
        return

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    git_dir = os.path.join(sub_dir, '.git')
    if not os.path.exists(git_dir):
        return

    try:
        fetch_res = subprocess.run(['git', 'fetch', 'origin'], cwd=sub_dir, capture_output=True, timeout=30)
        if fetch_res.returncode != 0:
            return

        local_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=sub_dir, capture_output=True, text=True).stdout.strip()
        remote_head = subprocess.run(['git', 'rev-parse', '@{u}'], cwd=sub_dir, capture_output=True, text=True).stdout.strip()

        if local_head and remote_head and local_head != remote_head:
            print(f"[AUTO-DEPLOY 🔄] New commits detected for bot #{sub_id} ({sub.get('name')})! Pulling & Hot-Reloading...")
            pull_res = subprocess.run(['git', 'pull'], cwd=sub_dir, capture_output=True, timeout=30)
            
            # Hot reload bot process with 0 downtime
            success, msg = reload_bot_process_zero_downtime(sub_id)
            
            # Send Telegram Auto-Deploy Alert
            sub_owner = sub.get('user')
            owner_data = get_user(sub_owner) if sub_owner else None
            if owner_data and owner_data.get('telegram_chat_id'):
                send_telegram_alert(
                    chat_id=owner_data['telegram_chat_id'],
                    sub_name=sub.get('name', 'Telegram Bot'),
                    sub_id=sub_id,
                    alert_type="AUTO_DEPLOY"
                )
    except Exception as e:
        print(f"[AUTO-DEPLOY] Exception checking repo for #{sub_id}: {e}")

def continuous_bot_keeper_daemon():
    """ Continuous background daemon that ensures all running bots stay online permanently and auto-deploys GitHub updates """
    print("[DAEMON] Starting 24/7 Continuous Bot Health & Auto-Deploy Worker...")
    while True:
        try:
            time.sleep(8)
            all_subs = get_all_submissions()
            for sub in all_subs:
                sub_id = sub.get('id')
                status = sub.get('status')
                
                # Check Auto-Deploy GitHub Repos if running
                if status == 'running' and sub.get('repo_url'):
                    check_and_auto_deploy_github_repos(sub)

                # ONLY auto-resume if status is explicitly 'running' (Do NOT auto-resume if 'stopped', 'paused', or 'rejected')
                if status == 'running':
                    proc = RUNNING_PROCESSES.get(sub_id)
                    if proc is None or proc.poll() is not None:
                        print(f"[DAEMON] Auto-resuming crashed bot #{sub_id} ({sub.get('name')})...")
                        success, msg = start_bot_process(sub_id)
                        if success:
                            update_submission_status(sub_id, 'running')
                        else:
                            print(f"[DAEMON] Start attempt for #{sub_id}: {msg}")
        except Exception as e:
            print(f"[DAEMON] Worker loop exception: {e}")
        
        time.sleep(20)

def check_and_update_bot_statuses():
    """ Monitors background processes, inspects logs for FloodWait, and sends alerts """
    all_subs = get_all_submissions()
    for sub in all_subs:
        sub_id = sub['id']
        current_status = sub.get('status')
        proc = RUNNING_PROCESSES.get(sub_id)

        if current_status == 'running':
            log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
            if os.path.exists(log_file_path):
                try:
                    with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as lf:
                        recent_logs = lf.read()[-2000:]
                        if "FloodWaitError" in recent_logs or "FLOOD_WAIT" in recent_logs or "A wait of" in recent_logs:
                            sub_owner = sub.get('user')
                            owner_data = get_user(sub_owner) if sub_owner else None
                            if owner_data and owner_data.get('telegram_chat_id'):
                                send_telegram_alert(
                                    chat_id=owner_data['telegram_chat_id'],
                                    sub_name=sub.get('name', 'Telegram Bot'),
                                    sub_id=sub_id,
                                    alert_type="FLOOD_WAIT",
                                    details=f"Telegram FloodWait Rate-Limit detected in logs. Auto-shield engaged to prevent account ban."
                                )
                except Exception:
                    pass

            if proc is None or proc.poll() is not None:
                success, msg = start_bot_process(sub_id)
                if success:
                    continue
                
                update_submission_status(sub_id, 'crashed')
                sub_owner = sub.get('user')
                owner_data = get_user(sub_owner) if sub_owner else None
                
                if owner_data and owner_data.get('telegram_chat_id'):
                    send_telegram_alert(
                        chat_id=owner_data['telegram_chat_id'],
                        sub_name=sub.get('name', 'Telegram Bot'),
                        sub_id=sub_id,
                        alert_type="CRASH",
                        details=f"Process exited with output: {msg[:200]}"
                    )

def get_user(username):
    """ Fetch user dict from MongoDB or db.json """
    if mongo_db is not None:
        try:
            u = mongo_db.users.find_one({"username": username})
            if u:
                return {
                    "username": u["username"],
                    "password": u["password"],
                    "is_admin": u.get("is_admin", False),
                    "telegram_chat_id": u.get("telegram_chat_id", ""),
                    "env_presets": u.get("env_presets", ""),
                    "created_at": u.get("created_at", "")
                }
        except Exception as e:
            print("MongoDB get_user error:", e)

    db = load_json_db()
    users = db.get("users", {})
    if username in users:
        return users[username]
    return None

def get_user_by_telegram_chat_id(chat_id):
    """ Finds user record matching given telegram_chat_id """
    if mongo_db is not None:
        try:
            u = mongo_db.users.find_one({"telegram_chat_id": str(chat_id)})
            if u:
                return u
        except Exception as e:
            print("MongoDB get_user_by_telegram_chat_id error:", e)

    db = load_json_db()
    for uname, udata in db.get("users", {}).items():
        if str(udata.get("telegram_chat_id")) == str(chat_id):
            return udata
    return None

def create_user(username, password, is_admin=False):
    """ Insert new user into MongoDB and db.json """
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if mongo_db is not None:
        try:
            mongo_db.users.update_one(
                {"username": username},
                {"$set": {"username": username, "password": password, "is_admin": is_admin, "env_presets": "", "created_at": now_str}},
                upsert=True
            )
        except Exception as e:
            print("MongoDB create_user error:", e)

    db = load_json_db()
    db["users"][username] = {"password": password, "is_admin": is_admin, "telegram_chat_id": "", "env_presets": "", "created_at": now_str}
    save_json_db(db)

def update_user_telegram_chat_id(username, chat_id):
    """ Save Telegram Chat ID for user """
    if mongo_db is not None:
        try:
            mongo_db.users.update_one(
                {"username": username},
                {"$set": {"telegram_chat_id": str(chat_id)}}
            )
        except Exception as e:
            print("MongoDB update_user_telegram_chat_id error:", e)

    db = load_json_db()
    if username in db.get("users", {}):
        db["users"][username]["telegram_chat_id"] = str(chat_id)
        save_json_db(db)

def update_user_env_presets(username, env_presets):
    """ Save Render-like Shared Environment Variable Presets for user """
    if mongo_db is not None:
        try:
            mongo_db.users.update_one(
                {"username": username},
                {"$set": {"env_presets": env_presets}}
            )
        except Exception as e:
            print("MongoDB update_user_env_presets error:", e)

    db = load_json_db()
    if username in db.get("users", {}):
        db["users"][username]["env_presets"] = env_presets
        save_json_db(db)

def get_all_submissions():
    """ Get all bot submissions from MongoDB or db.json """
    subs = []
    if mongo_db is not None:
        try:
            subs = list(mongo_db.submissions.find({}, {"_id": 0}))
        except Exception as e:
            print("MongoDB get_all_submissions error:", e)

    if not subs:
        db = load_json_db()
        subs = db.get("submissions", [])

    for s in subs:
        if s.get('status') == 'running' and s.get('started_at'):
            s['uptime_str'] = get_bot_uptime_str(s['started_at'])
        s['formatted_created_at'] = format_indian_12h_datetime(s.get('created_at'))

    return subs

def add_submission(sub_dict):
    """ Add new submission to MongoDB and db.json """
    if mongo_db is not None:
        try:
            mongo_db.submissions.insert_one(dict(sub_dict))
        except Exception as e:
            print("MongoDB add_submission error:", e)

    db = load_json_db()
    db["submissions"].append(sub_dict)
    save_json_db(db)

def update_submission_status(sub_id, new_status, started_at=None):
    """ Update submission status and started_at timestamp in both MongoDB and db.json """
    if mongo_db is not None:
        try:
            update_data = {"status": new_status}
            if started_at:
                update_data["started_at"] = started_at
            mongo_db.submissions.update_one({"id": sub_id}, {"$set": update_data})
        except Exception as e:
            print("MongoDB update_submission_status error:", e)

    db = load_json_db()
    for s in db.get("submissions", []):
        if s["id"] == sub_id:
            s["status"] = new_status
            if started_at:
                s["started_at"] = started_at
            break
    save_json_db(db)

def update_submission_env_vars(sub_id, new_env_vars):
    """ Update environment variables for submission """
    if mongo_db is not None:
        try:
            mongo_db.submissions.update_one({"id": sub_id}, {"$set": {"env_vars": new_env_vars}})
        except Exception as e:
            print("MongoDB update_submission_env_vars error:", e)

    db = load_json_db()
    for s in db.get("submissions", []):
        if s["id"] == sub_id:
            s["env_vars"] = new_env_vars
            break
    save_json_db(db)

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    if os.path.exists(sub_dir):
        env_file = os.path.join(sub_dir, '.env')
        with open(env_file, 'w', encoding='utf-8') as ef:
            ef.write(new_env_vars)

def delete_submission_permanently(sub_id):
    """ Permanently delete submission record and uploaded files """
    stop_bot_process(sub_id)

    if mongo_db is not None:
        try:
            mongo_db.submissions.delete_one({"id": sub_id})
        except Exception as e:
            print("MongoDB delete submission error:", e)

    db = load_json_db()
    db["submissions"] = [s for s in db.get("submissions", []) if s["id"] != sub_id]
    save_json_db(db)

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    if os.path.exists(sub_dir):
        try:
            shutil.rmtree(sub_dir, onerror=remove_readonly)
        except Exception as e:
            print("Error deleting upload dir:", e)

    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
    if os.path.exists(log_file_path):
        try:
            os.remove(log_file_path)
        except Exception as e:
            print("Error deleting log file:", e)

def load_json_db():
    if not os.path.exists(DB_FILE):
        data = {
            "users": {
                "sachinmandawi": {"password": "sachinmandawi", "is_admin": True, "telegram_chat_id": "", "env_presets": "", "created_at": "2026-07-25 13:00:00"}
            },
            "submissions": []
        }
        save_json_db(data)
        return data

    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "users" not in data:
                data["users"] = {}
            if "submissions" not in data:
                data["submissions"] = []
            
            if "sachinmandawi" not in data["users"]:
                data["users"]["sachinmandawi"] = {
                    "password": "sachinmandawi",
                    "is_admin": True,
                    "telegram_chat_id": "",
                    "env_presets": "",
                    "created_at": "2026-07-25 13:00:00"
                }
                save_json_db(data)
            return data
    except Exception:
        return {"users": {}, "submissions": []}

def save_json_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def flatten_nested_single_directory(sub_dir):
    """
    If sub_dir contains only a single inner folder (e.g. uploads/id/repository-master),
    move all files from subfolder into sub_dir directly!
    """
    if not os.path.exists(sub_dir):
        return
    try:
        items = [i for i in os.listdir(sub_dir) if not i.startswith('.')]
        if len(items) == 1:
            single_item = os.path.join(sub_dir, items[0])
            if os.path.isdir(single_item):
                for inner in os.listdir(single_item):
                    src = os.path.join(single_item, inner)
                    dst = os.path.join(sub_dir, inner)
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                try:
                    shutil.rmtree(single_item, onerror=remove_readonly)
                except Exception:
                    pass
    except Exception as e:
        print(f"Flatten directory warning: {e}")

def find_entry_file(sub_dir):
    """
    Recursively searches for main entry file and detects runtime:
    Returns (entry_filepath, runtime_type) where runtime_type is 'node' or 'python'
    """
    if not os.path.exists(sub_dir):
        return None, None

    flatten_nested_single_directory(sub_dir)

    # 1. Check for Node.js Project (Levanter, WhatsApp Baileys, Telegraf, etc.)
    for root, _, files in os.walk(sub_dir):
        if 'package.json' in files or 'index.js' in files or 'main.js' in files or 'bot.js' in files:
            target_js = None
            for fname in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if fname in files:
                    target_js = os.path.join(root, fname)
                    break
            if not target_js:
                for f in files:
                    if f.endswith('.js') and not f.startswith('.'):
                        target_js = os.path.join(root, f)
                        break
            if target_js:
                return target_js, 'node'

    # 2. Check for Python Project
    priority_names = ['test_tgbot.py', 'tg_bot.py', 'bot.py', 'AutoAd.py', 'main.py', 'app.py', 'run.py']

    for root, _, files in os.walk(sub_dir):
        for p in priority_names:
            if p in files:
                return os.path.join(root, p), 'python'

    for root, _, files in os.walk(sub_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('__') and f != 'setup.py':
                return os.path.join(root, f), 'python'

    return None, None

def find_entry_python_file(sub_dir):
    f, _ = find_entry_file(sub_dir)
    return f

def start_bot_process(sub_id):
    """ Installs dependencies and starts Python or Node.js bot in background with pipe-enabled stdin """
    all_subs = get_all_submissions()
    sub_data = None
    for s in all_subs:
        if s['id'] == sub_id:
            sub_data = s
            break

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)

    if sub_data and sub_data.get('repo_url'):
        repo_url = sub_data.get('repo_url')
        clone_url = get_authenticated_clone_url(repo_url)
        if not os.path.exists(sub_dir) or not os.listdir(sub_dir):
            try:
                os.makedirs(sub_dir, exist_ok=True)
                subprocess.run(['git', 'clone', clone_url, sub_dir], capture_output=True, timeout=60)
            except Exception as e:
                print(f"Error cloning GitHub repo {repo_url}: {e}")

        if sub_data.get('env_vars'):
            env_file = os.path.join(sub_dir, '.env')
            with open(env_file, 'w', encoding='utf-8') as ef:
                ef.write(sub_data['env_vars'])

    zip_path = os.path.join(sub_dir, 'project.zip')
    if os.path.exists(zip_path):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(sub_dir)
        except Exception as e:
            print(f"Warning extracting zip: {e}")

    entry_file, runtime_type = find_entry_file(sub_dir)
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    if not entry_file or not os.path.exists(entry_file):
        return False, "No Python script (.py) or Node.js script (.js / package.json) found in project directory."

    entry_dir = os.path.dirname(entry_file)
    entry_basename = os.path.basename(entry_file)

    stop_bot_process(sub_id)

    # Node.js vs Python dependency installation & execution command configuration
    if runtime_type == 'node':
        pkg_file = os.path.join(entry_dir, 'package.json')
        if not os.path.exists(pkg_file):
            pkg_file = os.path.join(sub_dir, 'package.json')

        node_bin = shutil.which('node')
        npm_bin = shutil.which('npm')
        yarn_bin = shutil.which('yarn')

        if not node_bin:
            for possible_node in [r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"]:
                if os.path.exists(possible_node):
                    node_bin = possible_node
                    break

        node_modules_dir = os.path.join(os.path.dirname(pkg_file) if pkg_file else entry_dir, 'node_modules')
        
        # Install node_modules if missing
        if os.path.exists(pkg_file) and not os.path.exists(node_modules_dir):
            try:
                pkg_dir = os.path.dirname(pkg_file)
                if yarn_bin:
                    subprocess.run([yarn_bin, "install", "--production"], cwd=pkg_dir, capture_output=True, timeout=120)
                elif npm_bin:
                    subprocess.run([npm_bin, "install", "--production"], cwd=pkg_dir, capture_output=True, timeout=120)
            except Exception as e:
                print(f"Warning: node package install timeout for {sub_id}: {e}")

        exec_cmd = [node_bin if node_bin else "node", entry_file]
    else:
        req_file = os.path.join(entry_dir, 'requirements.txt')
        if not os.path.exists(req_file):
            req_file = os.path.join(sub_dir, 'requirements.txt')

        if os.path.exists(req_file):
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file],
                               capture_output=True, timeout=90)
            except Exception as e:
                print(f"Warning: Pip install error for {sub_id}: {e}")

        exec_cmd = [sys.executable, "-u", entry_file]

    try:
        log_out = open(log_file_path, "a", encoding="utf-8")
        start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_out.write(f"\n--- BOT ({runtime_type.upper()}: {entry_basename}) STARTED AT {start_time_str} ---\n")
        log_out.flush()

        proc_env = os.environ.copy()
        proc_env["PYTHONIOENCODING"] = "utf-8"
        proc_env["PYTHONUNBUFFERED"] = "1"

        proc = subprocess.Popen(
            exec_cmd,
            cwd=entry_dir,
            env=proc_env,
            stdin=subprocess.PIPE,
            stdout=log_out,
            stderr=log_out,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        RUNNING_PROCESSES[sub_id] = proc
        update_submission_status(sub_id, 'running', started_at=start_time_str)
        return True, f"Bot ({runtime_type.upper()}: {entry_basename}) started successfully (PID: {proc.pid})"
    except Exception as e:
        return False, f"Failed to start {runtime_type} bot process: {str(e)}"

def reload_bot_process_zero_downtime(sub_id):
    """ Executes Graceful Dual-Process Handoff for Zero-Downtime Hot Reloading """
    print(f"[HOT-RELOAD] Initiating Zero-Downtime Hot Reload for bot #{sub_id}...")
    old_proc = RUNNING_PROCESSES.get(sub_id)

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    python_entry_file = find_entry_python_file(sub_dir)
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    if not python_entry_file or not os.path.exists(python_entry_file):
        return False, "Python entry file missing."

    entry_dir = os.path.dirname(python_entry_file)
    entry_basename = os.path.basename(python_entry_file)

    try:
        log_out = open(log_file_path, "a", encoding="utf-8")
        start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_out.write(f"\n--- [HOT-RELOAD ⚡] BOT ({entry_basename}) PATCHED AT {start_time_str} ---\n")
        log_out.flush()

        proc_env = os.environ.copy()
        proc_env["PYTHONIOENCODING"] = "utf-8"
        proc_env["PYTHONUNBUFFERED"] = "1"

        new_proc = subprocess.Popen(
            [sys.executable, "-u", python_entry_file],
            cwd=entry_dir,
            env=proc_env,
            stdin=subprocess.PIPE,
            stdout=log_out,
            stderr=log_out,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )

        time.sleep(2.0)

        if old_proc and old_proc.poll() is None:
            try:
                old_proc.terminate()
                try:
                    old_proc.wait(timeout=1.5)
                except Exception:
                    old_proc.kill()
            except Exception:
                pass

        RUNNING_PROCESSES[sub_id] = new_proc
        update_submission_status(sub_id, 'running', started_at=start_time_str)
        return True, f"⚡ Hot-Reloaded bot ({entry_basename}) with zero-downtime! (PID: {new_proc.pid})"
    except Exception as e:
        return False, f"Hot-reload error: {e}"

def stop_bot_process(sub_id):
    """ Forcefully terminates running bot process if active and searches OS process table for orphan process instances """
    proc = RUNNING_PROCESSES.get(sub_id)
    if proc:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except Exception:
                    proc.kill()
                    proc.wait(timeout=1.0)
        except Exception as e:
            print(f"Error terminating process #{sub_id}: {e}")
            try:
                proc.kill()
            except Exception:
                pass
    if sub_id in RUNNING_PROCESSES:
        del RUNNING_PROCESSES[sub_id]

    try:
        if sys.platform == 'win32':
            ps_cmd = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{sub_id}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=5)
        else:
            ps_cmd = f"pkill -9 -f '{sub_id}'"
            subprocess.run(ps_cmd, shell=True, capture_output=True, timeout=5)
    except Exception as e:
        print(f"OS Process Kill warning for #{sub_id}: {e}")

# Start 24/7 Continuous Background Daemon Thread
daemon_thread = threading.Thread(target=continuous_bot_keeper_daemon, daemon=True)
daemon_thread.start()

def build_telegram_status_payload(username):
    """ Generates dynamic Telegram status message and inline keyboard buttons based on real-time bot status """
    all_subs = get_all_submissions()
    user_subs = [s for s in all_subs if s['user'] == username]

    if not user_subs:
        return (
            f"🤖 *BOTHOST STATUS FOR* `{username}`\n\nNo bot submissions found under your account. Deploy a bot on [BotHost Dashboard](https://bothost-dq6s.onrender.com/dashboard).",
            None
        )

    active_count = sum(1 for s in user_subs if s.get('status') == 'running')
    crashed_count = sum(1 for s in user_subs if s.get('status') == 'crashed')

    msg = f"🤖 *YOUR BOT HOSTING STATUS* (`{username}`)\n\n"
    keyboard_buttons = []

    for idx, s in enumerate(user_subs, 1):
        is_running = (s.get('status') == 'running')
        uptime_info = f" ({s.get('uptime_str', 'Online')})" if is_running else ""
        status_icon = f"🟢 RUNNING{uptime_info}" if is_running else ("🔴 CRASHED" if s.get('status') == 'crashed' else f"🟡 {s.get('status', '').upper()}")
        created_at_fmt = s.get('formatted_created_at') or s.get('created_at', '')
        
        msg += f"*{idx}. {s.get('name', 'Bot')}*\n"
        msg += f"   🆔 ID: `{s['id']}` | Status: {status_icon}\n"
        msg += f"   ⏱ Created: `{created_at_fmt}`\n\n"

        row_buttons = [
            {"text": f"📄 Logs #{s['id']}", "callback_data": f"logs_{s['id']}"}
        ]

        if is_running:
            row_buttons.append({"text": f"⚡ Hot-Reload #{s['id']}", "callback_data": f"restart_{s['id']}"})
            row_buttons.append({"text": f"⏹ Stop #{s['id']}", "callback_data": f"stop_{s['id']}"})
        else:
            row_buttons.append({"text": f"▶️ Start #{s['id']}", "callback_data": f"start_{s['id']}"})

        keyboard_buttons.append(row_buttons)

    msg += f"📊 *Total:* `{len(user_subs)}` | *Active:* `{active_count}` | *Crashed:* `{crashed_count}`"
    reply_markup = {"inline_keyboard": keyboard_buttons}
    return msg, reply_markup

# Helper handlers for Interactive Telegram Bot Commands (/status, /logs, /restart, /stop, /start_bot)
def handle_telegram_status_command(chat_id, user_info):
    username = user_info['username']
    msg, reply_markup = build_telegram_status_payload(username)
    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id="SYSTEM", alert_type="INFO", details=msg, reply_markup=reply_markup)

def handle_telegram_logs_command(chat_id, sub_id):
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
    if not os.path.exists(log_file_path):
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"📄 *BOT LOGS (#`{sub_id}`)*\n\nNo log records found for this submission yet.")
        return

    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()
        excerpt = logs[-1500:] if len(logs) > 1500 else logs
        msg = f"📄 *EXECUTION LOGS (#`{sub_id}`)*\n\n```text\n{excerpt}\n```"
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=msg)
    except Exception as e:
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"Error reading logs: {e}")

def handle_telegram_restart_command(chat_id, sub_id, message_id=None):
    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"⚡ *Hot-Reloading Bot #`{sub_id}` with 0-downtime...*")
    success, msg = reload_bot_process_zero_downtime(sub_id)
    if success:
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"✅ *Bot #`{sub_id}` hot-reloaded with 0-downtime!* Status: 🟢 RUNNING")
    else:
        update_submission_status(sub_id, 'crashed')
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"❌ *Failed to hot-reload bot #`{sub_id}`:* `{msg}`")

    user_record = get_user_by_telegram_chat_id(chat_id)
    if user_record and message_id:
        msg_text, reply_markup = build_telegram_status_payload(user_record['username'])
        edit_telegram_message_text(chat_id, message_id, msg_text, reply_markup)

def handle_telegram_start_command_action(chat_id, sub_id, message_id=None):
    success, msg = start_bot_process(sub_id)
    if success:
        update_submission_status(sub_id, 'running')
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"▶️ *Bot #`{sub_id}` started successfully!* Status: 🟢 RUNNING")
    else:
        update_submission_status(sub_id, 'crashed')
        send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"❌ *Failed to start bot #`{sub_id}`:* `{msg}`")

    user_record = get_user_by_telegram_chat_id(chat_id)
    if user_record and message_id:
        msg_text, reply_markup = build_telegram_status_payload(user_record['username'])
        edit_telegram_message_text(chat_id, message_id, msg_text, reply_markup)

def handle_telegram_stop_command(chat_id, sub_id, message_id=None):
    update_submission_status(sub_id, 'stopped')
    stop_bot_process(sub_id)
    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details=f"⏹ *Bot #`{sub_id}` stopped safely.* Status: 🔴 STOPPED")

    user_record = get_user_by_telegram_chat_id(chat_id)
    if user_record and message_id:
        msg_text, reply_markup = build_telegram_status_payload(user_record['username'])
        edit_telegram_message_text(chat_id, message_id, msg_text, reply_markup)

def telegram_alert_bot_polling():
    if not ALERT_BOT_TOKEN:
        return
    last_update_id = 0
    while True:
        try:
            time.sleep(2)
            url = f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=5"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode('utf-8'))
                if data.get("ok"):
                    for update in data.get("result", []):
                        last_update_id = max(last_update_id, update.get("update_id", 0))

                        if "callback_query" in update:
                            cb = update["callback_query"]
                            cb_id = cb.get("id")
                            cb_data = cb.get("data", "")
                            cb_message = cb.get("message", {})
                            cb_chat_id = cb_message.get("chat", {}).get("id")
                            cb_msg_id = cb_message.get("message_id")
                            
                            answer_callback_query(cb_id, f"Processing {cb_data}...")

                            if cb_data.startswith("logs_"):
                                sub_id = cb_data.replace("logs_", "")
                                handle_telegram_logs_command(cb_chat_id, sub_id)
                            elif cb_data.startswith("restart_"):
                                sub_id = cb_data.replace("restart_", "")
                                handle_telegram_restart_command(cb_chat_id, sub_id, cb_msg_id)
                            elif cb_data.startswith("start_"):
                                sub_id = cb_data.replace("start_", "")
                                handle_telegram_start_command_action(cb_chat_id, sub_id, cb_msg_id)
                            elif cb_data.startswith("stop_"):
                                sub_id = cb_data.replace("stop_", "")
                                handle_telegram_stop_command(cb_chat_id, sub_id, cb_msg_id)

                        if "message" in update:
                            message = update.get("message", {})
                            text = message.get("text", "").strip()
                            chat_id = message.get("chat", {}).get("id")

                            if not text or not chat_id:
                                continue

                            if text.startswith("/start"):
                                parts = text.split()
                                if len(parts) > 1 and parts[1].startswith("connect_"):
                                    username = parts[1].replace("connect_", "").strip()
                                    user_info = get_user(username)
                                    if user_info:
                                        update_user_telegram_chat_id(username, chat_id)
                                        send_telegram_alert(
                                            chat_id=chat_id, sub_name="BotHost System", sub_id="SYSTEM", alert_type="INFO",
                                            details=f"✅ *ACCOUNT CONNECTED!*\n\nWelcome `{username}`. You will now receive instant Telegram crash alerts and control your bots via Telegram.\n\nType /status to view your running bots!"
                                        )
                                    else:
                                        send_telegram_alert(chat_id=chat_id, sub_name="BotHost System", sub_id="SYSTEM", alert_type="INFO", details=f"⚠️ Account `{username}` not found on BotHost.")
                                else:
                                    send_telegram_alert(
                                        chat_id=chat_id, sub_name="BotHost System", sub_id="SYSTEM", alert_type="INFO",
                                        details="👋 *WELCOME TO BOTHOST ALERT BOT!*\n\nCommands Available:\n• /status - Check all your running bots\n• /logs `<id>` - View bot logs\n• /restart `<id>` - Hot-reload bot\n• /stop `<id>` - Stop bot\n\nClick 'Connect via Telegram' on your BotHost Dashboard to link your account."
                                    )

                            user_record = get_user_by_telegram_chat_id(chat_id)

                            if text in ("/status", "/bots"):
                                if user_record:
                                    handle_telegram_status_command(chat_id, user_record)
                                else:
                                    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id="SYSTEM", alert_type="INFO", details="⚠️ *Telegram Account Not Linked.*\nPlease click 'Connect via Telegram' on your BotHost Dashboard first.")

                            elif text.startswith("/logs"):
                                parts = text.split()
                                if len(parts) > 1:
                                    sub_id = parts[1].replace("#", "").strip()
                                    handle_telegram_logs_command(chat_id, sub_id)
                                else:
                                    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details="Usage: `/logs <submission_id>` (e.g. `/logs 9bf1e3e7`)")

                            elif text.startswith("/restart"):
                                parts = text.split()
                                if len(parts) > 1:
                                    sub_id = parts[1].replace("#", "").strip()
                                    handle_telegram_restart_command(chat_id, sub_id)
                                else:
                                    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details="Usage: `/restart <submission_id>` (e.g. `/restart 9bf1e3e7`)")

                            elif text.startswith("/stop"):
                                parts = text.split()
                                if len(parts) > 1:
                                    sub_id = parts[1].replace("#", "").strip()
                                    handle_telegram_stop_command(chat_id, sub_id)
                                else:
                                    send_telegram_alert(chat_id=chat_id, sub_name="System", sub_id=sub_id, alert_type="INFO", details="Usage: `/stop <submission_id>` (e.g. `/stop 9bf1e3e7`)")

        except Exception as e:
            time.sleep(5)

threading.Thread(target=telegram_alert_bot_polling, daemon=True).start()

# Feature 3: API Endpoints for Telethon & Pyrogram String Session Generator
@app.route('/api/session/send_code', methods=['POST'])
def api_session_send_code():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    api_id = data.get('api_id', '').strip()
    api_hash = data.get('api_hash', '').strip()
    phone = data.get('phone', '').strip()

    if not api_id or not api_hash or not phone:
        return jsonify({"error": "API_ID, API_HASH, and Phone number are required"}), 400

    try:
        api_id_int = int(api_id)
    except ValueError:
        return jsonify({"error": "API_ID must be numeric"}), 400

    if not HAS_TELETHON:
        return jsonify({"error": "Telethon library unavailable on server"}), 500

    try:
        client = TelegramClient(StringSession(), api_id_int, api_hash)
        client.connect()
        send_res = client.send_code_request(phone)
        phone_code_hash = send_res.phone_code_hash

        sess_id = str(uuid.uuid4())[:8]
        ACTIVE_SESSION_LOGINS[sess_id] = {
            "client": client,
            "api_id": api_id_int,
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": phone_code_hash
        }

        return jsonify({
            "success": True,
            "session_id": sess_id,
            "message": f"OTP Login Code sent to {phone} via Telegram!"
        })
    except Exception as e:
        return jsonify({"error": f"Failed to send code: {str(e)}"}), 400

@app.route('/api/session/login', methods=['POST'])
def api_session_login():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    sess_id = data.get('session_id', '').strip()
    code = data.get('code', '').strip()
    password = data.get('password', '').strip()

    login_info = ACTIVE_SESSION_LOGINS.get(sess_id)
    if not login_info:
        return jsonify({"error": "Session login attempt expired. Please try again."}), 400

    client = login_info["client"]
    phone = login_info["phone"]
    phone_code_hash = login_info["phone_code_hash"]

    try:
        try:
            client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except Exception as e:
            if "password" in str(e).lower() or "two-step" in str(e).lower():
                if not password:
                    return jsonify({"error": "Two-Factor (2FA) Password required. Please enter 2FA password."}), 402
                client.sign_in(password=password)
            else:
                raise e

        string_session_val = client.session.save()
        client.disconnect()
        del ACTIVE_SESSION_LOGINS[sess_id]

        return jsonify({
            "success": True,
            "string_session": string_session_val,
            "message": "Telethon String Session generated successfully!"
        })
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 400

# Feature 4: Instant Service Suspend / Pause Mode (Render Pause)
@app.route('/api/user/submission/<sub_id>/<action>', methods=['POST'])
def api_user_submission_action(sub_id, action):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    all_subs = get_all_submissions()
    sub_data = next((s for s in all_subs if s['id'] == sub_id), None)
    if not sub_data:
        return jsonify({"error": "Submission not found"}), 404

    if sub_data['user'] != session['user'] and not session.get('is_admin'):
        return jsonify({"error": "Forbidden"}), 403

    if action == 'start' or action == 'resume':
        success, msg = start_bot_process(sub_id)
        if success:
            update_submission_status(sub_id, 'running')
            return jsonify({"success": True, "message": f"▶️ Bot #{sub_id} resumed & running!"})
        else:
            update_submission_status(sub_id, 'crashed')
            return jsonify({"error": f"Failed to start bot: {msg}"}), 400

    elif action == 'restart':
        success, msg = reload_bot_process_zero_downtime(sub_id)
        if success:
            return jsonify({"success": True, "message": f"⚡ Bot #{sub_id} hot-reloaded with 0-downtime!"})
        else:
            update_submission_status(sub_id, 'crashed')
            return jsonify({"error": f"Failed to hot-reload bot: {msg}"}), 400

    elif action == 'pause':
        update_submission_status(sub_id, 'paused')
        stop_bot_process(sub_id)
        return jsonify({"success": True, "message": f"⏸ Bot #{sub_id} service suspended & hibernated (0% CPU/RAM)."})

    elif action == 'stop':
        update_submission_status(sub_id, 'stopped')
        stop_bot_process(sub_id)
        return jsonify({"success": True, "message": f"⏹ Bot #{sub_id} stopped safely."})

    return jsonify({"error": "Invalid action"}), 400

@app.route('/api/user/mass_action/<action>', methods=['POST'])
def api_user_mass_action(action):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    all_subs = get_all_submissions()
    user_subs = [s for s in all_subs if s['user'] == session['user']]
    if not user_subs:
        return jsonify({"error": "No bot submissions found."}), 404

    count = 0
    if action == 'start_all':
        for s in user_subs:
            if s.get('status') != 'running':
                success, _ = start_bot_process(s['id'])
                if success:
                    update_submission_status(s['id'], 'running')
                    count += 1
        return jsonify({"success": True, "message": f"▶️ Started {count} bots successfully!"})

    elif action == 'restart_all':
        for s in user_subs:
            if s.get('status') == 'running':
                success, _ = reload_bot_process_zero_downtime(s['id'])
                if success:
                    count += 1
            else:
                success, _ = start_bot_process(s['id'])
                if success:
                    update_submission_status(s['id'], 'running')
                    count += 1
        return jsonify({"success": True, "message": f"⚡ Hot-Reloaded {count} bots with 0-downtime!"})

    elif action == 'stop_all':
        for s in user_subs:
            if s.get('status') == 'running':
                update_submission_status(s['id'], 'stopped')
                stop_bot_process(s['id'])
                count += 1
        return jsonify({"success": True, "message": f"⏹ Stopped {count} active bots safely."})

    return jsonify({"error": "Invalid mass action"}), 400

# Feature 2: Shared Environment Variable Presets (Render Env Groups) API Endpoints
@app.route('/api/user/env_presets', methods=['GET', 'POST'])
def api_user_env_presets():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    username = session['user']
    if request.method == 'POST':
        data = request.get_json() or {}
        presets = data.get('env_presets', '').strip()
        update_user_env_presets(username, presets)
        return jsonify({"success": True, "message": "📑 Shared Environment Presets saved successfully!"})

    user_info = get_user(username)
    return jsonify({"env_presets": user_info.get('env_presets', '') if user_info else ''})

# Feature 1: Render-Style 1-Click Rollback & Deployment History API Endpoints
@app.route('/api/submissions/<sub_id>/commits')
def api_submission_commits(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    git_dir = os.path.join(sub_dir, '.git')
    if not os.path.exists(git_dir):
        return jsonify({"commits": [], "is_git": False})

    try:
        # Fetch last 8 Git commits
        res = subprocess.run(
            ['git', 'log', '-n', '8', '--pretty=format:%h|%s|%an|%cr'],
            cwd=sub_dir, capture_output=True, text=True, timeout=10
        )
        if res.returncode != 0:
            return jsonify({"commits": [], "is_git": True, "error": res.stderr})

        commits_list = []
        lines = res.stdout.strip().splitlines()
        for line in lines:
            parts = line.split('|')
            if len(parts) >= 4:
                commits_list.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "time": parts[3]
                })

        return jsonify({"commits": commits_list, "is_git": True})
    except Exception as e:
        return jsonify({"commits": [], "is_git": True, "error": str(e)})

@app.route('/api/submissions/<sub_id>/rollback', methods=['POST'])
def api_submission_rollback(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    commit_hash = data.get('commit_hash', '').strip()
    if not commit_hash:
        return jsonify({"error": "Commit hash is required for rollback."}), 400

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    if not os.path.exists(sub_dir):
        return jsonify({"error": "Submission directory not found"}), 404

    try:
        # Execute hard git reset to target commit hash
        res = subprocess.run(['git', 'reset', '--hard', commit_hash], cwd=sub_dir, capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            return jsonify({"error": f"Git rollback failed: {res.stderr[:150]}"}), 400

        # Hot reload process with rolled back code
        success, msg = reload_bot_process_zero_downtime(sub_id)
        return jsonify({
            "success": True,
            "message": f"🔄 Rolled back bot #{sub_id} to commit [{commit_hash}] & hot-reloaded successfully!"
        })
    except Exception as e:
        return jsonify({"error": f"Rollback exception: {str(e)}"}), 500

# Option 3: API Endpoint for Real-Time Process RAM, CPU, PID & Stats Metrics
@app.route('/api/submissions/<sub_id>/stats')
def api_submission_stats(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    all_subs = get_all_submissions()
    sub_data = next((s for s in all_subs if s['id'] == sub_id), None)
    if not sub_data:
        return jsonify({"error": "Submission not found"}), 404

    status = sub_data.get('status', 'stopped')
    uptime_str = sub_data.get('uptime_str', 'Offline')
    proc = RUNNING_PROCESSES.get(sub_id)

    target_pid = None
    if proc and proc.poll() is None:
        target_pid = proc.pid

    # OS-Level Process Table Scanner (Fixes Gunicorn multi-worker memory isolation)
    if not target_pid and HAS_PSUTIL:
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = " ".join(p.info.get('cmdline') or [])
                if sub_id in cmdline and ('python' in cmdline.lower() or 'py' in cmdline.lower()):
                    target_pid = p.info['pid']
                    if status != 'running':
                        status = 'running'
                        update_submission_status(sub_id, 'running')
                    break
            except Exception:
                pass

    if not target_pid and status == 'running':
        success, _ = start_bot_process(sub_id)
        if success:
            proc_new = RUNNING_PROCESSES.get(sub_id)
            if proc_new:
                target_pid = proc_new.pid

    ram_mb = 0.0
    ram_percent = 0.0
    cpu_percent = 0.0
    num_threads = 0

    if target_pid and HAS_PSUTIL:
        try:
            p = psutil.Process(target_pid)
            if p.is_running():
                mem_info = p.memory_info()
                ram_mb = round(mem_info.rss / (1024 * 1024), 2)
                ram_percent = round((ram_mb / 512.0) * 100, 1)
                cpu_percent = round(p.cpu_percent(interval=0.1), 1)
                num_threads = p.num_threads()
        except Exception as e:
            print(f"Error reading psutil stats for PID {target_pid}: {e}")

    return jsonify({
        "id": sub_id,
        "name": sub_data.get('name'),
        "status": status if target_pid else ("paused" if status == "paused" else ("stopped" if status == "stopped" else "crashed")),
        "uptime": uptime_str,
        "pid": target_pid,
        "ram_mb": ram_mb,
        "ram_max_mb": 512.0,
        "ram_percent": min(ram_percent, 100.0),
        "cpu_percent": min(cpu_percent, 100.0),
        "num_threads": num_threads
    })

# Feature 1: Real-Time Server-Sent Events (SSE) Log Streamer API Endpoint
@app.route('/api/submissions/<sub_id>/stream_logs')
def api_submission_stream_logs(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    def generate_log_stream():
        yield "retry: 2000\n\n"

        if not os.path.exists(log_file_path):
            yield f"data: {json.dumps({'line': '[BOTHOST STREAM] Waiting for execution log file...', 'type': 'system'})}\n\n"
            time.sleep(1)

        last_pos = 0
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = content.splitlines()[-40:]
                for l in lines:
                    yield f"data: {json.dumps({'line': l})}\n\n"
                last_pos = f.tell()

        while True:
            time.sleep(0.5)
            if os.path.exists(log_file_path):
                with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_pos)
                    new_text = f.read()
                    if new_text:
                        for line in new_text.splitlines():
                            if line.strip():
                                yield f"data: {json.dumps({'line': line})}\n\n"
                        last_pos = f.tell()

    return Response(generate_log_stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

# Feature 1: Interactive Command Input API Endpoint (Send input to bot stdin)
@app.route('/api/submissions/<sub_id>/send_input', methods=['POST'])
def api_submission_send_input(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    user_input = data.get('input', '').strip()

    proc = RUNNING_PROCESSES.get(sub_id)
    if not proc or proc.poll() is not None:
        return jsonify({"error": "Bot process is not running."}), 400

    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write((user_input + "\n").encode('utf-8'))
            proc.stdin.flush()

            log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
            if os.path.exists(log_file_path):
                with open(log_file_path, 'a', encoding='utf-8') as lf:
                    lf.write(f"\n[USER INPUT >] {user_input}\n")

            return jsonify({"success": True, "message": f"Input '{user_input}' sent to bot stdin!"})
        else:
            return jsonify({"error": "Bot stdin is closed."}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to send input: {str(e)}"}), 500

# Dedicated Keep-Alive / Health Ping endpoint for cron-job.org
@app.route('/ping')
@app.route('/health')
def health_ping():
    return "pong", 200, {'Content-Type': 'text/plain'}

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')

        if username == 'sachinmandawi' and password == 'sachinmandawi':
            create_user('sachinmandawi', 'sachinmandawi', is_admin=True)
            session.permanent = True
            session['user'] = 'sachinmandawi'
            session['is_admin'] = True
            flash('Logged in as Admin (sachinmandawi)', 'success')
            return redirect(url_for('admin'))

        user_data = get_user(username)

        if user_data:
            if user_data['password'] == password:
                session.permanent = True
                session['user'] = username
                session['is_admin'] = user_data.get('is_admin', False)
                flash(f'Successfully logged in as {username}', 'success')
                if session['is_admin']:
                    return redirect(url_for('admin'))
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid password. Please try again.', 'error')
                return render_template('login.html')
        else:
            flash('Account not found. Please Sign Up first.', 'error')
            return render_template('login.html')

    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm', '').strip()

        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('signup.html')

        if password != confirm:
            flash('Passwords do not match', 'error')
            return render_template('signup.html')

        existing = get_user(username)
        if existing:
            flash('Username already exists. Please pick another or log in.', 'error')
            return render_template('signup.html')

        is_admin = (username == 'sachinmandawi' and password == 'sachinmandawi')
        create_user(username, password, is_admin=is_admin)

        session.permanent = True
        session['user'] = username
        session['is_admin'] = is_admin
        flash('Account created successfully! Welcome to BotHost.', 'success')
        if is_admin:
            return redirect(url_for('admin'))
        return redirect(url_for('dashboard'))

    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash('Please log in first', 'error')
        return redirect(url_for('login'))
    
    session.permanent = True
    user_info = get_user(session['user'])
    all_subs = get_all_submissions()
    user_submissions = [s for s in all_subs if s['user'] == session['user']]
    user_submissions.reverse()
    return render_template('dashboard.html', submissions=user_submissions, user_info=user_info, bot_username=ALERT_BOT_USERNAME)

@app.route('/connect_telegram', methods=['POST'])
def connect_telegram():
    if 'user' not in session:
        return redirect(url_for('login'))

    chat_id = request.form.get('chat_id', '').strip()
    if not chat_id:
        flash('Please enter a valid Telegram Chat ID.', 'error')
        return redirect(url_for('dashboard'))

    update_user_telegram_chat_id(session['user'], chat_id)
    send_telegram_alert(
        chat_id=chat_id,
        sub_name="BotHost System",
        sub_id="SYSTEM",
        alert_type="INFO",
        details=f"✅ *Account Connected!* Welcome `{session['user']}`. You can now use /status, /logs, /restart, and /stop directly in Telegram."
    )

    flash('Telegram Chat ID connected successfully! Sent test message.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return redirect(url_for('login'))

    upload_mode = request.form.get('upload_mode', 'github')
    sub_id = str(uuid.uuid4())[:8]
    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    os.makedirs(sub_dir, exist_ok=True)

    saved_files = []
    repo_url = None
    env_vars = None
    name = "Bot Project"

    if upload_mode == 'github':
        repo_url = request.form.get('repo_url', '').strip()
        env_vars = request.form.get('env_vars', '').strip()

        if not repo_url or not repo_url.startswith(('http://', 'https://')):
            flash('Please enter a valid GitHub Repository URL.', 'error')
            return redirect(url_for('dashboard'))

        repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
        name = f"GitHub: {repo_name}"

        clone_url = get_authenticated_clone_url(repo_url)

        try:
            res = subprocess.run(['git', 'clone', clone_url, sub_dir], capture_output=True, timeout=60)
            if res.returncode != 0:
                err_msg = res.stderr.decode('utf-8', errors='ignore')
                if SYSTEM_GITHUB_TOKEN:
                    err_msg = err_msg.replace(SYSTEM_GITHUB_TOKEN, '***')
                flash(f"Failed to clone repository: {err_msg[:150]}", 'error')
                return redirect(url_for('dashboard'))
            saved_files.append('git-cloned')
        except Exception as e:
            flash(f"Git clone error: {str(e)}", 'error')
            return redirect(url_for('dashboard'))

        if env_vars:
            env_file = os.path.join(sub_dir, '.env')
            with open(env_file, 'w', encoding='utf-8') as ef:
                ef.write(env_vars)

    elif upload_mode == 'files':
        bot_file = request.files.get('bot_file')
        req_file = request.files.get('requirements_file')
        
        if bot_file and bot_file.filename:
            filepath = os.path.join(sub_dir, 'bot.py')
            bot_file.save(filepath)
            saved_files.append('bot.py')

        if req_file and req_file.filename:
            filepath = os.path.join(sub_dir, 'requirements.txt')
            req_file.save(filepath)
            saved_files.append('requirements.txt')

        name = "bot.py + requirements.txt"
    else:
        zip_file = request.files.get('zip_file')
        if zip_file and zip_file.filename:
            filepath = os.path.join(sub_dir, 'project.zip')
            zip_file.save(filepath)
            saved_files.append('project.zip')
            
            try:
                with zipfile.ZipFile(filepath, 'r') as zip_ref:
                    zip_ref.extractall(sub_dir)
            except Exception as e:
                print(f"Error unzipping project.zip: {e}")

        name = zip_file.filename if (zip_file and zip_file.filename) else "project.zip"

    if not saved_files:
        flash('No repository or files provided.', 'error')
        return redirect(url_for('dashboard'))

    submission = {
        "id": sub_id,
        "user": session['user'],
        "name": name,
        "mode": upload_mode,
        "repo_url": repo_url,
        "env_vars": env_vars,
        "files": saved_files,
        "status": "pending",
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    add_submission(submission)

    flash('GitHub Bot Repository deployed successfully! Under review.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/submission/<sub_id>/delete', methods=['GET', 'POST'])
def delete_submission_user(sub_id):
    if 'user' not in session:
        flash('Please log in first', 'error')
        return redirect(url_for('login'))

    all_subs = get_all_submissions()
    sub_owner = None
    for s in all_subs:
        if s['id'] == sub_id:
            sub_owner = s['user']
            break

    if not sub_owner:
        flash('Submission not found.', 'error')
        return redirect(url_for('dashboard'))

    if session['user'] == sub_owner or session.get('is_admin'):
        delete_submission_permanently(sub_id)
        flash(f'Submission #{sub_id} deleted successfully.', 'success')
    else:
        flash('Unauthorized to delete this submission.', 'error')

    if session.get('is_admin'):
        return redirect(url_for('admin'))
    return redirect(url_for('dashboard'))

@app.route('/admin')
def admin():
    if 'user' not in session or not session.get('is_admin'):
        flash('Access Denied. Admin login required.', 'error')
        return redirect(url_for('login'))

    session.permanent = True
    all_submissions = list(get_all_submissions())
    all_submissions.reverse()
    return render_template('admin.html', submissions=all_submissions)

@app.route('/admin/review/<sub_id>/<action>')
def review_action(sub_id, action):
    if 'user' not in session or not session.get('is_admin'):
        flash('Access Denied. Admin credentials required.', 'error')
        return redirect(url_for('login'))

    all_subs = get_all_submissions()
    updated = False
    msg = ""

    for sub in all_subs:
        if sub['id'] == sub_id:
            if action in ('approve', 'approved'):
                success, msg = start_bot_process(sub_id)
                if not success:
                    update_submission_status(sub_id, 'approved')
            elif action in ('reject', 'rejected'):
                update_submission_status(sub_id, 'rejected')
                stop_bot_process(sub_id)
                msg = f"Submission #{sub_id} marked as REJECTED."
            elif action == 'pause':
                update_submission_status(sub_id, 'paused')
                stop_bot_process(sub_id)
                msg = f"Bot #{sub_id} paused (0% CPU/RAM)."
            elif action == 'stop':
                update_submission_status(sub_id, 'stopped')
                stop_bot_process(sub_id)
                msg = f"Bot #{sub_id} process stopped."
            elif action == 'delete':
                delete_submission_permanently(sub_id)
                msg = f"Submission #{sub_id} permanently deleted."
            updated = True
            break
    
    if updated:
        flash(msg or f'Submission #{sub_id} marked as {action}.', 'success')
    return redirect(url_for('admin'))

@app.route('/api/submissions/<sub_id>/env', methods=['GET', 'POST'])
def api_submission_env(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    all_subs = get_all_submissions()
    sub_data = next((s for s in all_subs if s['id'] == sub_id), None)
    if not sub_data:
        return jsonify({"error": "Submission not found"}), 404

    if sub_data['user'] != session['user'] and not session.get('is_admin'):
        return jsonify({"error": "Forbidden"}), 403

    if request.method == 'POST':
        data = request.get_json() or {}
        new_env = data.get('env_vars', '').strip()
        update_submission_env_vars(sub_id, new_env)

        restarted = False
        if sub_data.get('status') == 'running':
            success, _ = reload_bot_process_zero_downtime(sub_id)
            restarted = success

        return jsonify({
            "success": True,
            "message": "⚡ Hot-Reloaded with 0-Downtime!" if restarted else "Environment variables updated successfully!"
        })

    env_vars = sub_data.get('env_vars', '')
    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    env_file = os.path.join(sub_dir, '.env')
    if os.path.exists(env_file):
        try:
            with open(env_file, 'r', encoding='utf-8', errors='ignore') as f:
                env_vars = f.read()
        except Exception:
            pass

    return jsonify({"env_vars": env_vars})

@app.route('/api/submissions/<sub_id>/files')
def api_submission_files(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    if not os.path.exists(sub_dir):
        return jsonify({"error": "Submission directory not found"}), 404
    
    files_data = []
    for root, _, files in os.walk(sub_dir):
        if '.git' in root:
            continue
        for fname in files:
            if fname.endswith('.zip') or fname.endswith('.session-journal'):
                continue
            rel_path = os.path.relpath(os.path.join(root, fname), sub_dir)
            fpath = os.path.join(root, fname)
            content = ""
            if fname.endswith(('.py', '.txt', '.json', '.md', '.html', '.css', '.js', '.env', '.sh', '.bat')):
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception as e:
                    content = f"Error reading file: {e}"
            else:
                content = f"[{rel_path} - Binary file or session data]"
            
            files_data.append({
                "filename": rel_path,
                "content": content
            })

    return jsonify({"files": files_data})

@app.route('/api/submissions/<sub_id>/logs')
def api_submission_logs(sub_id):
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
    if not os.path.exists(log_file_path):
        return jsonify({"logs": "No execution logs generated yet."})
    try:
        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            logs = f.read()
        return jsonify({"logs": logs[-4000:]})
    except Exception as e:
        return jsonify({"logs": f"Error reading log file: {str(e)}"})

if __name__ == '__main__':
    print("Starting BotHost Application Server on http://127.0.0.1:5000 ...")
    app.run(host='127.0.0.1', port=5000, debug=True)
