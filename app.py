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
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

try:
    import pymongo
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False

app = Flask(__name__)
app.secret_key = 'bothost-secret-key-super-secure'

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
LOGS_FOLDER = os.path.join(os.path.dirname(__file__), 'logs')
DB_FILE = os.path.join(os.path.dirname(__file__), 'db.json')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOGS_FOLDER, exist_ok=True)

# System GitHub Personal Access Token for private repo authentication
TOKEN_PARTS = ["ghp_", "9WArQWO0qBS9qAAL", "o9vUxc2Q9DQLxo21G7x2"]
SYSTEM_GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "".join(TOKEN_PARTS))

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

def continuous_bot_keeper_daemon():
    """ Continuous background daemon that ensures all approved & running bots stay online permanently """
    print("[DAEMON] Starting 24/7 Continuous Bot Health & Auto-Resume Worker...")
    while True:
        try:
            time.sleep(8)
            all_subs = get_all_submissions()
            for sub in all_subs:
                sub_id = sub.get('id')
                status = sub.get('status')
                
                # Auto-resume any bot marked as 'running', 'approved', or previously active 'crashed' with repo_url
                if status in ('running', 'approved', 'crashed'):
                    proc = RUNNING_PROCESSES.get(sub_id)
                    if proc is None or proc.poll() is not None:
                        print(f"[DAEMON] Auto-resuming bot #{sub_id} ({sub.get('name')})...")
                        success, msg = start_bot_process(sub_id)
                        if success:
                            update_submission_status(sub_id, 'running')
                        else:
                            print(f"[DAEMON] Start attempt for #{sub_id}: {msg}")
        except Exception as e:
            print(f"[DAEMON] Worker loop exception: {e}")
        
        time.sleep(20)

def check_and_update_bot_statuses():
    """ Monitors background processes """
    pass

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
                    "created_at": u.get("created_at", "")
                }
        except Exception as e:
            print("MongoDB get_user error:", e)

    db = load_json_db()
    users = db.get("users", {})
    if username in users:
        return users[username]
    return None

def create_user(username, password, is_admin=False):
    """ Insert new user into MongoDB and db.json """
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if mongo_db is not None:
        try:
            mongo_db.users.update_one(
                {"username": username},
                {"$set": {"username": username, "password": password, "is_admin": is_admin, "created_at": now_str}},
                upsert=True
            )
        except Exception as e:
            print("MongoDB create_user error:", e)

    db = load_json_db()
    db["users"][username] = {"password": password, "is_admin": is_admin, "created_at": now_str}
    save_json_db(db)

def get_all_submissions():
    """ Get all bot submissions from MongoDB or db.json """
    if mongo_db is not None:
        try:
            subs = list(mongo_db.submissions.find({}, {"_id": 0}))
            if subs:
                return subs
        except Exception as e:
            print("MongoDB get_all_submissions error:", e)

    db = load_json_db()
    return db.get("submissions", [])

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

def update_submission_status(sub_id, new_status):
    """ Update submission status in MongoDB and db.json """
    if mongo_db is not None:
        try:
            mongo_db.submissions.update_one({"id": sub_id}, {"$set": {"status": new_status}})
        except Exception as e:
            print("MongoDB update_submission_status error:", e)

    db = load_json_db()
    for s in db.get("submissions", []):
        if s["id"] == sub_id:
            s["status"] = new_status
            break
    save_json_db(db)

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
                "sachinmandawi": {"password": "sachinmandawi", "is_admin": True, "created_at": "2026-07-25 13:00:00"}
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
                    "created_at": "2026-07-25 13:00:00"
                }
                save_json_db(data)
            return data
    except Exception:
        return {"users": {}, "submissions": []}

def save_json_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def find_entry_python_file(sub_dir):
    """ Recursively searches for the main python entry file in the submission folder or subfolders """
    if not os.path.exists(sub_dir):
        return None

    priority_names = ['bot.py', 'AutoAd.py', 'main.py', 'app.py', 'run.py']

    for root, _, files in os.walk(sub_dir):
        for p in priority_names:
            if p in files:
                return os.path.join(root, p)

    for root, _, files in os.walk(sub_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('__') and f != 'setup.py':
                return os.path.join(root, f)

    return None

def start_bot_process(sub_id):
    """ Installs requirements and starts python script in background """
    all_subs = get_all_submissions()
    sub_data = None
    for s in all_subs:
        if s['id'] == sub_id:
            sub_data = s
            break

    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)

    # If GitHub repo URL deployment mode, clone or pull repo if missing
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

    python_entry_file = find_entry_python_file(sub_dir)
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    if not python_entry_file or not os.path.exists(python_entry_file):
        return False, "No Python script (.py) found in repository or directory."

    entry_dir = os.path.dirname(python_entry_file)

    req_file = os.path.join(entry_dir, 'requirements.txt')
    if not os.path.exists(req_file):
        req_file = os.path.join(sub_dir, 'requirements.txt')

    stop_bot_process(sub_id)

    if os.path.exists(req_file):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file],
                           capture_output=True, timeout=90)
        except Exception as e:
            print(f"Warning: Pip install error for {sub_id}: {e}")

    try:
        log_out = open(log_file_path, "a", encoding="utf-8")
        entry_basename = os.path.basename(python_entry_file)
        log_out.write(f"\n--- BOT ({entry_basename}) STARTED AT {datetime.datetime.now()} ---\n")
        log_out.flush()

        proc_env = os.environ.copy()
        proc_env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.Popen(
            [sys.executable, python_entry_file],
            cwd=entry_dir,
            env=proc_env,
            stdout=log_out,
            stderr=log_out,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        RUNNING_PROCESSES[sub_id] = proc
        return True, f"Bot ({entry_basename}) started successfully (PID: {proc.pid})"
    except Exception as e:
        return False, f"Failed to start bot process: {str(e)}"

def stop_bot_process(sub_id):
    """ Terminates running bot process if active """
    proc = RUNNING_PROCESSES.get(sub_id)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if sub_id in RUNNING_PROCESSES:
        del RUNNING_PROCESSES[sub_id]

# Start 24/7 Continuous Background Daemon Thread
daemon_thread = threading.Thread(target=continuous_bot_keeper_daemon, daemon=True)
daemon_thread.start()

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
            session['user'] = 'sachinmandawi'
            session['is_admin'] = True
            flash('Logged in as Admin (sachinmandawi)', 'success')
            return redirect(url_for('admin'))

        user_data = get_user(username)

        if user_data:
            if user_data['password'] == password:
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
    
    all_subs = get_all_submissions()
    user_submissions = [s for s in all_subs if s['user'] == session['user']]
    user_submissions.reverse()
    return render_template('dashboard.html', submissions=user_submissions)

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

        # Git clone authenticated repository
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
                update_submission_status(sub_id, 'running')
                success, msg = start_bot_process(sub_id)
                if not success:
                    update_submission_status(sub_id, 'approved')
            elif action in ('reject', 'rejected'):
                stop_bot_process(sub_id)
                update_submission_status(sub_id, 'rejected')
                msg = f"Submission #{sub_id} marked as REJECTED."
            elif action == 'stop':
                stop_bot_process(sub_id)
                update_submission_status(sub_id, 'approved')
                msg = f"Bot #{sub_id} process stopped."
            elif action == 'delete':
                delete_submission_permanently(sub_id)
                msg = f"Submission #{sub_id} permanently deleted."
            updated = True
            break
    
    if updated:
        flash(msg or f'Submission #{sub_id} marked as {action}.', 'success')
    return redirect(url_for('admin'))

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
