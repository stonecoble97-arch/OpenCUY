#!/usr/bin/env python3
"""
OpenCUY v2.0 — Modern Engine
============================
YouTube Chat Bot + Ad Filter + Live Chat, rebuilt on the XenoIPC PySide6
host: embedded HTML/JS/CSS UI, QWebChannel bridge, switchable themes,
background images, dependency auto-installer, and a persistent theme file.

Safety: every user-driven navigation is pre-flighted in a separate,
headless Chrome instance. The visible window is never navigated to a
page the checker has not approved.
"""

import sys, os, re, json, shutil, signal, tempfile, threading, time, queue
import random
import importlib
import subprocess
import urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DEPS_FILE = HERE / "opencuy_deps.json"
THEME_FILE = HERE / "opencuy_theme.json"
CONFIG_FILE = HERE / "opencuy_config.json"
SCREENSHOT_DIR = HERE / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


def _sanitize_filename(name: str, fallback: str = "screenshot") -> str:
    if not name:
        return fallback
    name = re.sub(r"\s*-\s*YouTube\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:120].rstrip(" .")
    return name or fallback


def _parse_age_text(text):
    if not text:
        return None
    text = text.lower().strip()
    m = re.search(
        r'(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago',
        text)
    if not m:
        if re.search(r'\b(just now|moments? ago|premiered)\b', text):
            return 0
        return None
    n = int(m.group(1))
    unit = m.group(2)
    return n * {
        'second': 1, 'minute': 60, 'hour': 3600,
        'day': 86400, 'week': 604800,
        'month': 2592000, 'year': 31536000,
    }.get(unit, 0)


# ═══════════════════════════════════════════════════════════════════════════
#  DEPENDENCY CHECKER + INSTALLER
# ═══════════════════════════════════════════════════════════════════════════
DEPS = [
    {"key": "pyside6",  "module": "PySide6",            "package": "PySide6",
     "required": True, "description": "Qt bindings for the UI (required)"},
    {"key": "webengine","module": "PySide6.QtWebEngineWidgets",
     "package": "PySide6", "required": True,
     "description": "QtWebEngine (bundled with PySide6) for the embedded UI"},
    {"key": "selenium", "module": "selenium",           "package": "selenium",
     "required": True,
     "description": "Browser automation for YouTube navigation (required)"},
    {"key": "pytchat",  "module": "pytchat",            "package": "pytchat",
     "required": False,
     "description": "YouTube live chat reader (optional — for chat commands)"},
]


def _dep_state():
    out = {}
    for d in DEPS:
        try:
            importlib.import_module(d["module"]); out[d["key"]] = True
        except Exception:
            out[d["key"]] = False
    return out


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _pip_install(packages):
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade"] + list(packages),
            capture_output=True, text=True, timeout=900)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 15 minutes"
    except Exception as e:
        return False, f"pip install failed: {e}"


def _native_dialog(title, message, kind="ask"):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        if kind == "ask":   result = messagebox.askyesno(title, message)
        elif kind == "error": messagebox.showerror(title, message); result = None
        else:               messagebox.showinfo(title, message); result = None
        root.destroy(); return result
    except Exception:
        print(f"\n[{title}] {message}")
        if kind == "ask":
            try: return input("Install? [y/N] ").strip().lower() in ("y", "yes")
            except Exception: return False
        return None


def check_and_install_deps(interactive=True):
    settings = _load_json(DEPS_FILE, {})
    state = _dep_state()
    missing_required = [d for d in DEPS if d["required"] and not state[d["key"]]]
    missing_optional = [d for d in DEPS if not d["required"] and not state[d["key"]]]

    for d in missing_required:
        msg = (f"{d['description']} is missing.\n\n"
               f"Package: {d['package']}\n\n"
               f"OpenCUY cannot start without it. Install now?")
        if interactive and _native_dialog("Missing required dependency", msg, "ask"):
            ok, out = _pip_install([d["package"]])
            if ok:
                try:
                    importlib.invalidate_caches()
                    importlib.import_module(d["module"])
                    state[d["key"]] = True; continue
                except Exception as e:
                    _native_dialog("Install failed",
                                   f"pip reported success but import fails:\n{e}", "error")
            else:
                _native_dialog("Install failed",
                               f"Could not install {d['package']}.\n\n{out[-800:]}", "error")
        _native_dialog("Cannot continue",
                       f"OpenCUY needs {d['package']}. Exiting.", "error")
        return state

    ask_about = [d for d in missing_optional
                 if not settings.get(d["key"], {}).get("dont_ask")]
    if ask_about and interactive:
        lines = [f"• {d['package']} — {d['description']}" for d in ask_about]
        msg = ("Optional dependencies are missing. Install now?\n\n"
               + "\n".join(lines) + "\n\nChoose No to skip.")
        if _native_dialog("Optional dependencies", msg, "ask"):
            for d in ask_about:
                ok, out = _pip_install([d["package"]])
                if ok:
                    try:
                        importlib.invalidate_caches()
                        importlib.import_module(d["module"])
                        state[d["key"]] = True
                        settings.setdefault(d["key"], {})["installed"] = True
                    except Exception as e:
                        _native_dialog("Install issue",
                                       f"{d['package']} installed but import failed:\n{e}",
                                       "error")
                else:
                    _native_dialog("Install failed",
                                   f"Could not install {d['package']}.\n\n{out[-800:]}",
                                   "error")
        else:
            for d in ask_about:
                settings.setdefault(d["key"], {})["dont_ask"] = True
    _save_json(DEPS_FILE, settings)
    return state


_DEP_STATE = check_and_install_deps(interactive=True)

# ── Signal patch (before pytchat) ────────────────────────────────────────
_original_signal = signal.signal
def _safe_signal(signalnum, handler):
    if threading.current_thread() is not threading.main_thread():
        return signal.SIG_DFL
    return _original_signal(signalnum, handler)
signal.signal = _safe_signal

# ── Imports (safe now) ───────────────────────────────────────────────────
from PySide6.QtCore import Qt, QObject, Slot, Signal, QUrl, QTimer
from PySide6.QtGui import (QColor, QIcon, QAction, QPixmap, QPainter,
                           QBrush, QPen, QPalette)
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon,
                               QMenu, QFileDialog)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import pytchat
    PYCHAT_AVAILABLE = True
except ImportError:
    pytchat = None
    PYCHAT_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════
CHAT_COMMAND_PREFIX = "!"
BAIL_TO_BLANK_ON_AD = True
VIDEO_RETRY_ATTEMPTS = 3
VIDEO_RETRY_DELAY = 2.0

MOUSE_BOT_ENABLED = True
MOUSE_BOT_INTERVAL = 25.0
MOUSE_BOT_JITTER = 5

HOME_ON_VIDEO_END = True
OFF_SITE_WATCHDOG = True
BLOCK_LIVE_STREAMS = True
BLOCK_UNDER_24H = True
SCREENSHOT_ENABLED = True
SCREENSHOT_DELAY = 10.0
USE_HEADLESS_CHECKER = True

DISABLE_COOKIES = False

FALLBACK_UNKNOWN_AS_SEARCH = False
FALLBACK_PLAINTEXT_AS_SEARCH = True

_cfg = _load_json(CONFIG_FILE, {})
if isinstance(_cfg, dict):
    if "fallback_unknown_as_search" in _cfg:
        FALLBACK_UNKNOWN_AS_SEARCH = bool(_cfg["fallback_unknown_as_search"])
    if "fallback_plaintext_as_search" in _cfg:
        FALLBACK_PLAINTEXT_AS_SEARCH = bool(_cfg["fallback_plaintext_as_search"])
    if "home_on_video_end" in _cfg:
        HOME_ON_VIDEO_END = bool(_cfg["home_on_video_end"])
    if "off_site_watchdog" in _cfg:
        OFF_SITE_WATCHDOG = bool(_cfg["off_site_watchdog"])
    if "block_live_streams" in _cfg:
        BLOCK_LIVE_STREAMS = bool(_cfg["block_live_streams"])
    if "block_under_24h" in _cfg:
        BLOCK_UNDER_24H = bool(_cfg["block_under_24h"])
    if "screenshot_enabled" in _cfg:
        SCREENSHOT_ENABLED = bool(_cfg["screenshot_enabled"])
    if "screenshot_delay" in _cfg:
        try: SCREENSHOT_DELAY = float(_cfg["screenshot_delay"])
        except Exception: pass
    if "use_headless_checker" in _cfg:
        USE_HEADLESS_CHECKER = bool(_cfg["use_headless_checker"])

DEFAULT_THEME = {
    "name": "nexo", "image_path": "", "image_mode": "cover",
    "image_opacity": 0.35, "image_blur": 4, "image_dim": 0.72,
}


# ═══════════════════════════════════════════════════════════════════════════
#  Query helpers
# ═══════════════════════════════════════════════════════════════════════════
def normalize_query(q): return "+".join(q.strip().split())
def pretty_query(q):    return q.replace("+", " ")


def parse_command_chain(text):
    tokens = text.strip().split()
    commands = []
    current = None
    for tok in tokens:
        if tok.startswith("!"):
            if current is not None: commands.append(current)
            current = [tok[1:], []]
        else:
            if current is None: return None
            current[1].append(tok)
    if current is not None: commands.append(current)
    return [(name, " ".join(args)) for name, args in commands]


# ═══════════════════════════════════════════════════════════════════════════
#  Ad filter
# ═══════════════════════════════════════════════════════════════════════════
AD_TRIGGER_PHRASES = [
    r"free\s+with\s+ads", r"free\s+with\s+advertisements",
    r"ad[- ]?supported", r"supported\s+by\s+ads",
    r"watch\s+free\s+with\s+ads", r"free\s+\(with\s+ads\)",
    r"contains\s+ads", r"ad[-\s]?supported\s+free",
    r"free[\s\u00a0]+with[\s\u00a0]+ads",
]
AD_PATTERN = re.compile("|".join(AD_TRIGGER_PHRASES), re.IGNORECASE)


def detect_ad_phrases(text):
    if not text: return []
    matches = AD_PATTERN.findall(text)
    results = []
    for m in matches:
        if isinstance(m, tuple):
            results.extend([x for x in m if x])
        else:
            results.append(m)
    return list(set(results))


# ═══════════════════════════════════════════════════════════════════════════
#  HEADLESS CHECKER BROWSER
# ═══════════════════════════════════════════════════════════════════════════
class CheckerBrowser:
    """Headless Chrome used for pre-flight safety checks.

    Every user-driven navigation is routed through check() first. The
    visible window is only navigated when check() returns (True, ...).
    """

    # Reject these path suffixes outright — they are how URL-suffix
    # injections like "@handle/live" try to slip past the checks.
    BAD_SUFFIXES = ("/live", "/streams", "/shorts", "/videos",
                    "/featured", "/playlists", "/community", "/about",
                    "/watch", "/v/", "/embed/", "/clip/")

    ALLOWED_DOMAINS = ("youtube.com", "youtu.be")

    def __init__(self, out_queue):
        self.q = out_queue
        self.driver = None
        self.lock = threading.Lock()

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def start(self):
        if self.driver:
            return True
        try:
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--mute-audio")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            opts.add_argument("--window-size=1280,900")
            self.driver = webdriver.Chrome(options=opts)
            try:
                self.driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": "Object.defineProperty(navigator,'webdriver',"
                               "{get:()=>undefined})"})
            except Exception:
                pass
            self._emit("[checker] Headless checker ready.", "ok")
            return True
        except Exception as e:
            self.driver = None
            self._emit(f"[checker] Could not start headless checker: {e}",
                       "warn")
            return False

    def stop(self):
        d = self.driver
        self.driver = None
        if d:
            try: d.quit()
            except Exception: pass

    def check(self, url: str, timeout=12):
        """Return (safe: bool, reason: str)."""
        if not USE_HEADLESS_CHECKER:
            return True, "checker disabled"

        # ── URL-shape guard (runs before anything is loaded) ───────────
        low = (url or "").lower().strip()
        if not low.startswith(("http://", "https://")):
            return False, f"unsupported URL scheme: {url[:60]!r}"
        if not any(dom in low for dom in self.ALLOWED_DOMAINS):
            return False, f"off-domain URL: {url[:60]!r}"
        # Path check: strip query & fragment, then inspect the last segment
        path = low.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        for s in self.BAD_SUFFIXES:
            if path.endswith(s):
                return False, f"URL suffix {s!r} not allowed"

        with self.lock:
            if not self.start():
                return True, "checker unavailable"

            d = self.driver
            try:
                d.get(url)
                try:
                    WebDriverWait(d, timeout).until(
                        lambda x: x.execute_script(
                            "return document.readyState") == "complete")
                except Exception:
                    pass
                time.sleep(1.5)

                # Post-load URL check: make sure we didn't get redirected
                # to a watch/live page from a benign-looking URL.
                try:
                    cur = (d.current_url or "").lower()
                except Exception:
                    cur = ""
                if "/watch" in cur or cur.rstrip("/").endswith("/live"):
                    return False, "redirected to a watch/live page"

                # ── Live stream? ──────────────────────────────────────
                if BLOCK_LIVE_STREAMS:
                    try:
                        live = d.execute_script(r"""
                            const wf = document.querySelector('ytd-watch-flexy');
                            if (wf && wf.hasAttribute('is-live')) return true;
                            const p = document.querySelector('#movie_player');
                            if (p && p.classList.contains('ytp-live')) return true;
                            if (document.querySelector('ytd-live-chat-frame')) return true;
                            for (const b of document.querySelectorAll(
                                    'ytd-badge-supported-renderer, .ytBadgeShapeText')) {
                                const t = (b.textContent || '').trim().toLowerCase();
                                if (t === 'live' || t.startsWith('live ')) return true;
                            }
                            return false;
                        """)
                        if live:
                            return False, "live stream"
                    except Exception:
                        pass

                # ── < 24 hours old? ───────────────────────────────────
                if BLOCK_UNDER_24H:
                    try:
                        age_text = d.execute_script(r"""
                            const sels = [
                                '#info-strings yt-formatted-string',
                                '#info-contents #info-strings',
                                'ytd-watch-metadata #info-strings yt-formatted-string',
                                '#above-the-fold #info-strings',
                                'ytd-video-primary-info-renderer #info-strings',
                            ];
                            for (const sel of sels) {
                                for (const el of document.querySelectorAll(sel)) {
                                    const t = (el.textContent || '').trim();
                                    if (t) return t;
                                }
                            }
                            for (const el of document.querySelectorAll(
                                    '#primary-inner yt-formatted-string, ytd-watch-metadata yt-formatted-string')) {
                                const t = (el.textContent || '').trim();
                                if (/\bago\b/i.test(t) || /premiered/i.test(t)) return t;
                            }
                            return null;
                        """)
                        secs = _parse_age_text(age_text)
                        if secs is not None and secs < 86400:
                            return False, f"only {secs/3600:.1f}h old (< 24h)"
                    except Exception:
                        pass

                # ── "Free with ads" badge? ────────────────────────────
                if BAIL_TO_BLANK_ON_AD:
                    try:
                        badge_texts = d.execute_script(r"""
                            const scopes = [
                                'ytd-watch-metadata', '#above-the-fold',
                                '#primary-inner', '#info-contents'
                            ];
                            const out = [];
                            for (const s of scopes) {
                                const scope = document.querySelector(s);
                                if (!scope) continue;
                                for (const el of scope.querySelectorAll(
                                        '.ytBadgeShapeText, ytd-badge-supported-renderer')) {
                                    const t = (el.textContent || '').trim();
                                    if (t) out.push(t);
                                }
                            }
                            return Array.from(new Set(out));
                        """) or []
                        hits = detect_ad_phrases("\n".join(badge_texts))
                        explicit = any(
                            ("free" in t.lower() and "ads" in t.lower())
                            for t in badge_texts)
                        if hits or explicit:
                            return False, "Free with ads badge"
                    except Exception:
                        pass

                return True, "ok"
            except Exception as e:
                return True, f"checker error: {e}"


