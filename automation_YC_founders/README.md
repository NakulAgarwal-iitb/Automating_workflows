# LinkedIn Connect Automation

Automates sending personalized connection requests from a LinkedIn People search page, walking through pages until a target number of invites has been sent.

## What it does

1. Opens your LinkedIn search URL in a Chromium window (session is persisted, so you log in just once).
2. For every person on the page that has a **Connect** button:
   - Clicks **Connect**
   - Chooses **Add a note**
   - Fills your custom message (`{name}` is replaced by the person's first name)
   - Clicks **Send**
3. Moves to the next page (`&page=2`, `&page=3`, ...) and continues until `--count` invites have been sent.

## Setup

```bash
cd /Users/nakulagarwal/Desktop/learning/automation

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## First run (login)

Run the login flow once, log into LinkedIn in the Chromium window, and the session is saved to `./user_data/`. Keep the browser window open while you log in — the script auto-detects when you reach your feed and closes itself.

```bash
python linkedin_connect.py --login
```

If LinkedIn logs you out (or session gets corrupted), delete `./user_data/` and run `--login` again:

```bash
rm -rf user_data && python linkedin_connect.py --login
```

## Usage

After you've logged in once:

```bash
python linkedin_connect.py \
  --url "https://www.linkedin.com/search/results/people/?keywords=Y%20Combinator" \
  --count 30
```

If you want to override the built-in note, pass `--message "..."` with `{name}` as a placeholder.

**Important:** do NOT close the Chromium window while the script is running. Let the script finish on its own — it closes the browser cleanly.

### CLI flags

| Flag | Required | Description |
| --- | --- | --- |
| `--login` | no | Just open a browser so you can log in, save the session, and exit. |
| `--url` | yes* | Full LinkedIn people search URL. The script rewrites its `page` query param as it paginates. |
| `--count` | yes* | Total number of invites to send across all pages. |
| `--message` | no | Override the default note template. `{name}` is replaced by the person's first name. LinkedIn caps notes at ~300 chars. |
| `--headless` | no | Run without a visible window (only after login is already cached). |
| `--min-delay` | no | Min seconds between invites. Default `2.5`. |
| `--max-delay` | no | Max seconds between invites. Default `5.0`. |

\* Not required when `--login` is passed.

## Notes

- LinkedIn limits free accounts to roughly **100–200 invites per week**. Going above that triggers a cooldown; the script will simply start skipping people when it can't send.
- Some people expose **Follow** instead of **Connect** — the script ignores them automatically.
- If LinkedIn UI copy changes (button names like "Add a note" → "Add a free note"), update the regexes in `send_one_invite`.
- This is for personal use; LinkedIn's ToS discourages automated activity. Use respectfully and at your own risk.
