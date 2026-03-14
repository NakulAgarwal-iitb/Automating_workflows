"""
LinkedIn Auto-Connect Script
=============================
Automates sending connection requests with a personalized note
on LinkedIn company people pages.

Usage:
    python linkedin_connect.py "https://www.linkedin.com/company/bluedart/people/?keywords=head"
    python linkedin_connect.py "https://www.linkedin.com/company/bluedart/people/?keywords=head" --dry-run
    python linkedin_connect.py "https://www.linkedin.com/company/bluedart/people/?keywords=head" --max 10

How it works:
    1. Launches Chrome with your profile (with remote debugging so it doesn't conflict)
    2. Opens the URL you give
    3. Finds all "Connect" buttons and sends personalized connection requests

to kill:
pkill -f "remote-debugging-port=9222"

"""

import time
import random
import argparse
import subprocess
import socket
import sys
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

NOTE_TEMPLATE = (
    "Hi {name}, We're alumni of IITB Driverless Racing team and are looking to "
    "develop driverless trucks for logistics in India. We want an industry "
    "perspective on it. Your help can make all the difference. We can get on a "
    "call at your convenience. Thanks!"
)

# Delay range (seconds) between each connection request to appear human-like
MIN_DELAY = 3
MAX_DELAY = 7

# Maximum number of connection requests to send in one run (safety limit)
MAX_REQUESTS = 50

DEBUG_PORT = 9222

# Separate user-data dir for the automation Chrome so it doesn't conflict
# with your normal Chrome. We copy cookies from your real profile on first run.
AUTOMATION_PROFILE_DIR = os.path.expanduser("~/linkedin-automation-chrome-profile")

# ─── HELPERS ──────────────────────────────────────────────────────────────────


def is_port_open(port):
    """Check if a port is open on localhost."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return result == 0


def launch_chrome(url):
    """
    Launch a fresh Chrome instance with remote debugging.
    Uses a separate profile directory so it never conflicts with your normal Chrome.
    On first run, it will open LinkedIn — you log in once, and it remembers you.
    """
    if is_port_open(DEBUG_PORT):
        print(f"✅ Chrome automation instance already running on port {DEBUG_PORT}.")
        return

    print(f"🚀 Launching Chrome...")
    os.makedirs(AUTOMATION_PROFILE_DIR, exist_ok=True)

    subprocess.Popen(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={AUTOMATION_PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to start
    for i in range(20):
        time.sleep(1)
        if is_port_open(DEBUG_PORT):
            print("✅ Chrome is ready.")
            return
    print("❌ Chrome didn't start in time. Try closing any existing Chrome windows and retry.")
    sys.exit(1)


def get_chrome_driver():
    """Connect to the running Chrome instance via remote debugging."""
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    driver = webdriver.Chrome(options=opts)
    return driver


def human_delay():
    """Random sleep to mimic human behavior."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def scroll_to_bottom(driver, needed=None):
    """Scroll down to load people cards. Stops early if we already have enough Connect buttons."""
    max_scroll_attempts = 15
    scroll_pause = 3  # seconds between scrolls

    def count_connect_buttons():
        return len(driver.find_elements(
            By.XPATH,
            "//button[contains(@aria-label, 'Invite') and contains(@aria-label, 'to connect')]"
        ))

    # Check if we already have enough before scrolling
    if needed and count_connect_buttons() >= needed:
        print(f"   ✅ Already have {count_connect_buttons()} Connect buttons (need {needed}), skipping scroll.")
        return

    for attempt in range(max_scroll_attempts):
        last_height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)

        # Click "Show more results" if present
        try:
            show_more = driver.find_element(
                By.XPATH, "//button[contains(., 'Show more results')]"
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", show_more)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", show_more)
            print("   ↓ Loading more results...")
            time.sleep(3)
        except NoSuchElementException:
            pass

        # Stop early if we have enough
        if needed and count_connect_buttons() >= needed:
            print(f"   ✅ Found enough Connect buttons ({count_connect_buttons()}), stopping scroll.")
            break

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break

    # Scroll back to top so buttons are in a known state
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)


