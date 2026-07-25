import os
import json
import uuid
import datetime
import subprocess
import sys
import zipfile
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

# MongoDB Atlas Connection URI provided by user
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://sachinmandawitime_db_user:U8GnBrYwTOXTsa1M@gmailfarmer.d9lf5r2.mongodb.net/?retryWrites=true&w=majority")

mongo_client = None
mongo_db = None

if HAS_PYMONGO:
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
        mongo_client.admin.command('ping')
        mongo_db = mongo_client['bothost_database']
        print("[SUCCESS] Connected to MongoDB Atlas Cloud Database successfully!")
    except Exception as e:
        print(f"[WARNING] MongoDB Atlas connection unavailable ({str(e)[:80]}). Using db.json storage.")
        mongo_db = None

# In-memory dictionary to track running subprocesses: { sub_id: subprocess.Popen object }
RUNNING_PROCESSES = {}

def check_and_update_bot_statuses():
    """ Monitors background processes and marks crashed bots as 'crashed' """
    all_subs = get_all_submissions()
    for sub in all_subs:
        sub_id = sub['id']
        current_status = sub.get('status')
        proc = RUNNING_PROCESSES.get(sub_id)

        if current_status == 'running':
            if proc is None or proc.poll() is not None:
                exit_code = proc.poll() if proc else 'unknown'
                update_submission_status(sub_id, 'crashed')
                
                # Append crash summary to log file
                log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")
                try:
                    with open(log_file_path, "a", encoding="utf-8") as f:
                        f.write(f"\n❌ [CRASH DETECTED] Bot process terminated at {datetime.datetime.now()} with exit code: {exit_code}\n")
                except Exception:
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
    """ Finds the main python entry file in the submission folder """
    priority_names = ['bot.py', 'AutoAd.py', 'main.py', 'app.py', 'run.py']
    for p in priority_names:
        full_p = os.path.join(sub_dir, p)
        if os.path.exists(full_p):
            return full_p

    for root, _, files in os.walk(sub_dir):
        for f in files:
            if f.endswith('.py') and not f.startswith('__'):
                return os.path.join(root, f)

    return None

def start_bot_process(sub_id):
    """ Installs requirements and starts python script in background """
    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    
    zip_path = os.path.join(sub_dir, 'project.zip')
    if os.path.exists(zip_path):
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(sub_dir)
        except Exception as e:
            print(f"Warning extracting zip: {e}")

    python_entry_file = find_entry_python_file(sub_dir)
    req_file = os.path.join(sub_dir, 'requirements.txt')
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    if not python_entry_file or not os.path.exists(python_entry_file):
        return False, "No Python script (.py) found in submission directory."

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

        proc = subprocess.Popen(
            [sys.executable, python_entry_file],
            cwd=os.path.dirname(python_entry_file),
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

# Dedicated Keep-Alive / Health Ping endpoint for cron-job.org
@app.route('/ping')
@app.route('/health')
def health_ping():
    check_and_update_bot_statuses()
    return jsonify({
        "status": "ok",
        "service": "BotHost Server",
        "database": "MongoDB Atlas" if mongo_db is not None else "Local JSON",
        "timestamp": datetime.datetime.now().isoformat()
    }), 200

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
    
    check_and_update_bot_statuses()
    all_subs = get_all_submissions()
    user_submissions = [s for s in all_subs if s['user'] == session['user']]
    user_submissions.reverse()
    return render_template('dashboard.html', submissions=user_submissions)

@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return redirect(url_for('login'))

    upload_mode = request.form.get('upload_mode', 'files')
    sub_id = str(uuid.uuid4())[:8]
    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    os.makedirs(sub_dir, exist_ok=True)

    saved_files = []

    if upload_mode == 'files':
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
        flash('No files selected for upload.', 'error')
        return redirect(url_for('dashboard'))

    submission = {
        "id": sub_id,
        "user": session['user'],
        "name": name,
        "mode": upload_mode,
        "files": saved_files,
        "status": "pending",
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    add_submission(submission)

    flash('Bot project submitted successfully! Under review.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin')
def admin():
    if 'user' not in session or not session.get('is_admin'):
        flash('Access Denied. Admin login required.', 'error')
        return redirect(url_for('login'))

    check_and_update_bot_statuses()
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
