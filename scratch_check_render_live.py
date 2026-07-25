import requests

def check_live_render():
    print("=" * 60)
    print("     CHECKING LIVE RENDER RESTART STATUS & HEALTH")
    print("=" * 60)

    url_ping = "https://bothost-dq6s.onrender.com/ping"
    try:
        r = requests.get(url_ping, timeout=20)
        print(f"[RENDER PING TEST] Status Code: {r.status_code}")
        print(f"[RENDER PING TEST] Response Body: '{r.text}'")
        if r.status_code == 200 and r.text == "pong":
            print("  -> PASSED: Render Server is UP, Awake & Responding with 'pong' (200 OK)!")
    except Exception as e:
        print(f"[RENDER PING TEST] Error: {e}")

if __name__ == "__main__":
    check_live_render()
