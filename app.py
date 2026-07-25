import os
import json
import uuid
import datetime
import subprocess
import sys
import zipfile
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

app = Flask(__name__)
app.secret_key = 'bothost-secret-key-super-secure'

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
LOGS_FOLDER = os.path.join(os.path.dirname(__file__), 'logs')
DB_FILE = os.path.join(os.path.dirname(__file__), 'db.json')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOGS_FOLDER, exist_ok=True)

# In-memory dictionary to track running subprocesses: { sub_id: subprocess.Popen object }
RUNNING_PROCESSES = {}

def load_db():
    if not os.path.exists(DB_FILE):
        data = {
            "users": {
                "Kunal.soree": {"password": "pass123", "created_at": "2026-07-25 12:00:00"},
                "Admin": {"password": "pass123", "created_at": "2026-07-25 12:00:00"}
            },
            "submissions": []
        }
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return data

    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "users" not in data:
                data["users"] = {}
            if "submissions" not in data:
                data["submissions"] = []
            return data
    except Exception:
        return {"users": {}, "submissions": []}

def save_db(data):
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
    return jsonify({
        "status": "ok",
        "service": "BotHost Server",
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

        db = load_db()
        users = db.get('users', {})

        # Strict authentication check
        if username in users:
            if users[username]['password'] == password:
                session['user'] = username
                session['is_admin'] = (username.lower() == 'admin')
                flash(f'Successfully logged in as {username}', 'success')
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

        db = load_db()
        if username in db['users']:
            flash('Username already exists. Please pick another or log in.', 'error')
            return render_template('signup.html')

        db['users'][username] = {
            "password": password,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_db(db)

        session['user'] = username
        session['is_admin'] = (username.lower() == 'admin')
        flash('Account created successfully! Welcome to BotHost.', 'success')
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
    
    db = load_db()
    user_submissions = [s for s in db['submissions'] if s['user'] == session['user']]
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

    db = load_db()
    db['submissions'].append(submission)
    save_db(db)

    flash('Bot project submitted successfully! Under review.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/admin')
def admin():
    if 'user' not in session or not session.get('is_admin'):
        session['user'] = 'Admin'
        session['is_admin'] = True

    db = load_db()
    all_submissions = list(db['submissions'])
    all_submissions.reverse()
    return render_template('admin.html', submissions=all_submissions)

@app.route('/admin/review/<sub_id>/<action>')
def review_action(sub_id, action):
    db = load_db()
    updated = False
    msg = ""

    for sub in db['submissions']:
        if sub['id'] == sub_id:
            if action in ('approve', 'approved'):
                sub['status'] = 'running'
                success, msg = start_bot_process(sub_id)
                if not success:
                    sub['status'] = 'approved'
            elif action in ('reject', 'rejected'):
                stop_bot_process(sub_id)
                sub['status'] = 'rejected'
                msg = f"Submission #{sub_id} marked as REJECTED."
            elif action == 'stop':
                stop_bot_process(sub_id)
                sub['status'] = 'approved'
                msg = f"Bot #{sub_id} process stopped."
            updated = True
            break
    
    if updated:
        save_db(db)
        flash(msg or f'Submission #{sub_id} marked as {action}.', 'success')
    return redirect(url_for('admin'))

@app.route('/api/submissions/<sub_id>/files')
def api_submission_files(sub_id):
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
