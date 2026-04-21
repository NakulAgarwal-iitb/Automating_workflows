"""
LinkedIn Search Page Auto-Connect Script
========================================
Automates sending connection requests with a personalized note
on LinkedIn search results pages (with pagination).

Usage:
    python linkedin_search_connect.py "https://www.linkedin.com/search/results/people/..."
    python linkedin_search_connect.py "https://www.linkedin.com/search/results/people/..." --dry-run
    python linkedin_search_connect.py "https://www.linkedin.com/search/results/people/..." --max 10

How it works:
    1. Launches Chrome with your profile (with remote debugging)
    2. Opens the search URL
    3. Finds all "Connect" buttons on the current page, sends requests
    4. Navigates to the next page and repeats up to the limit
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
    "Hi {name}, I'm Nakul, a final-year student and IITB Driverless Racing Team alum. "
    "We're building Neural.KM, a modular, model-agnostic hood for driverless commercial vehicles. "
    "We're applying to YC and would love your help with an application review and further connects. Thanks!"
)

MIN_DELAY = 3
MAX_DELAY = 7
MAX_REQUESTS = 50
DEBUG_PORT = 9222
AUTOMATION_PROFILE_DIR = os.path.expanduser("~/linkedin-automation-chrome-profile")

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def is_port_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return result == 0

def launch_chrome(url):
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

    for i in range(20):
        time.sleep(1)
        if is_port_open(DEBUG_PORT):
            print("✅ Chrome is ready.")
            return
    print("❌ Chrome didn't start in time. Try closing any existing Chrome windows and retry.")
    sys.exit(1)

def get_chrome_driver():
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    return webdriver.Chrome(options=opts)

def human_delay():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

def scroll_page(driver):
    """Slowly scroll the page to ensure all elements load."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    steps = 4
    for i in range(1, steps + 1):
        driver.execute_script(f"window.scrollTo(0, {last_height * i / steps});")
        time.sleep(1)
    
    # Scroll back to a bit below top
    driver.execute_script("window.scrollTo(0, 200);")
    time.sleep(1)

def find_connect_buttons_and_names(driver):
    """
    Find all 'Connect' buttons on the search page.
    """
    results = []
    
    # LinkedIn search pages sometimes use <a> or <button> tags
    elements = driver.find_elements(By.XPATH, "//button[normalize-space(.)='Connect'] | //a[normalize-space(.)='Connect'] | //button[contains(@aria-label, 'to connect')]")
    
    for el in elements:
        try:
            if not el.is_displayed():
                continue
                
            first_name = "there"
            aria = (el.get_attribute("aria-label") or "").strip()
            
            if "Invite" in aria and "to connect" in aria:
                name_part = aria.replace("Invite ", "").replace(" to connect", "").strip()
                first_name = name_part.split()[0].title() if name_part else "there"
            else:
                # Fallback: search the ancestor card for the user's name
                try:
                    parent = el.find_element(By.XPATH, "./ancestor::li | ./ancestor::div[contains(@class, 'reusable-search__result-container')][1]")
                    name_el = parent.find_element(By.CSS_SELECTOR, "span[dir='ltr']")
                    first_name = name_el.text.strip().split()[0].title()
                except:
                    pass
                    
            results.append((el, first_name))
        except StaleElementReferenceException:
            continue
            
    return results

def send_connection_request(driver, button, name, note_template):
    personalized_note = note_template.format(name=name)
    if len(personalized_note) > 300:
        personalized_note = personalized_note[:297] + "..."

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", button)
        time.sleep(1.5)

        # Handle modal
        try:
            add_note_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@aria-label, 'Add a note') or contains(., 'Add a note')]"))
            )
            add_note_btn.click()
            time.sleep(1)
        except TimeoutException:
            # If no add a note button, try sending directly
            try:
                send_btn = driver.find_element(
                    By.XPATH,
                    "//button[contains(@aria-label, 'Send') or contains(., 'Send') or contains(., 'Send without a note')]"
                )
                send_btn.click()
                return True
            except NoSuchElementException:
                return False

        try:
            note_field = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "textarea[name='message'], textarea#custom-message, textarea.connect-button-send-invite__custom-message"))
            )
            note_field.clear()
            for char in personalized_note:
                note_field.send_keys(char)
                time.sleep(random.uniform(0.01, 0.04))
            time.sleep(0.5)
        except TimeoutException:
            print(f"  ⚠️  Could not find note text area for {name}.")
            _dismiss_modal(driver)
            return False

        try:
            send_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@aria-label, 'Send invitation') or contains(@aria-label, 'Send now') or contains(., 'Send')]")
                )
            )
            send_btn.click()
            time.sleep(1)
            return True
        except TimeoutException:
            print(f"  ⚠️  Could not find Send button for {name}.")
            _dismiss_modal(driver)
            return False

    except Exception as e:
        print(f"  ❌  Unexpected error for {name}: {e}")
        _dismiss_modal(driver)
        return False