# ═══════════════════════════════════════════════════════════════════════════
#  MOUSE BOT
# ═══════════════════════════════════════════════════════════════════════════
class MouseBot(threading.Thread):
    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True

    def run(self):
        self.q.put({"kind": "log", "level": "info",
                    "message": "[mouse] Mouse bot started."})
        while self.running:
            time.sleep(MOUSE_BOT_INTERVAL)
            if not self.running: break
            if not self.bot.driver: continue
            try:
                dx = random.randint(-MOUSE_BOT_JITTER, MOUSE_BOT_JITTER)
                dy = random.randint(-MOUSE_BOT_JITTER, MOUSE_BOT_JITTER)
                ActionChains(self.bot.driver).move_by_offset(dx, dy).perform()
                ActionChains(self.bot.driver).move_by_offset(-dx, -dy).perform()
                self.bot.driver.execute_script(
                    "const v=document.querySelector('video');"
                    "if(v&&v.paused){v.play().catch(()=>{});}")
            except Exception:
                pass
        self.q.put({"kind": "log", "level": "info",
                    "message": "[mouse] Mouse bot stopped."})

    def stop(self): self.running = False


# ═══════════════════════════════════════════════════════════════════════════
#  HOME-ON-VIDEO-END WATCHER
# ═══════════════════════════════════════════════════════════════════════════
class HomeOnVideoEnd(threading.Thread):
    POLL_INTERVAL = 2.0
    POST_END_SETTLE = 1.5

    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._last_ended_url = None

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def run(self):
        self._emit("[watcher] Video-end watcher started.")
        while self.running:
            time.sleep(self.POLL_INTERVAL)
            if not self.running: break
            if not HOME_ON_VIDEO_END: continue
            d = self.bot.driver
            if not d: continue
            try:
                url = (d.current_url or "")
            except Exception:
                continue
            if "/watch" not in url.lower():
                self._last_ended_url = None
                continue
            if url == self._last_ended_url:
                continue
            try:
                ended = d.execute_script(r"""
                    const v = document.querySelector('video');
                    if (!v) return false;
                    if (v.ended) return true;
                    if (v.duration && v.currentTime >= v.duration - 0.25
                        && v.paused) return true;
                    return false;
                """)
            except Exception:
                continue
            if ended:
                self._last_ended_url = url
                self._emit("[watcher] Video ended — going to YouTube home.",
                           "info")
                time.sleep(self.POST_END_SETTLE)
                try:
                    d.get("https://www.youtube.com/")
                except Exception as e:
                    self._emit(f"[watcher] Navigate home failed: {e}", "warn")

    def stop(self):
        self.running = False


# ═══════════════════════════════════════════════════════════════════════════
#  YOUTUBE WATCHDOG
# ═══════════════════════════════════════════════════════════════════════════
class YouTubeWatchdog(threading.Thread):
    POLL_INTERVAL = 3.0
    ALLOWED_SUBSTRINGS = (
        "youtube.com", "youtu.be",
        "googleusercontent.com", "googlevideo.com",
        "ytimg.com", "ggpht.com",
        "about:", "chrome://", "data:",
    )
    TRANSIENT_SUBSTRINGS = (
        "consent.youtube.com", "accounts.google.com", "google.com/sorry",
    )

    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._last_log_url = None

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def _is_allowed(self, url: str) -> bool:
        low = url.lower()
        if any(t in low for t in self.TRANSIENT_SUBSTRINGS):
            return True
        return any(s in low for s in self.ALLOWED_SUBSTRINGS)

    def run(self):
        self._emit("[watchdog] Off-site watchdog started.")
        while self.running:
            time.sleep(self.POLL_INTERVAL)
            if not self.running: break
            if not OFF_SITE_WATCHDOG: continue
            d = self.bot.driver
            if not d: continue
            try:
                url = d.current_url or ""
            except Exception:
                continue
            if not url: continue
            if self._is_allowed(url):
                self._last_log_url = None
                continue
            if url != self._last_log_url:
                self._emit(f"[watchdog] Off-site detected: {url[:120]}… "
                           f"— returning to YouTube home.", "warn")
                self._last_log_url = url
            try:
                d.get("https://www.youtube.com/")
                time.sleep(1.0)
            except Exception as e:
                self._emit(f"[watchdog] Recovery failed: {e}", "warn")

    def stop(self):
        self.running = False


