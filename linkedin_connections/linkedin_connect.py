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

import asyncio
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
    "Hi {name}! I'm exploring agri equipment's future. {sender_name} here, a "
    "final-year IITB student. In academia, practical industry insight "
    "is a blind spot. I'm curious about new machinery development and "
    "vision. Your expertise would be huge to learn from! "
    "Quick call or online meet?"
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


def _chrome_pages(port, timeout=2.0):
    """
    Return the list of page targets Chrome is exposing via CDP, or None
    if Chrome isn't responding to the debugging endpoint at all.

    Note: an empty list means Chrome is alive but has zero tabs — that's
    the state `browser-use` leaves it in after a session reset, and it's
    why ChromeDriver throws "unable to discover open pages".
    """
    import urllib.request
    import json
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode())
        return [p for p in data if p.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return None


def _chrome_open_new_tab(port, url, timeout=5.0):
    """Open a new tab in the running Chrome via CDP. Returns True on success."""
    import urllib.request
    try:
        # Encoded URL must be passed as a path segment to /json/new.
        from urllib.parse import quote
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        # Older Chrome versions accept GET on /json/new.
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/new?{url}", timeout=timeout
            ) as resp:
                return 200 <= resp.status < 300
        except Exception:  # noqa: BLE001
            return False


def _kill_chrome_on_debug_port(port):
    """Kill any Chrome process bound to the given remote-debugging port."""
    try:
        subprocess.run(
            ["pkill", "-f", f"remote-debugging-port={port}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give the OS a moment to release the port.
        for _ in range(10):
            time.sleep(0.5)
            if not is_port_open(port):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _find_chrome_executable():
    """Return the Chrome executable path for the current OS, prompting if not found."""
    import platform
    import shutil

    system = platform.system()
    candidates = []

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium-browser"),
            shutil.which("chromium"),
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]

    for path in candidates:
        if path and os.path.isfile(path):
            return path

    print(
        f"⚠️  Could not find Chrome automatically on {system}.\n"
        "Please enter the full path to your Chrome/Chromium executable:"
    )
    user_path = input("Chrome path: ").strip()
    if not os.path.isfile(user_path):
        raise FileNotFoundError(f"Chrome executable not found at: {user_path}")
    return user_path


def _spawn_chrome(url):
    """Spawn a fresh Chrome process with our automation profile.

    The flags after the basics matter for headless-style automation:
    they stop Chrome from pausing JS / animations when the tab is not
    in the foreground, which used to break the LinkedIn More-menu
    dropdown when the user switched away to a different tab/window.

    Note: --disable-blink-features=AutomationControlled was removed
    because Chrome shows a yellow "unsupported command-line flag"
    warning bar when it's set. Stealth (hiding navigator.webdriver)
    is now applied via CDP after attach — see _apply_cdp_stealth().
    """
    os.makedirs(AUTOMATION_PROFILE_DIR, exist_ok=True)
    chrome_exe = _find_chrome_executable()
    subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={AUTOMATION_PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            # Keep the tab running at full speed even when backgrounded —
            # LinkedIn's dropdowns animate in via requestAnimationFrame,
            # which Chrome pauses on hidden tabs by default.
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_chrome(url):
    """
    Ensure a Chrome instance is running with remote debugging AND at least
    one page open. Handles three states:

      1. Nothing on port 9222 → spawn Chrome.
      2. Port open + CDP responsive + ≥1 page → attach.
      3. Port open but CDP unresponsive OR zero pages (the state
         `browser-use` leaves behind after a session reset) → open a new
         tab via CDP, or as a last resort kill+respawn.
    """
    if is_port_open(DEBUG_PORT):
        pages = _chrome_pages(DEBUG_PORT)

        if pages is None:
            print(
                f"⚠️  Port {DEBUG_PORT} is open but Chrome isn't responding. "
                "Killing the zombie and relaunching..."
            )
            _kill_chrome_on_debug_port(DEBUG_PORT)
        elif len(pages) == 0:
            print(
                f"⚠️  Chrome on port {DEBUG_PORT} has 0 open pages "
                "(probably left over from a browser-use session). "
                "Opening a new tab..."
            )
            if _chrome_open_new_tab(DEBUG_PORT, url):
                # Give the new tab a moment to register with CDP.
                for _ in range(10):
                    time.sleep(0.5)
                    pages = _chrome_pages(DEBUG_PORT) or []
                    if pages:
                        print("✅ New tab opened — Chrome is ready.")
                        return
            print(
                "⚠️  Couldn't open a new tab via CDP. Killing and relaunching..."
            )
            _kill_chrome_on_debug_port(DEBUG_PORT)
        else:
            print(
                f"✅ Chrome automation instance already running on port "
                f"{DEBUG_PORT} ({len(pages)} page(s) open)."
            )
            return

    print("🚀 Launching Chrome...")
    _spawn_chrome(url)

    # Wait for Chrome to start AND expose at least one page.
    for _ in range(25):
        time.sleep(1)
        if is_port_open(DEBUG_PORT):
            pages = _chrome_pages(DEBUG_PORT) or []
            if pages:
                print("✅ Chrome is ready.")
                return
    print(
        "❌ Chrome didn't start cleanly. Run "
        f"`pkill -f 'remote-debugging-port={DEBUG_PORT}'` and try again."
    )
    sys.exit(1)


def _apply_cdp_stealth(driver):
    """Hide automation fingerprints via CDP.

    Replaces the `--disable-blink-features=AutomationControlled` flag we
    used to pass at launch — that flag causes Chrome to show a yellow
    warning bar at the top of every tab. Doing it through CDP runs before
    any page JS, so navigator.webdriver is undefined for everything
    LinkedIn inspects.
    """
    stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
        window.chrome = window.chrome || { runtime: {} };
    """
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js}
        )
        # addScriptToEvaluateOnNewDocument only fires on the next document
        # load, so apply once to the current page too.
        driver.execute_script(stealth_js)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Couldn't install CDP stealth shim: {e}")


def get_chrome_driver():
    """Connect to the running Chrome instance via remote debugging.

    Wraps the ChromeDriver init with a helpful message if Chrome is in a
    bad state (no pages, zombie process) so the user knows what to do.
    """
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "unable to discover open pages" in msg or "cannot connect to chrome" in msg:
            print(
                "\n❌ ChromeDriver couldn't attach to Chrome on port "
                f"{DEBUG_PORT}.\n"
                "   This usually happens when `browser-use` closed all tabs "
                "during cleanup.\n"
                "   Run:\n"
                f"     pkill -f 'remote-debugging-port={DEBUG_PORT}'\n"
                "   then re-run the script. (The new launch_chrome() should "
                "auto-recover, but a hard kill always works.)"
            )
        raise
    _apply_cdp_stealth(driver)
    return driver


def human_delay():
    """Random sleep to mimic human behavior."""
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def _scroll_load_more(driver, pause=2.5):
    """
    Scroll down one screen and click "Show more results" if present.
    Returns True if new content likely loaded (page got taller or button
    was found and clicked), False otherwise.

    Used by the via-profile loop to fetch more candidates when the
    initially-visible set runs out.
    """
    try:
        last_height = driver.execute_script("return document.body.scrollHeight")
    except Exception:  # noqa: BLE001
        last_height = 0

    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    except Exception:  # noqa: BLE001
        pass
    time.sleep(pause)

    clicked_more = False
    try:
        show_more = driver.find_element(
            By.XPATH, "//button[contains(., 'Show more results')]"
        )
        if show_more.is_displayed():
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", show_more
            )
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", show_more)
            clicked_more = True
            time.sleep(pause)
    except NoSuchElementException:
        pass

    try:
        new_height = driver.execute_script("return document.body.scrollHeight")
    except Exception:  # noqa: BLE001
        new_height = last_height

    return clicked_more or new_height > last_height


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


def _first_name_from_url(profile_url):
    """
    Extract a best-guess first name from a LinkedIn profile slug.

    LinkedIn slugs look like:
        /in/vineet-gautam-46040018
        /in/sameer-mannava-76251b1a0
        /in/john-doe
    We take the first token, drop trailing digits/IDs.
    """
    try:
        slug = profile_url.rstrip("/").split("/in/")[-1]
        slug = slug.split("?")[0]
        first_token = slug.split("-")[0]
        # Drop trailing digits and underscores.
        cleaned = "".join(ch for ch in first_token if ch.isalpha())
        if cleaned and len(cleaned) >= 2:
            return cleaned.title()
    except Exception:  # noqa: BLE001
        pass
    return "there"


def _looks_like_real_name(name):
    """Heuristic: reject things that obviously aren't a first name."""
    if not name or len(name) < 2:
        return False
    bad_tokens = {
        "linkedin", "member", "more", "follow", "message", "connect",
        "pending", "view", "open", "3rd", "2nd", "1st", "premium",
        "dole",  # observed in the wild as a badge artifact
    }
    if name.lower() in bad_tokens:
        return False
    # Must be mostly alphabetic.
    alpha = sum(1 for c in name if c.isalpha())
    return alpha >= max(2, int(len(name) * 0.7))


def _extract_first_name(card, link, profile_url):
    """Pick a believable first name for a person. Falls back to the URL slug."""
    candidates = []

    title_selectors = (
        "div.artdeco-entity-lockup__title",
        "span.artdeco-entity-lockup__title",
        "div.org-people-profile-card__profile-title",
        "span.org-people-profile-card__profile-title",
        "div.discover-person-card__name",
        "span.discover-person-card__name",
        ".entity-result__title-text",
        ".profile-card-name",
    )
    for sel in title_selectors:
        try:
            el = card.find_element(By.CSS_SELECTOR, sel)
            candidates.append(el.text.strip())
        except NoSuchElementException:
            pass

    # The profile link's aria-label is usually "View <Full Name>'s profile".
    aria = (link.get_attribute("aria-label") or "").strip()
    if aria:
        cleaned = aria
        for prefix in ("View ", "Open "):
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):]
        for suffix in ("'s profile", "’s profile"):
            if cleaned.lower().endswith(suffix.lower()):
                cleaned = cleaned[: -len(suffix)]
        candidates.append(cleaned.strip())

    candidates.append(link.text.strip())

    for text in candidates:
        if not text:
            continue
        if text.lower().startswith("linkedin member"):
            continue
        token = text.split()[0]
        # Strip trailing ellipsis / punctuation from truncated names like
        # "Sydney" (from "Sydney (Burlis...") or "Damián" (from "Damián Sanch...").
        token = token.rstrip(".…(),")
        if _looks_like_real_name(token):
            return token.title()

    return _first_name_from_url(profile_url)


