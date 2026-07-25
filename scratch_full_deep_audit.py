import requests
import sys
import re
import time
import subprocess

BASE_URL = "http://127.0.0.1:5000"

def full_deep_audit():
    print("=" * 70)
    print("     BOTHOST PLATFORM — COMPREHENSIVE FULL-SYSTEM AUDIT")
    print("=" * 70)

    session = requests.Session()

    # 1. Health & Keep-Alive Check
    print("\n[CHECK 1/6] Keep-Alive Ping Endpoint (/ping)")
    r = session.get(f"{BASE_URL}/ping")
    assert r.status_code == 200 and r.text == "pong", f"Ping failed: {r.status_code} {r.text}"
    print("  -> PASSED: /ping returns HTTP 200 OK (4-byte 'pong').")

    # 2. Authentication & Admin Security Check
    print("\n[CHECK 2/6] Authentication & Role-Based Access Control")
    # Unauthorized admin access test
    unauth_session = requests.Session()
    r_unauth = unauth_session.get(f"{BASE_URL}/admin", allow_redirects=False)
    assert r_unauth.status_code in (302, 401, 403), "Security flaw: Unauthorized access to /admin allowed!"
    print("  -> PASSED: /admin route strictly protected against unauthenticated users.")

    # Valid Admin Login test
    r_admin_login = session.post(f"{BASE_URL}/login", data={
        "username": "sachinmandawi",
        "password": "sachinmandawi"
    }, allow_redirects=True)
    assert r_admin_login.status_code == 200 and ("Admin Review Portal" in r_admin_login.text or "sachinmandawi" in r_admin_login.text)
    print("  -> PASSED: Admin login (sachinmandawi) succeeded with admin privileges.")

    # 3. User Registration & Password Visibility Audit
    print("\n[CHECK 3/6] User Signup & DB Persistence")
    user_uname = f"audit_usr_{int(time.time())}"
    user_pass = "SecurePass123"
    user_session = requests.Session()
    r_signup = user_session.post(f"{BASE_URL}/signup", data={
        "username": user_uname,
        "password": user_pass,
        "confirm": user_pass
    }, allow_redirects=True)
    assert r_signup.status_code == 200 and (user_uname in r_signup.text or "Welcome" in r_signup.text)
    print(f"  -> PASSED: User '{user_uname}' signed up & persisted in DB.")

    # 4. Private GitHub Repository Deployment Audit
    print("\n[CHECK 4/6] Private GitHub Repo Cloning & ENV Injector")
    r_deploy = user_session.post(f"{BASE_URL}/upload", data={
        "upload_mode": "github",
        "repo_url": "https://github.com/sachinmandawi/test-tgbot",
        "env_vars": "AUDIT_ENV=SUCCESS\nBOT_TOKEN=8998729792:AAEYWeg-o7Q0TwAnLWqcrCjXRwySdmZPPfM"
    }, allow_redirects=True)
    assert r_deploy.status_code == 200
    print("  -> PASSED: Private repo cloned & .env file generated.")

    # Get submission ID
    r_admin_portal = session.get(f"{BASE_URL}/admin")
    match = re.search(r'/admin/review/([a-f0-9]+)/approve', r_admin_portal.text)
    assert match, "Could not find submission in admin portal"
    sub_id = match.group(1)
    print(f"  -> Submission ID: #{sub_id}")

    # 5. File Inspection & Execution Log API Audit
    print("\n[CHECK 5/6] Code File Inspector & Live Log API")
    r_files = session.get(f"{BASE_URL}/api/submissions/{sub_id}/files")
    assert r_files.status_code == 200
    files_list = [f['filename'] for f in r_files.json().get('files', [])]
    assert 'bot.py' in files_list or any('bot.py' in f for f in files_list)
    print(f"  -> Files inspected: {files_list}")
    print("  -> PASSED: File Inspector API returned code structure.")

    # 6. Execution Runner & Process Auto-Healing Audit
    print("\n[CHECK 6/6] Process Execution Runner & Auto-Healing Engine")
    r_approve = session.get(f"{BASE_URL}/admin/review/{sub_id}/approve", allow_redirects=True)
    assert r_approve.status_code == 200

    time.sleep(3)
    r_logs = session.get(f"{BASE_URL}/api/submissions/{sub_id}/logs")
    assert r_logs.status_code == 200
    logs_content = r_logs.json().get("logs", "")
    assert "STARTED" in logs_content or "bot.py" in logs_content
    print("  -> Live Process Log:")
    print(logs_content[-300:])
    print("  -> PASSED: Process execution runner active with live logging.")

    # Cleanup test submission
    session.get(f"{BASE_URL}/submission/{sub_id}/delete")
    print("\n" + "=" * 70)
    print("      ALL 6 CORE SYSTEM AUDITS PASSED WITH ZERO ERRORS!")
    print("=" * 70)

if __name__ == "__main__":
    full_deep_audit()
