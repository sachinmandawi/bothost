import os
import json
import uuid
import datetime
import subprocess
import sys
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
        return {"users": {}, "submissions": []}
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

def start_bot_process(sub_id):
    """ Installs requirements and starts bot.py in background """
    sub_dir = os.path.join(UPLOAD_FOLDER, sub_id)
    bot_file = os.path.join(sub_dir, 'bot.py')
    req_file = os.path.join(sub_dir, 'requirements.txt')
    log_file_path = os.path.join(LOGS_FOLDER, f"{sub_id}.log")

    if not os.path.exists(bot_file):
        return False, "bot.py not found in submission directory."

    # Stop any previously running process for this submission
    stop_bot_process(sub_id)

    # 1. Install requirements if present
    if os.path.exists(req_file):
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file],
                           capture_output=True, timeout=60)
        except Exception as e:
            print(f"Warning: Pip install error for {sub_id}: {e}")

    # 2. Launch bot.py as background process writing to log file
    try:
        log_out = open(log_file_path, "a", encoding="utf-8")
        log_out.write(f"\n--- BOT STARTED AT {datetime.datetime.now()} ---\n")
        log_out.flush()

        proc = subprocess.Popen(
            [sys.executable, bot_file],
            cwd=sub_dir,
            stdout=log_out,
            stderr=log_out,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        RUNNING_PROCESSES[sub_id] = proc
        return True, f"Bot started successfully (PID: {proc.pid})"
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

        if username in users:
            if users[username]['password'] == password:
                session['user'] = username
                session['is_admin'] = (username.lower() == 'admin')
                flash(f'Successfully logged in as {username}', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username or password', 'error')
                return render_template('login.html')

        session['user'] = username
        session['is_admin'] = (username.lower() == 'admin')
        flash(f'Logged in as {username}', 'success')
        return redirect(url_for('dashboard'))

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

    flash('Bot submitted successfully! Under review.', 'success')
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
    for fname in os.listdir(sub_dir):
        fpath = os.path.join(sub_dir, fname)
        content = ""
        if fname.endswith(('.py', '.txt', '.json', '.md', '.html', '.css', '.js')):
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception as e:
                content = f"Error reading file: {e}"
        else:
            content = f"[{fname} - Binary file or compressed archive]"
        
        files_data.append({
            "filename": fname,
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