def _dismiss_modal(driver):
    try:
        close_btn = driver.find_element(By.XPATH, "//button[contains(@aria-label, 'Dismiss') or contains(@aria-label, 'Close')]")
        close_btn.click()
        time.sleep(0.5)
    except NoSuchElementException:
        from selenium.webdriver.common.keys import Keys
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)

def go_to_next_page(driver):
    try:
        # Search page next buttons can be styled differently or have inner spans
        next_btn = driver.find_element(By.XPATH, "//button[contains(@aria-label, 'Next')] | //button[*[normalize-space(.)='Next']] | //button[normalize-space(.)='Next']")
        
        if next_btn.get_attribute("disabled"):
            return False
            
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", next_btn)
        time.sleep(3)
        return True
    except NoSuchElementException:
        return False

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="LinkedIn Search URL")
    parser.add_argument("--note", default=NOTE_TEMPLATE)
    parser.add_argument("--max", type=int, default=MAX_REQUESTS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("🚀 LinkedIn Search Auto-Connect")
    print(f"   Max : {args.max} requests")
    
    launch_chrome(args.url)
    driver = get_chrome_driver()

    try:
        driver.get(args.url)
        print(f"📄 Opening: {args.url}")
        time.sleep(5)

        # Check if logged in
        if "login" in driver.current_url or "authwall" in driver.current_url:
            print("\n⚠️  You need to log in to LinkedIn first!")
            print("   A Chrome window has opened. Please log in there.")
            print("   After logging in, re-run this script with the same command.")
            input("\n   Press Enter to exit...")
            return

        # Wait robustly for page content
        print("⏳ Waiting for people cards to load...")
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'search-results-container')] | //ul[contains(@class, 'reusable-search__entity-result-list')]"))
            )
            print("✅ Page content loaded.")
        except TimeoutException:
            print("⚠️  Timed out waiting for search results container. Trying anyway...")
        
        # Give React another moment to render the buttons inside the containers
        time.sleep(3)

        sent = 0
        skipped = 0
        page = 1

        while sent < args.max:
            print(f"\n📄 Scanning Page {page}...")
            scroll_page(driver)
            
            targets = find_connect_buttons_and_names(driver)
            
            # Using a set of elements to avoid duplicates (often buttons change state if re-queried)
            unique_targets = []
            seen_elements = set()
            for btn, name in targets:
                try:
                    if btn.id not in seen_elements and "Pending" not in btn.text and btn.is_enabled():
                        unique_targets.append((btn, name))
                        seen_elements.add(btn.id)
                except StaleElementReferenceException:
                    continue

            print(f"🔎 Found {len(unique_targets)} new 'Connect' buttons.\n")

            if not unique_targets:
                print("No connectable people found on this page.")
            else:
                for i, (btn, name) in enumerate(unique_targets):
                    if sent >= args.max:
                        break
                    
                    print(f"[{sent+1}/{args.max}] {name}", end=" — ")
                    
                    if args.dry_run:
                        print("would send note.")
                        continue
                    
                    success = send_connection_request(driver, btn, name, args.note)
                    if success:
                        sent += 1
                        print("✅ Request sent!")
                    else:
                        skipped += 1
                        print("⏭️  Skipped.")
                        
                    human_delay()

            if sent >= args.max:
                break
                
            print("\n➡️ Moving to next page...")
            if not go_to_next_page(driver):
                print("🛑 No more pages available. Stopping.")
                break
            page += 1

        print(f"\n✅ Sent: {sent}   ⏭️  Skipped: {skipped}")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    print("Done.")

if __name__ == "__main__":
    main()
