"""Push notifications through ntfy.sh (free; install the ntfy phone app and subscribe to the topic)."""
import requests


def send_push(topic, title, body, tags="warning", click_url=None):
    """Send one push notification. Returns True if ntfy accepted it."""
    headers = {"Title": title, "Tags": tags}
    if click_url:
        headers["Click"] = click_url
    try:
        r = requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                          headers=headers, timeout=10)
        return r.ok
    except requests.RequestException:
        return False


if __name__ == "__main__":
    # Quick test: python src/alerts.py drainguard-city-yourtopic
    import sys
    ok = send_push(sys.argv[1], "DrainGuard test", "If you see this, alerts work.")
    print("sent" if ok else "failed")
