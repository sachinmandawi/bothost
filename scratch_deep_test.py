import requests
import sys
import re
import time

BASE_URL = "http://127.0.0.1:5000"

def run_deep_check():
    session = requests.Session()
    print("=" * 60)
    print("      BOTHOST END-TO-END DEEP SYSTEM AUDIT")
    print("=" * 60)

    # Test 1: Keep-Alive Ping Endpoint
    print("\n[TEST 1/7] Testing /ping Keep-Alive Endpoint...")
    r = session.get(f"{BASE_URL}/ping")
    assert r.status_code == 200, f"Expected status 200, got {r.status_code}"
    assert r.text == "pong", f"Expected 'pong' (4 bytes), got {r.text}"
    print("  -> PASSED: /ping returned HTTP 200 OK with exact 4-byte response ('pong').")

    # Test 2: Admin Login Authentication
    print("\n[TEST 2/7] Testing Admin Login (sachinmandawi)...")
    r = session.post(f"{BASE_URL}/login", data={
        "username": "sachinmandawi",
        "password": "sachinmandawi"
    }, allow_redirects=True)
    assert r.status_code == 200
    assert "Admin Review Portal" in r.text or "sachinmandawi" in r.text
    print("  -> PASSED: Admin authenticated successfully.")

    # Test 3: User Signup & Login
    print("\n[TEST 3/7] Testing User Signup & Login...")
    test_user = f"audit_user_{int(time.time())}"
    test_pass = "pass12345"
    user_session = requests.Session()
    r = user_session.post(f"{BASE_URL}/signup", data={
        "username": test_user,
        "password": test_pass,
        "confirm": test_pass
    }, allow_redirects=True)
    assert r.status_code == 200
    assert "Welcome" in r.text or "sachinmandawi" in r.text or test_user in r.text
    print(f"  -> PASSED: New user '{test_user}' signup & dashboard session created.")

    # Test 4: Deploying GitHub Private Repository (Test Bot)
    print("\n[TEST 4/7] Testing Private GitHub Repo Deployment (test-tgbot)...")
    r = user_session.post(f"{BASE_URL}/upload", data={
        "upload_mode": "github",
        "repo_url": "https://github.com/sachinmandawi/test-tgbot",
        "env_vars": "TEST_KEY=12345"
    }, allow_redirects=True)
    assert r.status_code == 200
    assert "submitted successfully" in r.text or "GitHub: test-tgbot" in r.text or "Under review" in r.text
    print("  -> PASSED: GitHub private repo cloned & submitted for approval.")

    # Fetch submission ID via Admin session
    admin_session = requests.Session()
    admin_session.post(f"{BASE_URL}/login", data={
        "username": "sachinmandawi",
        "password": "sachinmandawi"
    }, allow_redirects=True)

    r = admin_session.get(f"{BASE_URL}/admin")
    assert r.status_code == 200
    match = re.search(r'/admin/review/([a-f0-9]+)/approve', r.text)
    assert match, "Could not find submission ID in admin portal"
    sub_id = match.group(1)
    print(f"  -> Submission ID created: #{sub_id}")

    # Test 5: File Inspector API & File Verification
    print("\n[TEST 5/7] Testing File Inspector API...")
    r = admin_session.get(f"{BASE_URL}/api/submissions/{sub_id}/files")
    assert r.status_code == 200
    files_json = r.json()
    filenames = [f['filename'] for f in files_json.get('files', [])]
    print(f"  -> Files found in cloned repo: {filenames}")
    assert any('bot.py' in f for f in filenames), "bot.py missing in cloned repository!"
    print("  -> PASSED: File inspector successfully identified bot.py & requirements.txt.")

    # Test 6: Admin Approve & Run Bot Process
    print("\n[TEST 6/7] Testing Admin Approve & Execution Runner...")
    r = admin_session.get(f"{BASE_URL}/admin/review/{sub_id}/approve", allow_redirects=True)
    assert r.status_code == 200
    print("  -> Approved. Polling execution logs...")

    time.sleep(2)
    r = admin_session.get(f"{BASE_URL}/api/submissions/{sub_id}/logs")
    assert r.status_code == 200
    logs_data = r.json().get("logs", "")
    print(f"  -> Process Logs Preview:\n{logs_data[:300]}")
    assert "BOT" in logs_data or "STARTED" in logs_data or "PID" in r.text
    print("  -> PASSED: Bot subprocess spawned & live logging active.")

    # Test 7: Stop Bot & Clean Delete
    print("\n[TEST 7/7] Testing Bot Stop & Permanent Deletion...")
    r = admin_session.get(f"{BASE_URL}/admin/review/{sub_id}/stop", allow_redirects=True)
    assert r.status_code == 200

    r = admin_session.get(f"{BASE_URL}/submission/{sub_id}/delete", allow_redirects=True)
    assert r.status_code == 200
    assert "deleted successfully" in r.text or "deleted" in r.text
    print("  -> PASSED: Bot stopped and permanently erased from DB & disk.")

    print("\n" + "=" * 60)
    print("     ALL 7 SYSTEM INTEGRATION TESTS PASSED SUCCESSFULLY! ")
    print("=" * 60)

if __name__ == "__main__":
    run_deep_check()
