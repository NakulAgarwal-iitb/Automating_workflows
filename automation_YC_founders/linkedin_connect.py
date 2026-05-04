"""
LinkedIn automation: send personalized connection requests from a search results page.

Usage:
    # First-time login (saves session to ./user_data, skips login on future runs)
    python linkedin_connect.py --login

    # Regular run
    python linkedin_connect.py \
        --url "https://www.linkedin.com/search/results/people/?keywords=Y%20Combinator" \
        --count 25

    # Slower, safer batch (recommended)
    python linkedin_connect.py \
        --url "..." --count 25 --min-delay 5 --max-delay 12

On the first run a Chromium window opens. The script auto-detects when you finish
logging in (2FA / email verification is fine) and saves the session to ./user_data
so subsequent runs do not require logging in again.

Rate-limit guidance
-------------------
LinkedIn aggressively throttles automated behavior. If you see
`ERR_SSL_PROTOCOL_ERROR` or similar network errors mid-run, that is LinkedIn's
edge refusing connections — not a bug in the script. The script will retry with
exponential backoff and stop cleanly if it can't recover. Your successful
invites up to that point are safe.

Recommendations to avoid getting throttled:
  1. Wait at least 30-60 minutes between runs. LinkedIn's rate-limit windows
     are roughly hourly.
  2. Keep batches small: ~25-30 invites per session, <100 per day total.
  3. Use longer delays (--min-delay 5 --max-delay 12) to look less bursty.
     With human typing this makes each invite take ~1-2 min, close to real
     usage.
  4. Check LinkedIn's web UI occasionally. If you see a "we detected unusual
     activity" prompt, stop and wait a few days before running again.

Note-with-invite limit
----------------------
Free LinkedIn accounts are capped at ~5 invites-with-note per month (this changes
periodically). When you hit that cap the "Add a note" option disappears from the
modal — the script automatically falls back to sending the invite WITHOUT a note
instead of failing. That's expected behavior, not a bug.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

USER_DATA_DIR = Path(__file__).parent / "user_data"

NOTE_TEMPLATE = (
    "Hi {name}, I'm Nakul, a final-year student and IIT Bombay Driverless Racing Team alum. "
    "We're building Neural.KM, future of driverless commercial vehicles. "
    "We're applying to YC and would love your help with an application review and further connects. Thanks!"
)


def human_pause(min_s: float = 0.8, max_s: float = 2.0) -> None:
    """Sleep a random amount to look less bot-like."""
    time.sleep(random.uniform(min_s, max_s))


def human_type(page: Page, locator, text: str) -> None:
    """
    Type text into a field character by character with randomized per-keystroke
    delays, the occasional longer "thinking" pause, and slightly longer pauses
    after punctuation — makes typing look human instead of an instant paste.
    """
    locator.click()
    locator.fill("")
    time.sleep(random.uniform(0.25, 0.55))

    for i, ch in enumerate(text):
        page.keyboard.type(ch)
        base = random.uniform(0.04, 0.13)
        if ch in ".,!?":
            base += random.uniform(0.15, 0.35)
        elif ch == " ":
            base += random.uniform(0.02, 0.08)
        if random.random() < 0.03 and i > 3:
            base += random.uniform(0.25, 0.7)
        time.sleep(base)


def set_page_param(url: str, page_num: int) -> str:
    """Return the given LinkedIn search URL with the `page` query param set."""
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page_num)]
    flat = [(k, v[0]) for k, v in query.items()]
    return urlunparse(parts._replace(query=urlencode(flat)))


def extract_first_name(aria_label: str) -> str:
    """
    Given an aria-label like 'Invite Amit Maurya to connect', return 'Amit'.
    Falls back to 'there' if nothing can be extracted.
    """
    match = re.match(r"Invite\s+([^\s]+)", aria_label or "")
    if match:
        return match.group(1).strip(",.")
    return "there"


def is_logged_in(page: Page) -> bool:
    """Return True if the current page looks like an authenticated LinkedIn view."""
    url = page.url or ""
    if any(
        marker in url
        for marker in ("/login", "/checkpoint", "/uas/login", "/authwall", "signup")
    ):
        return False
    try:
        return page.locator("a[href='/feed/'], #global-nav, .global-nav").first.is_visible()
    except Exception:
        return False


def ensure_logged_in(page: Page, login_timeout_s: int = 600) -> None:
    """
    Make sure the user is logged in. If not, open the login page and poll until
    the user finishes logging in (handles 2FA / email checkpoints) or the timeout
    expires. Does not rely on the user pressing Enter at the right time.
    """
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    human_pause(1.5, 2.5)

    if is_logged_in(page):
        print("[ok] Already logged in.")
        return

    print(
        "\n[!] Not logged in. A browser window is open — please log into LinkedIn there.\n"
        "    2FA / email verification is fine; the script will auto-detect when you're in.\n"
        f"    (Will wait up to {login_timeout_s // 60} minutes.)\n"
    )
    try:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
    except PlaywrightTimeoutError:
        pass

    deadline = time.time() + login_timeout_s
    while time.time() < deadline:
        if is_logged_in(page):
            print("[ok] Login detected. Continuing...\n")
            human_pause(1.5, 2.5)
            return
        time.sleep(2)

    raise RuntimeError("Timed out waiting for login. Run again and try logging in manually first.")


CONNECT_SELECTOR = (
    "button[aria-label^='Invite '][aria-label$=' to connect'], "
    "a[aria-label^='Invite '][aria-label$=' to connect']"
)


def get_connect_buttons(page: Page):
    """
    Return all visible Connect buttons on the current search results page.
    LinkedIn marks them with aria-label='Invite <name> to connect'. Depending on
    the layout this element can be a <button> or an <a>.
    """
    page.wait_for_selector("main", timeout=15000)
    for _ in range(8):
        page.mouse.wheel(0, 1400)
        human_pause(0.5, 0.9)
    page.evaluate("window.scrollTo(0, 0)")
    human_pause(0.6, 1.0)

    return page.locator(CONNECT_SELECTOR)


def send_one_invite(page: Page, button, message_template: str) -> bool:
    """
    Click a single Connect button, open 'Add a note', fill the custom message
    and click Send. Returns True on success, False otherwise.
    """
    try:
        aria = button.get_attribute("aria-label") or ""
        name = extract_first_name(aria)
        print(f"  -> Connecting with {name}...")

        button.scroll_into_view_if_needed(timeout=5000)
        human_pause(0.4, 1.0)
        button.click(timeout=5000)

        dialog = page.locator("div[role='dialog']").first
        dialog.wait_for(state="visible", timeout=7000)
        human_pause(0.6, 1.2)

        add_note = dialog.get_by_role("button", name=re.compile(r"Add a (free )?note", re.I))
        if add_note.count() == 0:
            add_note = dialog.locator("button:has-text('Add a note')")

        if add_note.count() > 0 and add_note.first.is_visible():
            add_note.first.click()
            human_pause(0.5, 1.0)
            textarea = dialog.locator("textarea#custom-message, textarea[name='message']").first
            textarea.wait_for(state="visible", timeout=5000)
            human_type(page, textarea, message_template.format(name=name))
            human_pause(0.6, 1.2)
        else:
            print("    (note option unavailable — sending without a note)")

        send_btn = dialog.get_by_role("button", name=re.compile(r"^Send(\s|$)", re.I))
        if send_btn.count() == 0:
            send_btn = dialog.locator("button:has-text('Send')")
        send_btn.first.click(timeout=5000)
        human_pause(1.2, 2.0)
        return True

    except PlaywrightTimeoutError as e:
        print(f"    [skip] timeout: {e.message.splitlines()[0]}")
    except Exception as e:
        print(f"    [skip] {e}")

    try:
        page.keyboard.press("Escape")
        human_pause(0.4, 0.8)
    except Exception:
        pass
    return False


def _open_context(playwright: Playwright, headless: bool):
    USER_DATA_DIR.mkdir(exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(USER_DATA_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _safe_close(context) -> None:
    """Close the browser context, swallowing errors from an already-closed browser."""
    try:
        context.close()
    except Exception:
        pass


NETWORK_ERROR_HINTS = (
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_NETWORK_CHANGED",
    "ERR_TIMED_OUT",
    "ERR_EMPTY_RESPONSE",
    "net::ERR_",
)


def _is_transient_network_error(err: Exception) -> bool:
    msg = str(err) or ""
    return any(hint in msg for hint in NETWORK_ERROR_HINTS)


def goto_with_retry(page: Page, url: str, max_attempts: int = 4) -> bool:
    """
    Navigate to `url`, retrying on transient network errors with exponential
    backoff + jitter. Returns True on success, False if all attempts failed.
    """
    delay = 30.0
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True
        except PlaywrightTimeoutError:
            print(f"  [warn] navigation timed out (attempt {attempt}/{max_attempts}).")
        except Exception as e:
            if not _is_transient_network_error(e):
                raise
            wait = delay + random.uniform(0, 15)
            print(
                f"  [warn] LinkedIn refused the connection ({str(e).splitlines()[0]}).\n"
                f"        Cooling down {wait:.0f}s before retry {attempt}/{max_attempts}..."
            )
            time.sleep(wait)
            delay *= 2
    return False


def run_login(playwright: Playwright) -> None:
    """Open a visible browser just to capture a login session in ./user_data."""
    context = _open_context(playwright, headless=False)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        ensure_logged_in(page)
        print("Session saved. You can now run the script without --login.")
    finally:
        _safe_close(context)


def run(playwright: Playwright, args: argparse.Namespace) -> None:
    context = _open_context(playwright, headless=args.headless)
    page = context.pages[0] if context.pages else context.new_page()

    try:
        ensure_logged_in(page)

        sent = 0
        page_num = 1
        while sent < args.count:
            target_url = set_page_param(args.url, page_num)
            print(f"\n[page {page_num}] {target_url}")
            if not goto_with_retry(page, target_url):
                print(
                    "  [stop] LinkedIn is refusing connections — likely rate-limited.\n"
                    f"         Stopping cleanly with {sent} invites sent.\n"
                    "         Wait 15-60 minutes (or longer) and run the script again.\n"
                    "         It will resume with fresh pagination; already-sent people\n"
                    "         will just be skipped (they won't show a Connect button)."
                )
                break
            human_pause(2.0, 3.5)

            buttons = get_connect_buttons(page)
            total = buttons.count()
            print(f"  Found {total} Connect buttons on this page.")

            if total == 0:
                invite_any = page.locator("[aria-label*='Invite']").count()
                follow_any = page.locator("button[aria-label^='Follow ']").count()
                print(
                    f"  (diagnostic: elements matching 'Invite' = {invite_any}, "
                    f"Follow buttons = {follow_any})"
                )
                print("  No more Connect buttons — stopping.")
                break

            i = 0
            while i < total and sent < args.count:
                current = get_connect_buttons(page).nth(i)
                if current.count() == 0:
                    break
                if send_one_invite(page, current, args.message):
                    sent += 1
                    print(f"  [{sent}/{args.count}] sent.")
                human_pause(args.min_delay, args.max_delay)
                i += 1

            if sent >= args.count:
                break
            page_num += 1

        print(f"\nDone. Total invites sent: {sent}")

    except KeyboardInterrupt:
        print("\n[interrupted] shutting down cleanly.")
    finally:
        _safe_close(context)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automate LinkedIn connection requests.")
    p.add_argument(
        "--login",
        action="store_true",
        help="Only open a browser so you can log in; save the session and exit.",
    )
    p.add_argument("--url", help="LinkedIn people search results URL.")
    p.add_argument("--count", type=int, help="Total number of invites to send.")
    p.add_argument(
        "--message",
        default=NOTE_TEMPLATE,
        help="Custom note. Use {name} to insert the recipient's first name. "
        "Defaults to the built-in NOTE_TEMPLATE.",
    )
    p.add_argument("--headless", action="store_true", help="Run the browser headless.")
    p.add_argument("--min-delay", type=float, default=2.5, help="Min seconds between invites.")
    p.add_argument("--max-delay", type=float, default=5.0, help="Max seconds between invites.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.login:
        with sync_playwright() as pw:
            run_login(pw)
        return 0

    if not args.url or not args.count:
        print("--url and --count are required (or pass --login to just sign in).", file=sys.stderr)
        return 2
    if args.count <= 0:
        print("--count must be > 0", file=sys.stderr)
        return 2
    with sync_playwright() as pw:
        run(pw, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
