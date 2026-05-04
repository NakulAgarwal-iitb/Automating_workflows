# BookMyShow Ticket Monitor & Auto-Booker

Automatically watches a BookMyShow event page and books tickets the moment they go live.

---

## ⚡ Quick Setup (5 minutes)

### 1. Install Python dependencies
```bash
pip install playwright plyer requests
playwright install chromium
```

### 2. Edit the script
Open `bookmyshow_monitor.py` and update **line 26**:
```python
BMS_URL = "https://in.bookmyshow.com/events/YOUR-EVENT-URL-HERE"
```
Paste the exact URL of the match/event from BookMyShow.

### 3. Run it
```bash
python bookmyshow_monitor.py
```

A browser window opens. The script checks every 10 seconds. When "Coming Soon" flips to "Book Now", it:
- 🔔 Plays a sound alarm
- 🖥️ Shows a desktop notification
- ⚡ Auto-clicks "Book Now" for you

---

## 🔧 Configuration Options

| Setting | Default | Description |
|---|---|---|
| `CHECK_INTERVAL_SECONDS` | `10` | How often to refresh (don't go below 5) |
| `AUTO_CLICK` | `True` | Click "Book Now" automatically |
| `PLAY_SOUND` | `True` | Sound alarm when live |
| `DESKTOP_NOTIFY` | `True` | Desktop popup |
| `TELEGRAM_ENABLED` | `False` | Send Telegram message |
| `EMAIL_ENABLED` | `False` | Send email alert |

---

## 📱 Optional: Telegram Notifications

1. Message [@BotFather](https://t.me/botfather) on Telegram → create a bot → copy the **token**
2. Message [@userinfobot](https://t.me/userinfobot) → get your **Chat ID**
3. In the script, set:
```python
TELEGRAM_ENABLED = True
TELEGRAM_BOT_TOKEN = "123456:ABC-your-token"
TELEGRAM_CHAT_ID   = "987654321"
```

---

## 📧 Optional: Email Notifications

Use a Gmail account with an **App Password** (not your regular password):
1. Go to Google Account → Security → 2-Step Verification → App Passwords
2. Generate a password for "Mail"
3. In the script, set:
```python
EMAIL_ENABLED = True
EMAIL_FROM    = "youremail@gmail.com"
EMAIL_TO      = "youremail@gmail.com"
EMAIL_PASS    = "your-16-char-app-password"
```

---

## ⚠️ Tips

- Run this **well before** tickets are expected to open
- Keep the browser window visible so you can intervene if needed
- After auto-click, quickly complete the booking (select seats → pay)
- BookMyShow may occasionally show CAPTCHA — the browser window lets you handle it manually
- Set `CHECK_INTERVAL_SECONDS = 5` for high-demand events

---

## Troubleshooting

**"Coming Soon" even after tickets should be live?**
→ Try refreshing BMS manually; the page element names may have changed. Share the URL and I can update the selectors.

**Auto-click didn't work?**
→ The browser window is open — just click manually. You're already on the right page!