def find_profiles_without_connect(driver):
    """
    Find people on the page who DON'T have an inline "Connect" button and
    aren't already "Pending". These get handed to the LLM agent.

    Robust to multiple page sections (e.g. "Employees" + "People you may
    know") because we walk every /in/ profile link and find its card-ish
    ancestor, rather than betting on one card-class selector.

    Returns list of (profile_url, first_name), deduped by profile URL.
    """
    by_url = {}  # url -> {"first_name": str, "has_connect": bool}

    # Every visible profile link on the page.
    profile_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/in/']")

    for link in profile_links:
        try:
            href = (link.get_attribute("href") or "").strip()
            if "/in/" not in href:
                continue
            # Strip query params and trailing slash for stable dedup.
            href = href.split("?")[0].rstrip("/")

            # Find the nearest "card-ish" ancestor. We try common shapes;
            # first match wins. Doesn't matter which section the card is in.
            card = None
            for xp in (
                "./ancestor::li[1]",
                "./ancestor::section[1]",
                "./ancestor::div[contains(@class, 'card') "
                "or contains(@class, 'lockup') "
                "or contains(@class, 'entity-result')][1]",
            ):
                try:
                    card = link.find_element(By.XPATH, xp)
                    break
                except NoSuchElementException:
                    continue
            if card is None:
                continue

            # Heuristic: a real people card has at least one action button
            # (Connect / Message / Follow / Pending). Skips nav-bar profile
            # links, mentions in posts, etc.
            if not card.find_elements(By.TAG_NAME, "button"):
                continue

            has_connect_btn = bool(card.find_elements(
                By.XPATH,
                ".//button[contains(@aria-label, 'Invite') "
                "and contains(@aria-label, 'to connect')]",
            ))
            is_pending = bool(card.find_elements(
                By.XPATH,
                ".//button[contains(@aria-label, 'Pending')] "
                "| .//button[normalize-space()='Pending']",
            ))

            if has_connect_btn:
                # Mark as handled by the inline Selenium flow so we never
                # add them to the agent queue even if they appear twice.
                by_url[href] = {"first_name": "", "has_connect": True}
                continue
            if is_pending:
                continue

            existing = by_url.get(href)
            if existing is None:
                first_name = _extract_first_name(card, link, href)
                by_url[href] = {"first_name": first_name, "has_connect": False}
            # If we already saw this URL elsewhere as agent-eligible, keep it.
        except StaleElementReferenceException:
            continue

    return [
        (url, info["first_name"])
        for url, info in by_url.items()
        if not info["has_connect"]
    ]


def _fill_and_send_connect_modal(driver, name, personalized_note):
    """
    Run the shared "Add a note → type → Send" flow once a Connect dialog
    has been opened. Returns True on Send-clicked, False otherwise.
    Caller is responsible for dismissing leftover modals on False.
    """
    # Wait for the connect modal to actually appear. On Linux Chrome it can
    # take several seconds. We poll for any of the modal's canonical markers
    # (the Add-a-note button, the Send button, or the textarea) — these are
    # only ever present when the modal is open, so we won't false-match on
    # other page state.
    modal_ready = False
    modal_match_info = None
    poll_start = time.time()
    deadline = poll_start + 12
    # Counter so we only log JS errors a couple of times rather than 30x.
    js_err_count = [0]
    while time.time() < deadline:
        try:
            found = driver.execute_script("""
                // Walk the WHOLE DOM (including same-origin iframes and the
                // shadow DOMs we can reach) looking for a button that is
                // textually 'Add a note' / 'Send without a note' / 'Send
                // invitation', or a textarea[name=message]. Returns an
                // object describing the first visible match, or null.
                function describe(el, sel, where) {
                    const r = el.getBoundingClientRect();
                    return {
                        selector: sel,
                        where: where,
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                        ariaLabel: ((el.getAttribute && el.getAttribute('aria-label')) || '').slice(0, 80),
                        rect: {x: Math.round(r.x), y: Math.round(r.y),
                               w: Math.round(r.width), h: Math.round(r.height)},
                    };
                }
                function walk(root, where) {
                    const markerSelectors = [
                        'button[aria-label*="Add a note" i]',
                        'button[aria-label*="Send invitation" i]',
                        'button[aria-label*="Send without a note" i]',
                        'textarea[name="message"]',
                        'textarea#custom-message',
                        '[data-test-modal-id="send-invite-modal"]',
                        '[data-test-modal]',
                        'dialog[open]',
                    ];
                    for (const sel of markerSelectors) {
                        let nodes;
                        try { nodes = root.querySelectorAll(sel); }
                        catch (e) { continue; }
                        for (const el of nodes) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                return describe(el, sel, where);
                            }
                        }
                    }
                    let btns;
                    try {
                        btns = root.querySelectorAll('button, [role="button"], a[role="button"]');
                    } catch (e) { btns = []; }
                    for (const b of btns) {
                        const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                        const aria = (b.getAttribute && (b.getAttribute('aria-label') || '')).toLowerCase();
                        const combined = t + ' ' + aria;
                        if (combined.includes('add a note')
                            || combined.includes('send without a note')
                            || combined.includes('send invitation')) {
                            const r = b.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                return describe(b, '[text-walk]', where);
                            }
                        }
                    }
                    try {
                        const all = root.querySelectorAll('*');
                        for (const el of all) {
                            if (el.shadowRoot) {
                                const m = walk(el.shadowRoot, where + ' > shadow');
                                if (m) return m;
                            }
                        }
                    } catch (e) { /* ignore */ }
                    return null;
                }
                let m = walk(document, 'document');
                if (m) return m;
                try {
                    let i = 0;
                    for (const f of document.querySelectorAll('iframe')) {
                        const src = (f.getAttribute('src') || '');
                        try {
                            const doc = f.contentDocument;
                            if (doc) {
                                m = walk(doc, 'iframe[' + i + '] src=' + src.slice(0, 60));
                                if (m) return m;
                            }
                        } catch (e) { /* cross-origin */ }
                        i++;
                    }
                } catch (e) { /* ignore */ }
                return null;
            """)
            if found:
                modal_ready = True
                modal_match_info = found
                break
        except Exception as e:  # noqa: BLE001
            # Log the first 2 errors only — silent failure was hiding bugs.
            if js_err_count[0] < 2:
                print(f"      ⚠️  Modal-poll JS error: {e}")
                js_err_count[0] += 1
        time.sleep(0.4)
    poll_elapsed = time.time() - poll_start
    if modal_ready:
        info = modal_match_info or {}
        print(
            f"      ✓ Modal detected after {poll_elapsed:.1f}s "
            f"via selector {info.get('selector')!r} in {info.get('where')!r} "
            f"(tag={info.get('tag')!r} text={info.get('text')!r} "
            f"aria-label={info.get('ariaLabel')!r} rect={info.get('rect')})"
        )
        time.sleep(0.6)  # let React finish mounting children
    else:
        print(f"      ✗ Modal never appeared (waited {poll_elapsed:.1f}s)")
        _dump_modal_diagnostics(driver, name, "modal-poll-timeout")

    # LinkedIn now hosts the Connect modal inside an iframe (src='/preload/...')
    # on many profiles. None of our XPath/CSS searches will see anything
    # inside that iframe until we switch the Selenium context into it.
    # We detect this once here and stay inside the frame for the rest of
    # this function. A try/finally at the call site (this function's outer
    # wrapper) restores default_content even on exceptions.
    switched_into_iframe = False
    iframe_el = _find_invite_iframe(driver)
    if iframe_el is not None and _iframe_contains_invite_markers(driver, iframe_el):
        try:
            src = (iframe_el.get_attribute("src") or "")[:80]
            driver.switch_to.frame(iframe_el)
            switched_into_iframe = True
            print(
                f"      🔁 Switched into invite iframe — modal lives there "
                f"(src={src!r})."
            )
        except Exception as e:  # noqa: BLE001
            print(f"      ⚠️  Could not switch into invite iframe: {e}")

    try:
        return _do_invite_modal_in_current_context(
            driver, name, personalized_note
        )
    finally:
        if switched_into_iframe:
            try:
                driver.switch_to.default_content()
                print(f"      🔁 Switched back to main document.")
            except Exception:  # noqa: BLE001
                pass