# ═══════════════════════════════════════════════════════════════════════════
#  YOUTUBE BOT
# ═══════════════════════════════════════════════════════════════════════════
class YouTubeBot(threading.Thread):
    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self._queue = out_queue
        self.q = out_queue
        self.driver = None
        self.running = True
        self.tasks = queue.Queue()
        self.current_lang = None
        self.mouse_bot = None
        self.end_watcher = None
        self.watchdog = None
        self.checker = CheckerBrowser(out_queue)
        self.temp_profile = None
        self._shot_timers = []

    # ---- queue API ----
    def request_search(self, query, lucky=False, lang=None, source=""):
        self.tasks.put(("search", normalize_query(query), lucky, source, lang))
    def request_channel(self, handle, lang=None, source=""):
        self.tasks.put(("channel", handle.strip(), False, source, lang))
    def request_home(self, lang=None, source=""):
        self.tasks.put(("home", "", False, source, lang))
    def request_language(self, lang, source=""):
        self.tasks.put(("language", lang.strip(), False, source, None))
    def request_scroll(self, pixels, source=""):
        self.tasks.put(("scroll", str(pixels), False, source, None))
    def request_watchhome(self, index, source=""):
        self.tasks.put(("watchhome", str(index), False, source, None))
    def request_playback(self, kind, arg="", source=""):
        self.tasks.put((kind, arg, False, source, None))
    def request_clear_cookies(self, source=""):
        self.tasks.put(("clear_cookies", "", False, source, None))

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    # ── The single choke point for user-driven navigation ──────────────
    def _safe_navigate(self, url, label="page"):
        """Pre-flight `url` in the headless checker; only navigate the
        visible window if it is approved."""
        if not url:
            return False
        safe, reason = self.checker.check(url)
        if not safe:
            self._emit(f"[bot] BLOCKED {label}: {reason}. "
                       f"Visible window NOT navigated.", "warn")
            return False
        try:
            self.driver.get(url)
            return True
        except Exception as e:
            self._emit(f"[bot] Navigate {label} failed: {e}", "err")
            return False

    # ---- fallback in-page checks (used only if checker is off) ----
    def _is_live_stream(self) -> bool:
        d = self.driver
        if not d:
            return False
        try:
            return bool(d.execute_script(r"""
                const wf = document.querySelector('ytd-watch-flexy');
                if (wf && wf.hasAttribute('is-live')) return true;
                const player = document.querySelector('#movie_player');
                if (player && player.classList.contains('ytp-live')) return true;
                if (document.querySelector('ytd-live-chat-frame')) return true;
                for (const b of document.querySelectorAll(
                        'ytd-badge-supported-renderer, .ytBadgeShapeText')) {
                    const t = (b.textContent || '').trim().toLowerCase();
                    if (t === 'live' || t.startsWith('live ')) return true;
                }
                return false;
            """))
        except Exception:
            return False

    def _get_video_age_seconds(self):
        d = self.driver
        if not d:
            return None
        try:
            age_text = d.execute_script(r"""
                const sels = [
                    '#info-strings yt-formatted-string',
                    '#info-contents #info-strings',
                    'ytd-watch-metadata #info-strings yt-formatted-string',
                    '#above-the-fold #info-strings',
                    'ytd-video-primary-info-renderer #info-strings',
                ];
                for (const sel of sels) {
                    for (const el of document.querySelectorAll(sel)) {
                        const t = (el.textContent || '').trim();
                        if (t) return t;
                    }
                }
                for (const el of document.querySelectorAll(
                        '#primary-inner yt-formatted-string, ytd-watch-metadata yt-formatted-string')) {
                    const t = (el.textContent || '').trim();
                    if (/\bago\b/i.test(t) || /premiered/i.test(t)) return t;
                }
                return null;
            """)
            return _parse_age_text(age_text)
        except Exception:
            return None

    def _video_is_too_new(self):
        if not BLOCK_UNDER_24H:
            return False
        age = self._get_video_age_seconds()
        if age is None:
            self._emit("[bot] Could not determine video age — allowing.",
                       "info")
            return False
        if age < 86400:
            self._emit(f"[bot] Video is only {age/3600.0:.1f}h old "
                       f"(< 24h) — blocking.", "warn")
            return True
        return False

    # ---- screenshot scheduling ----
    def _schedule_screenshot(self, reason: str):
        if not SCREENSHOT_ENABLED:
            return

        def _take():
            d = self.driver
            if not d:
                return
            try:
                title = ""
                try: title = d.title or ""
                except Exception: pass
                stem = _sanitize_filename(title, fallback="screenshot")
                stamp = time.strftime("%Y%m%d_%H%M%S")
                path = SCREENSHOT_DIR / f"{stem}_{stamp}.png"
                ok = d.save_screenshot(str(path))
                if ok:
                    self._emit(f"[shot] Saved {path.name} ({reason}).", "ok")
                else:
                    self._emit(f"[shot] save_screenshot returned False "
                               f"for {path.name}", "warn")
            except Exception as e:
                self._emit(f"[shot] Screenshot failed: {e}", "warn")

        t = threading.Timer(SCREENSHOT_DELAY, _take)
        t.daemon = True
        t.start()
        self._shot_timers.append(t)
        self._shot_timers = [x for x in self._shot_timers if x.is_alive()]

    # ---- main loop ----
    def run(self):
        opts = Options()
        opts.add_argument("--start-maximized")
        if DISABLE_COOKIES:
            self.temp_profile = tempfile.mkdtemp(prefix="opencuy_")
            opts.add_argument(f"--user-data-dir={self.temp_profile}")
            opts.add_argument("--incognito")
            opts.add_experimental_option("prefs", {
                "profile.default_content_setting_values.cookies": 2,
                "profile.block_third_party_cookies": True})
        else:
            opts.add_experimental_option("prefs", {
                "profile.default_content_setting_values.cookies": 1})

        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-backgrounding-occluded-windows")
        opts.add_argument("--disable-renderer-backgrounding")
        opts.add_argument("--disable-features=CalculateNativeWinOcclusion")
        opts.add_argument("--autoplay-policy=no-user-gesture-required")

        try:
            self.driver = webdriver.Chrome(options=opts)
        except Exception as e:
            self._emit(f"[bot] Failed to launch Chrome: {e}", "err")
            return

        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',"
                           "{get:()=>undefined})"})
        except Exception as e:
            self._emit(f"[bot] stealth apply failed: {e}", "warn")

        self._emit("[bot] Browser ready."
                   + (" (COOKIES DISABLED, temp profile)" if DISABLE_COOKIES else ""),
                   "ok")

        if MOUSE_BOT_ENABLED:
            self.mouse_bot = MouseBot(self, self.q)
            self.mouse_bot.start()

        self.end_watcher = HomeOnVideoEnd(self, self.q)
        self.end_watcher.start()

        self.watchdog = YouTubeWatchdog(self, self.q)
        self.watchdog.start()

        threading.Thread(target=self.checker.start, daemon=True).start()

        while self.running:
            try:
                kind, arg, lucky, source, lang = self.tasks.get(timeout=0.5)
            except queue.Empty:
                continue
            prefix = f"[{source}] " if source else ""
            try:
                self._dispatch(kind, arg, lucky, prefix, lang)
            except Exception as e:
                self._emit(f"{prefix}[bot] Error in '{kind}': {e}", "err")

        for t in self._shot_timers:
            try: t.cancel()
            except Exception: pass
        try: self.checker.stop()
        except Exception: pass
        if self.watchdog:
            self.watchdog.stop()
            self.watchdog.join(timeout=2)
        if self.end_watcher:
            self.end_watcher.stop()
            self.end_watcher.join(timeout=2)
        if self.mouse_bot:
            self.mouse_bot.stop()
            self.mouse_bot.join(timeout=2)
        try: self.driver.quit()
        except Exception: pass
        if self.temp_profile and os.path.isdir(self.temp_profile):
            try: shutil.rmtree(self.temp_profile, ignore_errors=True)
            except Exception: pass

    def _dispatch(self, kind, arg, lucky, prefix, lang):
        if kind == "search":
            mode = "lucky" if lucky else "search"
            self._emit(f"{prefix}→ {mode}: {pretty_query(arg)!r}"
                       + (f" (lang={lang})" if lang else ""))
            self._do_search(arg, lucky, lang)
        elif kind == "channel":
            self._emit(f"{prefix}→ channel: {arg}" + (f" (lang={lang})" if lang else ""))
            self._do_channel(arg, lang)
        elif kind == "home":
            self._emit(f"{prefix}→ home" + (f" (lang={lang})" if lang else ""))
            self._do_home(lang)
        elif kind == "language":
            self._emit(f"{prefix}→ set language: {arg}")
            self._do_set_language(arg)
        elif kind == "scroll": self._do_scroll(arg)
        elif kind == "watchhome": self._do_watchhome(arg)
        elif kind == "clear_cookies": self._do_clear_cookies()
        elif kind in ("speed","pitch","volume","playpause","refresh",
                      "captions","captionstranslate","loop","autoplay",
                      "fullscreen","seek"):
            self._do_playback(kind, arg)

    def _do_clear_cookies(self):
        if not self.driver: return
        try:
            self.driver.delete_all_cookies()
            try:
                self.driver.execute_script(
                    "window.localStorage.clear();window.sessionStorage.clear();")
            except Exception: pass
            try: self.driver.refresh()
            except Exception: pass
            self._emit("[cookies] Cookies + storage cleared.", "ok")
        except Exception as e:
            self._emit(f"[cookies] Error: {e}", "err")

    def _page_shows_error(self):
        try:
            body = self.driver.find_element(By.TAG_NAME, "body").text.lower()
            return any(p in body for p in [
                "something went wrong", "an error has occurred",
                "playback error", "tap to retry",
                "please try again later", "video unavailable"])
        except Exception:
            return False

    def _reload_until_playable(self, max_attempts=VIDEO_RETRY_ATTEMPTS):
        for attempt in range(1, max_attempts + 1):
            if not self._page_shows_error():
                if attempt > 1:
                    self._emit(f"[bot] Recovered after {attempt-1} reload(s).", "ok")
                return True
            self._emit(f"[bot] ⚠ error page ({attempt}/{max_attempts}). Reloading…",
                       "warn")
            try:
                self.driver.refresh(); time.sleep(VIDEO_RETRY_DELAY)
                WebDriverWait(self.driver, 8).until(
                    lambda d: d.execute_script("return document.readyState") == "complete")
            except Exception as e:
                self._emit(f"[bot] Reload error: {e}", "warn")
        self._emit("[bot] Page still shows an error.", "err")
        return False

    def _qmark_lang(self, lang):
        return f"?hl={lang}&persist_hl=1" if lang else ""

    def _do_search(self, query, lucky=False, lang=None):
        url = "https://www.youtube.com/results?search_query=" + query
        if lang: url += f"&hl={lang}&persist_hl=1"

        if not self._safe_navigate(url, label=f"search {pretty_query(query)!r}"):
            return
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "video-title")))
            results = self.driver.find_elements(By.ID, "video-title")
            if not results:
                self._emit("[bot] No results (maybe Restricted Mode).", "warn")
                return
            if lucky:
                picked = False
                for cand in results[:5]:
                    href = ""
                    try: href = cand.get_attribute("href") or ""
                    except Exception: pass
                    if href:
                        if not href.startswith("http"):
                            href = "https://www.youtube.com" + href
                        safe, reason = self.checker.check(href)
                        if not safe:
                            self._emit(f"[bot] Lucky skip: {reason} "
                                       f"({cand.text[:50]}…)", "info")
                            continue
                    self._emit(f"[bot] Lucky pick: {cand.text}", "ok")
                    cand.click(); time.sleep(3)
                    self._reload_until_playable()
                    if BLOCK_LIVE_STREAMS and self._is_live_stream():
                        self._emit("[bot] Lucky pick was live (fallback) — "
                                   "next.", "warn")
                        try: self.driver.back()
                        except Exception: pass
                        continue
                    if self._video_is_too_new():
                        try: self.driver.back()
                        except Exception: pass
                        continue
                    self._scan_current_page_for_ads()
                    picked = True
                    break
                if not picked:
                    self._emit("[bot] Lucky: no safe result found.", "warn")
            else:
                self._emit(f"[bot] Found {len(results)} results for "
                           f"'{pretty_query(query)}'.", "ok")
                for r in results[:5]:
                    self._emit(f"   • {r.text}")
        except Exception as e:
            self._emit(f"[bot] Error: {e}", "err")

    def _do_channel(self, handle, lang=None):
        # Reject suffixes outright
        if any(ch in handle for ch in ("/", "?", "#", "\\")):
            self._emit(f"[bot] !channel: invalid handle {handle!r} "
                       f"(suffix injection blocked).", "warn")
            return
        h = handle.lstrip("@")
        if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", h):
            url = f"https://www.youtube.com/channel/{h}"
        else:
            if not re.fullmatch(r"[A-Za-z0-9._-]{3,60}", h):
                self._emit(f"[bot] !channel: bad handle {handle!r}", "warn")
                return
            url = f"https://www.youtube.com/@{h}"
        url += self._qmark_lang(lang)

        if not self._safe_navigate(url, label=f"channel {handle}"):
            return
        time.sleep(3)

        # Post-nav sanity: if it redirected to /watch or /live, back out.
        try: cur = (self.driver.current_url or "").lower()
        except Exception: cur = ""
        if "/watch" in cur or cur.rstrip("/").endswith("/live"):
            self._emit("[bot] !channel: redirect to live/watch — backing out.",
                       "warn")
            try: self.driver.get("https://www.youtube.com/")
            except Exception: pass
            return
        if BLOCK_LIVE_STREAMS and self._is_live_stream():
            self._emit("[bot] !channel: live stream (fallback) — backing out.",
                       "warn")
            try: self.driver.get("https://www.youtube.com/")
            except Exception: pass
            return

        self._emit(f"[bot] Loaded channel: {handle}", "ok")

    def _do_home(self, lang=None):
        url = "https://www.youtube.com/" + self._qmark_lang(lang)
        if not self._safe_navigate(url, label="home"):
            return
        time.sleep(2)
        self._emit("[bot] Loaded YouTube home.", "ok")

    def _do_set_language(self, lang):
        if not lang:
            self._emit("[bot] !changelanguage needs a code.", "warn"); return
        try: cur = self.driver.current_url or ""
        except Exception: cur = ""
        if not cur:
            self._emit("[bot] No current page to relang.", "warn"); return
        sep = "&" if "?" in cur else "?"
        self.current_lang = lang
        self._emit(f"[bot] Language set to '{lang}'.", "ok")
        try: self.driver.get(f"{cur}{sep}hl={lang}&persist_hl=1")
        except Exception: pass

    def _do_scroll(self, arg):
        raw = (arg or "").strip()
        if not raw:
            self._emit("[bot] !scroll expects: [pixels] [duration]  "
                       "e.g. 500, +500 3, -300 12.5", "warn")
            return
        parts = raw.split()
        if len(parts) > 2:
            self._emit(f"[bot] !scroll: too many arguments: {raw!r}", "warn")
            return
        px_tok = parts[0]
        m = re.fullmatch(r"([+-]?)(\d+)", px_tok)
        if not m:
            self._emit(f"[bot] !scroll: bad pixel value {px_tok!r}", "warn")
            return
        sign, digits = m.group(1), m.group(2)
        px = int(digits)
        if sign == "-": px = -px
        duration = 0.0
        if len(parts) == 2:
            try: duration = float(parts[1])
            except ValueError:
                self._emit(f"[bot] !scroll: bad duration {parts[1]!r}", "warn")
                return
            if duration <= 0:
                self._emit("[bot] !scroll: duration must be > 0", "warn"); return
            if duration > 120:
                self._emit("[bot] !scroll: duration capped at 120s", "warn")
                duration = 120.0
        try:
            if duration <= 0:
                self.driver.execute_script(
                    "window.scrollBy(0, arguments[0]);", px)
                self._emit(f"[bot] Scrolled {px:+d}px (instant).")
            else:
                self.driver.execute_async_script("""
                    const px       = arguments[0];
                    const duration = arguments[1] * 1000;
                    const done     = arguments[arguments.length - 1];
                    const startY   = window.scrollY;
                    const startT   = performance.now();
                    function step(now) {
                        const t = Math.min(1, (now - startT) / duration);
                        window.scrollTo(0, startY + px * t);
                        if (t < 1) {
                            requestAnimationFrame(step);
                        } else {
                            done('ok');
                        }
                    }
                    requestAnimationFrame(step);
                """, px, duration)
                self._emit(f"[bot] Scrolled {px:+d}px over {duration:g}s "
                           f"(linear, no ease).")
        except Exception as e:
            self._emit(f"[bot] Scroll error: {e}", "err")

    def _do_watchhome(self, arg):
        try:
            idx = int(arg)
        except ValueError:
            self._emit("[bot] !watchhome expects an integer.", "warn"); return
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "ytd-rich-item-renderer")))

            cards = self.driver.execute_script(r"""
                const out = [];
                for (const card of document.querySelectorAll(
                        'ytd-rich-item-renderer')) {
                    if (card.closest('ytd-ad-slot-renderer')) continue;
                    if (card.querySelector(
                            'ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ' +
                            'ytd-promoted-sparkles-web-renderer, ' +
                            'ytd-display-ad-renderer, [is-ad], .ytd-ad-slot-renderer'))
                        continue;
                    const txt = (card.innerText || '').toLowerCase();
                    if (txt.includes('sponsored') || txt.includes('promoted'))
                        continue;
                    const link = card.querySelector(
                        'a#video-title-link, a#thumbnail, a[href*="/watch?v="]');
                    if (!link) continue;
                    out.push(card);
                }
                return out;
            """) or []

            if not cards:
                self._emit("[bot] !watchhome: no non-ad video cards found. "
                           "Going to YouTube home.", "warn")
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                return

            if idx < 1:
                self._emit(f"[bot] !watchhome {idx}: index must be >= 1. "
                           f"Using 1.", "warn")
                idx = 1
            if idx > len(cards):
                self._emit(f"[bot] !watchhome {idx}: only {len(cards)} "
                           f"non-ad card(s). Clamping to last.", "warn")
                idx = len(cards)

            card = cards[idx - 1]

            try:
                href = card.find_element(
                    By.CSS_SELECTOR, "a[href*='/watch?v=']").get_attribute("href")
            except Exception:
                href = ""
            if href:
                safe, reason = self.checker.check(href)
                if not safe:
                    self._emit(f"[bot] !watchhome {idx}: card is {reason} — "
                               f"skipping.", "warn")
                    return

            link = None
            for sel in ["a#video-title-link", "a#thumbnail",
                        "a.yt-simple-endpoint", "h3 a",
                        "a[href*='/watch?v=']", "ytd-rich-grid-media a"]:
                try:
                    link = card.find_element(By.CSS_SELECTOR, sel)
                    if link: break
                except Exception:
                    continue
            if not link:
                links = card.find_elements(By.TAG_NAME, "a")
                if links: link = links[0]
            if not link:
                self._emit(f"[bot] !watchhome {idx}: no link in card.", "warn")
                return

            self.driver.execute_script("arguments[0].click();", link)
            time.sleep(3)

            try:
                cur = (self.driver.current_url or "").lower()
            except Exception:
                cur = ""
            allowed = ("youtube.com", "youtu.be", "chrome://")
            if not any(host in cur for host in allowed):
                self._emit(f"[bot] !watchhome: landed off-site ({cur[:80]}…) "
                           f"— returning to YouTube home.", "warn")
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                return

            self._reload_until_playable()

            if BLOCK_LIVE_STREAMS and self._is_live_stream():
                self._emit(f"[bot] !watchhome {idx}: live stream (fallback). "
                           f"Backing out.", "warn")
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                return
            if self._video_is_too_new():
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                return

            time.sleep(1.5)
            self._emit(f"[bot] Clicked video #{idx} (ads skipped).", "ok")
            self._scan_current_page_for_ads()
        except Exception as e:
            self._emit(f"[bot] !watchhome error: {e}", "err")

    def _do_playback(self, kind, arg):
        try:
            if kind == "speed":
                v = float(arg)
                if not (0.25 <= v <= 2.0):
                    self._emit("[bot] !speed range 0.25–2.0", "warn"); return
                self.driver.execute_script(
                    "document.querySelector('video').playbackRate=arguments[0];", v)
                self._emit(f"[bot] Speed set to {v}x.")
            elif kind == "pitch":
                try: semis = float(arg)
                except ValueError:
                    self._emit("[bot] !pitch expects semitones (-24 to +24)", "warn")
                    return
                if not (-24 <= semis <= 24):
                    self._emit("[bot] !pitch range: -24 to +24 semitones", "warn")
                    return
                factor = 2 ** (semis / 12.0)
                self.driver.execute_script("""
                    const v = document.querySelector('video');
                    if (!v) return 'no-video';
                    v.preservesPitch = false;
                    v.mozPreservesPitch = false;
                    v.webkitPreservesPitch = false;
                    v.playbackRate = arguments[0];
                    return 'ok';
                """, factor)
                self._emit(f"[bot] Pitch {semis:+g} semitones "
                           f"(playbackRate={factor:.3f}x — speed also changes).")
            elif kind == "volume":
                v = int(arg)
                if not (0 <= v <= 100):
                    self._emit("[bot] !volume range 0–100", "warn"); return
                self.driver.execute_script(
                    "document.querySelector('video').volume=arguments[0];", v/100.0)
                self._emit(f"[bot] Volume set to {v}%.")
            elif kind == "playpause":
                self.driver.execute_script(
                    "const v=document.querySelector('video');"
                    "if(v.paused)v.play();else v.pause();")
                self._emit("[bot] Toggled play/pause.")
            elif kind == "refresh":
                self.driver.refresh(); time.sleep(2)
                self._emit("[bot] Page refreshed.")
            elif kind == "captions":
                on = arg.lower() in ("on","true","1")
                self.driver.execute_script("""
                    const p=document.querySelector('#movie_player');
                    if(!p||!p.getOption) return 'no-api';
                    try{ if(arguments[0]){p.loadModule&&p.loadModule('captions');}
                         else {p.unloadModule&&p.unloadModule('captions');}
                         return 'ok'; }catch(e){return 'err:'+e;}""", on)
                self._emit(f"[bot] Captions {'on' if on else 'off'}.")
            elif kind == "captionstranslate":
                lang = (arg or "").strip().lower()
                if not lang:
                    self._emit("[bot] !captionstranslate needs a language code", "warn")
                    return
                result = self.driver.execute_script("""
                    const p = document.querySelector('#movie_player');
                    if (!p || !p.setOption) return 'no-api';
                    try {
                        p.loadModule && p.loadModule('captions');
                        p.setOption('captions', 'track', {});
                        p.setOption('captions', 'translationLanguage',
                                    {languageCode: arguments[0]});
                        return 'ok';
                    } catch(e) { return 'err:' + e; }
                """, lang)
                if str(result).startswith("ok"):
                    self._emit(f"[bot] Caption translation → {lang}.")
                else:
                    self._emit(f"[bot] Caption translation failed: {result}", "warn")
            elif kind == "loop":
                mode = arg.lower()
                if mode not in ("video","playlist","off"):
                    self._emit("[bot] !loop expects video/playlist/off", "warn"); return
                if mode == "video":
                    self.driver.execute_script(
                        "const v=document.querySelector('video'); if(v) v.loop=true;")
                    self._emit("[bot] Loop mode: video.")
                elif mode == "off":
                    self.driver.execute_script(
                        "const v=document.querySelector('video'); if(v) v.loop=false;")
                    self._emit("[bot] Loop mode: off.")
                else:
                    result = self.driver.execute_script("""
                        const p = document.querySelector('#movie_player');
                        if (!p) return 'no-player';
                        const btn = p.querySelector('.ytp-playlist-loop-button') ||
                                    p.querySelector('[aria-label*="loop" i]');
                        if (!btn) return 'no-button';
                        btn.click();
                        return 'ok';
                    """)
                    self._emit(f"[bot] Loop mode: playlist ({result}).")
            elif kind == "autoplay":
                on = arg.lower() in ("on","true","1")
                result = self.driver.execute_script("""
                    const p = document.querySelector('#movie_player');
                    if (!p) return 'no-player';
                    const btn = p.querySelector('.ytp-autonav-toggle-button');
                    if (!btn) return 'no-button';
                    const isOn = btn.getAttribute('aria-checked') === 'true';
                    if (isOn !== arguments[0]) btn.click();
                    return 'ok';
                """, on)
                self._emit(f"[bot] Autoplay {'on' if on else 'off'} ({result}).")
            elif kind == "fullscreen":
                self.driver.execute_script("""
                    const p=document.querySelector('#movie_player');
                    const b=p&&p.querySelector('.ytp-fullscreen-button');
                    if(b)b.click();""")
                self._emit("[bot] Toggled fullscreen.")
            elif kind == "seek":
                parts = arg.split()
                if len(parts) != 2:
                    self._emit("[bot] !seek expects '+ N', '- N', or 'set N'", "warn")
                    return
                op, secs = parts[0], float(parts[1])
                if op == "+":
                    self.driver.execute_script(
                        "document.querySelector('video').currentTime+=arguments[0];", secs)
                elif op == "-":
                    self.driver.execute_script(
                        "document.querySelector('video').currentTime-=arguments[0];", secs)
                elif op == "set":
                    self.driver.execute_script(
                        "document.querySelector('video').currentTime=arguments[0];", secs)
                else:
                    self._emit("[bot] !seek expects +, -, or set", "warn"); return
                self._emit(f"[bot] Seeked {op} {secs}s.")
        except Exception as e:
            self._emit(f"[bot] Playback '{kind}' error: {e}", "err")

    def _scan_current_page_for_ads(self):
        if not self.driver: return
        try:
            url = (self.driver.current_url or "").lower()
        except Exception:
            url = ""
        if "/watch" not in url:
            return
        try:
            badge_js = r"""
                const scopes = [
                    'ytd-watch-metadata', '#above-the-fold',
                    '#primary-inner', '#info-contents'
                ];
                const out = [];
                for (const scopeSel of scopes) {
                    const scope = document.querySelector(scopeSel);
                    if (!scope) continue;
                    for (const el of scope.querySelectorAll(
                            '.ytBadgeShapeText, ytd-badge-supported-renderer')) {
                        const t = (el.textContent || '').trim();
                        if (t) out.push(t);
                    }
                }
                return Array.from(new Set(out));
            """
            try:
                badge_texts = self.driver.execute_script(badge_js) or []
            except Exception as e:
                self._emit(f"[filter] badge scan error: {e}", "warn")
                badge_texts = []
            hits = detect_ad_phrases("\n".join(badge_texts))
            explicit = any(
                ("free" in t.lower() and "ads" in t.lower())
                for t in badge_texts)
            if explicit and not hits:
                hits = ["Free with ads (badge)"]
            if hits:
                self._emit(f"[filter] ⚠ ad phrase(s) on this video: "
                           f"{', '.join(hits)}", "warn")
                if BAIL_TO_BLANK_ON_AD:
                    self._emit("[filter] → YouTube home", "warn")
                    try: self.driver.get("https://www.youtube.com/")
                    except Exception as e: self._emit(f"[filter] {e}", "err")
        except Exception as e:
            self._emit(f"[filter] Scan error: {e}", "err")

    def stop(self):
        self.running = False