def extract_name_from_card(card):
    """Extract the person's first name from a people card element."""
    try:
        # The name is typically in a span with specific classes inside the card
        name_el = card.find_element(
            By.CSS_SELECTOR,
            "div.org-people-profile-card__profile-title, "
            "span.org-people-profile-card__profile-title, "
            "div.artdeco-entity-lockup__title span[aria-hidden='true'], "
            "span.artdeco-entity-lockup__title"
        )
        full_name = name_el.text.strip()
        # Return first name only
        first_name = full_name.split()[0] if full_name else "there"
        return first_name
    except NoSuchElementException:
        return "there"


def find_connect_buttons_and_names(driver):
    """
    Find all 'Connect' buttons on the page and extract names from aria-label.
    aria-label format: "Invite [FULL NAME] to connect"
    Returns list of (button_element, first_name).
    """
    results = []

    buttons = driver.find_elements(
        By.XPATH,
        "//button[contains(@aria-label, 'Invite') and contains(@aria-label, 'to connect')]"
    )

    for btn in buttons:
        aria = btn.get_attribute("aria-label") or ""
        # aria-label is like "Invite SONIA NAIR to connect"
        name_part = aria.replace("Invite ", "").replace(" to connect", "").strip()
        first_name = name_part.split()[0].title() if name_part else "there"
        results.append((btn, first_name))

    return results


def send_connection_request(driver, button, name, note_template):
    """Click Connect, add a personalized note, and send."""
    personalized_note = note_template.format(name=name)

    if len(personalized_note) > 300:
        print(f"  ⚠️  Note for {name} is {len(personalized_note)} chars (max 300). Truncating.")
        personalized_note = personalized_note[:297] + "..."

    try:
        # Scroll button into view and click
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", button
        )
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", button)
        time.sleep(1.5)

        # --- Handle the modal/popup ---
        # Click "Add a note" button
        try:
            add_note_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@aria-label, 'Add a note')]")
                )
            )
            add_note_btn.click()
            time.sleep(1)
        except TimeoutException:
            # Sometimes the "Add a note" button text is different
            try:
                add_note_btn = driver.find_element(
                    By.XPATH, "//button[contains(., 'Add a note')]"
                )
                add_note_btn.click()
                time.sleep(1)
            except NoSuchElementException:
                print(f"  ⚠️  Could not find 'Add a note' button for {name}. Sending without note.")
                # Try to just click Send
                try:
                    send_btn = driver.find_element(
                        By.XPATH,
                        "//button[contains(@aria-label, 'Send')]"
                        " | //button[contains(@aria-label, 'Send without a note')]"
                        " | //button[contains(., 'Send')]"
                    )
                    send_btn.click()
                    return True
                except NoSuchElementException:
                    return False

        # Type the note
        try:
            note_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "textarea[name='message'], textarea#custom-message, textarea.connect-button-send-invite__custom-message")
                )
            )
            note_field.clear()
            # Type character by character for a more human-like feel
            for char in personalized_note:
                note_field.send_keys(char)
                time.sleep(random.uniform(0.01, 0.04))
            time.sleep(0.5)
        except TimeoutException:
            print(f"  ⚠️  Could not find note text area for {name}.")
            # Dismiss modal
            _dismiss_modal(driver)
            return False

        # Click "Send" button
        try:
            send_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//button[contains(@aria-label, 'Send invitation')]"
                     " | //button[contains(@aria-label, 'Send now')]"
                     " | //button[contains(., 'Send')]")
                )
            )
            send_btn.click()
            time.sleep(1)
            return True
        except TimeoutException:
            print(f"  ⚠️  Could not find Send button for {name}.")
            _dismiss_modal(driver)
            return False

    except ElementClickInterceptedException:
        print(f"  ⚠️  Click intercepted for {name}. Dismissing overlays...")
        _dismiss_modal(driver)
        return False
    except Exception as e:
        print(f"  ❌  Unexpected error for {name}: {e}")
        _dismiss_modal(driver)
        return False