def _do_invite_modal_in_current_context(driver, name, personalized_note):
    """
    Add-a-note → type note → Send, with full Shadow-DOM support.

    LinkedIn renders the Connect modal inside a Shadow DOM (poll output
    reports 'where=document > shadow'). Selenium's find_element / XPath
    APIs cannot pierce shadow roots — every interaction here therefore
    runs via execute_script with JS that recursively walks the shadow
    tree (plus same-origin iframes for older variants).

    For the textarea we use React's native value setter + dispatchEvent
    so React's onChange fires and the Send button enables.
    """

    # Helpers injected into every step. Walks document + every reachable
    # shadow root, returning the first element matching predicate(el).
    js_helpers = """
        function findShadowDeep(root, predicate) {
            const stack = [root];
            while (stack.length) {
                const node = stack.pop();
                let kids;
                try {
                    kids = node.querySelectorAll
                        ? node.querySelectorAll('*') : [];
                } catch (e) { continue; }
                for (const k of kids) {
                    try { if (predicate(k)) return k; } catch (e) {}
                    if (k.shadowRoot) stack.push(k.shadowRoot);
                }
            }
            return null;
        }
        function findAnywhere(predicate) {
            let el = findShadowDeep(document, predicate);
            if (el) return el;
            for (const f of document.querySelectorAll('iframe')) {
                try {
                    const doc = f.contentDocument;
                    if (doc) {
                        el = findShadowDeep(doc, predicate);
                        if (el) return el;
                    }
                } catch (e) { /* cross-origin */ }
            }
            return null;
        }
        function isVisible(el) {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }
        function btnText(el) {
            const t = (el.innerText || el.textContent || '').trim().toLowerCase();
            const aria = ((el.getAttribute && el.getAttribute('aria-label')) || '').toLowerCase();
            return t + ' | ' + aria;
        }
        function isBtnish(el) {
            if (!el || !el.tagName) return false;
            if (el.tagName === 'BUTTON') return true;
            if (el.getAttribute && el.getAttribute('role') === 'button') return true;
            return false;
        }
    """

    # Step 1: click "Add a note" (or notice the textarea is already mounted).
    click_add_note_js = js_helpers + """
        const taPred = el => (
            el.tagName === 'TEXTAREA' &&
            (el.name === 'message' || el.id === 'custom-message') &&
            isVisible(el)
        );
        if (findAnywhere(taPred)) return {state: 'textarea-ready'};

        const btnPred = el => {
            if (!isBtnish(el) || el.disabled) return false;
            const combined = btnText(el);
            if (!combined.includes('add a note')) return false;
            return isVisible(el);
        };
        const btn = findAnywhere(btnPred);
        if (!btn) return {state: 'add-note-missing'};
        try { btn.scrollIntoView({block: 'center'}); } catch (e) {}
        btn.click();
        return {state: 'clicked',
                text: (btn.innerText || '').trim().slice(0, 60),
                aria: (btn.getAttribute('aria-label') || '').slice(0, 60)};
    """

    try:
        result = driver.execute_script(click_add_note_js) or {}
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️  Add-a-note JS error: {e}")
        result = {}

    state = result.get('state')
    if state == 'add-note-missing':
        print(f"  ⚠️  Could not find 'Add a note' button for {name}.")
        _dump_modal_diagnostics(driver, name, "add-note-missing")
        return False
    if state == 'textarea-ready':
        print(f"      ℹ️  Textarea already mounted — skipping Add-a-note step.")
    elif state == 'clicked':
        print(
            f"      ✓ Clicked Add a note (text={result.get('text')!r}, "
            f"aria={result.get('aria')!r}). Waiting for textarea..."
        )
        time.sleep(1.5)
    else:
        print(f"  ⚠️  Unexpected Add-a-note result for {name}: {result!r}")
        return False

    # Step 2: type the note via React-compatible setter, then dispatch
    # input/change events so React re-renders and enables the Send button.
    # We poll up to 5s on the Python side for the textarea to mount.
    type_note_js = js_helpers + """
        const note = arguments[0];
        const taPred = el => (
            el.tagName === 'TEXTAREA' && (
                el.name === 'message'
                || el.id === 'custom-message'
                || ((el.className || '').toString()
                    .includes('connect-button-send-invite__custom-message'))
            )
        );
        const ta = findAnywhere(taPred);
        if (!ta) return {state: 'no-textarea'};
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value'
        ).set;
        ta.focus();
        setter.call(ta, '');
        ta.dispatchEvent(new Event('input', {bubbles: true}));
        setter.call(ta, note);
        ta.dispatchEvent(new Event('input', {bubbles: true}));
        ta.dispatchEvent(new Event('change', {bubbles: true}));
        return {state: 'typed', length: ta.value.length};
    """

    type_result = {}
    _t0 = time.time()
    while time.time() - _t0 < 5:
        try:
            type_result = driver.execute_script(
                type_note_js, personalized_note
            ) or {}
        except Exception as e:  # noqa: BLE001
            print(f"      ⚠️  Type-note JS error: {e}")
            type_result = {}
        if type_result.get('state') == 'typed':
            break
        time.sleep(0.3)

    if type_result.get('state') == 'no-textarea':
        print(f"  ⚠️  Could not find note text area for {name}.")
        _dump_modal_diagnostics(driver, name, "textarea-missing")
        return False
    if type_result.get('state') != 'typed':
        print(f"  ⚠️  Type-note unexpected result for {name}: {type_result!r}")
        return False
    print(f"      ✓ Typed note ({type_result.get('length')} chars).")
    time.sleep(0.8)

    # Step 3: click Send.
    click_send_js = js_helpers + """
        const btnPred = el => {
            if (!isBtnish(el) || el.disabled) return false;
            const combined = btnText(el);
            // Match the post-Add-a-note Send button. We deliberately do NOT
            // match plain 'Send' on its own, since that could be a messaging
            // panel button — Send buttons in the invite modal always have
            // aria-label of 'Send invitation' or 'Send now'.
            if (combined.includes('send invitation')
                || combined.includes('send now')) {
                return isVisible(el);
            }
            return false;
        };
        const btn = findAnywhere(btnPred);
        if (!btn) return {state: 'no-send'};
        try { btn.scrollIntoView({block: 'center'}); } catch (e) {}
        btn.click();
        return {state: 'sent',
                text: (btn.innerText || '').trim().slice(0, 60),
                aria: (btn.getAttribute('aria-label') || '').slice(0, 60)};
    """

    try:
        send_result = driver.execute_script(click_send_js) or {}
    except Exception as e:  # noqa: BLE001
        print(f"      ⚠️  Send JS error: {e}")
        send_result = {}

    if send_result.get('state') != 'sent':
        print(f"  ⚠️  Could not find Send button for {name}.")
        _dump_modal_diagnostics(driver, name, "send-button-missing")
        return False
    print(
        f"      ✓ Clicked Send (text={send_result.get('text')!r}, "
        f"aria={send_result.get('aria')!r})."
    )
    time.sleep(1.5)
    return True


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

        ok = _fill_and_send_connect_modal(driver, name, personalized_note)
        if not ok:
            _dismiss_modal(driver)
        return ok

    except ElementClickInterceptedException:
        print(f"  ⚠️  Click intercepted for {name}. Dismissing overlays...")
        _dismiss_modal(driver)
        return False
    except Exception as e:
        print(f"  ❌  Unexpected error for {name}: {e}")
        _dismiss_modal(driver)
        return False


