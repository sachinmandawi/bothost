import requests
import time
import re

BASE_URL = "http://127.0.0.1:5000"

def deploy_and_run_bots():
    print("=" * 60)
    print("    DEPLOYING & LAUNCHING BOTH BOTS ON BOTHOST PLATFORM")
    print("=" * 60)

    # 1. Login as Admin sachinmandawi
    session = requests.Session()
    r = session.post(f"{BASE_URL}/login", data={
        "username": "sachinmandawi",
        "password": "sachinmandawi"
    }, allow_redirects=True)
    assert r.status_code == 200, "Admin login failed"
    print("\n[STEP 1] Authenticated as Admin (sachinmandawi).")

    # 2. Submit Bot 1: Test Bot (test-tgbot)
    print("\n[STEP 2] Deploying Bot #1: Test Bot (https://github.com/sachinmandawi/test-tgbot)...")
    r1 = session.post(f"{BASE_URL}/upload", data={
        "upload_mode": "github",
        "repo_url": "https://github.com/sachinmandawi/test-tgbot",
        "env_vars": "BOT_TOKEN=8998729792:AAEYWeg-o7Q0TwAnLWqcrCjXRwySdmZPPfM"
    }, allow_redirects=True)
    assert r1.status_code == 200
    print("  -> Test Bot submitted successfully!")

    # 3. Submit Bot 2: AutoAd Bot (autoad-bot)
    print("\n[STEP 3] Deploying Bot #2: AutoAd Bot (https://github.com/sachinmandawi/autoad-bot)...")
    r2 = session.post(f"{BASE_URL}/upload", data={
        "upload_mode": "github",
        "repo_url": "https://github.com/sachinmandawi/autoad-bot",
        "env_vars": "# AutoAd Configuration\nTELEGRAM_API_ID=28000000\nTELEGRAM_API_HASH=abcdef123456"
    }, allow_redirects=True)
    assert r2.status_code == 200
    print("  -> AutoAd Bot submitted successfully!")

    # 4. Fetch all pending submissions from Admin Panel
    r_admin = session.get(f"{BASE_URL}/admin")
    approve_links = re.findall(r'/admin/review/([a-f0-9]+)/approve', r_admin.text)
    print(f"\n[STEP 4] Found Pending Submission IDs to Approve: {approve_links}")

    # 5. Approve & Run all pending bots
    running_bots = []
    for sub_id in approve_links:
        print(f"\n[STEP 5] Approving & Launching Bot #{sub_id}...")
        r_app = session.get(f"{BASE_URL}/admin/review/{sub_id}/approve", allow_redirects=True)
        assert r_app.status_code == 200
        running_bots.append(sub_id)

    # 6. Wait for processes to initialize and verify execution logs
    print("\n[STEP 6] Polling execution logs for launched bots...")
    time.sleep(3)

    for sub_id in running_bots:
        r_log = session.get(f"{BASE_URL}/api/submissions/{sub_id}/logs")
        logs = r_log.json().get("logs", "")
        print(f"\n----------------------------------------")
        print(f"   LOGS FOR BOT #{sub_id}")
        print(f"----------------------------------------")
        print(logs[-500:])

    print("\n" + "=" * 60)
    print("      BOTH BOTS DEPLOYED & RUNNING SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    deploy_and_run_bots()