# ═══════════════════════════════════════════════════════════════════════════
#  LIVE CHAT READER
# ═══════════════════════════════════════════════════════════════════════════
class LiveChatReader(threading.Thread):
    def __init__(self, video_id, out_queue, stop_event, on_command=None):
        super().__init__(daemon=True)
        self.video_id = video_id
        self.q = out_queue
        self.stop_event = stop_event
        self.on_command = on_command

    def run(self):
        if not PYCHAT_AVAILABLE:
            self.q.put({"kind": "chat_status", "level": "err",
                        "message": "[chat] pytchat not installed."})
            return
        self.q.put({"kind": "chat_status", "level": "info",
                    "message": f"[chat] Connecting to {self.video_id}…"})
        while not self.stop_event.is_set():
            try:
                chat = pytchat.create(video_id=self.video_id)
                if chat is None or not chat.is_alive():
                    self.q.put({"kind": "chat_status", "level": "warn",
                                "message": "[chat] Stream not live / unavailable. Retrying in 5s…"})
                    if self.stop_event.wait(5):
                        break
                    continue
                self.q.put({"kind": "chat_status", "level": "ok",
                            "message": "[chat] Connected. Listening…"})
                while not self.stop_event.is_set() and chat.is_alive():
                    try:
                        data = chat.get()
                        if data is None or not hasattr(data, "sync_items"):
                            continue
                        for c in data.sync_items():
                            if self.stop_event.is_set():
                                break
                            try:
                                author = getattr(c.author, "name", "unknown")
                                msg = c.message
                            except Exception:
                                continue
                            self.q.put({
                                "kind": "chat",
                                "author": author,
                                "message": msg,
                                "datetime": getattr(c, "datetime", ""),
                                "is_owner": bool(getattr(c.author, "isChatOwner", False)),
                                "is_mod":   bool(getattr(c.author, "isChatModerator", False)),
                            })
                    except AttributeError:
                        self.q.put({"kind": "chat_status", "level": "warn",
                                    "message": "[chat] Reconnecting…"})
                        break
                    except Exception as e:
                        self.q.put({"kind": "chat_status", "level": "warn",
                                    "message": f"[chat] Message error: {e}"})
                        continue
                if not self.stop_event.is_set():
                    self.q.put({"kind": "chat_status", "level": "warn",
                                "message": "[chat] Chat ended. Reconnecting in 5s…"})
                    if self.stop_event.wait(5):
                        break
            except Exception as e:
                self.q.put({"kind": "chat_status", "level": "err",
                            "message": f"[chat] Error: {e}"})
                if self.stop_event.wait(5):
                    break
        self.q.put({"kind": "chat_status", "level": "info",
                    "message": "[chat] Stopped."})


# ═══════════════════════════════════════════════════════════════════════════
#  COMMAND DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════
class CommandDispatcher:
    def __init__(self, bot, log_fn, source=""):
        self.bot = bot
        self.log = log_fn
        self.source = source

    def handle(self, body):
        chain = parse_command_chain("!" + body if not body.startswith("!") else body)
        if chain is None:
            if FALLBACK_PLAINTEXT_AS_SEARCH:
                self.bot.request_search(body, lucky=False, source=self.source)
            else:
                self.log(f"{self.source} plain text ignored: {body!r}", "info")
            return
        for name, arg in chain:
            self._one(name.lower(), arg)

    def _one(self, cmd, arg):
        bot, src = self.bot, self.source
        arg = (arg or "").strip()

        if cmd == "search":
            if not arg: self.log(f"{src} !search needs a query", "warn"); return
            q, lang = self._split_lang(arg)
            bot.request_search(q, lucky=False, lang=lang, source=src)
        elif cmd in ("lucky","searchwatch"):
            if not arg: self.log(f"{src} !{cmd} needs a query", "warn"); return
            q, lang = self._split_lang(arg)
            bot.request_search(q, lucky=True, lang=lang, source=src)
        elif cmd == "channel":
            parts = arg.split()
            if not parts:
                self.log(f"{src} !channel needs a handle", "warn"); return
            handle = parts[0]
            if any(ch in handle for ch in ("/", "?", "#", "\\", " ", "\t")):
                self.log(f"{src} !channel: invalid handle {handle!r} "
                         f"(no slashes/queries)", "warn")
                return
            if "://" in handle or handle.lower().startswith("http"):
                self.log(f"{src} !channel: URLs not allowed", "warn"); return
            lang = parts[1] if len(parts) > 1 else None
            bot.request_channel(handle, lang=lang, source=src)
        elif cmd == "home":
            lang = arg.split()[0] if arg else None
            bot.request_home(lang=lang, source=src)
        elif cmd == "changelanguage":
            if not arg: self.log(f"{src} !changelanguage needs a code", "warn"); return
            bot.request_language(arg.split()[0], source=src)
        elif cmd == "english":
            bot.request_language("en", source=src)
        elif cmd == "scroll":
            if not arg:
                self.log(f"{src} !scroll needs pixels", "warn"); return
            parts = arg.split()
            if len(parts) > 2:
                self.log(f"{src} !scroll: too many args: {arg!r}", "warn"); return
            if not re.fullmatch(r"[+-]?\d+", parts[0]):
                self.log(f"{src} !scroll: bad pixel value {parts[0]!r}", "warn"); return
            if len(parts) == 2:
                try:
                    d = float(parts[1])
                except ValueError:
                    self.log(f"{src} !scroll: bad duration {parts[1]!r}", "warn"); return
                if d <= 0:
                    self.log(f"{src} !scroll: duration must be > 0", "warn"); return
            bot.request_scroll(arg, source=src)
        elif cmd == "watchhome":
            if not arg: self.log(f"{src} !watchhome needs a number", "warn"); return
            bot.request_watchhome(int_or_zero(arg), source=src)
        elif cmd in ("clearcookies","cc"):
            bot.request_clear_cookies(source=src)
        elif cmd == "speed":
            if not arg: self.log(f"{src} !speed needs a value", "warn"); return
            try:
                v = float(arg)
                if not (0.25 <= v <= 2.0):
                    self.log(f"{src} !speed range 0.25–2.0", "warn"); return
            except ValueError:
                self.log(f"{src} !speed: bad number {arg!r}", "warn"); return
            bot.request_playback("speed", str(v), source=src)
        elif cmd == "pitch":
            if not arg: self.log(f"{src} !pitch needs semitones", "warn"); return
            try:
                v = float(arg)
                if not (-24 <= v <= 24):
                    self.log(f"{src} !pitch range -24..+24", "warn"); return
            except ValueError:
                self.log(f"{src} !pitch: bad number {arg!r}", "warn"); return
            bot.request_playback("pitch", str(v), source=src)
        elif cmd == "volume":
            if not arg: self.log(f"{src} !volume needs 0–100", "warn"); return
            if not arg.isdigit():
                self.log(f"{src} !volume: bad number {arg!r}", "warn"); return
            v = int(arg)
            if not (0 <= v <= 100):
                self.log(f"{src} !volume range 0–100", "warn"); return
            bot.request_playback("volume", str(v), source=src)
        elif cmd == "seek":
            parts = arg.split()
            if len(parts) != 2 or parts[0] not in ("+","-","set"):
                self.log(f"{src} !seek expects '+ N', '- N', or 'set N'", "warn"); return
            try: float(parts[1])
            except ValueError:
                self.log(f"{src} !seek: bad seconds {parts[1]!r}", "warn"); return
            bot.request_playback("seek", arg, source=src)
        elif cmd == "captions":
            if arg.lower() not in ("on","off","true","false","1","0"):
                self.log(f"{src} !captions expects on/off", "warn"); return
            bot.request_playback("captions", arg, source=src)
        elif cmd == "captionstranslate":
            if not arg:
                self.log(f"{src} !captionstranslate needs a language code", "warn")
                return
            bot.request_playback("captionstranslate", arg, source=src)
        elif cmd == "loop":
            if arg.lower() not in ("video","playlist","off"):
                self.log(f"{src} !loop expects video/playlist/off", "warn"); return
            bot.request_playback("loop", arg, source=src)
        elif cmd == "autoplay":
            if arg.lower() not in ("on","off","true","false","1","0"):
                self.log(f"{src} !autoplay expects on/off", "warn"); return
            bot.request_playback("autoplay", arg, source=src)
        elif cmd in ("playpause","refresh","fullscreen"):
            bot.request_playback(cmd, "", source=src)
        elif cmd == "me":
            self.log(f"{src} !me ignored", "info")
        else:
            if FALLBACK_UNKNOWN_AS_SEARCH and arg:
                self.log(f"{src} fallback search for {arg!r}", "info")
                bot.request_search(arg, lucky=False, source=src)
            else:
                if arg:
                    self.log(f"{src} unknown command '!{cmd} {arg}' — ignored", "warn")
                else:
                    self.log(f"{src} unknown command '!{cmd}' — ignored", "warn")

    @staticmethod
    def _split_lang(arg):
        tokens = arg.split()
        if len(tokens) >= 2 and re.fullmatch(r"[a-zA-Z]{2,3}", tokens[-1]):
            return " ".join(tokens[:-1]), tokens[-1].lower()
        return arg, None