def _click_with_fallback(driver, element):
    """Click an element trying multiple strategies (some LinkedIn buttons
    only respond to real / trusted mouse events, others reject JS clicks)."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", element
        )
        time.sleep(0.4)
    except Exception:  # noqa: BLE001
        pass

    # 1) Native click — generates a trusted event.
    try:
        element.click()
        return True
    except (ElementClickInterceptedException, Exception):  # noqa: BLE001
        pass

    # 2) ActionChains — moves to the element, also trusted.
    try:
        webdriver.ActionChains(driver).move_to_element(element).pause(0.2).click().perform()
        return True
    except Exception:  # noqa: BLE001
        pass

    # 3) JS click — fastest but some menus ignore it.
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:  # noqa: BLE001
        return False


def _describe_button(el):
    """Return a short, human-readable description of a button for logging."""
    try:
        aria = el.get_attribute("aria-label") or ""
        text = " ".join((el.text or "").split())[:60]
        cls = (el.get_attribute("class") or "")[:80]
        try:
            rect = el.rect
            pos = f"y={int(rect.get('y', 0))} h={int(rect.get('height', 0))}"
        except Exception:  # noqa: BLE001
            pos = ""
        return f"text={text!r} aria-label={aria!r} {pos} class={cls!r}"
    except StaleElementReferenceException:
        return "<stale>"


def _find_action_bar(driver):
    """
    Locate the profile action-bar container by anchoring on the Message
    or Follow button (which only exist in the action bar at the top of
    a profile), then walking UP to find the smallest ancestor that ALSO
    contains a 'More' button. That ancestor is the action bar.

    This avoids matching the "Show more activity" / "More" buttons that
    appear elsewhere on the profile page.
    """
    anchors = driver.find_elements(
        By.XPATH,
        "//main//button[normalize-space()='Message' "
        "or normalize-space()='Follow' "
        "or contains(@aria-label, 'Message ') "
        "or contains(@aria-label, 'Follow ')]",
    )
    for anchor in anchors:
        try:
            if not anchor.is_displayed():
                continue
            parent = anchor
            # Walk up at most ~10 levels looking for an ancestor that holds
            # a "More" button alongside the anchor.
            for _ in range(10):
                try:
                    parent = parent.find_element(By.XPATH, "./..")
                except NoSuchElementException:
                    break
                if (parent.tag_name or "").lower() == "body":
                    break
                mores = parent.find_elements(
                    By.XPATH,
                    ".//button[normalize-space()='More' "
                    "or @aria-label='More' "
                    "or contains(@aria-label, 'More actions')]",
                )
                visible_mores = []
                for m in mores:
                    try:
                        if m.is_displayed() and m.is_enabled():
                            visible_mores.append(m)
                    except StaleElementReferenceException:
                        continue
                if visible_mores:
                    return parent, visible_mores
        except StaleElementReferenceException:
            continue
    return None, []


def _find_connect_in_action_bar(driver, verbose=False):
    """
    Find a direct Connect control (button OR <a>) that lives inside the
    profile's top action bar. Anchored via _find_action_bar() so we never
    match the 'Invite <name> to connect' links in the 'More profiles for
    you' sidebar (which were silently getting clicked before).
    """
    action_bar, _ = _find_action_bar(driver)
    if action_bar is None:
        if verbose:
            print("      🔍 _find_connect_in_action_bar: no action bar found")
        return None
    candidates = action_bar.find_elements(
        By.XPATH,
        ".//*[(self::button or self::a) and ("
        "@aria-label='Connect' "
        "or (contains(@aria-label, 'Invite') "
        "and contains(@aria-label, 'to connect')))]",
    )
    for cand in candidates:
        try:
            if cand.is_displayed():
                if verbose:
                    print(
                        f"      🔍 Connect in action bar: "
                        f"{_describe_button(cand)}"
                    )
                return cand
        except StaleElementReferenceException:
            continue
    if verbose:
        print(
            f"      🔍 _find_connect_in_action_bar: action bar found but "
            f"no direct Connect inside it ({len(candidates)} candidates)"
        )
    return None


def _find_invite_iframe(driver):
    """
    Return the <iframe> that hosts LinkedIn's Connect modal, or None.

    Newer LinkedIn UIs preload the invitation modal inside an iframe with
    src='/preload/custom-invite/...' (or similar). The modal markup lives
    in that iframe's document, so Selenium driver-level find_element calls
    against the top-level page cannot see the Add-a-note button, textarea,
    or Send button.
    """
    for f in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            src = (f.get_attribute("src") or "").lower()
            if not src or src == "about:blank":
                continue
            if "/preload" in src or "invite" in src or "send-invite" in src:
                return f
        except StaleElementReferenceException:
            continue
    return None


def _iframe_contains_invite_markers(driver, iframe):
    """
    Switch into the given iframe and check if it contains Add-a-note /
    Send / textarea markers. Always switches back before returning.
    """
    try:
        driver.switch_to.frame(iframe)
    except Exception:  # noqa: BLE001
        return False
    try:
        has_modal = driver.execute_script("""
            const btns = document.querySelectorAll(
                'button, [role="button"], a[role="button"]'
            );
            for (const b of btns) {
                const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                const aria = ((b.getAttribute && b.getAttribute('aria-label')) || '').toLowerCase();
                const combined = t + ' ' + aria;
                if (combined.includes('add a note')
                    || combined.includes('send without a note')
                    || combined.includes('send invitation')
                    || combined.includes('personalize invite')) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return true;
                }
            }
            if (document.querySelector(
                'textarea[name="message"], textarea#custom-message'
            )) return true;
            return false;
        """)
        return bool(has_modal)
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:  # noqa: BLE001
            pass


def _dump_modal_diagnostics(driver, name, label):
    """
    Print everything we can about the page state when a modal step fails.
    Catches the two most common LinkedIn surprises:
      - The Connect click navigated to a different URL (e.g. /preload/...)
      - A dialog/modal IS in the DOM but is aria-hidden or off-screen
    """
    print(f"  🩺 [{label}] Diagnostic dump for {name}:")
    try:
        print(f"      • current_url: {driver.current_url}")
    except Exception as e:  # noqa: BLE001
        print(f"      • current_url: <error: {e}>")
    try:
        title = driver.title
        print(f"      • title:       {title!r}")
    except Exception:  # noqa: BLE001
        pass
    try:
        info = driver.execute_script("""
            const out = {
                dialogs: [], nativeDialogs: [], modals: [], testModals: [],
                addNoteBtns: [], sendBtns: [], textareas: [],
                topLevelOverlays: [], iframes: []
            };
            function describe(el) {
                const r = el.getBoundingClientRect();
                return {
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    role: el.getAttribute('role') || '',
                    ariaHidden: el.getAttribute('aria-hidden') || '',
                    ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
                    ariaLabelledby: el.getAttribute('aria-labelledby') || '',
                    dataTestModalId: el.getAttribute('data-test-modal-id') || '',
                    dataTestModal: el.getAttribute('data-test-modal') || '',
                    cls: (el.className && el.className.toString
                          ? el.className.toString() : (el.className || '')).slice(0, 200),
                    text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                    visible: r.width > 0 && r.height > 0,
                    rect: {x: Math.round(r.x), y: Math.round(r.y),
                           w: Math.round(r.width), h: Math.round(r.height)},
                };
            }
            document.querySelectorAll('[role="dialog"]').forEach(d =>
                out.dialogs.push(describe(d)));
            document.querySelectorAll('dialog').forEach(d =>
                out.nativeDialogs.push(describe(d)));
            document.querySelectorAll('.artdeco-modal').forEach(m =>
                out.modals.push(describe(m)));
            document.querySelectorAll(
                '[data-test-modal], [data-test-modal-id]'
            ).forEach(m => out.testModals.push(describe(m)));
            document.querySelectorAll(
                'button, [role="button"], a[role="button"]'
            ).forEach(b => {
                const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                const aria = ((b.getAttribute && b.getAttribute('aria-label')) || '').toLowerCase();
                const combined = t + ' ' + aria;
                const r = b.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return;
                if (combined.includes('add a note') || combined.includes('personalize invite')) {
                    out.addNoteBtns.push(describe(b));
                }
                if (combined.includes('send invitation')
                    || combined.includes('send without a note')
                    || combined === 'send') {
                    out.sendBtns.push(describe(b));
                }
            });
            document.querySelectorAll('textarea').forEach(t =>
                out.textareas.push(describe(t)));
            // Any visible large fixed-positioned element directly under <body>
            // — modals are often portaled here.
            Array.from(document.body.children).forEach(c => {
                const r = c.getBoundingClientRect();
                if (r.width >= 200 && r.height >= 100 && r.width > 0) {
                    const cs = getComputedStyle(c);
                    if (cs.position === 'fixed' || cs.position === 'absolute') {
                        out.topLevelOverlays.push(describe(c));
                    }
                }
            });
            document.querySelectorAll('iframe').forEach(f => {
                out.iframes.push({
                    src: (f.getAttribute('src') || '').slice(0, 120),
                    rect: f.getBoundingClientRect(),
                });
            });
            return out;
        """) or {}

        def _print_group(label, items, max_items=6):
            print(f"      • {label} count: {len(items)}")
            for i, d in enumerate(items[:max_items]):
                rect = d.get("rect", {})
                print(
                    f"          [{i}] tag={d.get('tag')!r} id={d.get('id')!r} "
                    f"role={d.get('role')!r} aria-hidden={d.get('ariaHidden')!r} "
                    f"data-test-modal-id={d.get('dataTestModalId')!r} "
                    f"visible={d.get('visible')} rect={rect}"
                )
                if d.get("ariaLabel"):
                    print(f"               aria-label={d.get('ariaLabel')!r}")
                if d.get("text"):
                    print(f"               text={d.get('text')!r}")
                if d.get("cls"):
                    print(f"               class={d.get('cls')!r}")

        _print_group("[role=dialog]", info.get("dialogs", []))
        _print_group("<dialog>", info.get("nativeDialogs", []))
        _print_group(".artdeco-modal", info.get("modals", []))
        _print_group("[data-test-modal*]", info.get("testModals", []))
        _print_group("Buttons containing 'Add a note'", info.get("addNoteBtns", []))
        _print_group("Buttons containing 'Send...'", info.get("sendBtns", []))
        _print_group("<textarea>", info.get("textareas", []))
        _print_group(
            "Top-level fixed/absolute overlays (potential portal modals)",
            info.get("topLevelOverlays", []),
        )
        iframes = info.get("iframes", [])
        if iframes:
            print(f"      • iframes: {len(iframes)}")
            for i, f in enumerate(iframes[:4]):
                print(f"          [{i}] src={f.get('src')!r}")
    except Exception as e:  # noqa: BLE001
        print(f"      • (dialog/modal probe failed: {e})")

    # Now look INSIDE each plausible invite-iframe. The top-level scan above
    # won't see anything in there, which was the root cause of every
    # 'modal never appeared' failure on the current LinkedIn UI.
    try:
        iframes_to_probe = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:  # noqa: BLE001
        iframes_to_probe = []
    for idx, f in enumerate(iframes_to_probe):
        try:
            src = (f.get_attribute("src") or "")
        except Exception:  # noqa: BLE001
            src = ""
        # Probe EVERY iframe — LinkedIn sometimes dynamically writes the
        # modal into an about:blank iframe, so we can't skip those.
        print(f"      🔬 Probing iframe[{idx}] src={src[:120]!r} ...")
        try:
            driver.switch_to.frame(f)
        except Exception as e:  # noqa: BLE001
            print(f"           (could not switch in: {e})")
            continue
        try:
            iframe_info = driver.execute_script("""
                const out = {
                    addNoteBtns: [], sendBtns: [], textareas: [],
                    testModals: [], nativeDialogs: [], roleDialogs: [],
                    artdecoModals: [],
                    allButtons: 0,
                    bodyTextSnippet: ''
                };
                function describe(el) {
                    const r = el.getBoundingClientRect();
                    return {
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
                        text: (el.innerText || el.textContent || '').trim().slice(0, 80),
                        visible: r.width > 0 && r.height > 0,
                        rect: {x: Math.round(r.x), y: Math.round(r.y),
                               w: Math.round(r.width), h: Math.round(r.height)},
                    };
                }
                const btns = document.querySelectorAll(
                    'button, [role="button"], a[role="button"]'
                );
                out.allButtons = btns.length;
                btns.forEach(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    const aria = ((b.getAttribute && b.getAttribute('aria-label')) || '').toLowerCase();
                    const combined = t + ' ' + aria;
                    const r = b.getBoundingClientRect();
                    // Report ALL relevant buttons, even if currently invisible —
                    // we want to know if the modal is mounted but display:none'd.
                    if (combined.includes('add a note') || combined.includes('personalize')) {
                        out.addNoteBtns.push(describe(b));
                    }
                    if (combined.includes('send invitation')
                        || combined.includes('send without a note')
                        || combined === 'send') {
                        out.sendBtns.push(describe(b));
                    }
                });
                document.querySelectorAll('textarea').forEach(t =>
                    out.textareas.push(describe(t)));
                document.querySelectorAll(
                    '[data-test-modal], [data-test-modal-id]'
                ).forEach(d => out.testModals.push(describe(d)));
                document.querySelectorAll('dialog').forEach(d =>
                    out.nativeDialogs.push(describe(d)));
                document.querySelectorAll('[role="dialog"]').forEach(d =>
                    out.roleDialogs.push(describe(d)));
                document.querySelectorAll('.artdeco-modal').forEach(d =>
                    out.artdecoModals.push(describe(d)));
                out.bodyTextSnippet = (document.body ? document.body.innerText : '').slice(0, 200);
                return out;
            """) or {}
            print(
                f"           buttons={iframe_info.get('allButtons', 0)}, "
                f"role=dialog={len(iframe_info.get('roleDialogs', []))}, "
                f".artdeco-modal={len(iframe_info.get('artdecoModals', []))}, "
                f"<dialog>={len(iframe_info.get('nativeDialogs', []))}, "
                f"[data-test-modal*]={len(iframe_info.get('testModals', []))}"
            )
            for grp_label, key in (
                ("Add a note buttons", "addNoteBtns"),
                ("Send buttons", "sendBtns"),
                ("Textareas", "textareas"),
                ("[data-test-modal*]", "testModals"),
                ("<dialog>", "nativeDialogs"),
                ("[role=dialog]", "roleDialogs"),
                (".artdeco-modal", "artdecoModals"),
            ):
                items = iframe_info.get(key, [])
                if items:
                    print(f"           {grp_label}: {len(items)}")
                    for j, it in enumerate(items[:4]):
                        print(
                            f"               [{j}] tag={it.get('tag')!r} "
                            f"text={it.get('text')!r} "
                            f"aria-label={it.get('ariaLabel')!r} "
                            f"visible={it.get('visible')} rect={it.get('rect')}"
                        )
            snippet = iframe_info.get("bodyTextSnippet") or ""
            if snippet.strip():
                print(f"           body-text-preview: {snippet!r}")
        except Exception as e:  # noqa: BLE001
            print(f"           (iframe probe failed: {e})")
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:  # noqa: BLE001
                pass

    # Save a full-page screenshot so we can eyeball what LinkedIn rendered.
    try:
        shot_dir = "/tmp/linkedin_debug"
        os.makedirs(shot_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() else "_" for c in name)[:40]
        path = os.path.join(
            shot_dir, f"{int(time.time())}_{label}_{safe_name}.png"
        )
        driver.save_screenshot(path)
        print(f"      • screenshot:  {path}")
    except Exception as e:  # noqa: BLE001
        print(f"      • (screenshot failed: {e})")


def _find_more_button(driver, verbose=False):
    """Find the action-bar "More" button on a profile page.

    Strategy (in order of preference):
      1. The Message/Follow-anchored action-bar container — only "More"
         buttons inside that container are real candidates.
      2. Explicit aria-label matches ("More actions", "More actions, ...").
      3. As a last resort, any visible "More" button in <main>, but only
         if its vertical position is in the top half of the page (action
         bars live near the top).
    """
    # Strategy 1: anchor on the action-bar container.
    action_bar, action_bar_mores = _find_action_bar(driver)
    if action_bar_mores:
        chosen = action_bar_mores[0]
        if verbose:
            print(
                f"      🎯 More-button candidates inside action bar: "
                f"{len(action_bar_mores)}. Picking first: {_describe_button(chosen)}"
            )
        return chosen

    # Strategy 2: explicit aria-label.
    for xp in (
        "//button[@aria-label='More actions']",
        "//button[starts-with(@aria-label, 'More actions')]",
        "//button[contains(@aria-label, 'More actions, distance')]",
    ):
        for cand in driver.find_elements(By.XPATH, xp):
            try:
                if cand.is_displayed() and cand.is_enabled():
                    if verbose:
                        print(
                            f"      🎯 Found via aria-label: "
                            f"{_describe_button(cand)}"
                        )
                    return cand
            except StaleElementReferenceException:
                continue

    # Strategy 3: top-of-page "More" button.
    try:
        viewport_h = driver.execute_script("return window.innerHeight;") or 800
    except Exception:  # noqa: BLE001
        viewport_h = 800
    candidates = driver.find_elements(
        By.XPATH,
        "//main//button[normalize-space()='More' "
        "or @aria-label='More' "
        "or .//span[normalize-space()='More']]",
    )
    for cand in candidates:
        try:
            if not (cand.is_displayed() and cand.is_enabled()):
                continue
            rect = cand.rect
            # Only accept if button is in the upper ~60% of the viewport.
            if rect.get("y", 0) < viewport_h * 0.6:
                if verbose:
                    print(
                        f"      🎯 Fallback top-of-page match: "
                        f"{_describe_button(cand)}"
                    )
                return cand
        except StaleElementReferenceException:
            continue
    return None


def _dump_more_buttons(driver):
    """Print all visible More-ish buttons we can see, for debugging."""
    print("      🔍 All visible 'More' candidates on the page:")
    seen = []
    for xp in (
        "//button[normalize-space()='More']",
        "//button[@aria-label='More']",
        "//button[contains(@aria-label, 'More actions')]",
        "//button[.//span[normalize-space()='More']]",
    ):
        for cand in driver.find_elements(By.XPATH, xp):
            try:
                if not cand.is_displayed():
                    continue
                key = (cand.location.get("y"), cand.text or "", cand.get_attribute("aria-label") or "")
                if key in seen:
                    continue
                seen.append(key)
                print(f"         - {_describe_button(cand)}")
            except StaleElementReferenceException:
                continue
    if not seen:
        print("         (none found)")


def _wait_for_open_dropdown(driver, timeout=6.0):
    """Wait until ANY visible dropdown/menu element appears."""
    deadline = time.time() + timeout
    selectors = (
        "//div[@role='menu' and not(contains(@style, 'display: none'))]",
        "//div[contains(@class, 'artdeco-dropdown__content--is-open')]",
        "//div[contains(@class, 'artdeco-dropdown__content')]",
        "//ul[@role='menu']",
    )
    while time.time() < deadline:
        for sel in selectors:
            for el in driver.find_elements(By.XPATH, sel):
                try:
                    if el.is_displayed():
                        return el
                except StaleElementReferenceException:
                    continue
        time.sleep(0.25)
    return None


def _find_connect_in_dropdown(driver):
    """Find the 'Connect' item inside an open dropdown.

    IMPORTANT: every strategy must scope its match to an actual dropdown
    container — otherwise it'll match sidebar 'Invite X to connect' buttons
    in the 'More profiles for you' section, which are always present on the
    page and have NOTHING to do with the More menu we just opened.
    """
    # Anchor every search inside an *open* (not aria-hidden='true') dropdown
    # or menu container.
    open_menu = (
        "//div[(@role='menu' or contains(@class, 'artdeco-dropdown__content')) "
        "and not(@aria-hidden='true')]"
    )
    strategies = (
        # role=menuitem variants, scoped to an open menu.
        open_menu + "//*[@role='menuitem' and (normalize-space()='Connect' "
        "or .//*[normalize-space()='Connect'])]",
        open_menu + "//*[@role='menuitem' and contains(., 'Connect') "
        "and not(contains(., 'Remove connection'))]",
        # LinkedIn's dropdown item classes, scoped to an open menu.
        open_menu + "//*[contains(@class, 'artdeco-dropdown__item') "
        "and contains(., 'Connect') and not(contains(., 'Remove connection'))]",
        open_menu + "//*[contains(@class, 'dropdown__item') "
        "and contains(., 'Connect') and not(contains(., 'Remove connection'))]",
        # Plain text match, scoped to an open menu.
        open_menu + "//*[normalize-space()='Connect']",
        # Aria-label match — MUST be scoped to the open dropdown, otherwise
        # it matches sidebar 'Invite <Name> to connect' buttons (which are
        # always present on the page and unrelated to the More menu).
        open_menu + "//*[contains(@aria-label, 'Invite') "
        "and contains(@aria-label, 'to connect')]",
        # "Personalize invite" wording, scoped.
        open_menu + "//*[@role='menuitem' and contains(., 'Personalize invite')]",
        open_menu + "//*[contains(., 'Personalize invite')]",
    )
    for xp in strategies:
        for cand in driver.find_elements(By.XPATH, xp):
            try:
                if cand.is_displayed():
                    return cand
            except StaleElementReferenceException:
                continue
    return None


def _dump_dropdown_items(driver, label="dropdown"):
    """Print visible text of any open dropdown's items for debugging."""
    items = driver.find_elements(
        By.XPATH,
        "//div[@role='menu']//*[@role='menuitem'] "
        "| //div[contains(@class, 'artdeco-dropdown__content')]"
        "//*[contains(@class, 'artdeco-dropdown__item')]",
    )
    visible_texts = []
    for it in items:
        try:
            if it.is_displayed():
                text = " ".join(it.text.split())
                if text:
                    visible_texts.append(text)
        except StaleElementReferenceException:
            continue
    if visible_texts:
        print(f"      🔍 Visible {label} items: {visible_texts}")
    else:
        print(f"      🔍 No visible {label} items found.")


