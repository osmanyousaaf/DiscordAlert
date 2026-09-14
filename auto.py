"""
Discord DM Fullscreen Popup Alert (Windows only)
--------------------------------------------------
Watches Windows notification center for Discord toast notifications
and shows a glassmorphism alert until dismissed (Esc, Enter, or OK).

Critical design:
  The notification watcher runs in a BACKGROUND thread and NEVER stops
  while a popup is open. Alerts are queued so message 2, 3, 4... are not
  lost while the user is still dismissing an earlier popup.
"""

import asyncio
import hashlib
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback

from winsdk.windows.ui.notifications import NotificationKinds
from winsdk.windows.ui.notifications.management import (
    UserNotificationListener,
    UserNotificationListenerAccessStatus,
)

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DiscordAlert")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "log.txt")

if sys.stdout is None or sys.stderr is None:
    _log_file = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
    sys.stdout = _log_file
    sys.stderr = _log_file


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
# Notification helpers
# ---------------------------------------------------------------------------

def extract_text(notif):
    texts = []
    try:
        for binding in notif.notification.visual.bindings:
            for t in binding.get_text_elements():
                texts.append(t.text)
    except Exception:
        pass
    title = texts[0] if len(texts) > 0 else "Discord"
    body = texts[1] if len(texts) > 1 else ""
    return title, body


def notif_creation_key(notif) -> str:
    try:
        ct = notif.creation_time
        # winsdk DateTime / datetime-like
        if hasattr(ct, "timestamp"):
            return str(ct.timestamp())
        return str(ct)
    except Exception:
        return ""


def content_fingerprint(notif) -> str:
    """Fingerprint that changes when Discord updates a toast in-place
    (same id, new text / new creation time)."""
    title, body = extract_text(notif)
    raw = f"{notif.id}|{notif_creation_key(notif)}|{title}|{body}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def is_discord(notif) -> bool:
    try:
        app_info = notif.app_info
        name = app_info.display_info.display_name if app_info else ""
        return "discord" in (name or "").lower()
    except Exception:
        return False


async def get_listener():
    listener = UserNotificationListener.current
    access = await listener.request_access_async()
    if access != UserNotificationListenerAccessStatus.ALLOWED:
        print(
            "Notification access denied.\n"
            "Enable it manually: Windows Settings > Privacy & Security "
            "> Notifications > let apps access notifications, then re-run.",
            flush=True,
        )
        return None
    return listener


# ---------------------------------------------------------------------------
# Background watcher — never blocked by UI
# ---------------------------------------------------------------------------

class NotificationWatcher:
    """Polls (and wakes on NotificationChanged) continuously in a thread.
    Puts every new/changed Discord toast onto alert_queue immediately."""

    def __init__(self, alert_queue: queue.Queue):
        self.alert_queue = alert_queue
        # id -> fingerprint we already enqueued an alert for
        self.seen_content = {}
        # fingerprints enqueued recently (dedupe across id recycle)
        self.recent_fps = {}
        self._wake = None  # asyncio.Event, set from winrt callback
        self._loop = None

    def _remember_fp(self, fp: str):
        now = time.monotonic()
        self.recent_fps[fp] = now
        # prune old entries (5 minutes)
        cutoff = now - 300
        for k, t in list(self.recent_fps.items()):
            if t < cutoff:
                del self.recent_fps[k]

    def _enqueue(self, title: str, body: str, fp: str):
        if fp in self.recent_fps:
            return
        self._remember_fp(fp)
        print(f"Queued Discord alert: {title!r} / {body!r}", flush=True)
        self.alert_queue.put((title, body))

    async def _scan_once(self, listener):
        notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
        current_ids = set()

        for notif in notifications:
            current_ids.add(notif.id)
            if not is_discord(notif):
                continue

            fp = content_fingerprint(notif)
            prev = self.seen_content.get(notif.id)
            if prev == fp:
                continue

            # New id OR same id with changed content → alert
            self.seen_content[notif.id] = fp
            title, body = extract_text(notif)
            self._enqueue(title, body, fp)

        # Toasts that left Action Center: forget their id so a future toast
        # reusing that id is treated as new. Keep recent_fps so we don't
        # double-fire the exact same content within the prune window.
        for stale_id in list(self.seen_content.keys()):
            if stale_id not in current_ids:
                del self.seen_content[stale_id]

    async def run(self):
        listener = await get_listener()
        if not listener:
            self.alert_queue.put(("__error__", "Notification access denied"))
            return

        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()

        # Seed: mark current Discord toasts as already seen (no popup on launch)
        try:
            existing = await listener.get_notifications_async(NotificationKinds.TOAST)
            for notif in existing:
                if not is_discord(notif):
                    continue
                fp = content_fingerprint(notif)
                self.seen_content[notif.id] = fp
                self._remember_fp(fp)
            print(f"Seeded {len(self.seen_content)} existing Discord toast(s).", flush=True)
        except Exception as e:
            print("Warning: could not seed existing notifications:", e, flush=True)

        # Wake immediately whenever Windows says notifications changed
        def on_changed(sender, args):
            try:
                if self._loop and self._wake:
                    self._loop.call_soon_threadsafe(self._wake.set)
            except Exception:
                pass

        try:
            listener.add_notification_changed(on_changed)
            print("Subscribed to NotificationChanged.", flush=True)
        except Exception as e:
            print("NotificationChanged subscribe failed (polling only):", e, flush=True)

        print("Watching for Discord notifications...", flush=True)

        while True:
            try:
                await self._scan_once(listener)
            except Exception as e:
                print("Error while checking notifications:", e, flush=True)
                traceback.print_exc()

            # Wait for a change event OR a short poll interval.
            # Clear only if set, and re-check immediately if a wake arrived
            # during scan so we never drop a notification that showed up mid-loop.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.35)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()


def start_watcher_thread(alert_queue: queue.Queue) -> threading.Thread:
    def target():
        try:
            asyncio.run(NotificationWatcher(alert_queue).run())
        except Exception:
            traceback.print_exc()

    t = threading.Thread(target=target, name="DiscordNotifWatcher", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# UI — single persistent Tk root; popups are Toplevels (Esc / Enter dismiss)
# ---------------------------------------------------------------------------

class AlertApp:
    def __init__(self):
        self.alert_queue = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("DiscordAlert")
        # Keep a tiny off-screen presence so Tk stays alive on Windows
        self.root.geometry("1x1+0+0")

        self._popup = None
        self._showing = False

        self._show_started_toast()
        start_watcher_thread(self.alert_queue)
        self.root.after(150, self._poll_queue)

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
        """Show at most one queued alert if idle."""
        if self._showing:
            return
        try:
            title, body = self.alert_queue.get_nowait()
        except queue.Empty:
            return

        if title == "__error__":
            print(body, flush=True)
            # Keep draining in case more items follow an error marker.
            self.root.after(10, self._try_show_next)
            return

        self._open_popup(title, body)

    def _poll_queue(self):
        """Periodic tick — watcher may have enqueued while we were idle."""
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
        win.grab_set()

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
        if title and title.strip() and title.strip().lower() != "discord":
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
            # Idempotent: Escape must not double-fire and race the next queued popup.
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
            # Show the next queued alert immediately (don't wait for the poll tick).
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

        # Single binding site only (no bind_all) so Escape/Enter fire once.
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
        AlertApp().run()
    except Exception:
        traceback.print_exc()
        raise
