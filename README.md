# DiscordAlert

**Never miss a Discord DM again.**

DiscordAlert watches Windows for Discord desktop notifications and throws a fullscreen glass-style alert on your screen until you dismiss it with **Esc**, **Enter**, or **OK**.

Built for people who keep Discord muted, buried under windows, or open on another monitor — and still need to *actually* notice when someone messages them.

---

## Download (Windows)

Grab the ready-to-run app — no Python install needed:

**[Download DiscordAlert.exe](https://github.com/osmanyousaaf/DiscordAlert/raw/main/dist/DiscordAlert.exe)**

| | |
|---|---|
| **Platform** | Windows 10 / 11 (64-bit) |
| **Install** | None — double-click and go |
| **Dismiss** | `Esc` · `Enter` · OK button |

> First launch can take a few seconds while Windows unpacks the app. That’s normal.

---

## Quick start

1. Download [`DiscordAlert.exe`](https://github.com/osmanyousaaf/DiscordAlert/raw/main/dist/DiscordAlert.exe)
2. Run it — you should see a small “DiscordAlert is running” toast
3. When Windows asks for **notification access**, click **Allow**
4. In Discord:
   - **Settings → Notifications**
   - Turn **ON** Desktop Notifications
   - Turn **ON** notifications for Direct Messages
5. Keep DiscordAlert running in the background
6. Get a DM → fullscreen **“new message appears”** alert shows up

### Optional: start with Windows

1. Press `Win + R`, type `shell:startup`, press Enter
2. Drop a shortcut to `DiscordAlert.exe` in that folder

---

## What it looks like

A dark glassmorphism popup (frosted card, bell icon, clean OK button) over a deep blue grid — not a tiny toast you’ll ignore.

- Title: **new message appears**
- Shows sender / message preview when Discord provides it
- Stays on top until you dismiss it

---

## Why every message triggers (not just the first)

Older versions only watched notification *IDs*. Discord often **updates the same toast** for the next DM, so message 2 and 3 got ignored.

DiscordAlert now:

- Listens in a **background thread** (never pauses while a popup is open)
- **Queues** alerts so later messages aren’t lost
- Detects **content changes**, not just new IDs
- Lets you dismiss with **Esc / Enter / OK**, then shows the next queued alert

---

## Privacy

- Runs **locally** on your PC
- Reads Discord toasts through the official Windows notification listener
- Does **not** log into Discord, scrape chats, or phone home
- No account, no cloud, no telemetry

---

## Run from source (developers)

```bash
pip install winsdk pyinstaller
python auto.py
```

Build a fresh exe:

```powershell
.\build_exe.ps1
```

Output lands in `dist\DiscordAlert.exe`.

---

## Requirements

- Windows 10 or 11 (64-bit)
- Discord desktop app with desktop notifications enabled
- Windows permission: **let apps access notifications**

If alerts never show:

1. Windows Settings → **Privacy & security → Notifications** → allow notification access  
2. Confirm Discord itself is showing normal Windows toasts  
3. Check `%LOCALAPPDATA%\DiscordAlert\log.txt` if the windowed exe wrote errors

---

## Disclaimer

Unofficial tool. Not affiliated with Discord Inc. Use responsibly.

---

<p align="center">
  <b>Stay reachable.</b><br/>
  <a href="https://github.com/osmanyousaaf/DiscordAlert/raw/main/dist/DiscordAlert.exe">Download DiscordAlert.exe</a>
</p>