def _dismiss_modal(driver):
    """Try to close any open modal/dialog."""
    try:
        close_btn = driver.find_element(
            By.XPATH,
            "//button[contains(@aria-label, 'Dismiss')] | //button[contains(@aria-label, 'Close')]"
        )
        close_btn.click()
        time.sleep(0.5)
    except NoSuchElementException:
        # Press Escape as fallback
        from selenium.webdriver.common.keys import Keys
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)


# ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Auto-send LinkedIn connection requests with a personalized note."
    )
    parser.add_argument(
        "url",
        help="LinkedIn people page URL, e.g. https://www.linkedin.com/company/bluedart/people/?keywords=head",
    )
    parser.add_argument(
        "--note",
        default=NOTE_TEMPLATE,
        help="Note template. Use {name} as placeholder for the person's first name.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_REQUESTS,
        help=f"Max connection requests to send (default: {MAX_REQUESTS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only list people found — don't actually send requests.",
    )
    args = parser.parse_args()

    print("🚀 LinkedIn Auto-Connect")
    print(f"   URL : {args.url}")
    print(f"   Max : {args.max} requests")
    print(f"   Note: {args.note[:80]}...")
    print()

    if args.dry_run:
        print("🔍 DRY RUN — no requests will be sent.\n")

    # Launch Chrome and open the URL
    launch_chrome(args.url)
    driver = get_chrome_driver()

    try:
        # Navigate to the URL (in case Chrome was already running)
        driver.get(args.url)
        print(f"📄 Opening: {args.url}")
        print("⏳ Waiting for page to load...")
        time.sleep(5)

        # Check if logged in
        if "login" in driver.current_url or "authwall" in driver.current_url:
            print("\n⚠️  You need to log in to LinkedIn first!")
            print("   A Chrome window has opened. Please log in there.")
            print("   After logging in, re-run this script with the same command.")
            print("   (Your login will be remembered for future runs.)")
            input("\n   Press Enter to exit...")
            return

        # Wait for the page content to actually render (people cards load via JS)
        print("⏳ Waiting for people cards to load...")
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//button[contains(@aria-label, 'Invite') and contains(@aria-label, 'to connect')]"
                    " | //button[contains(@aria-label, 'Pending')]"
                    " | //button[text()='Follow']"
                ))
            )
            print("✅ Page content loaded.")
        except TimeoutException:
            print("⚠️  Timed out waiting for people cards. Trying anyway...")

        # Scroll to load results (stops early if we have enough)
        print("📜 Scrolling to load people...")
        scroll_to_bottom(driver, needed=args.max)
        time.sleep(2)

        # Find connect buttons
        targets = find_connect_buttons_and_names(driver)
        print(f"\n🔎 Found {len(targets)} people with 'Connect' button.\n")

        if not targets:
            print("No connectable people found on this page.")
            print("Make sure the URL points to a company People page with visible 'Connect' buttons.")
            return

        sent = 0
        skipped = 0

        for i, (btn, name) in enumerate(targets):
            if sent >= args.max:
                print(f"\n🛑 Reached max limit of {args.max} requests. Stopping.")
                break

            print(f"[{i+1}/{len(targets)}] {name}", end=" — ")

            if args.dry_run:
                print(f"would send note: \"{args.note.format(name=name)[:60]}...\"")
                continue

            success = send_connection_request(driver, btn, name, args.note)
            if success:
                sent += 1
                print(f"✅ Request sent!")
            else:
                skipped += 1
                print(f"⏭️  Skipped.")

            human_delay()

        print(f"\n{'='*50}")
        print(f"✅ Sent: {sent}   ⏭️  Skipped: {skipped}   Total: {len(targets)}")
        print(f"{'='*50}")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    print("\n✅ Done. Chrome is still open — you can close it or run the script again with a new URL.")


if __name__ == "__main__":
    main()
