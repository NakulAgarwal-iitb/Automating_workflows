# LinkedIn Auto-Connect

Automates sending LinkedIn connection requests with a personalized note on company People pages.

Three pluggable paths, all sharing the same Chrome window via CDP on port 9222:

| Path | Cost | Speed | Reliability when LinkedIn changes UI |
|---|---|---|---|
| **Inline Selenium** — for cards with a visible **Connect** button | Free | Fast (~5s/person) | Solid |
| **Profile-page Selenium** — for cards without a Connect button (visits profile, clicks **More → Connect → Add a note → Send**, returns to People page) | **Free** | ~15–25s/person | Brittle; will need selector tweaks when LinkedIn redesigns |
| **Claude agent** (`browser-use`) — same profile-page flow, but driven by an LLM | ~$0.05/person | ~30–60s/person | Self-heals when LinkedIn UI shifts |

You can mix them: rules first, agent as a fallback for the leftovers.

## Setup

```bash
pip install -r requirements.txt
```

If you only need the **free** paths (inline + profile-page Selenium), this is enough.

For the Claude agent fallback, also:

```bash
pip install browser-use
export ANTHROPIC_API_KEY=sk-ant-...
```

ChromeDriver is auto-managed by Selenium 4.6+; no manual download needed.

## Usage

### Step 1 — First-run login

The script auto-launches a dedicated Chrome window pinned to remote debugging port `9222`. **Log into LinkedIn in that window once** — the session is remembered for future runs.

### Step 2 — Run

```bash
# 1) Dry run first — preview every target across all enabled paths
python linkedin_connect.py "<URL>" --via-profile --dry-run

# 2) Inline Connect-button flow only (original behavior)
python linkedin_connect.py "<URL>"

# 3) Inline + profile-page flow (FREE, recommended for most users)
python linkedin_connect.py "<URL>" --via-profile

# 4) Profile-page flow ONLY (everyone needs the More menu)
python linkedin_connect.py "<URL>" --via-profile-only --max-profile 10

# 5) Free rules first, paid agent only for whatever rules couldn't handle
python linkedin_connect.py "<URL>" --via-profile --agent-fallback

# 6) Pure agent (most adaptable, most expensive)
python linkedin_connect.py "<URL>" --agent-only --max-agent 10

# Custom note ({name} = first name)
python linkedin_connect.py "<URL>" --via-profile \
    --note "Hi {name}, I'd love to connect about logistics innovation."
```

> Example URL: `https://www.linkedin.com/company/bluedart/people/?keywords=head`

## Flag reference

| Flag | Default | Purpose |
|------|---------|---------|
| `--note` | IITB Driverless note | Message template, `{name}` placeholder = first name |
| `--max` | 50 | Cap on inline Selenium requests |
| `--dry-run` | off | Preview targets without sending; lists the exact personalized note per profile |
| `--via-profile` | off | Rule-based profile-page flow for people without inline Connect (free) |
| `--via-profile-only` | off | Skip inline flow; only run the profile-page flow |
| `--max-profile` | 20 | Cap on profile-page requests |
| `--agent-fallback` | off | Use Claude agent to retry profiles the rule-based flow couldn't handle |
| `--agent-only` | off | Skip all Selenium paths; only run the agent |
| `--max-agent` | 20 | Cap on agent-driven requests |
| `--agent-model` | `claude-sonnet-4-0` | Anthropic model the agent uses |

## How it works

### Inline flow (Selenium)
1. Connect Selenium to the running Chrome on port 9222.
2. Open the People-page URL, scroll to load all cards.
3. For every card with a visible **Connect** button: click → "Add a note" → type the personalized message → **Send**.
4. Wait 3–7 seconds between requests.

### Profile-page flow (Selenium, free)
1. Find every people card on the page that has **no** inline Connect and isn't already "Pending".
2. For each one:
   - Navigate to their profile URL.
   - Try a direct Connect button in the action bar.
   - If not present, click **More** → find **Connect** in the dropdown (also handles "Personalize invite").
   - Open the note dialog → type the personalized note → click **Send**.
   - Navigate back to the People page so the loop can continue.
3. Anyone the flow couldn't handle gets logged and (if `--agent-fallback`) passed to the agent.

### Agent flow (`browser-use` + Claude)
Same goal as the profile-page flow, but Claude reads the screen and decides what to click. It adapts to:
- "More" button being relabeled / re-iconed / localized
- Dropdown shifting wording ("Connect" vs "Personalize invite")
- Unexpected popups (Premium upsell, email-required, captcha)

The agent reports one of: `success`, `connect_not_available`, `email_required`, `captcha`, `failed: <reason>`.

## Recommended workflow

1. **Start free, dry first.**
   ```bash
   python linkedin_connect.py "<URL>" --via-profile --dry-run
   ```
   Verify the names and URLs look right.

2. **Run small.**
   ```bash
   python linkedin_connect.py "<URL>" --via-profile --max-profile 3
   ```
   Watch the Chrome window and confirm 3 successful requests.

3. **Scale up.**
   ```bash
   python linkedin_connect.py "<URL>" --via-profile --max-profile 15
   ```

4. **Add the agent only if rules start failing.**
   ```bash
   python linkedin_connect.py "<URL>" --via-profile --agent-fallback --max-agent 5
   ```

## Important notes

- **LinkedIn limits**: ~100 connection requests/week. Going much higher triggers restrictions.
- **Use the caps**: `--max`, `--max-profile`, and `--max-agent` are independent dials.
- **Dry run first**: confirms which set of people each path would touch and shows the exact note.
- **The free profile flow will break when LinkedIn redesigns its UI.** Selectors live in `send_connection_via_profile()` in `linkedin_connect.py` — adjust as needed, or switch to `--agent-fallback`.

## Kill the automation Chrome window

```bash
pkill -f "remote-debugging-port=9222"
```