def int_or_zero(s):
    try: return int(s)
    except ValueError: return 0


# ═══════════════════════════════════════════════════════════════════════════
#  EMBEDDED UI  (HTML + CSS + JS)
# ═══════════════════════════════════════════════════════════════════════════
UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OpenCUY</title>
<style>
  :root, [data-theme="nexo"] {
    --bg:#0d0b1a; --card:#141129; --bg3:#1c1836; --bg4:#241f45;
    --border:#2a2547; --border2:#3a3462;
    --text:#f4f4ff; --dim:#8b88b8; --mute:#5d5a85;
    --accent:#8b5cf6; --accent2:#a78bfa; --accent3:#c4b5fd; --accent-dk:#6d28d9;
    --green:#34d399; --red:#f43f5e; --yellow:#fbbf24; --console:#08061a;
  }
  [data-theme="black"] { --bg:#000; --card:#0a0a0a; --bg3:#121212; --bg4:#1c1c1c;
    --border:#1f1f1f; --border2:#2e2e2e; --text:#f0f0f0; --dim:#9a9a9a; --mute:#5a5a5a;
    --accent:#d4d4d4; --accent2:#e5e5e5; --accent3:#f5f5f5; --accent-dk:#a3a3a3;
    --green:#4ade80; --red:#f87171; --yellow:#facc15; --console:#000; }
  [data-theme="red"] { --bg:#1a0606; --card:#240808; --bg3:#2e0b0b; --bg4:#3d1010;
    --border:#4a1515; --border2:#5c1c1c; --text:#ffeaea; --dim:#c98a8a; --mute:#8a5555;
    --accent:#ef4444; --accent2:#f87171; --accent3:#fca5a5; --accent-dk:#b91c1c;
    --green:#4ade80; --red:#fca5a5; --yellow:#fbbf24; --console:#0d0303; }
  [data-theme="blue"] { --bg:#050f1f; --card:#08182e; --bg3:#0b2038; --bg4:#102a4a;
    --border:#13345a; --border2:#1e4573; --text:#e6f0ff; --dim:#7ba0c9; --mute:#4a6a8a;
    --accent:#3b82f6; --accent2:#60a5fa; --accent3:#93c5fd; --accent-dk:#1d4ed8;
    --green:#34d399; --red:#f87171; --yellow:#fbbf24; --console:#020814; }
  [data-theme="lightblue"] { --bg:#eaf4fb; --card:#f5faff; --bg3:#ffffff; --bg4:#dbeafe;
    --border:#b6d4ec; --border2:#8fb8d8; --text:#0b2540; --dim:#4a6a8a; --mute:#7a96b0;
    --accent:#0284c7; --accent2:#0ea5e9; --accent3:#38bdf8; --accent-dk:#0369a1;
    --green:#059669; --red:#dc2626; --yellow:#d97706; --console:#0b2540; }
  [data-theme="green"] { --bg:#041409; --card:#06210e; --bg3:#0a2e15; --bg4:#0f3d1d;
    --border:#124a24; --border2:#1b5c30; --text:#e8ffef; --dim:#7bc995; --mute:#4a8060;
    --accent:#10b981; --accent2:#34d399; --accent3:#6ee7b7; --accent-dk:#047857;
    --green:#6ee7b7; --red:#f87171; --yellow:#fbbf24; --console:#020b05; }
  [data-theme="amber"] { --bg:#1a1000; --card:#241800; --bg3:#2e1f00; --bg4:#3d2a00;
    --border:#4a3400; --border2:#5c4200; --text:#fff7e6; --dim:#c9a566; --mute:#8a6e3a;
    --accent:#f59e0b; --accent2:#fbbf24; --accent3:#fcd34d; --accent-dk:#b45309;
    --green:#84cc16; --red:#f87171; --yellow:#fde047; --console:#0d0800; }
  [data-theme="pink"] { --bg:#1a0614; --card:#24081c; --bg3:#2e0b26; --bg4:#3d1033;
    --border:#4a1540; --border2:#5c1c50; --text:#ffeaf7; --dim:#c98ab8; --mute:#8a5578;
    --accent:#ec4899; --accent2:#f472b6; --accent3:#f9a8d4; --accent-dk:#be185d;
    --green:#4ade80; --red:#fb7185; --yellow:#fbbf24; --console:#0d0308; }
  [data-theme="light"] { --bg:#f4f4f8; --card:#ffffff; --bg3:#ffffff; --bg4:#eef0f5;
    --border:#d4d6de; --border2:#b4b6c0; --text:#1a1a2e; --dim:#5a5a70; --mute:#9090a0;
    --accent:#6366f1; --accent2:#818cf8; --accent3:#a5b4fc; --accent-dk:#4338ca;
    --green:#059669; --red:#dc2626; --yellow:#d97706; --console:#1a1a2e; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; background: var(--bg); color: var(--text);
    font-family:'Segoe UI', Tahoma, sans-serif; font-size:13px;
    height:100%; overflow:hidden; }
  #bgLayer { position:fixed; inset:0; z-index:0;
    background-position:center; background-repeat:no-repeat;
    background-size:cover; pointer-events:none; opacity:0; transition:opacity 0.3s; }
  #bgLayer.tile { background-repeat:repeat; background-size:auto; }
  #bgLayer.stretch { background-size:100% 100%; }
  #bgLayer.contain { background-size:contain; }
  #bgLayer.cover { background-size:cover; }
  #bgOverlay { position:fixed; inset:0; z-index:1; background: var(--bg);
    opacity:0; pointer-events:none; transition:opacity 0.3s; }
  #app { position:relative; z-index:2; display:flex; flex-direction:column; height:100vh; }
  header { display:flex; align-items:center; justify-content:space-between;
    padding:14px 22px; border-bottom:1px solid var(--border);
    background: color-mix(in srgb, var(--card) 92%, transparent);
    backdrop-filter: blur(4px); flex-shrink:0; }
  .brand { display:flex; align-items:center; gap:12px; }
  .logo { width:22px; height:22px; border-radius:50%; background: var(--accent);
    box-shadow: 0 0 16px var(--accent); position:relative; }
  .logo::after { content:''; position:absolute; inset:5px;
    border-radius:50%; background: var(--card); }
  .brand h1 { margin:0; font-size:15px; font-weight:600; color: var(--text); }
  .brand .sub { color: var(--mute); font-size:12px; margin-left:4px; }
  .conn { display:flex; align-items:center; gap:8px; font-size:12px; color: var(--dim); }
  .dot { width:8px; height:8px; border-radius:50%; background: var(--mute); transition:all 0.3s; }
  .dot.on { background: var(--green); box-shadow: 0 0 10px var(--green); }
  .dot.off { background: var(--red); box-shadow: 0 0 10px var(--red); }
  nav { display:flex; gap:2px; padding:0 14px; overflow-x:auto;
    border-bottom:1px solid var(--border);
    background: color-mix(in srgb, var(--card) 92%, transparent);
    backdrop-filter: blur(4px); flex-shrink:0; }
  nav::-webkit-scrollbar { height:6px; }
  nav::-webkit-scrollbar-thumb { background: var(--border); border-radius:3px; }
  nav button { background:transparent; color: var(--mute); border:none;
    padding:12px 18px; font-size:13px; font-weight:600; cursor:pointer;
    border-radius:8px 8px 0 0; border-bottom:2px solid transparent;
    transition:all 0.15s; font-family:inherit; white-space:nowrap; }
  nav button:hover { color: var(--text); background: var(--bg4); }
  nav button.active { color: var(--accent2); border-bottom-color: var(--accent); }
  main { flex:1; overflow:hidden; padding:20px; display:flex; flex-direction:column; min-height:0; }
  .tab { display:none; }
  .tab.active { display:flex; flex-direction:column; gap:14px; flex:1;
    min-height:0; overflow-y:auto; animation: fade 0.2s; }
  @keyframes fade { from { opacity:0; transform:translateY(4px); } to { opacity:1; } }
  .card { background: color-mix(in srgb, var(--card) 94%, transparent);
    backdrop-filter: blur(6px); border:1px solid var(--border);
    border-radius:12px; padding:18px; }
  .card h2 { margin:0 0 14px; font-size:13px; font-weight:700;
    color: var(--accent2); text-transform:uppercase; letter-spacing:0.6px; }
  .row { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .row:last-child { margin-bottom:0; }
  .row label { width:120px; color: var(--dim); font-size:12px; flex-shrink:0; }
  input[type=text], input[type=number], textarea, select {
    flex:1; background: var(--bg3); color: var(--text);
    border:1px solid var(--border); border-radius:8px;
    padding:9px 12px; font-family:inherit; font-size:13px;
    outline:none; transition: border-color 0.15s, box-shadow 0.15s; min-width:0; }
  input:focus, textarea:focus, select:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }
  input[type=range] { flex:1; accent-color: var(--accent);
    background:transparent; border:none; padding:0; }
  textarea { font-family:'Consolas', monospace; font-size:12.5px;
    line-height:1.55; width:100%; tab-size:4; }
  select { cursor:pointer; appearance:none; padding-right:32px;
    background-image: linear-gradient(45deg, transparent 50%, var(--accent2) 50%),
                      linear-gradient(135deg, var(--accent2) 50%, transparent 50%);
    background-position: calc(100% - 16px) 50%, calc(100% - 11px) 50%;
    background-size: 5px 5px, 5px 5px; background-repeat:no-repeat; }
  button.btn { background: var(--bg3); color: var(--text);
    border:1px solid var(--border); padding:9px 16px; border-radius:8px;
    font-family:inherit; font-size:13px; font-weight:600; cursor:pointer;
    transition:all 0.15s; white-space:nowrap; }
  button.btn:hover { background: var(--bg4); border-color: var(--border2); }
  button.btn:active { transform: scale(0.98); }
  button.btn.accent { background: var(--accent); color:#fff; border-color: var(--accent); }
  button.btn.accent:hover { background: var(--accent2); border-color: var(--accent2); }
  button.btn.danger { background: color-mix(in srgb, var(--red) 20%, transparent);
    color: var(--text); border-color: var(--red); }
  button.btn.danger:hover { background: color-mix(in srgb, var(--red) 35%, transparent); }
  button.btn.sm { padding:6px 12px; font-size:12px; }
  .console { background: var(--console); border:1px solid var(--border);
    border-radius:8px; padding:14px; overflow-y:auto;
    font-family:'Consolas', monospace; font-size:12.5px; line-height:1.55;
    color: var(--green); }
  .log-line { margin-bottom:4px; word-break:break-word; }
  .log-time { color: var(--mute); margin-right:6px; }
  .log-info { color: var(--accent2); }
  .log-err { color: var(--red); }
  .log-warn { color: var(--yellow); }
  .log-ok { color: var(--green); }
  .status { color: var(--accent2); font-size:12px; font-weight:600; }
  .hint { color: var(--mute); font-size:11.5px; line-height:1.6; }
  .flex { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .grow { flex:1; }
  .themegrid { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:10px; }
  .themecard { background: var(--bg3); border:2px solid var(--border);
    border-radius:10px; padding:10px; cursor:pointer; transition:all 0.15s;
    display:flex; flex-direction:column; gap:8px; }
  .themecard:hover { border-color: var(--accent); transform: translateY(-1px); }
  .themecard.active { border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }
  .themecard .name { font-size:12px; font-weight:700; color: var(--text); }
  .themecard .swatches { display:flex; gap:4px; }
  .themecard .sw { width:18px; height:18px; border-radius:50%;
    border:1px solid rgba(255,255,255,0.1); }
  .imgpreview { width:100%; height:140px; border:1px solid var(--border);
    border-radius:8px; overflow:hidden; background: var(--bg3);
    display:flex; align-items:center; justify-content:center;
    color: var(--mute); font-size:12px; }
  .imgpreview img { max-width:100%; max-height:100%; object-fit:contain; }
  .dep-row { display:flex; align-items:center; gap:10px; padding:8px 0;
    border-bottom:1px solid var(--border); font-size:13px; }
  .dep-row:last-child { border-bottom:none; }
  .dep-row .dep-icon { width:18px; height:18px; border-radius:50%;
    flex-shrink:0; display:flex; align-items:center; justify-content:center;
    font-size:12px; font-weight:700; }
  .dep-row .dep-icon.ok { background: color-mix(in srgb, var(--green) 30%, transparent);
    color: var(--green); }
  .dep-row .dep-icon.miss { background: color-mix(in srgb, var(--red) 30%, transparent);
    color: var(--red); }
  .dep-row .dep-icon.opt { background: color-mix(in srgb, var(--yellow) 30%, transparent);
    color: var(--yellow); }
  .dep-row .dep-name { font-weight:600; color: var(--text); min-width:130px; }
  .dep-row .dep-desc { color: var(--dim); flex:1; }
  .dep-row .dep-state { color: var(--mute); font-size:12px; }
  .cfg-row { display:flex; align-items:center; gap:10px; padding:10px 0;
    border-bottom:1px solid var(--border); font-size:13px; }
  .cfg-row:last-child { border-bottom:none; }
  .cfg-row label { flex:1; color: var(--text); }
  .cfg-row .hint { flex:2; }
  .toggle { position:relative; width:44px; height:24px; flex-shrink:0; }
  .toggle input { opacity:0; width:0; height:0; }
  .toggle .slider { position:absolute; cursor:pointer; inset:0;
    background: var(--bg4); border:1px solid var(--border2);
    border-radius:24px; transition:0.2s; }
  .toggle .slider:before { content:''; position:absolute; height:16px; width:16px;
    left:3px; bottom:3px; background: var(--mute); border-radius:50%;
    transition:0.2s; }
  .toggle input:checked + .slider { background: var(--accent);
    border-color: var(--accent); }
  .toggle input:checked + .slider:before { transform: translateX(20px);
    background:#fff; }
</style>
</head>
<body>
<div id="bgLayer"></div>
<div id="bgOverlay"></div>
<div id="app">
  <header>
    <div class="brand">
      <div class="logo"></div>
      <h1>OpenCUY <span class="sub">· v2.0 modern engine</span></h1>
    </div>
    <div class="conn">
      <span id="connText">Booting…</span>
      <span class="dot" id="connDot"></span>
    </div>
  </header>
  <nav>
    <button class="active" data-tab="bot">Bot</button>
    <button data-tab="chat">Live Chat</button>
    <button data-tab="cmds">Commands</button>
    <button data-tab="theme">Theme</button>
    <button data-tab="config">Config</button>
    <button data-tab="deps">Dependencies</button>
  </nav>
  <main>

    <section class="tab active" id="tab-bot">
      <div class="card">
        <h2>Quick Actions</h2>
        <div class="row"><label>Query</label>
          <input type="text" id="query" placeholder="search text, or !lucky cats, or !channel @handle"
                 onkeydown="if(event.key==='Enter'){runQuery();}">
          <button class="btn accent" onclick="runQuery()">▶  Send</button>
          <button class="btn" onclick="quickLucky()">🍀  Lucky</button>
          <button class="btn" onclick="quickHome()">🏠  Home</button>
          <button class="btn danger" onclick="quickClear()">🧹  Clear cookies</button>
        </div>
        <div class="row"><label>Language</label>
          <input type="text" id="lang" placeholder="e.g. en, es, ja" style="max-width:120px">
          <button class="btn sm" onclick="setLang()">Apply to next nav</button>
          <span class="hint">appended as &amp;hl=… to search / channel URLs</span>
        </div>
      </div>
      <div class="card">
        <h2>Playback Control</h2>
        <div class="row">
          <button class="btn sm" onclick="pb('playpause')">⏯  Play/Pause</button>
          <button class="btn sm" onclick="pb('refresh')">↻  Refresh</button>
          <button class="btn sm" onclick="pb('fullscreen')">⛶  Fullscreen</button>
          <button class="btn sm" onclick="pb('captions','on')">CC on</button>
          <button class="btn sm" onclick="pb('captions','off')">CC off</button>
        </div>
        <div class="row"><label>Speed</label>
          <input type="number" id="speed" value="1.0" step="0.05" min="0.25" max="2.0" style="max-width:100px">
          <button class="btn sm" onclick="pb('speed', document.getElementById('speed').value)">Apply</button>
          <label>Volume</label>
          <input type="number" id="vol" value="50" min="0" max="100" style="max-width:100px">
          <button class="btn sm" onclick="pb('volume', document.getElementById('vol').value)">Apply</button>
        </div>
        <div class="row"><label>Scroll</label>
          <input type="number" id="scrollPx" value="500" style="max-width:120px">
          <input type="number" id="scrollDur" placeholder="sec" step="0.5" min="0" max="120" style="max-width:80px">
          <button class="btn sm" onclick="scrollPage()">↕  Scroll</button>
          <label>Watch homepage</label>
          <input type="number" id="watchIdx" value="1" min="1" style="max-width:80px">
          <button class="btn sm" onclick="watchHome()">▶  Go</button>
        </div>
      </div>
      <div class="card log-card">
        <h2>Bot / Filter Log</h2>
        <div class="console" id="ipcLog"></div>
      </div>
    </section>

    <section class="tab" id="tab-chat">
      <div class="card">
        <h2>YouTube Live Chat</h2>
        <div class="row"><label>Video ID</label>
          <input type="text" id="chatVid" placeholder="11-char video ID">
          <button class="btn accent" onclick="startChat()">▶  Connect</button>
          <button class="btn danger" onclick="stopChat()">■  Disconnect</button></div>
        <div class="row"><label>Prefix</label>
          <input type="text" id="chatPrefix" value="!" style="max-width:70px">
          <span class="status" id="chatStatus">Chat: idle</span></div>
        <p class="hint" style="margin:8px 0 0">
          Chat commands route through the same dispatcher as the local box.
          Prefix defaults to <code>!</code>.
        </p>
      </div>
      <div class="card log-card">
        <h2>Chat Log</h2>
        <div class="console" id="chatLog"></div>
      </div>
    </section>

    <section class="tab" id="tab-cmds">
      <div class="card">
        <h2>Command Reference</h2>
        <pre class="hint" style="margin:0;white-space:pre-wrap;font-size:12px;line-height:1.6">
!search [query] [lang]         search YouTube (results listed)
!lucky [query] [lang]          search &amp; play the first SAFE result
!searchwatch [query] [lang]    alias of !lucky
!channel [handle|UC-id] [lang] load a channel page
!home [lang]                   go to youtube.com
!changelanguage [code]         set hl for future navigations
!english                       shortcut for lang=en
!scroll [+N | -N | N] [sec]    scroll page (+down, -up)
                               optional linear animation, max 120s
!watchhome [n]                 click the nth non-ad card on the home feed
!cc / !clearcookies            wipe cookies + localStorage

Playback:
!speed [0.25-2.0]              set playbackRate
!pitch [-24..+24]              shift pitch in semitones
!volume [0-100]                set volume
!playpause                     toggle
!refresh                       reload
!captions on|off               toggle captions module
!captionstranslate [lang]      translate captions to a language
!loop video|playlist|off       loop mode
!autoplay on|off               toggle autoplay
!fullscreen                    toggle fullscreen button
!seek + N | - N | set N        seek relative/absolute

Safety: every user-driven navigation is pre-flighted in a
separate headless Chrome instance. Live streams and videos
under 24 hours old are blocked.
        </pre>
      </div>
    </section>

    <section class="tab" id="tab-theme">
      <div class="card">
        <h2>Color Theme</h2>
        <div class="themegrid" id="themeGrid"></div>
      </div>
      <div class="card">
        <h2>Background Image</h2>
        <div class="row"><label>Image file</label>
          <input type="text" id="bgPath" placeholder="(none)" readonly>
          <button class="btn" onclick="browseBgImage()">Browse…</button>
          <button class="btn danger" onclick="clearBgImage()">Clear</button></div>
        <div class="row"><label>Fit mode</label>
          <select id="bgMode" style="max-width:200px">
            <option value="cover">Cover (fill, crop)</option>
            <option value="contain">Contain (fit, letterbox)</option>
            <option value="stretch">Stretch (distort)</option>
            <option value="tile">Tile (repeat)</option></select></div>
        <div class="row"><label>Image opacity</label>
          <input type="range" id="bgOpacity" min="0" max="100" value="35">
          <span class="status" id="bgOpacityVal" style="width:44px;text-align:right">35%</span></div>
        <div class="row"><label>Blur</label>
          <input type="range" id="bgBlur" min="0" max="20" value="4">
          <span class="status" id="bgBlurVal" style="width:44px;text-align:right">4px</span></div>
        <div class="row"><label>Overlay dim</label>
          <input type="range" id="bgDim" min="0" max="100" value="72">
          <span class="status" id="bgDimVal" style="width:44px;text-align:right">72%</span></div>
        <div class="flex" style="margin-top:8px">
          <button class="btn accent" onclick="saveTheme()">Save theme</button>
          <button class="btn" onclick="resetTheme()">Reset to defaults</button>
          <span class="hint" id="themeStatus">Saved automatically on change.</span></div>
      </div>
      <div class="card">
        <h2>Preview</h2>
        <div class="imgpreview" id="bgPreview">(no image)</div>
      </div>
    </section>

    <section class="tab" id="tab-config">
      <div class="card">
        <h2>Command Fallbacks</h2>
        <div class="cfg-row">
          <label>Unknown commands → search</label>
          <span class="hint">
            When ON: <code>!randomcommand foo</code> is treated as a search for <code>foo</code>.<br>
            When OFF: unknown commands log a warning and are ignored.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgUnknown" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Plain text → search</label>
          <span class="hint">
            When ON: a message with no leading <code>!</code> triggers a search.<br>
            When OFF: plain text is ignored.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgPlain" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Home when video ends</label>
          <span class="hint">
            When ON: after a video finishes on a <code>/watch</code> page,
            navigate to <code>https://www.youtube.com/</code>.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgHomeOnEnd" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Off-site watchdog</label>
          <span class="hint">
            When ON: if the browser is ever on a non-YouTube page,
            navigate back to <code>https://www.youtube.com/</code>.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgWatchdog" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Block live streams</label>
          <span class="hint">
            When ON: refuse to play any video that is a live broadcast.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgBlockLive" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Block videos &lt; 24h old</label>
          <span class="hint">
            When ON: refuse to play videos uploaded less than 24 hours ago.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgBlock24h" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Command screenshots</label>
          <span class="hint">
            When ON: 10 seconds after every command, save a PNG of the
            Chrome viewport to <code>./screenshots/</code>.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgScreens" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="cfg-row">
          <label>Use headless checker</label>
          <span class="hint">
            When ON: every user-driven navigation is pre-flighted in a
            headless Chrome instance so the visible window never touches
            unsafe content.
          </span>
          <label class="toggle">
            <input type="checkbox" id="cfgChecker" onchange="saveConfig()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="flex" style="margin-top:14px">
          <button class="btn accent" onclick="saveConfig()">Save config</button>
          <button class="btn" onclick="loadConfig()">Reload from file</button>
          <span class="hint" id="cfgStatus">Saved to opencuy_config.json</span>
        </div>
      </div>
      <div class="card">
        <h2>Live Config Values</h2>
        <div class="console" id="cfgView" style="min-height:100px"></div>
      </div>
    </section>

    <section class="tab" id="tab-deps">
      <div class="card">
        <h2>Dependencies</h2>
        <div class="flex" style="margin-bottom:12px">
          <button class="btn" onclick="refreshDeps()">↻ Re-check</button>
          <button class="btn accent" onclick="installMissing()">⬇ Install missing</button>
          <button class="btn danger" onclick="clearDepPrefs()">Reset prefs</button>
        </div>
        <div id="depsList"></div>
      </div>
      <div class="card">
        <h2>Install Log</h2>
        <div class="console" id="depsLog" style="min-height:180px"></div>
      </div>
    </section>

  </main>
</div>

<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
let bridge = null;
let currentTheme = { name:'nexo', image_path:'', image_mode:'cover',
                     image_opacity:0.35, image_blur:4, image_dim:0.72 };

function asString(v) { return (v === null || v === undefined) ? '' : String(v); }
function asJSON(v, fb) {
  try { const s = asString(v); if (!s) return fb; return JSON.parse(s); }
  catch(e) { return fb; }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const THEMES = [
  { id:'nexo', name:'Nexo Violet', swatches:['#0d0b1a','#8b5cf6','#a78bfa','#34d399'] },
  { id:'black', name:'Black', swatches:['#000','#d4d4d4','#e5e5e5','#4ade80'] },
  { id:'red', name:'Red', swatches:['#1a0606','#ef4444','#f87171','#fca5a5'] },
  { id:'blue', name:'Blue', swatches:['#050f1f','#3b82f6','#60a5fa','#93c5fd'] },
  { id:'lightblue', name:'Light Blue', swatches:['#eaf4fb','#0284c7','#0ea5e9','#38bdf8'] },
  { id:'green', name:'Green', swatches:['#041409','#10b981','#34d399','#6ee7b7'] },
  { id:'amber', name:'Amber', swatches:['#1a1000','#f59e0b','#fbbf24','#fcd34d'] },
  { id:'pink', name:'Pink', swatches:['#1a0614','#ec4899','#f472b6','#f9a8d4'] },
  { id:'light', name:'Light', swatches:['#f4f4f8','#6366f1','#818cf8','#a5b4fc'] },
];

new QWebChannel(qt.webChannelTransport, ch => {
  bridge = ch.objects.bridge;
  bridge.log_message.connect((msg, lvl) => appendLog('ipcLog', asString(msg), lvl));
  bridge.chat_event.connect(s => onChatEvent(asJSON(s, {})));
  bridge.status_update.connect((label, ok) => {
    const t = document.getElementById('connText');
    t.textContent = asString(label);
    t.style.color = ok ? 'var(--green)' : 'var(--red)';
    document.getElementById('connDot').className = 'dot ' + (ok ? 'on' : 'off');
  });
  bridge.deps_event.connect(s => onDepsEvent(asJSON(s, {})));

  try {
    const saved = asJSON(bridge.get_theme(), {});
    if (saved && saved.name) currentTheme = Object.assign({}, currentTheme, saved);
  } catch(e) {}

  buildThemeGrid();
  applyTheme(currentTheme);
  appendLog('ipcLog', 'Bridge connected.', 'ok');
  refreshDeps();
  loadConfig();
});

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-' + b.dataset.tab).classList.add('active');
    if (b.dataset.tab === 'config') loadConfig();
  };
});

function appendLog(elId, msg, level) {
  const box = document.getElementById(elId); if (!box) return;
  const time = new Date().toLocaleTimeString();
  const cls = level ? 'log-' + level : '';
  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = `<span class="log-time">[${time}]</span><span class="${cls}">${escapeHtml(msg)}</span>`;
  box.appendChild(line); box.scrollTop = box.scrollHeight;
}

/* ═══════ BOT TAB ═══════ */
function runQuery() {
  const q = document.getElementById('query').value.trim();
  if (!q) return;
  const lang = document.getElementById('lang').value.trim();
  if (q.startsWith('!')) {
    bridge.dispatch_command(q.slice(1), lang, 'you');
  } else {
    bridge.dispatch_command(q, lang, 'you');
  }
  document.getElementById('query').value = '';
}
function quickLucky() {
  const q = document.getElementById('query').value.trim();
  if (!q) return;
  const lang = document.getElementById('lang').value.trim();
  bridge.request_search_lucky(q, lang, 'you-lucky', true);
  document.getElementById('query').value = '';
}
function quickHome() {
  const lang = document.getElementById('lang').value.trim();
  bridge.dispatch_command('home ' + (lang||''), '', 'you');
}
function quickClear() { bridge.dispatch_command('cc', '', 'you'); }
function setLang() {
  const l = document.getElementById('lang').value.trim();
  if (!l) return;
  bridge.dispatch_command('changelanguage ' + l, '', 'you');
}
function pb(kind, arg) { bridge.request_playback(kind, arg || ''); }
function scrollPage() {
  const px = document.getElementById('scrollPx').value.trim();
  const dur = document.getElementById('scrollDur').value.trim();
  let cmd = 'scroll ' + (px || '500');
  if (dur) cmd += ' ' + dur;
  bridge.dispatch_command(cmd, '', 'you');
}
function watchHome() {
  const n = parseInt(document.getElementById('watchIdx').value) || 1;
  bridge.dispatch_command('watchhome ' + n, '', 'you');
}

/* ═══════ CHAT TAB ═══════ */
function startChat() {
  const vid = document.getElementById('chatVid').value.trim();
  if (!vid) { appendLog('chatLog', 'Enter a video ID.', 'warn'); return; }
  bridge.chat_start(vid, document.getElementById('chatPrefix').value || '!');
}
function stopChat() { bridge.chat_stop_remote(); }
function onChatEvent(ev) {
  if (!ev || !ev.type) return;
  if (ev.type === 'system') {
    appendLog('chatLog', '* ' + ev.message, 'info');
    document.getElementById('chatStatus').textContent = 'Chat: connected';
  } else if (ev.type === 'error') appendLog('chatLog', '[err] ' + ev.message, 'err');
  else if (ev.type === 'end') appendLog('chatLog', '[end] ' + ev.message, 'warn');
  else if (ev.type === 'chat') appendLog('chatLog', `${ev.author}: ${ev.message}`, '');
  else if (ev.type === 'cmd') appendLog('chatLog', '  ↳ ' + ev.message, 'info');
  else if (ev.type === 'status') document.getElementById('chatStatus').textContent = ev.message;
  else if (ev.type === 'log') appendLog('ipcLog', ev.message, ev.level || 'info');
}

/* ═══════ THEME TAB ═══════ */
function buildThemeGrid() {
  const grid = document.getElementById('themeGrid');
  grid.innerHTML = '';
  THEMES.forEach(t => {
    const card = document.createElement('div');
    card.className = 'themecard' + (t.id === currentTheme.name ? ' active' : '');
    card.dataset.themeId = t.id;
    const sw = t.swatches.map(c => `<div class="sw" style="background:${c}"></div>`).join('');
    card.innerHTML = `<div class="name">${escapeHtml(t.name)}</div><div class="swatches">${sw}</div>`;
    card.onclick = () => {
      currentTheme.name = t.id; applyTheme(currentTheme);
      document.querySelectorAll('.themecard').forEach(x =>
        x.classList.toggle('active', x.dataset.themeId === t.id));
      saveTheme(true);
    };
    grid.appendChild(card);
  });
}
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme.name || 'nexo');
  const bg = document.getElementById('bgLayer');
  const ov = document.getElementById('bgOverlay');
  if (theme.image_path) {
    let url = theme.image_path.replace(/\\/g, '/');
    if (!/^file:\/\//i.test(url) && !/^https?:/i.test(url)) {
      url = 'file:///' + url.replace(/^\/+/, '');
    }
    bg.style.backgroundImage = `url("${url}")`;
    bg.classList.remove('cover','contain','stretch','tile');
    bg.classList.add(theme.image_mode || 'cover');
    bg.style.opacity = String(theme.image_opacity != null ? theme.image_opacity : 0.35);
    bg.style.filter = `blur(${theme.image_blur || 0}px)`;
  } else { bg.style.backgroundImage = ''; bg.style.opacity = '0'; }
  const dim = theme.image_dim != null ? theme.image_dim : 0.72;
  ov.style.opacity = theme.image_path ? String(dim) : '0';
  const p = document.getElementById('bgPath'); if (p) p.value = theme.image_path || '';
  const m = document.getElementById('bgMode'); if (m) m.value = theme.image_mode || 'cover';
  const o = document.getElementById('bgOpacity');
  if (o) { o.value = Math.round((theme.image_opacity ?? 0.35) * 100);
           document.getElementById('bgOpacityVal').textContent = o.value + '%'; }
  const b = document.getElementById('bgBlur');
  if (b) { b.value = theme.image_blur ?? 4;
           document.getElementById('bgBlurVal').textContent = b.value + 'px'; }
  const d = document.getElementById('bgDim');
  if (d) { d.value = Math.round((theme.image_dim ?? 0.72) * 100);
           document.getElementById('bgDimVal').textContent = d.value + '%'; }
  const prev = document.getElementById('bgPreview');
  if (prev) {
    if (theme.image_path) {
      let url = theme.image_path.replace(/\\/g, '/');
      if (!/^file:\/\//i.test(url) && !/^https?:/i.test(url)) {
        url = 'file:///' + url.replace(/^\/+/, '');
      }
      prev.innerHTML = `<img src="${url}" alt="preview">`;
    } else prev.textContent = '(no image)';
  }
}
function browseBgImage() {
  const p = asString(bridge.browse_image()); if (!p) return;
  currentTheme.image_path = p; applyTheme(currentTheme); saveTheme(true);
}
function clearBgImage() { currentTheme.image_path=''; applyTheme(currentTheme); saveTheme(true); }
function saveTheme(silent) {
  try {
    bridge.save_theme(JSON.stringify(currentTheme));
    if (!silent) {
      const s = document.getElementById('themeStatus');
      if (s) { s.textContent='Theme saved.'; setTimeout(()=>s.textContent='Saved automatically on change.',1500); }
    }
  } catch(e) {}
}
function resetTheme() {
  if (!confirm('Reset theme and background to defaults?')) return;
  currentTheme = { name:'nexo', image_path:'', image_mode:'cover',
                   image_opacity:0.35, image_blur:4, image_dim:0.72 };
  applyTheme(currentTheme); buildThemeGrid(); saveTheme(true);
}
document.addEventListener('DOMContentLoaded', () => {
  const modeEl = document.getElementById('bgMode');
  const opEl = document.getElementById('bgOpacity');
  const blEl = document.getElementById('bgBlur');
  const dimEl = document.getElementById('bgDim');
  if (modeEl) modeEl.onchange = () => { currentTheme.image_mode = modeEl.value; applyTheme(currentTheme); saveTheme(true); };
  if (opEl) opEl.oninput = () => { currentTheme.image_opacity = opEl.value/100;
    document.getElementById('bgOpacityVal').textContent = opEl.value+'%';
    applyTheme(currentTheme); saveTheme(true); };
  if (blEl) blEl.oninput = () => { currentTheme.image_blur = parseInt(blEl.value,10);
    document.getElementById('bgBlurVal').textContent = blEl.value+'px';
    applyTheme(currentTheme); saveTheme(true); };
  if (dimEl) dimEl.oninput = () => { currentTheme.image_dim = dimEl.value/100;
    document.getElementById('bgDimVal').textContent = dimEl.value+'%';
    applyTheme(currentTheme); saveTheme(true); };
});

/* ═══════ CONFIG TAB ═══════ */
function loadConfig() {
  try {
    const cfg = asJSON(bridge.get_config(), {});
    document.getElementById('cfgUnknown').checked   = !!(cfg.fallback_unknown_as_search);
    document.getElementById('cfgPlain').checked     = !!(cfg.fallback_plaintext_as_search);
    document.getElementById('cfgHomeOnEnd').checked = !!(cfg.home_on_video_end);
    document.getElementById('cfgWatchdog').checked  = !!(cfg.off_site_watchdog);
    document.getElementById('cfgBlockLive').checked = !!(cfg.block_live_streams);
    document.getElementById('cfgBlock24h').checked  = !!(cfg.block_under_24h);
    document.getElementById('cfgScreens').checked   = !!(cfg.screenshot_enabled);
    document.getElementById('cfgChecker').checked   = !!(cfg.use_headless_checker);
    document.getElementById('cfgView').textContent  = JSON.stringify(cfg, null, 2);
  } catch(e) {
    appendLog('ipcLog', 'Config load failed: ' + e.message, 'err');
  }
}
function saveConfig() {
  const cfg = {
    fallback_unknown_as_search:  document.getElementById('cfgUnknown').checked,
    fallback_plaintext_as_search:document.getElementById('cfgPlain').checked,
    home_on_video_end:           document.getElementById('cfgHomeOnEnd').checked,
    off_site_watchdog:           document.getElementById('cfgWatchdog').checked,
    block_live_streams:          document.getElementById('cfgBlockLive').checked,
    block_under_24h:             document.getElementById('cfgBlock24h').checked,
    screenshot_enabled:          document.getElementById('cfgScreens').checked,
    use_headless_checker:        document.getElementById('cfgChecker').checked,
  };
  try {
    bridge.save_config(JSON.stringify(cfg));
    document.getElementById('cfgView').textContent = JSON.stringify(cfg, null, 2);
    const s = document.getElementById('cfgStatus');
    s.textContent = 'Config saved.';
    setTimeout(() => s.textContent = 'Saved to opencuy_config.json', 1500);
  } catch(e) {
    appendLog('ipcLog', 'Config save failed: ' + e.message, 'err');
  }
}

/* ═══════ DEPS TAB ═══════ */
function refreshDeps() {
  try {
    const info = asJSON(bridge.get_dep_info(), {});
    renderDeps(info);
  } catch(e) { appendLog('depsLog', 'Failed: ' + e.message, 'err'); }
}
function renderDeps(info) {
  const box = document.getElementById('depsList');
  if (!info || !Array.isArray(info.deps)) { box.textContent = 'No info'; return; }
  box.innerHTML = '';
  info.deps.forEach(d => {
    const row = document.createElement('div');
    row.className = 'dep-row';
    const ic = d.installed ? 'ok' : (d.required ? 'miss' : 'opt');
    const ch = d.installed ? '✓' : (d.required ? '✗' : '!');
    row.innerHTML =
      `<div class="dep-icon ${ic}">${ch}</div>
       <div class="dep-name">${escapeHtml(d.package)}</div>
       <div class="dep-desc">${escapeHtml(d.description)}</div>
       <div class="dep-state">${d.installed ? 'installed' : (d.required ? 'required' : 'optional')}</div>`;
    box.appendChild(row);
  });
}
function installMissing() {
  appendLog('depsLog', 'Installing missing dependencies…', 'info');
  bridge.install_deps_async();
}
function clearDepPrefs() {
  if (!confirm('Clear dependency preferences?')) return;
  bridge.clear_dep_prefs();
  appendLog('depsLog', 'Preferences cleared.', 'ok');
}
function onDepsEvent(ev) {
  if (!ev || !ev.type) return;
  if (ev.type === 'log') appendLog('depsLog', ev.message, ev.level || 'info');
  else if (ev.type === 'installed') { appendLog('depsLog', '✓ ' + ev.package, 'ok'); refreshDeps(); }
  else if (ev.type === 'failed') appendLog('depsLog', '✗ ' + ev.package + ': ' + ev.error, 'err');
  else if (ev.type === 'done') {
    appendLog('depsLog', `Done. ${ev.installed} installed, ${ev.failed} failed.`,
              ev.failed > 0 ? 'warn' : 'ok');
    refreshDeps();
  }
}
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════
#  BRIDGE  (QObject exposed to JS)
# ═══════════════════════════════════════════════════════════════════════════
class Bridge(QObject):
    log_message  = Signal(str, str)
    chat_event   = Signal(str)
    status_update= Signal(str, bool)
    deps_event   = Signal(str)

    def __init__(self):
        super().__init__()
        self._bot_queue = queue.Queue()
        self.bot = YouTubeBot(self._bot_queue)

        self.chat_reader = None
        self.chat_stop = threading.Event()
        self.chat_cfg = {"prefix": CHAT_COMMAND_PREFIX}
        self._chat_want = False

        self.bot.start()

        self._pump = QTimer()
        self._pump.setInterval(120)
        self._pump.timeout.connect(self._pump_bot_queue)
        self._pump.start()

    def _pump_bot_queue(self):
        q = self._bot_queue
        while True:
            try: ev = q.get_nowait()
            except queue.Empty: break
            k = ev.get("kind")
            if k == "log":
                self.log_message.emit(ev.get("message", ""), ev.get("level", "info"))
            elif k == "chat":
                self.chat_event.emit(json.dumps({
                    "type": "chat",
                    "author": ev.get("author", "?"),
                    "message": ev.get("message", ""),
                    "datetime": ev.get("datetime", ""),
                    "is_owner": bool(ev.get("is_owner")),
                    "is_mod":   bool(ev.get("is_mod")),
                }))
                msg = ev.get("message", "")
                prefix = self.chat_cfg.get("prefix", "!")
                if msg.startswith(prefix):
                    body = msg[len(prefix):].strip()
                    if body:
                        self.dispatch_command(body, "",
                                              f"chat:{ev.get('author','?')}")
            elif k == "chat_status":
                self.chat_event.emit(json.dumps({
                    "type": "status", "message": ev.get("message", ""),
                    "level": ev.get("level", "info")}))

    @Slot(str, str, str)
    def request_search(self, query, lang="", source="ui"):
        lang = (lang or "").strip() or None
        self.bot.request_search(query, lucky=False, lang=lang, source=source)

    @Slot(str, str, str, bool)
    def request_search_lucky(self, query, lang, source, lucky=True):
        lang = (lang or "").strip() or None
        self.bot.request_search(query, lucky=bool(lucky), lang=lang, source=source)

    @Slot(str, str)
    def request_playback(self, kind, arg=""):
        self.bot.request_playback(kind, arg, source="ui")

    @Slot(str, str, str)
    def dispatch_command(self, body, lang_extra, source):
        body = (body or "").strip()
        if not body: return
        lang_extra = (lang_extra or "").strip()
        if lang_extra:
            first = body.split()[0].lower()
            if first in ("search", "lucky", "searchwatch", "channel", "home"):
                toks = body.split()
                if not (len(toks) >= 2 and re.fullmatch(r"[a-zA-Z]{2,3}", toks[-1])):
                    body = body + " " + lang_extra
        try:
            self.bot._schedule_screenshot(f"{source}: !{body[:40]}")
        except Exception:
            pass
        disp = CommandDispatcher(self.bot, self._log_from_dispatcher, source=source)
        disp.handle(body)

    def _log_from_dispatcher(self, msg, level="info"):
        self.log_message.emit(msg, level)

    @Slot(str, str)
    def chat_start(self, video_id, prefix):
        if not PYCHAT_AVAILABLE:
            self.chat_event.emit(json.dumps({"type": "error",
                "message": "pytchat not installed"})); return
        self.chat_cfg["prefix"] = prefix or "!"
        self.chat_stop = threading.Event()
        self.chat_reader = LiveChatReader(video_id.strip(), self._bot_queue,
                                          self.chat_stop)
        self.chat_reader.start()
        self._chat_want = True
        self.chat_event.emit(json.dumps({"type": "system",
            "message": f"Starting chat for {video_id.strip()}…"}))

    @Slot()
    def chat_stop_remote(self):
        self._chat_want = False
        if self.chat_stop: self.chat_stop.set()
        self.chat_event.emit(json.dumps({"type": "status", "message": "Chat: stopped"}))

    @Slot(result=str)
    def get_theme(self):
        cfg = _load_json(THEME_FILE, None) or dict(DEFAULT_THEME)
        for k, v in DEFAULT_THEME.items(): cfg.setdefault(k, v)
        return str(json.dumps(cfg))

    @Slot(str, result=bool)
    def save_theme(self, json_str):
        try:
            data = json.loads(json_str)
            merged = dict(DEFAULT_THEME); merged.update(data or {})
            _save_json(THEME_FILE, merged); return True
        except Exception as e:
            self.log_message.emit(f"Theme save failed: {e}", "err"); return False

    @Slot(result=str)
    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Select background image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;All files (*.*)")
        return str(path or "")

    @Slot(result=str)
    def get_config(self):
        """Reload config from opencuy_config.json (if present) and return
        the fresh values. Also updates the module-level globals so
        subsequent commands see the new settings immediately."""
        global FALLBACK_UNKNOWN_AS_SEARCH, FALLBACK_PLAINTEXT_AS_SEARCH, \
               HOME_ON_VIDEO_END, OFF_SITE_WATCHDOG, \
               BLOCK_LIVE_STREAMS, BLOCK_UNDER_24H, \
               SCREENSHOT_ENABLED, SCREENSHOT_DELAY, USE_HEADLESS_CHECKER

        cfg = _load_json(CONFIG_FILE, {})
        if isinstance(cfg, dict) and cfg:
            FALLBACK_UNKNOWN_AS_SEARCH   = bool(cfg.get("fallback_unknown_as_search",  False))
            FALLBACK_PLAINTEXT_AS_SEARCH = bool(cfg.get("fallback_plaintext_as_search",True))
            HOME_ON_VIDEO_END            = bool(cfg.get("home_on_video_end",           True))
            OFF_SITE_WATCHDOG            = bool(cfg.get("off_site_watchdog",           True))
            BLOCK_LIVE_STREAMS           = bool(cfg.get("block_live_streams",          True))
            BLOCK_UNDER_24H              = bool(cfg.get("block_under_24h",             True))
            SCREENSHOT_ENABLED           = bool(cfg.get("screenshot_enabled",          True))
            USE_HEADLESS_CHECKER         = bool(cfg.get("use_headless_checker",        True))
            try:
                SCREENSHOT_DELAY = float(cfg.get("screenshot_delay", 10.0))
            except Exception:
                SCREENSHOT_DELAY = 10.0

            self.log_message.emit(
                f"[config] reloaded from {CONFIG_FILE.name}: "
                f"unknown→search={FALLBACK_UNKNOWN_AS_SEARCH}, "
                f"plaintext→search={FALLBACK_PLAINTEXT_AS_SEARCH}, "
                f"home_on_end={HOME_ON_VIDEO_END}, "
                f"watchdog={OFF_SITE_WATCHDOG}, "
                f"block_live={BLOCK_LIVE_STREAMS}, "
                f"block_24h={BLOCK_UNDER_24H}, "
                f"screenshots={SCREENSHOT_ENABLED}, "
                f"checker={USE_HEADLESS_CHECKER}", "info")
        else:
            self.log_message.emit(
                f"[config] no {CONFIG_FILE.name} on disk — "
                f"keeping current values.", "info")

        return str(json.dumps({
            "fallback_unknown_as_search":  bool(FALLBACK_UNKNOWN_AS_SEARCH),
            "fallback_plaintext_as_search":bool(FALLBACK_PLAINTEXT_AS_SEARCH),
            "home_on_video_end":           bool(HOME_ON_VIDEO_END),
            "off_site_watchdog":           bool(OFF_SITE_WATCHDOG),
            "block_live_streams":          bool(BLOCK_LIVE_STREAMS),
            "block_under_24h":             bool(BLOCK_UNDER_24H),
            "screenshot_enabled":          bool(SCREENSHOT_ENABLED),
            "screenshot_delay":            float(SCREENSHOT_DELAY),
            "use_headless_checker":        bool(USE_HEADLESS_CHECKER),
        }))

    @Slot(str, result=bool)
    def save_config(self, json_str):
        global FALLBACK_UNKNOWN_AS_SEARCH, FALLBACK_PLAINTEXT_AS_SEARCH, \
               HOME_ON_VIDEO_END, OFF_SITE_WATCHDOG, \
               BLOCK_LIVE_STREAMS, BLOCK_UNDER_24H, \
               SCREENSHOT_ENABLED, SCREENSHOT_DELAY, USE_HEADLESS_CHECKER
        try:
            data = json.loads(json_str or "{}")
            if "fallback_unknown_as_search" in data:
                FALLBACK_UNKNOWN_AS_SEARCH = bool(data["fallback_unknown_as_search"])
            if "fallback_plaintext_as_search" in data:
                FALLBACK_PLAINTEXT_AS_SEARCH = bool(data["fallback_plaintext_as_search"])
            if "home_on_video_end" in data:
                HOME_ON_VIDEO_END = bool(data["home_on_video_end"])
            if "off_site_watchdog" in data:
                OFF_SITE_WATCHDOG = bool(data["off_site_watchdog"])
            if "block_live_streams" in data:
                BLOCK_LIVE_STREAMS = bool(data["block_live_streams"])
            if "block_under_24h" in data:
                BLOCK_UNDER_24H = bool(data["block_under_24h"])
            if "screenshot_enabled" in data:
                SCREENSHOT_ENABLED = bool(data["screenshot_enabled"])
            if "screenshot_delay" in data:
                try: SCREENSHOT_DELAY = float(data["screenshot_delay"])
                except Exception: pass
            if "use_headless_checker" in data:
                USE_HEADLESS_CHECKER = bool(data["use_headless_checker"])
            _save_json(CONFIG_FILE, {
                "fallback_unknown_as_search":  FALLBACK_UNKNOWN_AS_SEARCH,
                "fallback_plaintext_as_search":FALLBACK_PLAINTEXT_AS_SEARCH,
                "home_on_video_end":           HOME_ON_VIDEO_END,
                "off_site_watchdog":           OFF_SITE_WATCHDOG,
                "block_live_streams":          BLOCK_LIVE_STREAMS,
                "block_under_24h":             BLOCK_UNDER_24H,
                "screenshot_enabled":          SCREENSHOT_ENABLED,
                "screenshot_delay":            SCREENSHOT_DELAY,
                "use_headless_checker":        USE_HEADLESS_CHECKER,
            })
            self.log_message.emit(
                f"[config] saved: "
                f"unknown→search={FALLBACK_UNKNOWN_AS_SEARCH}, "
                f"plaintext→search={FALLBACK_PLAINTEXT_AS_SEARCH}, "
                f"home_on_end={HOME_ON_VIDEO_END}, "
                f"watchdog={OFF_SITE_WATCHDOG}, "
                f"block_live={BLOCK_LIVE_STREAMS}, "
                f"block_24h={BLOCK_UNDER_24H}, "
                f"screenshots={SCREENSHOT_ENABLED}, "
                f"checker={USE_HEADLESS_CHECKER}", "info")
            return True
        except Exception as e:
            self.log_message.emit(f"Config save failed: {e}", "err"); return False

    @Slot(result=str)
    def get_dep_info(self):
        state = _dep_state()
        deps = []
        for d in DEPS:
            deps.append({
                "key": d["key"], "module": d["module"], "package": d["package"],
                "required": bool(d["required"]),
                "description": d["description"],
                "installed": bool(state.get(d["key"])),
            })
        return str(json.dumps({"deps": deps}))

    @Slot()
    def install_deps_async(self):
        def run():
            state = _dep_state()
            missing = [d for d in DEPS if not state.get(d["key"])]
            self.deps_event.emit(json.dumps({"type": "log", "level": "info",
                "message": f"Installing {len(missing)} package(s)…"}))
            installed = 0; failed = 0
            for d in missing:
                self.deps_event.emit(json.dumps({"type": "log", "level": "info",
                    "message": f"pip install {d['package']} …"}))
                ok, out = _pip_install([d["package"]])
                if ok:
                    try:
                        importlib.invalidate_caches()
                        importlib.import_module(d["module"])
                        installed += 1
                        self.deps_event.emit(json.dumps({"type": "installed",
                            "package": d["package"]}))
                    except Exception as e:
                        failed += 1
                        self.deps_event.emit(json.dumps({"type": "failed",
                            "package": d["package"],
                            "error": f"import failed: {e}"}))
                else:
                    failed += 1
                    self.deps_event.emit(json.dumps({"type": "failed",
                        "package": d["package"], "error": out[-400:]}))
            self.deps_event.emit(json.dumps({"type": "done",
                "installed": installed, "failed": failed}))
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def clear_dep_prefs(self):
        _save_json(DEPS_FILE, {})

    def shutdown(self):
        try: self._pump.stop()
        except Exception: pass
        try: self.chat_stop.set()
        except Exception: pass
        try:
            if self.chat_reader and self.chat_reader.is_alive():
                self.chat_reader.join(timeout=2)
        except Exception: pass
        try: self.bot.stop()
        except Exception: pass
        try:
            if self.bot.is_alive():
                self.bot.join(timeout=3)
        except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenCUY — Modern Engine")
        self.resize(1280, 900)
        self.setMinimumSize(860, 600)
        self.setStyleSheet(
            "QMainWindow { background: #0d0b1a; }"
            "QToolTip { background: #1c1836; color: #f4f4ff; border: 1px solid #2a2547; }")

        self.bridge = Bridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        self.view = QWebEngineView()
        self.view.page().setWebChannel(self.channel)
        self.view.page().setBackgroundColor(QColor("#0d0b1a"))
        base_url = QUrl.fromLocalFile(str(HERE) + os.sep)
        self.view.setHtml(UI_HTML, base_url)
        self.setCentralWidget(self.view)
        self._build_tray()

    def _build_tray(self):
        pix = QPixmap(64, 64); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor("#8b5cf6"))); p.setPen(QPen(Qt.PenStyle.NoPen))
        p.drawEllipse(10, 10, 44, 44); p.end()
        self.tray = QSystemTrayIcon(QIcon(pix), self)
        self.tray.setToolTip("OpenCUY")
        menu = QMenu()
        show_act = QAction("Show / Hide", self)
        show_act.triggered.connect(self._toggle_window)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self._quit)
        menu.addAction(show_act); menu.addSeparator(); menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._toggle_window()
            if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _toggle_window(self):
        if self.isVisible(): self.hide()
        else: self.show(); self.raise_(); self.activateWindow()

    def _quit(self):
        try: self.bridge.shutdown()
        except Exception: pass
        QApplication.instance().quit()

    def closeEvent(self, e):
        if self.tray.isVisible():
            e.ignore(); self.hide()
        else:
            self._quit(); e.accept()


# ═══════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenCUY")
    app.setQuitOnLastWindowClosed(False)

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor("#0d0b1a"))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor("#f4f4ff"))
    pal.setColor(QPalette.ColorRole.Base,            QColor("#1c1836"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#141129"))
    pal.setColor(QPalette.ColorRole.Text,            QColor("#f4f4ff"))
    pal.setColor(QPalette.ColorRole.Button,          QColor("#1c1836"))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor("#8b5cf6"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#1c1836"))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor("#f4f4ff"))
    app.setPalette(pal)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()