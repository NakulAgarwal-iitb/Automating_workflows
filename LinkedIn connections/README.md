# LinkedIn Auto-Connect

Automates sending LinkedIn connection requests with a personalized note on company people pages.

## Setup

```bash
pip install selenium
```

ChromeDriver is auto-managed by Selenium 4.6+. No manual download needed.

## Usage

### Step 1 — Launch Chrome with debugging (only need to do this once per session)

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

This opens your normal Chrome (with all your tabs, bookmarks, LinkedIn login, etc.) but with a debugging port that the script can connect to.

### Step 2 — Navigate to the LinkedIn page

In that Chrome window, go to the company people page you want, e.g.:
- `linkedin.com/company/bluedart/people/?keywords=head`
- `linkedin.com/company/some-company/people/?keywords=manager`

### Step 3 — Run the script

```bash
# Dry run first (preview who would be contacted)
python linkedin_connect.py --dry-run

# Actually send requests
python linkedin_connect.py

# Limit to 10 requests
python linkedin_connect.py --max 10

# Custom note ({name} = person's first name)
python linkedin_connect.py --note "Hi {name}, I'd love to connect!"
```

The script connects to your **already-open Chrome tab** and works on whatever page is showing. No need to close Chrome.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--note` | IITB Driverless note | Message template (`{name}` = first name) |
| `--max` | 50 | Max requests per run |
| `--dry-run` | off | List targets without sending |

## How It Works

1. Connects to your already-running Chrome (via remote debugging port)
2. Reads the current LinkedIn people page you have open
3. Scrolls down to load all people cards
4. For each person with a **Connect** button:
   - Extracts their first name
   - Clicks Connect → Add a note
   - Types a personalized message (replacing `{name}`)
   - Clicks Send
   - Waits 3–7 seconds between requests
5. Your Chrome stays open — you can keep browsing after

## Important Notes

- **LinkedIn limits**: ~100 connection requests/week. Sending too many triggers restrictions.
- **Run in moderation**: Use `--max` to limit each session.
- **Dry run first**: Always `--dry-run` to verify before sending.
- **No need to close Chrome**: The script attaches to your running browser.
