"""
DiscordAlert — Windows Discord DM fullscreen alert
--------------------------------------------------
First launch: Install wizard → pick UI mode (5 themes) → accept terms → Finish.
Afterwards: watches Discord notifications and shows themed popups for EVERY message.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
import traceback
import winreg

# ---------------------------------------------------------------------------
# Paths / logging
# ---------------------------------------------------------------------------

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DiscordAlert")
os.makedirs(APP_DIR, exist_ok=True)
LOG_PATH = os.path.join(APP_DIR, "log.txt")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
INSTALL_EXE = os.path.join(APP_DIR, "DiscordAlert.exe")

_log_file = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
if sys.stdout is None:
    sys.stdout = _log_file
else:
    class _Tee:
        def __init__(self, a, b):
            self.a, self.b = a, b

        def write(self, data):
            for s in (self.a, self.b):
                try:
                    s.write(data)
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

DISCORD_AUMIDS = (
    "com.squirrel.Discord.Discord",
    "com.squirrel.DiscordPTB.DiscordPTB",
    "com.squirrel.DiscordCanary.DiscordCanary",
    "com.squirrel.DiscordDevelopment.DiscordDevelopment",
)
NOTIF_SETTINGS_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"

TERMS_TEXT = (
    "DiscordAlert Terms of Use\n"
    "=========================\n\n"
    "1. DiscordAlert is an unofficial local Windows utility. It is not affiliated "
    "with Discord Inc.\n\n"
    "2. The app runs on your PC only. It watches Windows notification signals from "
    "Discord and shows an on-screen alert. It does not log into Discord, read your "
    "password, or upload your messages to any server.\n\n"
    "3. You are responsible for enabling Discord desktop notifications and granting "
    "Windows notification access when prompted.\n\n"
    "4. THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND. Use at your "
    "own risk.\n\n"
    "5. By clicking Agree, you confirm you understand the above and want to install "
    "and use DiscordAlert on this computer."
)

# ---------------------------------------------------------------------------
# Themes (matching the 5 mockup styles)
# ---------------------------------------------------------------------------

THEMES = {
    "cyan": {
        "name": "Cyan HUD",
        "blurb": "Classic tech borders",
        "bg": "#071018",
        "panel": "#0d1a24",
        "panel2": "#122230",
        "accent": "#2ec8ff",
        "accent_dim": "#1a6a88",
        "text": "#ffffff",
        "muted": "#9bb4c4",
        "card": "#0a1520",
        "badge_bg": "#0a2030",
        "radius": 6,
        "frame": "hud",
        "bell": "rings",
    },
    "nebula": {
        "name": "Nebula Glass",
        "blurb": "Soft purple glass",
        "bg": "#12081c",
        "panel": "#1a0f2e",
        "panel2": "#241540",
        "accent": "#a855f7",
        "accent_dim": "#6b21a8",
        "text": "#ffffff",
        "muted": "#c4b5d8",
        "card": "#140a24",
        "badge_bg": "#1e1038",
        "radius": 22,
        "frame": "glass",
        "bell": "orb",
    },
    "emerald": {
        "name": "Emerald Hex",
        "blurb": "Green cyber look",
        "bg": "#061410",
        "panel": "#0c1c18",
        "panel2": "#122820",
        "accent": "#34d399",
        "accent_dim": "#0f766e",
        "text": "#ffffff",
        "muted": "#a7c4b8",
        "card": "#081612",
        "badge_bg": "#0d221c",
        "radius": 10,
        "frame": "hex",
        "bell": "hex",
    },
    "midnight": {
        "name": "Classic Midnight",
        "blurb": "Clean & classic",
        "bg": "#0b0f14",
        "panel": "#151a22",
        "panel2": "#1c2330",
        "accent": "#3b82f6",
        "accent_dim": "#1e3a5f",
        "text": "#ffffff",
        "muted": "#9aa3b2",
        "card": "#10151c",
        "badge_bg": "#1a2030",
        "radius": 18,
        "frame": "soft",
        "bell": "soft",
    },
    "amber": {
        "name": "Amber Industrial",
        "blurb": "Bold orange frame",
        "bg": "#120a04",
        "panel": "#1a1008",
        "panel2": "#261808",
        "accent": "#f59e0b",
        "accent_dim": "#92400e",
        "text": "#ffffff",
        "muted": "#d6c4a8",
        "card": "#140c06",
        "badge_bg": "#221408",
        "radius": 4,
        "frame": "industrial",
        "bell": "rings",
    },
}

THEME_ORDER = ("cyan", "nebula", "emerald", "midnight", "amber")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_config(data: dict) -> None:
    merged = load_config()
    merged.update(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)


def is_installed() -> bool:
    cfg = load_config()
    return bool(cfg.get("installed") and cfg.get("terms_accepted") and cfg.get("theme") in THEMES)


def get_theme_id() -> str:
    cfg = load_config()
    tid = cfg.get("theme", "midnight")
    return tid if tid in THEMES else "midnight"


def install_files() -> None:
    """Copy running exe into LocalAppData when frozen."""
    os.makedirs(APP_DIR, exist_ok=True)
    if getattr(sys, "frozen", False):
        src = sys.executable
        try:
            if os.path.abspath(src) != os.path.abspath(INSTALL_EXE):
                shutil.copy2(src, INSTALL_EXE)
                print(f"Installed exe → {INSTALL_EXE}", flush=True)
        except Exception as e:
            print("Install copy skipped:", e, flush=True)

    # Desktop shortcut (best-effort via PowerShell)
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        link = os.path.join(desktop, "DiscordAlert.lnk")
        target = INSTALL_EXE if os.path.isfile(INSTALL_EXE) else (
            sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
        )
        ps = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{link}'); "
            f"$s.TargetPath = '{target}'; "
            f"$s.WorkingDirectory = '{APP_DIR}'; "
            f"$s.Description = 'DiscordAlert'; "
            f"$s.Save()"
        )
        os.system(f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{ps}" >nul 2>&1')
    except Exception as e:
        print("Shortcut skipped:", e, flush=True)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    r = max(0, min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _draw_grid(canvas, w, h, bg, cell=40):
    canvas.create_rectangle(0, 0, w, h, fill=bg, outline="")
    line = "#ffffff"
    # very faint grid
    for x in range(0, w, cell):
        canvas.create_line(x, 0, x, h, fill=line, width=1, stipple="gray25")
    for y in range(0, h, cell):
        canvas.create_line(0, y, w, y, fill=line, width=1, stipple="gray25")


def _draw_corner_brackets(canvas, x1, y1, x2, y2, color, size=18, width=2):
    s = size
    # top-left
    canvas.create_line(x1, y1 + s, x1, y1, x1 + s, y1, fill=color, width=width)
    # top-right
    canvas.create_line(x2 - s, y1, x2, y1, x2, y1 + s, fill=color, width=width)
    # bottom-left
    canvas.create_line(x1, y2 - s, x1, y2, x1 + s, y2, fill=color, width=width)
    # bottom-right
    canvas.create_line(x2 - s, y2, x2, y2, x2, y2 - s, fill=color, width=width)


def _draw_bell(canvas, cx, cy, color="#ffffff", scale=1.0):
    s = scale
    canvas.create_arc(
        cx - 22 * s, cy - 16 * s, cx + 22 * s, cy + 30 * s,
        start=0, extent=180, style="arc", outline=color, width=2,
    )
    canvas.create_line(cx - 22 * s, cy + 7 * s, cx + 22 * s, cy + 7 * s, fill=color, width=2)
    canvas.create_oval(cx - 5 * s, cy + 8 * s, cx + 5 * s, cy + 18 * s, outline=color, width=2)
    canvas.create_oval(cx - 4 * s, cy - 22 * s, cx + 4 * s, cy - 14 * s, outline=color, width=2)


def _draw_bell_frame(canvas, cx, cy, theme):
    accent = theme["accent"]
    style = theme.get("bell", "soft")
    if style == "orb":
        for r, w in ((48, 1), (36, 2), (26, 0)):
            if w:
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=accent, width=w)
            else:
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=theme["panel2"], outline=accent, width=2)
    elif style == "hex":
        import math
        r = 40
        pts = []
        for i in range(6):
            a = math.radians(60 * i - 30)
            pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
        canvas.create_polygon(pts, outline=accent, fill=theme["panel2"], width=2)
        canvas.create_oval(cx - 28, cy - 28, cx + 28, cy + 28, outline=accent, width=1)
    elif style == "rings":
        for r in (42, 34, 26):
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=accent, width=1)
        canvas.create_oval(cx - 22, cy - 22, cx + 22, cy + 22, fill=theme["panel2"], outline=accent, width=2)
    else:  # soft classic
        canvas.create_oval(cx - 40, cy - 40, cx + 40, cy + 40, outline=accent, width=1)
        canvas.create_oval(cx - 28, cy - 28, cx + 28, cy + 28, fill=theme["panel2"], outline=accent, width=2)
    _draw_bell(canvas, cx, cy, color=theme["text"], scale=1.0)


def _draw_panel_frame(canvas, x1, y1, x2, y2, theme):
    accent = theme["accent"]
    radius = theme["radius"]
    frame = theme.get("frame", "soft")
    _rounded_rect(canvas, x1, y1, x2, y2, radius, fill=theme["panel"], outline=accent, width=2)
    if frame == "hud":
        _draw_corner_brackets(canvas, x1 + 8, y1 + 8, x2 - 8, y2 - 8, accent, size=16, width=2)
        # corner tick marks
        for dx, dy in ((12, 28), (28, 12)):
            canvas.create_line(x1 + 4, y1 + dy, x1 + 14, y1 + dy, fill=accent, width=1)
            canvas.create_line(x1 + dx, y1 + 4, x1 + dx, y1 + 14, fill=accent, width=1)
    elif frame == "industrial":
        _draw_corner_brackets(canvas, x1 + 6, y1 + 6, x2 - 6, y2 - 6, accent, size=22, width=3)
    elif frame == "hex":
        _draw_corner_brackets(canvas, x1 + 10, y1 + 10, x2 - 10, y2 - 10, accent, size=14, width=1)
    elif frame == "glass":
        canvas.create_line(x1 + 24, y1 + 3, x2 - 24, y1 + 3, fill="#ffffff", width=1)
    # soft / default: just rounded border already drawn


def focus_discord_window() -> bool:
    """Bring a Discord window to the foreground if found."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    found = []

    def foreach(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "discord" in (buf.value or "").lower():
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "Chrome_WidgetWin" in (cls.value or ""):
                found.append(hwnd)
                return False
        return True

    EnumWindows(EnumWindowsProc(foreach), 0)
    if not found:
        return False
    hwnd = found[0]
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Installer wizard
# ---------------------------------------------------------------------------

class InstallWizard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DiscordAlert Setup")
        self.root.geometry("820x640")
        self.root.minsize(820, 640)
        self.root.resizable(False, False)
        self.root.configure(bg="#0e1218")
        self.theme_id = "midnight"
        self.agreed = tk.BooleanVar(value=False)
        self.finished_ok = False
        self._center()
        self._build_shell()
        self.show_welcome()

    def _center(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = 820, 640
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_shell(self):
        header = tk.Frame(self.root, bg="#151a22", height=64)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(
            header, text="DiscordAlert Setup", font=("Segoe UI Semibold", 18),
            fg="#ffffff", bg="#151a22",
        ).pack(side="left", padx=24, pady=16)
        self.step_label = tk.Label(
            header, text="Step 1 of 4", font=("Segoe UI", 11),
            fg="#8b95a5", bg="#151a22",
        )
        self.step_label.pack(side="right", padx=24)

        # Pack footer FIRST so it always stays visible at the bottom.
        self.footer = tk.Frame(self.root, bg="#151a22", height=78)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)

        self.body = tk.Frame(self.root, bg="#0e1218")
        self.body.pack(fill="both", expand=True, padx=28, pady=(16, 8))

    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.footer.winfo_children():
            w.destroy()

    def _btn(self, parent, text, command, primary=False, state="normal"):
        bg = "#3b82f6" if primary else "#2a3140"
        fg = "#ffffff"
        b = tk.Button(
            parent, text=text, command=command, font=("Segoe UI Semibold", 11),
            bg=bg, fg=fg, activebackground="#60a5fa" if primary else "#3a4558",
            activeforeground="#ffffff", relief="flat", padx=22, pady=10,
            cursor="hand2", state=state, bd=0,
        )
        return b

    def show_welcome(self):
        self._clear_body()
        self.step_label.config(text="Step 1 of 4 — Install")
        tk.Label(
            self.body, text="Welcome to DiscordAlert",
            font=("Segoe UI Semibold", 26), fg="#ffffff", bg="#0e1218",
        ).pack(anchor="w", pady=(12, 8))
        tk.Label(
            self.body,
            text=(
                "Never miss a Discord DM again.\n\n"
                "This setup will install DiscordAlert on your PC, let you pick a UI mode, "
                "and start watching for new messages."
            ),
            font=("Segoe UI", 12), fg="#a8b0bd", bg="#0e1218",
            justify="left", wraplength=720,
        ).pack(anchor="w")

        card = tk.Frame(self.body, bg="#151a22", padx=18, pady=16)
        card.pack(anchor="w", fill="x", pady=(28, 0))
        for line in (
            "• Fullscreen alert for every Discord notification",
            "• Choose from 5 UI modes (classic + neon styles)",
            "• Runs locally — no Discord login required",
        ):
            tk.Label(card, text=line, font=("Segoe UI", 11), fg="#d7dde8", bg="#151a22").pack(anchor="w", pady=2)

        self._btn(self.footer, "Install", self.show_theme_picker, primary=True).pack(
            side="right", padx=24, pady=16
        )
        self._btn(self.footer, "Cancel", self.root.destroy).pack(side="right", pady=16)

    def show_theme_picker(self):
        self._clear_body()
        self.step_label.config(text="Step 2 of 4 — UI Mode")
        tk.Label(
            self.body, text="Select UI Mode",
            font=("Segoe UI Semibold", 20), fg="#ffffff", bg="#0e1218",
        ).pack(anchor="w")
        tk.Label(
            self.body,
            text="Pick how your message alerts will look. Classic Midnight is the clean option.",
            font=("Segoe UI", 11), fg="#8b95a5", bg="#0e1218",
        ).pack(anchor="w", pady=(2, 10))

        grid = tk.Frame(self.body, bg="#0e1218")
        grid.pack(fill="both", expand=True)

        self._theme_cards = {}
        positions = [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]
        for (row, col), tid in zip(positions, THEME_ORDER):
            theme = THEMES[tid]
            selected = tid == self.theme_id
            card = tk.Frame(
                grid, bg=theme["panel"], highlightthickness=3 if selected else 2,
                highlightbackground=theme["accent"] if selected else "#2a3140",
                highlightcolor=theme["accent"], padx=8, pady=8, cursor="hand2",
            )
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            swatch = tk.Canvas(card, width=180, height=54, bg=theme["bg"], highlightthickness=0)
            swatch.pack()
            swatch.create_rectangle(6, 8, 174, 46, outline=theme["accent"], width=2)
            swatch.create_oval(16, 16, 40, 40, outline=theme["accent"], width=2)
            swatch.create_text(
                110, 28, text="New message", fill=theme["text"],
                font=("Segoe UI", 9, "bold"),
            )
            tk.Label(
                card, text=theme["name"], font=("Segoe UI Semibold", 11),
                fg=theme["text"], bg=theme["panel"],
            ).pack(anchor="w", pady=(6, 0))
            tk.Label(
                card, text=theme["blurb"], font=("Segoe UI", 9),
                fg=theme["muted"], bg=theme["panel"],
            ).pack(anchor="w")

            def select(tid=tid):
                self.theme_id = tid
                self._refresh_theme_selection()

            for w in (card, swatch):
                w.bind("<Button-1>", lambda e, tid=tid: select(tid))
            for child in card.winfo_children():
                if isinstance(child, tk.Label):
                    child.bind("<Button-1>", lambda e, tid=tid: select(tid))
            self._theme_cards[tid] = card

        for c in range(3):
            grid.columnconfigure(c, weight=1)
        for r in range(2):
            grid.rowconfigure(r, weight=1)

        # Step 2 buttons — Next goes to Terms; Finish is on the last step
        self._btn(self.footer, "Next", self.show_terms, primary=True).pack(
            side="right", padx=24, pady=16
        )
        self._btn(self.footer, "Back", self.show_welcome).pack(side="right", pady=16)

    def _refresh_theme_selection(self):
        for tid, card in self._theme_cards.items():
            theme = THEMES[tid]
            card.configure(
                highlightbackground=theme["accent"] if tid == self.theme_id else "#2a3140",
                highlightthickness=3 if tid == self.theme_id else 2,
            )

    def show_terms(self):
        self._clear_body()
        self.step_label.config(text="Step 3 of 4 — Terms")
        tk.Label(
            self.body, text="Agree to Terms",
            font=("Segoe UI Semibold", 22), fg="#ffffff", bg="#0e1218",
        ).pack(anchor="w")

        frame = tk.Frame(self.body, bg="#151a22")
        frame.pack(fill="both", expand=True, pady=(12, 8))
        scroll = tk.Scrollbar(frame)
        scroll.pack(side="right", fill="y")
        text = tk.Text(
            frame, wrap="word", font=("Consolas", 10), bg="#10151c", fg="#d7dde8",
            insertbackground="#ffffff", relief="flat", padx=14, pady=12,
            yscrollcommand=scroll.set,
        )
        text.pack(fill="both", expand=True)
        scroll.config(command=text.yview)
        text.insert("1.0", TERMS_TEXT)
        text.configure(state="disabled")

        self.agreed.set(False)
        tk.Checkbutton(
            self.body,
            text="I have read and agree to the Terms of Use",
            variable=self.agreed, onvalue=True, offvalue=False,
            font=("Segoe UI", 11), fg="#ffffff", bg="#0e1218",
            activebackground="#0e1218", activeforeground="#ffffff",
            selectcolor="#1c2330",
        ).pack(anchor="w", pady=(8, 0))

        self._btn(self.footer, "Agree & Continue", self.show_finish, primary=True).pack(
            side="right", padx=24, pady=16
        )
        self._btn(self.footer, "Back", self.show_theme_picker).pack(side="right", pady=16)

    def show_finish(self):
        if not self.agreed.get():
            messagebox.showwarning("Terms required", "Please agree to the Terms of Use to continue.")
            return

        self._clear_body()
        self.step_label.config(text="Step 4 of 4 — Finish")
        theme = THEMES[self.theme_id]
        tk.Label(
            self.body, text="Ready to finish",
            font=("Segoe UI Semibold", 22), fg="#ffffff", bg="#0e1218",
        ).pack(anchor="w", pady=(20, 8))
        tk.Label(
            self.body,
            text=(
                f"UI Mode: {theme['name']}\n"
                f"Install folder: {APP_DIR}\n\n"
                "Click Finish to install and start DiscordAlert.\n"
                "A desktop shortcut will be created."
            ),
            font=("Segoe UI", 12), fg="#a8b0bd", bg="#0e1218", justify="left",
        ).pack(anchor="w")

        preview = tk.Canvas(self.body, width=420, height=140, bg=theme["bg"], highlightthickness=0)
        preview.pack(anchor="w", pady=24)
        preview.create_rectangle(20, 20, 400, 120, outline=theme["accent"], width=2)
        preview.create_text(210, 55, text="New message appears", fill=theme["text"],
                            font=("Segoe UI Semibold", 14))
        preview.create_text(210, 85, text=theme["name"], fill=theme["muted"], font=("Segoe UI", 10))

        self._btn(self.footer, "Finish", self._complete, primary=True).pack(side="right", padx=24, pady=16)
        self._btn(self.footer, "Back", self.show_terms).pack(side="right", pady=16)

    def _complete(self):
        try:
            install_files()
            save_config({
                "installed": True,
                "terms_accepted": True,
                "theme": self.theme_id,
                "installed_at": datetime.now().isoformat(timespec="seconds"),
            })
            self.finished_ok = True
            print(f"Setup complete. theme={self.theme_id}", flush=True)
            self.root.destroy()
        except Exception as e:
            messagebox.showerror("Install failed", str(e))
            traceback.print_exc()

    def run(self) -> bool:
        self.root.mainloop()
        return self.finished_ok


# ---------------------------------------------------------------------------
# Detection (unchanged behavior — every Discord notif queues a popup)
# ---------------------------------------------------------------------------

class AlertBus:
    def __init__(self, alert_queue: queue.Queue):
        self.alert_queue = alert_queue
        self._lock = threading.Lock()
        self._last_registry = 0.0

    def emit(self, source: str, title: str = "Discord", body: str = ""):
        body = body or "You have a new message waiting for review."
        with self._lock:
            now = time.monotonic()
            if source == "title-badge":
                if now - self._last_registry < 1.2:
                    print(f"Skip {source} (registry already handled)", flush=True)
                    return
            elif source == "registry":
                self._last_registry = now
        print(f"ALERT [{source}]: queue popup", flush=True)
        self.alert_queue.put((title, body))


def read_last_notification_time(aumid: str):
    path = rf"{NOTIF_SETTINGS_ROOT}\{aumid}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, regtype = winreg.QueryValueEx(key, "LastNotificationAddedTime")
            if regtype == winreg.REG_QWORD or isinstance(value, int):
                return int(value)
    except OSError:
        return None
    return None


def discover_discord_aumids():
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
    def __init__(self, bus: AlertBus):
        self.bus = bus
        self._stop = threading.Event()

    def run(self):
        last_seen = {a: read_last_notification_time(a) for a in discover_discord_aumids()}
        print(f"Registry watcher ready: {[a for a, v in last_seen.items() if v]}", flush=True)
        while not self._stop.is_set():
            try:
                for aumid in discover_discord_aumids():
                    if aumid not in last_seen:
                        last_seen[aumid] = read_last_notification_time(aumid)
                for aumid, prev in list(last_seen.items()):
                    current = read_last_notification_time(aumid)
                    if current is None:
                        continue
                    if prev is None:
                        last_seen[aumid] = current
                        continue
                    if current != prev:
                        last_seen[aumid] = current
                        self.bus.emit("registry")
            except Exception:
                traceback.print_exc()
            self._stop.wait(0.15)


def start_registry_watcher(bus: AlertBus):
    w = DiscordRegistryWatcher(bus)
    threading.Thread(target=w.run, name="DiscordRegistryWatcher", daemon=True).start()
    return w


def _discord_window_unread_counts():
    import ctypes
    from ctypes import wintypes
    import re

    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    counts = []

    def foreach(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if "discord" not in title.lower():
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "Chrome_WidgetWin" not in (cls.value or ""):
            return True
        m = re.match(r"^\((\d+)\)\s+", title)
        counts.append(int(m.group(1)) if m else 0)
        return True

    user32.EnumWindows(EnumWindowsProc(foreach), 0)
    return max(counts) if counts else None


class DiscordTitleWatcher:
    def __init__(self, bus: AlertBus):
        self.bus = bus
        self._stop = threading.Event()

    def run(self):
        last = _discord_window_unread_counts()
        print(f"Title badge watcher ready. unread={last}", flush=True)
        while not self._stop.is_set():
            try:
                cur = _discord_window_unread_counts()
                if cur is not None and last is not None and cur > last:
                    for _ in range(cur - last):
                        self.bus.emit("title-badge")
                if cur is not None:
                    last = cur
            except Exception:
                traceback.print_exc()
            self._stop.wait(0.4)


def start_title_watcher(bus: AlertBus):
    w = DiscordTitleWatcher(bus)
    threading.Thread(target=w.run, name="DiscordTitleWatcher", daemon=True).start()
    return w


# ---------------------------------------------------------------------------
# Runtime app + themed popup
# ---------------------------------------------------------------------------

class AlertApp:
    def __init__(self):
        self.alert_queue = queue.Queue()
        self.bus = AlertBus(self.alert_queue)
        self.theme_id = get_theme_id()
        self.theme = THEMES[self.theme_id]

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
        print(f"DiscordAlert running. theme={self.theme_id}", flush=True)

    def _show_started_toast(self):
        t = self.theme
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.attributes("-alpha", 0.95)
        w, h = 380, 96
        sw, sh = toast.winfo_screenwidth(), toast.winfo_screenheight()
        toast.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 60}")
        toast.configure(bg=t["bg"])
        c = tk.Canvas(toast, width=w, height=h, bg=t["bg"], highlightthickness=0)
        c.pack(fill="both", expand=True)
        _rounded_rect(c, 4, 4, w - 4, h - 4, 14, fill=t["panel"], outline=t["accent"], width=2)
        c.create_text(w / 2, h / 2 - 8, text="DiscordAlert is running",
                      font=("Segoe UI Semibold", 14), fill=t["text"])
        c.create_text(w / 2, h / 2 + 16, text=t["name"], font=("Segoe UI", 10), fill=t["muted"])
        toast.after(2400, toast.destroy)

    def _try_show_next(self):
        if self._showing:
            return
        try:
            title, body = self.alert_queue.get_nowait()
        except queue.Empty:
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
        t = THEMES[get_theme_id()]  # allow live theme from config
        self.theme = t

        win = tk.Toplevel(self.root)
        self._popup = win
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        win.configure(bg=t["bg"])
        try:
            win.grab_set()
        except Exception:
            pass

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        canvas = tk.Canvas(win, width=sw, height=sh, bg=t["bg"], highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        _draw_grid(canvas, sw, sh, t["bg"])

        # Center panel (classic notification card proportions)
        card_w = min(560, int(sw * 0.46))
        card_h = min(420, int(sh * 0.52))
        cx, cy = sw / 2, sh / 2
        x1, y1 = cx - card_w / 2, cy - card_h / 2
        x2, y2 = cx + card_w / 2, cy + card_h / 2

        _draw_panel_frame(canvas, x1, y1, x2, y2, t)

        # Bell
        bell_y = y1 + 62
        _draw_bell_frame(canvas, cx, bell_y, t)

        # SYSTEM NOTIFICATION badge (top-right of panel)
        badge_text = "SYSTEM NOTIFICATION"
        bx2 = x2 - 22
        bx1 = bx2 - 168
        by1, by2 = y1 + 18, y1 + 40
        _rounded_rect(canvas, bx1, by1, bx2, by2, 10, fill=t["badge_bg"], outline=t["accent_dim"], width=1)
        canvas.create_oval(bx1 + 10, by1 + 7, bx1 + 18, by2 - 7, fill=t["accent"], outline="")
        canvas.create_text((bx1 + bx2) / 2 + 6, (by1 + by2) / 2, text=badge_text,
                           font=("Segoe UI", 8, "bold"), fill=t["text"])

        # Title + subtitle
        canvas.create_text(
            cx, y1 + 130, text="New message appears",
            font=("Segoe UI Semibold", 26), fill=t["text"],
        )
        canvas.create_text(
            cx, y1 + 168,
            text="You have a new message waiting for review.",
            font=("Segoe UI", 11), fill=t["muted"],
        )

        # Inner message card
        mx1, my1 = x1 + 36, y1 + 200
        mx2, my2 = x2 - 36, y1 + 278
        _rounded_rect(canvas, mx1, my1, mx2, my2, 12, fill=t["card"], outline=t["accent_dim"], width=1)
        canvas.create_rectangle(mx1, my1 + 8, mx1 + 4, my2 - 8, fill=t["accent"], outline="")
        # avatar circle
        canvas.create_oval(mx1 + 18, my1 + 18, mx1 + 54, my2 - 18, outline=t["muted"], width=2)
        canvas.create_oval(mx1 + 28, my1 + 24, mx1 + 44, my1 + 40, outline=t["muted"], width=1)
        canvas.create_arc(mx1 + 24, my1 + 38, mx1 + 48, my2 - 16, start=20, extent=140,
                          style="arc", outline=t["muted"], width=1)

        sender = title.strip() if title and title.strip() else "System"
        preview = (message or "You have a new message waiting for review.").strip()
        if len(preview) > 64:
            preview = preview[:61] + "..."
        stamp = datetime.now().strftime("%I:%M %p").lstrip("0")

        canvas.create_text(mx1 + 68, my1 + 28, text=sender, anchor="w",
                           font=("Segoe UI Semibold", 12), fill=t["text"])
        canvas.create_text(mx1 + 68, my1 + 52, text=preview, anchor="w",
                           font=("Segoe UI", 10), fill=t["muted"], width=(mx2 - mx1 - 140))
        canvas.create_text(mx2 - 16, my1 + 28, text=stamp, anchor="e",
                           font=("Segoe UI", 9), fill=t["muted"])

        # Buttons: View message | Dismiss
        btn_y1 = y2 - 78
        btn_y2 = y2 - 34
        view_x1, view_x2 = x1 + 48, cx - 12
        dis_x1, dis_x2 = cx + 12, x2 - 48

        view_btn = _rounded_rect(
            canvas, view_x1, btn_y1, view_x2, btn_y2, 10,
            fill=t["accent"], outline=t["accent"], width=1,
        )
        view_label = canvas.create_text(
            (view_x1 + view_x2) / 2, (btn_y1 + btn_y2) / 2,
            text="View message", font=("Segoe UI Semibold", 11), fill="#0b0f14",
        )

        dis_btn = _rounded_rect(
            canvas, dis_x1, btn_y1, dis_x2, btn_y2, 10,
            fill=t["panel2"], outline="#ffffff", width=1,
        )
        dis_label = canvas.create_text(
            (dis_x1 + dis_x2) / 2, (btn_y1 + btn_y2) / 2,
            text="Dismiss", font=("Segoe UI Semibold", 11), fill=t["text"],
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

        def view_message(event=None):
            focus_discord_window()
            return dismiss()

        def hover_view(_=None):
            canvas.itemconfig(view_btn, fill=t["text"])

        def leave_view(_=None):
            canvas.itemconfig(view_btn, fill=t["accent"])

        def hover_dis(_=None):
            canvas.itemconfig(dis_btn, fill=t["accent_dim"])

        def leave_dis(_=None):
            canvas.itemconfig(dis_btn, fill=t["panel2"])

        for item in (view_btn, view_label):
            canvas.tag_bind(item, "<Button-1>", view_message)
            canvas.tag_bind(item, "<Enter>", hover_view)
            canvas.tag_bind(item, "<Leave>", leave_view)
        for item in (dis_btn, dis_label):
            canvas.tag_bind(item, "<Button-1>", dismiss)
            canvas.tag_bind(item, "<Enter>", hover_dis)
            canvas.tag_bind(item, "<Leave>", leave_dis)

        for seq in ("<Escape>", "<Return>", "<KP_Enter>"):
            win.bind(seq, dismiss)
            canvas.bind(seq, dismiss)

        win.protocol("WM_DELETE_WINDOW", dismiss)
        canvas.focus_set()
        win.focus_force()
        win.after(50, lambda: (canvas.focus_set(), win.focus_force()))

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main():
    print("=== DiscordAlert starting ===", flush=True)
    if not is_installed():
        print("First run — launching installer wizard", flush=True)
        ok = InstallWizard().run()
        if not ok:
            print("Setup cancelled.", flush=True)
            return
    AlertApp().run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
