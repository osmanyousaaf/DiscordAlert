"""
Discord DM Fullscreen Popup Alert (Windows only)
--------------------------------------------------
Shows a glassmorphism alert when Discord posts a desktop notification.

Primary detection (reliable):
  Watches Discord's Windows notification registry timestamp
  (LastNotificationAddedTime). Discord updates this for EVERY toast,
  even when the toast never appears in UserNotificationListener.

Secondary detection (optional enrichment):
  Also polls UserNotificationListener for title/body when available.

Alerts are queued on a background thread so message 2/3/4 are never lost
while an earlier popup is still open. Dismiss with Esc, Enter, or OK.
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import winreg

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DiscordAlert")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "log.txt")

# Always log to file so windowed .exe has a trail to debug.
_log_file = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
if sys.stdout is None:
    sys.stdout = _log_file
else:
    class _Tee:
        def __init__(self, a, b):
            self.a, self.b = a, b
        def write(self, data):
            try:
                self.a.write(data)
            except Exception:
                pass
            try:
                self.b.write(data)
            except Exception:
                pass
        def flush(self):
            for s in (self.a, self.b):
                try:
                    s.flush()
                except Exception:
                    pass
    sys.stdout = _Tee(sys.stdout, _log_file)

if sys.stderr is None:
    sys.stderr = _log_file

# Known Discord AppUserModelIDs (stable / PTB / Canary / development)
DISCORD_AUMIDS = (
    "com.squirrel.Discord.Discord",
    "com.squirrel.DiscordPTB.DiscordPTB",
    "com.squirrel.DiscordCanary.DiscordCanary",
    "com.squirrel.DiscordDevelopment.DiscordDevelopment",
)
NOTIF_SETTINGS_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"


# ---------------------------------------------------------------------------
# Drawing helpers (glass UI)
# ---------------------------------------------------------------------------

def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _draw_grid(canvas, w, h, cell=48):
    canvas.create_rectangle(0, 0, w, h, fill="#0a1628", outline="")
    c1 = "#0d1c32"
    for row, y in enumerate(range(0, h, cell)):
        for col, x in enumerate(range(0, w, cell)):
            if (row + col) % 2 == 0:
                canvas.create_rectangle(x, y, x + cell, y + cell, fill=c1, outline="")
    for x in range(0, w, cell):
        canvas.create_line(x, 0, x, h, fill="#132238", width=1)
    for y in range(0, h, cell):
        canvas.create_line(0, y, w, y, fill="#132238", width=1)


def _draw_bell(canvas, cx, cy, scale=1.0):
    s = scale
    color = "#ffffff"
    canvas.create_arc(
        cx - 28 * s, cy - 22 * s, cx + 28 * s, cy + 38 * s,
        start=0, extent=180, style="arc", outline=color, width=3,
    )
    canvas.create_line(cx - 28 * s, cy + 8 * s, cx + 28 * s, cy + 8 * s, fill=color, width=3)
    canvas.create_oval(
        cx - 6 * s, cy + 10 * s, cx + 6 * s, cy + 22 * s,
        outline=color, width=2,
    )
    canvas.create_oval(
        cx - 5 * s, cy - 28 * s, cx + 5 * s, cy - 18 * s,
        outline=color, width=2,
    )
    canvas.create_arc(
        cx - 48 * s, cy - 18 * s, cx - 30 * s, cy + 18 * s,
        start=110, extent=140, style="arc", outline=color, width=2,
    )
    canvas.create_arc(
        cx + 30 * s, cy - 18 * s, cx + 48 * s, cy + 18 * s,
        start=290, extent=140, style="arc", outline=color, width=2,
    )


# ---------------------------------------------------------------------------
# Shared alert bus — every detected Discord message becomes one queued popup
# ---------------------------------------------------------------------------

class AlertBus:
    """Thread-safe queue feeder.

    - registry signals ALWAYS enqueue (one popup per Discord notification)
    - title-badge is backup only (used when Discord skips Windows toasts);
      ignored if registry already fired for that same message
    """

    def __init__(self, alert_queue: queue.Queue):
        self.alert_queue = alert_queue
        self._lock = threading.Lock()
        self._last_registry = 0.0

    def emit(self, source: str, title: str = "Discord", body: str = ""):
        body = body or "Important system notification. Please review the details below."
        with self._lock:
            now = time.monotonic()
            if source == "title-badge":
                # Backup path only — don't double-popup when registry already caught it
                if now - self._last_registry < 1.2:
                    print(f"Skip {source} (registry already handled this message)", flush=True)
                    return
            elif source == "registry":
                self._last_registry = now
        print(f"ALERT [{source}]: queue popup", flush=True)
        self.alert_queue.put((title, body))



# ---------------------------------------------------------------------------
# Discord detection via Windows notification registry (PRIMARY)
# ---------------------------------------------------------------------------

def read_last_notification_time(aumid: str):
    """Return Discord's LastNotificationAddedTime QWORD, or None."""
    path = rf"{NOTIF_SETTINGS_ROOT}\{aumid}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, regtype = winreg.QueryValueEx(key, "LastNotificationAddedTime")
            if regtype == winreg.REG_QWORD or isinstance(value, int):
                return int(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def discover_discord_aumids():
    """Known AUMIDs plus any Discord* keys present on this machine."""
    found = list(DISCORD_AUMIDS)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIF_SETTINGS_ROOT, 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if "discord" in name.lower() and name not in found:
                    found.append(name)
    except OSError:
        pass
    return found


class DiscordRegistryWatcher:
    """
    Polls LastNotificationAddedTime for Discord AUMIDs.

    Discord updates this for EVERY desktop notification — including ones that
    never show up in UserNotificationListener.
    """

    def __init__(self, bus: AlertBus):
        self.bus = bus
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        aumids = discover_discord_aumids()
        last_seen = {}
        for aumid in aumids:
            last_seen[aumid] = read_last_notification_time(aumid)

        present = [a for a, v in last_seen.items() if v is not None]
        print(f"Registry watcher ready. Tracking: {present or aumids}", flush=True)
        if not present:
            print(
                "WARNING: No Discord notification registry key found yet.\n"
                "Enable Discord desktop notifications and send a test DM.",
                flush=True,
            )

        while not self._stop.is_set():
            try:
                for aumid in discover_discord_aumids():
                    if aumid not in last_seen:
                        last_seen[aumid] = read_last_notification_time(aumid)
                        print(f"Now tracking AUMID: {aumid}", flush=True)

                for aumid, prev in list(last_seen.items()):
                    current = read_last_notification_time(aumid)
                    if current is None:
                        continue
                    if prev is None:
                        last_seen[aumid] = current
                        print(f"Seeded {aumid} = {current}", flush=True)
                        continue
                    if current != prev:
                        last_seen[aumid] = current
                        print(
                            f"Registry change {aumid}: {prev} -> {current}",
                            flush=True,
                        )
                        self.bus.emit("registry")
            except Exception:
                traceback.print_exc()

            self._stop.wait(0.15)


def start_registry_watcher(bus: AlertBus) -> DiscordRegistryWatcher:
    watcher = DiscordRegistryWatcher(bus)
    threading.Thread(target=watcher.run, name="DiscordRegistryWatcher", daemon=True).start()
    return watcher


# ---------------------------------------------------------------------------
# Backup: Discord window title unread badge  e.g. "(3) #general - Discord"
# ---------------------------------------------------------------------------

def _discord_window_unread_counts():
    """Return max unread count parsed from visible Discord window titles."""
    import ctypes
    from ctypes import wintypes
    import re

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    IsWindowVisible = user32.IsWindowVisible
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextW = user32.GetWindowTextW
    GetClassNameW = user32.GetClassNameW

    counts = []

    def foreach(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if "discord" not in title.lower():
            return True
        cls = ctypes.create_unicode_buffer(256)
        GetClassNameW(hwnd, cls, 256)
        # Discord / Electron main window
        if "Chrome_WidgetWin" not in (cls.value or ""):
            return True
        m = re.match(r"^\((\d+)\)\s+", title)
        if m:
            counts.append(int(m.group(1)))
        else:
            counts.append(0)
        return True

    EnumWindows(EnumWindowsProc(foreach), 0)
    return max(counts) if counts else None


class DiscordTitleWatcher:
    """Fires when Discord's taskbar/title unread (N) increases — covers cases
    where Discord skips a Windows toast (e.g. window focused)."""

    def __init__(self, bus: AlertBus):
        self.bus = bus
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        last = _discord_window_unread_counts()
        print(f"Title badge watcher ready. Initial unread={last}", flush=True)
        while not self._stop.is_set():
            try:
                cur = _discord_window_unread_counts()
                if cur is not None and last is not None and cur > last:
                    # One popup per unread increase (handles jumps like 1→3)
                    for i in range(cur - last):
                        print(f"Title badge unread {last + i} -> {last + i + 1}", flush=True)
                        self.bus.emit("title-badge")
                if cur is not None:
                    last = cur
            except Exception:
                traceback.print_exc()
            self._stop.wait(0.4)


def start_title_watcher(bus: AlertBus):
    watcher = DiscordTitleWatcher(bus)
    threading.Thread(target=watcher.run, name="DiscordTitleWatcher", daemon=True).start()
    return watcher


# ---------------------------------------------------------------------------
# UI — single persistent Tk root; popups are Toplevels (Esc / Enter dismiss)
# ---------------------------------------------------------------------------

class AlertApp:
    def __init__(self):
        self.alert_queue = queue.Queue()
        self.bus = AlertBus(self.alert_queue)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("DiscordAlert")
        self.root.geometry("1x1+0+0")

        self._popup = None
        self._showing = False

        self._show_started_toast()
        start_registry_watcher(self.bus)
        start_title_watcher(self.bus)
        self.root.after(150, self._poll_queue)
        print("DiscordAlert UI ready — every Discord notification will queue a popup.", flush=True)

    def _show_started_toast(self):
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.attributes("-alpha", 0.94)
        w, h = 360, 100
        sw, sh = toast.winfo_screenwidth(), toast.winfo_screenheight()
        toast.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 60}")
        toast.configure(bg="#1a2838")

        canvas = tk.Canvas(toast, width=w, height=h, bg="#1a2838", highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        _rounded_rect(
            canvas, 4, 4, w - 4, h - 4, 18,
            fill="#2a3a4c", outline="#c8d4e0", width=1,
        )
        canvas.create_text(
            w / 2, h / 2,
            text="DiscordAlert is running",
            font=("Segoe UI Semibold", 15),
            fill="#ffffff",
        )
        toast.after(2200, toast.destroy)

    def _try_show_next(self):
        if self._showing:
            return
        try:
            title, body = self.alert_queue.get_nowait()
        except queue.Empty:
            return

        if title == "__error__":
            print(body, flush=True)
            self.root.after(10, self._try_show_next)
            return

        self._open_popup(title, body)

    def _poll_queue(self):
        self._try_show_next()
        self.root.after(120, self._poll_queue)

    def _open_popup(self, title: str, message: str):
        if self._popup is not None:
            try:
                self._popup.destroy()
            except Exception:
                pass
            self._popup = None

        self._showing = True
        win = tk.Toplevel(self.root)
        self._popup = win
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        win.configure(bg="#0a1628")
        win.focus_force()
        try:
            win.grab_set()
        except Exception:
            pass

        w = win.winfo_screenwidth()
        h = win.winfo_screenheight()

        canvas = tk.Canvas(win, width=w, height=h, bg="#0a1628", highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        _draw_grid(canvas, w, h)

        card_w = min(520, int(w * 0.42))
        card_h = min(340, int(h * 0.42))
        cx, cy = w / 2, h / 2
        x1, y1 = cx - card_w / 2, cy - card_h / 2
        x2, y2 = cx + card_w / 2, cy + card_h / 2

        for pad, fill in ((18, "#1a2a3c"), (10, "#243448")):
            _rounded_rect(
                canvas, x1 - pad, y1 - pad, x2 + pad, y2 + pad, 28,
                fill=fill, outline="",
            )

        _rounded_rect(
            canvas, x1, y1, x2, y2, 22,
            fill="#4a5a6c", outline="#e8eef4", width=2,
        )
        canvas.create_line(x1 + 24, y1 + 3, x2 - 24, y1 + 3, fill="#ffffff", width=1)
        canvas.create_line(x1 + 3, y1 + 24, x1 + 3, y2 - 24, fill="#d0d8e0", width=1)

        _draw_bell(canvas, cx, y1 + card_h * 0.22, scale=1.15)

        canvas.create_text(
            cx, y1 + card_h * 0.42,
            text="new message appears",
            font=("Segoe UI", 28, "bold"),
            fill="#ffffff",
        )

        sub = (
            message.strip()
            if message and message.strip()
            else "Important system notification. Please review the details below."
        )
        if title and title.strip() and title.strip().lower() not in ("discord",):
            sub = f"{title.strip()}\n{sub}" if message and message.strip() else title.strip()

        canvas.create_text(
            cx, y1 + card_h * 0.58,
            text=sub,
            font=("Segoe UI", 12),
            fill="#e8eef4",
            width=card_w * 0.78,
            justify="center",
        )

        btn_w, btn_h = 150, 44
        bx1 = cx - btn_w / 2
        by1 = y2 - card_h * 0.22 - btn_h / 2
        bx2 = bx1 + btn_w
        by2 = by1 + btn_h
        btn = _rounded_rect(
            canvas, bx1, by1, bx2, by2, 12,
            fill="#5a6a7c", outline="#ffffff", width=2,
        )
        btn_label = canvas.create_text(
            cx, (by1 + by2) / 2,
            text="OK",
            font=("Segoe UI Semibold", 13),
            fill="#ffffff",
        )

        closed = {"done": False}

        def dismiss(event=None):
            if closed["done"]:
                return "break"
            closed["done"] = True
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._popup = None
            self._showing = False
            self.root.after(10, self._try_show_next)
            return "break"

        def on_hover(event=None):
            canvas.itemconfig(btn, fill="#6a7a8c")

        def on_leave(event=None):
            canvas.itemconfig(btn, fill="#5a6a7c")

        for item in (btn, btn_label):
            canvas.tag_bind(item, "<Button-1>", dismiss)
            canvas.tag_bind(item, "<Enter>", on_hover)
            canvas.tag_bind(item, "<Leave>", on_leave)

        for seq in ("<Escape>", "<Return>", "<KP_Enter>"):
            win.bind(seq, dismiss)
            canvas.bind(seq, dismiss)

        win.protocol("WM_DELETE_WINDOW", dismiss)
        canvas.focus_set()
        win.focus_force()
        win.after(50, lambda: (canvas.focus_set(), win.focus_force()))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        print("=== DiscordAlert starting ===", flush=True)
        AlertApp().run()
    except Exception:
        traceback.print_exc()
        raise