def send_connection_via_profile(
    driver,
    profile_url,
    name,
    note_template,
    return_to_url=None,
):
    """
    Rule-based profile-page connect flow (no LLM, no API costs).

    Steps:
      1. Navigate to the profile URL.
      2. Try a direct Connect button in the action bar (some profiles show one).
      3. Else click the "More" button, find "Connect" in its dropdown, click it.
      4. Run the shared Add-a-note → type → Send flow.
      5. If return_to_url is given, navigate back to it so the outer loop
         can keep scanning the People page.

    Returns True on success, False on skip/failure.
    """
    personalized_note = note_template.format(name=name)
    if len(personalized_note) > 300:
        print(
            f"  ⚠️  Note for {name} is {len(personalized_note)} chars (max 300). "
            "Truncating."
        )
        personalized_note = personalized_note[:297] + "..."

    success = False
    try:
        driver.get(profile_url)
        time.sleep(2)

        # Wait for the profile to render and for action buttons to be present.
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "main"))
            )
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//main//button[contains(@aria-label, 'More') "
                    "or contains(@aria-label, 'Message') "
                    "or contains(@aria-label, 'Connect') "
                    "or contains(@aria-label, 'Follow') "
                    "or normalize-space()='More' "
                    "or normalize-space()='Message']",
                ))
            )
        except TimeoutException:
            print(f"      ⚠️  Profile didn't load in time for {name}.")
            return False

        # Make sure we're at the top — More button is in the header.
        try:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass

        # 1) Try a direct Connect control IN THE ACTION BAR ONLY.
        # The previous version searched all of <main>, which silently matched
        # 'Invite <other-person> to connect' links in the 'More profiles for
        # you' sidebar — we'd click the wrong person's Connect, no modal
        # would open, and the run would fail in confusing ways.
        url_before_connect = driver.current_url
        connect_opened = False
        connect_btn = _find_connect_in_action_bar(driver, verbose=True)
        if connect_btn is not None:
            tag = connect_btn.tag_name
            aria = connect_btn.get_attribute("aria-label") or ""
            print(
                f"      → Direct Connect control found in action bar "
                f"(<{tag}> aria-label={aria!r})."
            )
            # Sanity check: if the aria-label names a DIFFERENT person than
            # the one whose profile we're on, refuse to click — that's the
            # exact bug we just fixed and we want a loud failure if it
            # ever recurs.
            if aria and "Invite " in aria and "to connect" in aria:
                aria_target = aria.replace("Invite ", "").replace(
                    " to connect", "").strip()
                if (
                    aria_target
                    and name.lower() not in aria_target.lower()
                    and aria_target.split()[0].lower() != name.lower()
                ):
                    print(
                        f"      ⚠️  Action-bar Connect names a different "
                        f"person ({aria_target!r}); falling back to More menu."
                    )
                    connect_btn = None
        if connect_btn is not None:
            tag = connect_btn.tag_name
            # For <a> tags, the React click handler should preventDefault
            # and open the modal — but if React isn't fully bound yet, the
            # browser will navigate to the href instead. Strip the href
            # temporarily so a normal click can only fire the JS handler.
            if tag == "a":
                try:
                    driver.execute_script("""
                        const a = arguments[0];
                        a.dataset._origHref = a.getAttribute('href') || '';
                        a.removeAttribute('href');
                    """, connect_btn)
                except Exception:  # noqa: BLE001
                    pass
            if _click_with_fallback(driver, connect_btn):
                time.sleep(1.5)
                connect_opened = True
            # Restore href so subsequent runs / page interactions are clean.
            if tag == "a":
                try:
                    driver.execute_script("""
                        const a = arguments[0];
                        if (a.dataset._origHref) {
                            a.setAttribute('href', a.dataset._origHref);
                            delete a.dataset._origHref;
                        }
                    """, connect_btn)
                except Exception:  # noqa: BLE001
                    pass

            # Detect if the click navigated us off the profile (means React
            # didn't catch the click — we'll need to recover).
            try:
                url_after = driver.current_url
            except Exception:  # noqa: BLE001
                url_after = ""
            if url_after and url_after != url_before_connect:
                print(
                    f"      ⚠️  URL changed after Connect click: "
                    f"{url_before_connect!r} → {url_after!r}"
                )

        # 2) Otherwise open the More menu and find Connect inside it.
        if not connect_opened:
            more_btn = _find_more_button(driver, verbose=True)
            if more_btn is None:
                print(f"      ⚠️  No 'More' button on {name}'s profile.")
                _dump_more_buttons(driver)
                return False

            # Best-effort: make sure the window is focused (so Chrome
            # doesn't suppress animations) and that we're scrolled to the
            # action bar.
            try:
                driver.execute_script("window.focus();")
            except Exception:  # noqa: BLE001
                pass

            print(f"      → Clicking 'More' button…")
            if not _click_with_fallback(driver, more_btn):
                print(f"      ⚠️  Could not click 'More' for {name}.")
                _dump_more_buttons(driver)
                return False

            # Wait for the dropdown to actually appear before searching.
            dropdown = _wait_for_open_dropdown(driver, timeout=6.0)

            # Retry once: sometimes Chrome's first click reaches the page
            # before the dropdown's JS handler is fully bound (esp. right
            # after profile-page load).
            if dropdown is None:
                print(f"      ↻ No dropdown yet — retrying click once…")
                time.sleep(1.0)
                try:
                    fresh = _find_more_button(driver, verbose=False)
                except Exception:  # noqa: BLE001
                    fresh = None
                if fresh is not None:
                    _click_with_fallback(driver, fresh)
                    dropdown = _wait_for_open_dropdown(driver, timeout=6.0)

            if dropdown is None:
                print(f"      ⚠️  'More' clicked but dropdown didn't open for {name}.")
                _dump_more_buttons(driver)
                # Also list any dropdown-like elements that ARE on the page,
                # in case our open-dropdown detection is too strict.
                hints = driver.find_elements(
                    By.XPATH,
                    "//*[contains(@class, 'dropdown') and not(contains(@style, 'display: none'))]",
                )
                visible_dropdowns = []
                for h in hints[:15]:
                    try:
                        if h.is_displayed():
                            cls = (h.get_attribute("class") or "")[:90]
                            visible_dropdowns.append(cls)
                    except StaleElementReferenceException:
                        continue
                if visible_dropdowns:
                    print(f"      🔍 Visible elements with 'dropdown' in class:")
                    for d in visible_dropdowns:
                        print(f"         - class={d!r}")
                return False

            connect_item = _find_connect_in_dropdown(driver)
            if connect_item is None:
                print(
                    f"      ⚠️  No 'Connect' option in More menu for {name} "
                    "(already pending, blocked, or out of network)."
                )
                _dump_dropdown_items(driver, label="More menu")
                from selenium.webdriver.common.keys import Keys
                try:
                    webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                except Exception:  # noqa: BLE001
                    pass
                return False

            print(f"      → Clicking 'Connect' in More menu…")
            if not _click_with_fallback(driver, connect_item):
                print(f"      ⚠️  Could not click 'Connect' menu item for {name}.")
                return False
            # The dropdown's Connect item triggers the modal asynchronously.
            # Give it a moment, then _fill_and_send_connect_modal's own poll
            # will wait for the modal to actually appear.
            time.sleep(2.5)

        success = _fill_and_send_connect_modal(driver, name, personalized_note)
        if not success:
            _dismiss_modal(driver)
        return success

    except ElementClickInterceptedException:
        print(f"      ⚠️  Click intercepted for {name}. Dismissing overlays...")
        _dismiss_modal(driver)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"      ❌  Profile-flow error for {name}: {e}")
        _dismiss_modal(driver)
        return False
    finally:
        if return_to_url:
            try:
                driver.get(return_to_url)
                time.sleep(2)
            except Exception:  # noqa: BLE001
                pass


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
        help="Note template. Use {name} as placeholder for the person's first name "
             "and {sender_name} for your own name.",
    )
    parser.add_argument(
        "--name-on-note",
        default=None,
        metavar="NAME",
        help="Your name as it should appear in the connection note (e.g. 'Nakul').",
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
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        metavar="N",
        help="Skip the first N candidates in the page's natural order before "
             "starting to send requests. Useful for resuming after a previous "
             "run, or skipping the top profiles you've already contacted "
             "manually. Applies independently to the inline, profile, and "
             "agent flows (since they operate on different candidate pools).",
    )
    parser.add_argument(
        "--inline-only",
        action="store_true",
        help="Only run the fast inline 'Connect'-button flow on the People "
             "page. Explicitly skips the profile-page flow and the agent "
             "flow even if --via-profile / --agent-fallback are also passed. "
             "Same as the default behavior, just made explicit.",
    )
    parser.add_argument(
        "--via-profile",
        action="store_true",
        help="For people without an inline Connect button, visit their "
             "profile page and connect via the 'More' menu using rule-based "
             "Selenium (FREE — no LLM, no API costs). Returns to the People "
             "page between profiles.",
    )
    parser.add_argument(
        "--via-profile-only",
        action="store_true",
        help="Skip the inline Connect-button flow entirely; only run the "
             "rule-based profile-page flow. Implies --via-profile.",
    )
    parser.add_argument(
        "--max-profile",
        type=int,
        default=None,
        help="TARGET number of successful sends via the profile flow. "
             "If unset, inherits --max (so a single --max acts as a global "
             "cap). If some profiles get skipped, the script scrolls for "
             "more candidates and keeps trying until this many sends "
             "succeed (or a 3× safety cap is hit).",
    )
    parser.add_argument(
        "--agent-fallback",
        action="store_true",
        help="If the rule-based profile flow fails for a person (e.g. "
             "LinkedIn shifted the More menu), retry that person with a "
             "Claude-driven browser-use agent. Requires `browser-use` "
             "installed and ANTHROPIC_API_KEY set. Costs ~$0.05/profile.",
    )
    parser.add_argument(
        "--agent-only",
        action="store_true",
        help="Skip ALL Selenium paths (inline + profile-page); only run "
             "the LLM agent on profiles without an inline Connect button.",
    )
    parser.add_argument(
        "--max-agent",
        type=int,
        default=None,
        help="Max profiles to hand off to the agent. If unset, inherits "
             "--max (so a single --max acts as a global cap).",
    )
    parser.add_argument(
        "--agent-model",
        default="claude-sonnet-4-0",
        help="Anthropic model the agent uses (default: claude-sonnet-4-0).",
    )
    args = parser.parse_args()

    if not args.name_on_note:
        args.name_on_note = input(
            "⚠️  Who is sending these requests? Pass --name-on-note <NAME> "
            "or enter it now: "
        ).strip() or "there"

    args.note = args.note.replace("{sender_name}", args.name_on_note)

    # --agent-only implies the agent; --via-profile-only implies via-profile.
    # If the user only set --max, treat it as a global cap that also raises
    # the profile and agent caps. Explicit per-flow flags still win.
    inherited_profile = args.max_profile is None
    inherited_agent = args.max_agent is None
    if args.max_profile is None:
        args.max_profile = args.max
    if args.max_agent is None:
        args.max_agent = args.max
    if inherited_profile or inherited_agent:
        inherited_bits = []
        if inherited_profile:
            inherited_bits.append(f"--max-profile={args.max_profile}")
        if inherited_agent:
            inherited_bits.append(f"--max-agent={args.max_agent}")
        print(
            f"ℹ️  Inheriting --max for: {', '.join(inherited_bits)} "
            f"(pass them explicitly to override)."
        )

    use_via_profile = args.via_profile or args.via_profile_only
    use_agent = args.agent_fallback or args.agent_only
    skip_inline = args.via_profile_only or args.agent_only

    # --inline-only is the explicit "fast path only" switch. It wins over
    # every other path flag if the user passed conflicting options.
    if args.inline_only:
        if use_via_profile or use_agent or skip_inline:
            print(
                "ℹ️  --inline-only is set; ignoring --via-profile / "
                "--via-profile-only / --agent-fallback / --agent-only."
            )
        use_via_profile = False
        use_agent = False
        skip_inline = False

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

        sent = 0
        skipped = 0
        targets = []

        if not skip_inline:
            # Find connect buttons (fast Selenium path).
            targets = find_connect_buttons_and_names(driver)
            skip_msg = f", will skip first {args.skip}" if args.skip > 0 else ""
            print(
                f"\n🔎 Found {len(targets)} people with an inline 'Connect' "
                f"button{skip_msg}.\n"
            )

            for i, (btn, name) in enumerate(targets):
                if i < args.skip:
                    print(f"[{i+1}/{len(targets)}] {name} — ⏩ skipped (--skip)")
                    continue
                if sent >= args.max:
                    print(f"\n🛑 Reached max limit of {args.max} requests. Stopping inline flow.")
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
            print(f"Inline Selenium flow — Sent: {sent}   ⏭️  Skipped: {skipped}   Total: {len(targets)}")
            print(f"{'='*50}")
        else:
            reason = "--agent-only" if args.agent_only else "--via-profile-only"
            print(f"\n⏭️  {reason}: skipping inline Selenium 'Connect' flow.")

        # ── Rule-based profile-page flow (FREE) ──
        # Anyone missed by the inline flow is processed by walking to their
        # profile, clicking More → Connect → Add a note → Send. The agent
        # fallback (if enabled) only retries profiles this flow couldn't handle.
        profile_remaining = []  # profiles that even the rule flow couldn't connect

        if use_via_profile:
            target_sent = args.max_profile
            attempts_cap = max(target_sent * 3, target_sent + 10)
            initial_targets = find_profiles_without_connect(driver)

            if not initial_targets:
                print("\nℹ️  No profiles found that need the profile-page flow.")
            elif args.dry_run:
                # In dry-run we just preview the candidates that are visible
                # right now (after honoring --skip). No scrolling happens.
                effective = initial_targets[max(0, args.skip):]
                preview = effective[:target_sent]
                skip_note = (
                    f", skipping first {args.skip}" if args.skip > 0 else ""
                )
                print(
                    f"\n🔎 Found {len(initial_targets)} profiles without inline Connect "
                    f"(showing first {len(preview)} for dry run{skip_note}; "
                    f"target is {target_sent} successful sends)."
                )
                if args.skip > 0 and initial_targets[:args.skip]:
                    print(
                        f"\n⏩ Would skip these {min(args.skip, len(initial_targets))} "
                        "profile(s) at the top:"
                    )
                    for i, (url, name) in enumerate(initial_targets[:args.skip], 1):
                        print(f"   ⏩ [{i}] {name}  →  {url}")
                print("\n🔍 DRY RUN — profile-flow targets that WOULD be processed:\n")
                for i, (url, name) in enumerate(preview, 1):
                    note = args.note.format(name=name)
                    truncated = ""
                    if len(note) > 300:
                        note = note[:297] + "..."
                        truncated = " (truncated to 300 chars)"
                    print(f"   [{i}] {name}")
                    print(f"       URL  : {url}")
                    print(f"       Note{truncated}:")
                    for line in note.splitlines() or [note]:
                        print(f"         │ {line}")
                    print(
                        "       Will: open URL → click More → click Connect → "
                        "click Add a note → type the above note → click Send → "
                        "return to People page"
                    )
                    print()
            else:
                skip_msg = f", skipping first {args.skip}" if args.skip > 0 else ""
                print(
                    f"\n🛠️  Running rule-based profile flow — target: {target_sent} "
                    f"successful sends (safety cap: {attempts_cap} attempts{skip_msg})...\n"
                )

                p_sent = 0
                p_skipped = 0
                attempts = 0
                attempted_urls = set()
                scroll_failures = 0
                skip_remaining = max(0, args.skip)

                while p_sent < target_sent and attempts < attempts_cap:
                    # Make sure we're on the People page before scanning.
                    try:
                        cur = driver.current_url.split("?")[0].rstrip("/")
                        want = args.url.split("?")[0].rstrip("/")
                        if cur != want:
                            driver.get(args.url)
                            time.sleep(2)
                    except Exception:  # noqa: BLE001
                        pass

                    candidates = find_profiles_without_connect(driver)
                    fresh = [
                        (u, n) for u, n in candidates if u not in attempted_urls
                    ]

                    if not fresh:
                        # Out of unseen candidates → try to scroll for more.
                        if scroll_failures >= 3:
                            print(
                                f"\nℹ️  No more profiles loadable after "
                                f"{scroll_failures} scroll attempts. "
                                f"Stopping at {p_sent}/{target_sent} sent."
                            )
                            break
                        remaining = target_sent - p_sent
                        print(
                            f"\n📜 Need {remaining} more sends — "
                            f"loading additional profiles..."
                        )
                        if _scroll_load_more(driver):
                            scroll_failures = 0
                        else:
                            scroll_failures += 1
                            print(
                                f"   (page didn't grow — attempt "
                                f"{scroll_failures}/3)"
                            )
                        continue

                    scroll_failures = 0
                    for url, name in fresh:
                        if p_sent >= target_sent:
                            break
                        if attempts >= attempts_cap:
                            break

                        # Honor --skip BEFORE doing anything: mark these as
                        # "attempted" so they're never re-fetched, but don't
                        # count them toward attempts/sends.
                        if skip_remaining > 0:
                            attempted_urls.add(url)
                            consumed = args.skip - skip_remaining + 1
                            skip_remaining -= 1
                            print(
                                f"   ⏩ Skipping {name}  →  {url}  "
                                f"({consumed}/{args.skip})"
                            )
                            continue

                        attempts += 1
                        attempted_urls.add(url)
                        print(
                            f"   [attempt {attempts}] {name}  →  {url}  "
                            f"(sent {p_sent}/{target_sent})"
                        )
                        ok = send_connection_via_profile(
                            driver,
                            url,
                            name,
                            args.note,
                            return_to_url=args.url,
                        )
                        if ok:
                            p_sent += 1
                            print(f"      ✅ Sent.  ({p_sent}/{target_sent})")
                        else:
                            p_skipped += 1
                            profile_remaining.append((url, name))
                            print(
                                f"      ⏭️  Skipped — will load another to "
                                f"compensate (still need {target_sent - p_sent})."
                            )
                        human_delay()

                if p_sent < target_sent and attempts >= attempts_cap:
                    print(
                        f"\n🛑 Hit safety cap of {attempts_cap} attempts. "
                        f"Stopping at {p_sent}/{target_sent} sent."
                    )

                print(f"\n{'='*50}")
                print(
                    f"Profile flow — Sent: {p_sent}/{target_sent}   "
                    f"⏭️  Skipped: {p_skipped}   "
                    f"Total attempts: {attempts}"
                )
                print(f"{'='*50}")

        # ── Agent fallback for profiles without an inline Connect button ──
        # If the rule-based profile flow already ran, the agent only retries
        # the people it failed on. Otherwise, the agent processes everyone
        # without an inline Connect button.
        if use_agent:
            if use_via_profile:
                agent_targets = profile_remaining
                if agent_targets:
                    print(
                        f"\n🔁 Agent will retry {len(agent_targets)} profile(s) "
                        "that the rule-based flow couldn't handle."
                    )
            else:
                agent_targets = find_profiles_without_connect(driver)
                # --skip only makes sense for the standalone agent path
                # (i.e. NOT after the via-profile flow, where leftovers are
                # already filtered).
                if args.skip > 0 and agent_targets:
                    skipped_top = agent_targets[: args.skip]
                    agent_targets = agent_targets[args.skip:]
                    print(
                        f"\n⏩ Skipping first {len(skipped_top)} agent target(s) "
                        "as requested by --skip."
                    )

            if not agent_targets:
                print("\nℹ️  No profiles found that need agent fallback.")
            else:
                if len(agent_targets) > args.max_agent:
                    print(
                        f"\n🔎 Found {len(agent_targets)} profiles without inline Connect; "
                        f"capping at --max-agent={args.max_agent}."
                    )
                    agent_targets = agent_targets[: args.max_agent]
                else:
                    print(
                        f"\n🔎 Found {len(agent_targets)} profiles without inline Connect."
                    )

                if args.dry_run:
                    print("\n🔍 DRY RUN — agent targets that WOULD be processed:\n")
                    for i, (url, name) in enumerate(agent_targets, 1):
                        note = args.note.format(name=name)
                        truncated = ""
                        if len(note) > 300:
                            note = note[:297] + "..."
                            truncated = " (truncated to 300 chars)"
                        print(f"   [{i}] {name}")
                        print(f"       URL  : {url}")
                        print(f"       Note{truncated}:")
                        # Indent the note for readability.
                        for line in note.splitlines() or [note]:
                            print(f"         │ {line}")
                        print(
                            f"       Agent will: open URL → click More → click Connect "
                            f"→ click Add a note → type the above note → click Send"
                        )
                        print()
                else:
                    if not os.environ.get("ANTHROPIC_API_KEY"):
                        print(
                            "\n❌ ANTHROPIC_API_KEY is not set. "
                            "Export it before using --agent-fallback.\n"
                            "   export ANTHROPIC_API_KEY=sk-ant-..."
                        )
                    else:
                        try:
                            from agent_fallback import connect_batch_via_agent
                        except ImportError as e:
                            print(f"\n❌ Could not import agent_fallback: {e}")
                            connect_batch_via_agent = None  # type: ignore

                        if connect_batch_via_agent is not None:
                            print(
                                f"\n🤖 Handing {len(agent_targets)} profiles to the "
                                f"Claude agent ({args.agent_model}) via CDP on port {DEBUG_PORT}...\n"
                            )
                            results = asyncio.run(
                                connect_batch_via_agent(
                                    agent_targets,
                                    note_template=args.note,
                                    cdp_port=DEBUG_PORT,
                                    model=args.agent_model,
                                )
                            )
                            agent_sent = sum(1 for r in results if r.success)
                            print(f"\n{'='*50}")
                            print(
                                f"Agent flow — Sent: {agent_sent}   "
                                f"⏭️  Skipped/failed: {len(results) - agent_sent}   "
                                f"Total: {len(results)}"
                            )
                            print(f"{'='*50}")

        if not targets and not use_via_profile and not use_agent:
            print("No connectable people found on this page.")
            print("Make sure the URL points to a company People page with visible 'Connect' buttons,")
            print("or re-run with --via-profile (free) or --agent-fallback (paid) to also try")
            print("profiles that require the 'More' menu on their profile page.")
            return

    except Exception as e:
        print(f"\n❌ Error: {e}")

    print("\n✅ Done. Chrome is still open — you can close it or run the script again with a new URL.")


if __name__ == "__main__":
    main()
