#!/usr/bin/env python3
"""
BookMyShow Monitor — Simple & Reliable
Checks for "Book Now" button, clicks it when it appears.

    pip install playwright playwright-stealth
    playwright install chromium
    python bookmyshow_monitor.py
"""

import time, random, threading, subprocess, sys, os, requests
from datetime import datetime

# ── CONFIG ──
BMS_URL = "https://in.bookmyshow.com/sports/mumbai-indians-vs-royal-challengers-bengaluru/ET00491196?data=groupPage"
CHECK_INTERVAL = 15  # seconds between checks (normal)
FAST_CHECK_INTERVAL = 3  # seconds between checks (around event time)
EVENT_HOUR = 18  # 6 PM in 24-hour format
FAST_MODE_WINDOW = 1  # minutes before/after event time to use fast polling

# ── iPHONE NOTIFICATION (ntfy.sh) ──
# 1. Install "ntfy" app on iPhone from App Store
# 2. Subscribe to this same topic name in the app
NTFY_TOPIC = "bms-nakul-tickets"  # change this to any unique name you want

# ── ALARM ──
_alarm_stop = threading.Event()


def play_alarm():
    _alarm_stop.clear()
    subprocess.Popen(["say", "-v", "Samantha", "Tickets are live! Book now!"])
    while not _alarm_stop.is_set():
        subprocess.Popen(["afplay", "/System/Library/Sounds/Ping.aiff"])
        _alarm_stop.wait(0.6)


def stop_alarm():
    _alarm_stop.set()


def get_check_interval():
    """Return shorter interval when close to event time (6 PM)."""
    now = datetime.now()
    event_minute = EVENT_HOUR * 60  # 6 PM = 1080 minutes from midnight
    current_minute = now.hour * 60 + now.minute

    # Check if within the fast-polling window
    if abs(current_minute - event_minute) <= FAST_MODE_WINDOW:
        return FAST_CHECK_INTERVAL
    return CHECK_INTERVAL


def send_iphone_notification(message):
    """Send push notification to iPhone via ntfy.sh with a clickable link to BMS."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
   
                "Title": "BMS Tickets LIVE!",
                "Priority": "urgent",
                "Tags": "ticket,warning",
                "Click": BMS_URL,
                "Actions": f"view, Open BookMyShow, {BMS_URL}",
            },
            timeout=10,
        )
        print("  📱 iPhone notification sent!")
    except Exception as e:
        print(f"  📱 iPhone notify failed: {e}")


def notify(message):
    print(f"\n🎉 {message}\n")
    threading.Thread(target=play_alarm, daemon=True).start()
    # Mac notification
    try:
        safe = message.replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "BMS LIVE" sound name "Ping"'], timeout=5)
    except Exception:
        pass
    # iPhone notification
    threading.Thread(target=send_iphone_notification, args=(message,), daemon=True).start()


def run():
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    profile_dir = os.path.join(os.path.dirname(__file__), ".bms_profile")
    os.makedirs(profile_dir, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        Stealth().apply_stealth_sync(page)

        print(f"🔍 Monitoring: {BMS_URL}")
        print(f"   Normal: every ~{CHECK_INTERVAL}s | Fast mode (5:50-6:10 PM): every ~{FAST_CHECK_INTERVAL}s\n")

        page.goto(BMS_URL, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Let user log in if needed
        try:
            body = (page.inner_text("body") or "").lower()
            if "sign in" in body:
                print("⚠️  Please log in in the browser window.")
                input("👉 Press ENTER after logging in... ")
                page.reload(wait_until="domcontentloaded", timeout=20000)
                time.sleep(3)
        except Exception:
            pass

        print("✅ Monitoring started.\n")
        check = 0

        while True:
            check += 1
            now = time.strftime("%H:%M:%S")
            try:
                page.reload(wait_until="domcontentloaded", timeout=20000)
                time.sleep(random.uniform(2, 4))

                # ── THE SIMPLE CHECK ──
                # Use Playwright's text matching to find "Book Now" anywhere visible on the page.
                # This finds the actual visible element containing that text — no CSS guessing.
                book_btn = None
                for label in ["Book Now", "BUY NOW", "Buy Now", "Book Tickets", "Buy Tickets", "BOOK NOW"]:
                    try:
                        loc = page.get_by_text(label, exact=True)
                        if loc.count() > 0 and loc.first.is_visible():
                            book_btn = loc.first
                            print(f"  ✅ Found: '{label}'")
                            break
                    except Exception:
                        continue

                if book_btn:
                    notify(f"Tickets are LIVE! Found button on page.")

                    # CLICK IT
                    try:
                        book_btn.scroll_into_view_if_needed()
                        time.sleep(0.3)
                        book_btn.click()
                        print("⚡ Clicked Book Now!")
                    except Exception as e:
                        print(f"  Click failed ({e}), trying JS click...")
                        try:
                            book_btn.evaluate("el => el.click()")
                            print("⚡ Clicked via JS!")
                        except Exception:
                            print("  ❌ Could not click. Do it manually in the browser!")

                    time.sleep(2)

                    # If click opened a new tab, switch to it (same browser, still logged in)
                    if len(browser.pages) > 1:
                        page = browser.pages[-1]
                        page.bring_to_front()
                        print("  ↪ Switched to new tab")

                    # Stop alarm on ENTER
                    input("\n🔔 Press ENTER to silence alarm...\n")
                    stop_alarm()
                    input("Press ENTER to resume monitoring (or Ctrl+C to quit)...\n")

                else:
                    # Check if it says "Coming Soon"
                    try:
                        body_text = (page.inner_text("body") or "").lower()
                    except Exception:
                        body_text = ""

                    interval = get_check_interval()
                    mode = "⚡FAST" if interval == FAST_CHECK_INTERVAL else ""

                    if "coming soon" in body_text:
                        print(f"[{now}] #{check}: Coming Soon {mode}")
                    elif "sold out" in body_text:
                        print(f"[{now}] #{check}: Sold Out {mode}")
                    else:
                        print(f"[{now}] #{check}: No 'Book Now' found yet {mode}")

            except KeyboardInterrupt:
                print("\nStopped.")
                break
            except Exception as e:
                print(f"[{now}] Error: {e}")

            interval = get_check_interval()
            time.sleep(interval + random.uniform(-1, 1))

        browser.close()


if __name__ == "__main__":
    run()
