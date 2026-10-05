#!/usr/bin/env python3
r"""
OpenCUY v3.5
============
Single-file build. yt-dlp is the only video metadata backend.

v3.5 is a bug-fix release on top of v3.4:
  * Screenshots: the capture function (_schedule_screenshot) was missing, so
    no screenshot was ever taken. Implemented.
  * Privileged requests (owner/mod/UI) were never marked privileged because
    queued tasks had 5 fields but the run loop expects 6. Mod-lock and the
    under-24h bypass could never engage. Fixed (thread-local flag).
  * !infscroll with a negative distance stopped instantly. Now scrolls up.
  * Filter tables: ad-phrase blob was invalid JSON (silently fell back) and
    the Rocholo pattern had a typo (l[0] -> l[o0]).
  * block_gaming / block_shorts toggles had no effect (paths always blocked).
  * No more second, unused headless Chrome launched by the URL checker.
  * Stale one-shot flag swallowed the next real "blocked" banner. Removed.
  * ScreenshotCleaner called a method that doesn't exist on it (archive
    count never refreshed); _archive_files deleted files that failed to zip;
    clear_screenshots reported "0 cleared" after archiving. Fixed.
  * UI thread could freeze: stats pushes queried chromedriver from the Qt
    thread. Driver info is now cached and refreshed off-thread.
  * SSE streams: keepalive/initial writes raced with push_*() on one socket.
  * Overlay script now skips sub-frames (its own iframes / YouTube embeds).
  * !changelanguage stacked hl= params; now replaces them and stays on YT.
  * _reload_until_playable never re-checked after its last reload.
  * !watchhome on the blanked home feed timed out noisily; now explains.
  * !speed/!volume/!playpause/!seek threw JS errors when no <video> exists.
  * Mod-lock log said "250ss"; PySide6/webengine installer re-prompted for
    the same package; JSON saves are now atomic; shutdown waits for Chrome.

Video metadata (age, live status, 360°/VR) can be detected by either the
rendered watch page DOM or the optional yt-dlp backend, selected in Config.

  • DOM mode — reads the rendered watch page for age/live/360 markers.
  • yt-dlp mode — extracts YouTube metadata directly without downloading media.

Metadata checks happen after navigation, so pre-flight click blocking
is not possible. Cuss-word title/channel blocking remains a separate
post-navigation check.
"""

import sys, os, re, json, shutil, signal, tempfile, threading, time, queue
import collections
import random
import importlib
import subprocess
import http.server
import socketserver
import socket
import mimetypes
import urllib.parse
import zipfile
from pathlib import Path
from fractions import Fraction

try:
    sys.set_int_max_str_digits(0)
except Exception:
    pass

HERE = Path(__file__).parent.resolve()
DEPS_FILE = HERE / "opencuy_deps.json"
CONFIG_FILE = HERE / "opencuy_config.json"
THEME_FILE = HERE / "opencuy_theme.json"
STATS_FILE = HERE / "opencuy_stats.json"
SCREENSHOT_DIR = HERE / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)
SCREENSHOT_ARCHIVE_DIR = HERE / "screenshots_archive"
SCREENSHOT_ARCHIVE_DIR.mkdir(exist_ok=True)

APP_TITLE = "OpenCUY v3.5"

PREFERRED_PORT = 7000

_PRINT_LOCK = threading.Lock()

def _plog(message, level="info"):
    try:
        ts = time.strftime("%H:%M:%S")
        tag = {"info": "INFO", "ok": " OK ", "warn": "WARN", "err": "ERR "}.get(
            level, "INFO")
        with _PRINT_LOCK:
            print(f"[{ts}] [{tag}] {message}", flush=True)
    except Exception:
        pass

# ─── Default config ─────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "version": 14,
    "behavior": {
        "home_on_video_end": True,
        "off_site_watchdog": True,
        "block_live_streams": True,
        "block_under_24h": True,
        "use_headless_checker": True,
        "fallback_unknown_as_search": True,
        "fallback_plaintext_as_search": True,
        "block_gaming": True,
        "block_shorts": True,
        "blank_home_feed": True,
        "detect_rocholo": True,
        "hide_sidebar": True,
        "hide_topbar": True,
        "block_360": True,
        "filter_home_feed": True,
        "theme_youtube_bg": True,
        "block_channel_featured": True,
        "mute_channel_autoplay": True,
        "probe_clicks": True,
        "mod_lock_videos": True,
        "dom_settle_seconds": 2.5,
        "archive_screenshots": True,
        "screenshot_retention_hours": 3,
    },
    "timing": {
        "headless_settle_seconds": 1.0,
        "screenshot_delay_seconds": 1.0,
        "video_retry_delay_seconds": 2.0,
    },
    "spam": {
        "ui_repeat_cap": 500,
        "chat_repeat_cap": 20,
    },
    "overlay": {
        "show_chat_iframe": True,
        "chat_iframe_mode": "iframe",
        "chat_iframe_width": 340,
        "chat_iframe_height": 220,
        "chat_iframe_bottom": 60,
        "chat_iframe_left": 14,
        "show_log_iframe": True,
        "log_iframe_mode": "iframe",
        "log_iframe_width": 340,
        "log_iframe_height": 220,
        "log_iframe_bottom": 60,
        "log_iframe_right": 14,
    },
    "transitions": {
        "tab_ms": 180,
        "tab_slide_px": 4,
    },
    "chat": {
        "prefix": "!",
    },
}

DEFAULT_THEME = {
    "name": "nexo",
    "image_path": "",
    "image_mode": "cover",
    "image_opacity": 0.35,
    "image_blur": 4,
    "image_dim": 0.72,
}

THEME_PALETTE = {
    "nexo":  {"bg": "#0d0b1a", "card": "#141129", "accent": "#8b5cf6",
              "text": "#f4f4ff", "dim": "#8b88b8"},
    "black": {"bg": "#000000", "card": "#0a0a0a", "accent": "#d4d4d4",
              "text": "#f0f0f0", "dim": "#9a9a9a"},
    "red":   {"bg": "#1a0606", "card": "#240808", "accent": "#ef4444",
              "text": "#ffeaea", "dim": "#c98a8a"},
    "blue":  {"bg": "#050f1f", "card": "#08182e", "accent": "#3b82f6",
              "text": "#e6f0ff", "dim": "#7ba0c9"},
    "green": {"bg": "#041409", "card": "#06210e", "accent": "#10b981",
              "text": "#e8ffef", "dim": "#7bc995"},
    "amber": {"bg": "#1a1000", "card": "#241800", "accent": "#f59e0b",
              "text": "#fff7e6", "dim": "#c9a566"},
    "pink":  {"bg": "#1a0614", "card": "#24081c", "accent": "#ec4899",
              "text": "#ffeaf7", "dim": "#c98ab8"},
    "light": {"bg": "#f4f4f8", "card": "#ffffff", "accent": "#6366f1",
              "text": "#1a1a2e", "dim": "#5a5a70"},
}


def _current_theme_palette():
    try:
        cfg = _load_json(THEME_FILE, None) or {}
        name = (cfg.get("name") or DEFAULT_THEME["name"]).lower()
    except Exception:
        name = DEFAULT_THEME["name"]
    return THEME_PALETTE.get(name, THEME_PALETTE[DEFAULT_THEME["name"]])


def _deep_merge(base, override):
    out = {}
    for k, v in base.items():
        if k in override and isinstance(v, dict) and isinstance(override[k], dict):
            out[k] = _deep_merge(v, override[k])
        elif k in override:
            out[k] = override[k]
        else:
            out[k] = v
    for k, v in override.items():
        if k not in out:
            out[k] = v
    return out


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        with self._lock:
            first_run = not self.path.exists()
            raw = {}
            if not first_run:
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        raw = json.load(f) or {}
                except Exception:
                    raw = {}
            self.data = _deep_merge(
                DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})
            if first_run:
                self.save()
                _plog(f"Created default config at {self.path}")
            else:
                if any(k not in raw for k in DEFAULT_CONFIG):
                    self.save()
            self.apply()

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
                os.replace(tmp, self.path)
            except Exception as e:
                _plog(f"Config save failed: {e}", "err")

    def reset_to_defaults(self):
        with self._lock:
            self.data = json.loads(json.dumps(DEFAULT_CONFIG))
            self.save()
            self.apply()

    def update_from_dict(self, incoming):
        with self._lock:
            self.data = _deep_merge(
                self.data, incoming if isinstance(incoming, dict) else {})
            self.save()
            self.apply()

    def set(self, section, key, value):
        with self._lock:
            self.data.setdefault(section, {})[key] = value
            self.save()
            self.apply()

    def apply(self):
        global HOME_ON_VIDEO_END, OFF_SITE_WATCHDOG, BLOCK_LIVE_STREAMS, \
               BLOCK_UNDER_24H, USE_HEADLESS_CHECKER, \
               FALLBACK_UNKNOWN_AS_SEARCH, FALLBACK_PLAINTEXT_AS_SEARCH, \
               BLOCK_GAMING, BLOCK_SHORTS, BLANK_HOME_FEED, \
               DETECT_ROCHOLO, HIDE_SIDEBAR, HIDE_TOPBAR, \
               BLOCK_360, FILTER_HOME_FEED, THEME_YOUTUBE_BG, \
               BLOCK_CHANNEL_FEATURED, MUTE_CHANNEL_AUTOPLAY, \
               PROBE_CLICKS, MOD_LOCK_VIDEOS, \
               DOM_SETTLE_SECONDS, ARCHIVE_SCREENSHOTS, \
               SCREENSHOT_RETENTION_HOURS, \
               SCREENSHOT_DELAY, VIDEO_RETRY_DELAY, \
               UI_REPEAT_CAP, CHAT_REPEAT_CAP, CHAT_COMMAND_PREFIX, \
               HEADLESS_SETTLE, SHOW_CHAT_IFRAME, \
               CHAT_IFRAME_W, CHAT_IFRAME_H, CHAT_IFRAME_BOTTOM, \
               CHAT_IFRAME_LEFT, CHAT_IFRAME_MODE, \
               SHOW_LOG_IFRAME, LOG_IFRAME_W, LOG_IFRAME_H, \
               LOG_IFRAME_BOTTOM, LOG_IFRAME_RIGHT, LOG_IFRAME_MODE, \
               TAB_TRANS_MS, TAB_SLIDE_PX
        b = self.data.get("behavior", {})
        t = self.data.get("timing", {})
        s = self.data.get("spam", {})
        o = self.data.get("overlay", {})
        tr = self.data.get("transitions", {})
        c = self.data.get("chat", {})

        HOME_ON_VIDEO_END          = bool(b.get("home_on_video_end", True))
        OFF_SITE_WATCHDOG          = bool(b.get("off_site_watchdog", True))
        BLOCK_LIVE_STREAMS         = bool(b.get("block_live_streams", True))
        BLOCK_UNDER_24H            = bool(b.get("block_under_24h", True))
        USE_HEADLESS_CHECKER       = bool(b.get("use_headless_checker", True))
        FALLBACK_UNKNOWN_AS_SEARCH = bool(b.get("fallback_unknown_as_search", True))
        FALLBACK_PLAINTEXT_AS_SEARCH = bool(b.get("fallback_plaintext_as_search", True))
        BLOCK_GAMING               = bool(b.get("block_gaming", True))
        BLOCK_SHORTS               = bool(b.get("block_shorts", True))
        BLANK_HOME_FEED            = bool(b.get("blank_home_feed", True))
        DETECT_ROCHOLO             = bool(b.get("detect_rocholo", True))
        HIDE_SIDEBAR               = bool(b.get("hide_sidebar", True))
        HIDE_TOPBAR                = bool(b.get("hide_topbar", True))
        BLOCK_360                  = bool(b.get("block_360", True))
        FILTER_HOME_FEED           = bool(b.get("filter_home_feed", True))
        THEME_YOUTUBE_BG           = bool(b.get("theme_youtube_bg", True))
        BLOCK_CHANNEL_FEATURED     = bool(b.get("block_channel_featured", True))
        MUTE_CHANNEL_AUTOPLAY      = bool(b.get("mute_channel_autoplay", True))
        PROBE_CLICKS               = bool(b.get("probe_clicks", True))
        MOD_LOCK_VIDEOS            = bool(b.get("mod_lock_videos", True))
        try: DOM_SETTLE_SECONDS = max(0.0, min(30.0, float(
            b.get("dom_settle_seconds", 2.5))))
        except Exception: DOM_SETTLE_SECONDS = 2.5
        ARCHIVE_SCREENSHOTS        = bool(b.get("archive_screenshots", True))
        try: SCREENSHOT_RETENTION_HOURS = max(1, min(168, int(
            b.get("screenshot_retention_hours", 3))))
        except Exception: SCREENSHOT_RETENTION_HOURS = 3

        try: HEADLESS_SETTLE = float(t.get("headless_settle_seconds", 1.0))
        except Exception: HEADLESS_SETTLE = 1.0
        try: SCREENSHOT_DELAY = float(t.get("screenshot_delay_seconds", 1.0))
        except Exception: SCREENSHOT_DELAY = 1.0
        try: VIDEO_RETRY_DELAY = float(t.get("video_retry_delay_seconds", 2.0))
        except Exception: VIDEO_RETRY_DELAY = 2.0

        try: UI_REPEAT_CAP = int(s.get("ui_repeat_cap", 500))
        except Exception: UI_REPEAT_CAP = 500
        try: CHAT_REPEAT_CAP = int(s.get("chat_repeat_cap", 20))
        except Exception: CHAT_REPEAT_CAP = 20

        SHOW_CHAT_IFRAME = bool(o.get("show_chat_iframe", True))
        try: CHAT_IFRAME_W = int(o.get("chat_iframe_width", 340))
        except Exception: CHAT_IFRAME_W = 340
        try: CHAT_IFRAME_H = int(o.get("chat_iframe_height", 220))
        except Exception: CHAT_IFRAME_H = 220
        try: CHAT_IFRAME_BOTTOM = int(o.get("chat_iframe_bottom", 60))
        except Exception: CHAT_IFRAME_BOTTOM = 60
        try: CHAT_IFRAME_LEFT = int(o.get("chat_iframe_left", 14))
        except Exception: CHAT_IFRAME_LEFT = 14
        mode = str(o.get("chat_iframe_mode", "iframe") or "iframe").lower()
        CHAT_IFRAME_MODE = mode if mode in ("iframe", "pill", "off") else "iframe"

        SHOW_LOG_IFRAME = bool(o.get("show_log_iframe", True))
        try: LOG_IFRAME_W = int(o.get("log_iframe_width", 340))
        except Exception: LOG_IFRAME_W = 340
        try: LOG_IFRAME_H = int(o.get("log_iframe_height", 220))
        except Exception: LOG_IFRAME_H = 220
        try: LOG_IFRAME_BOTTOM = int(o.get("log_iframe_bottom", 60))
        except Exception: LOG_IFRAME_BOTTOM = 60
        try: LOG_IFRAME_RIGHT = int(o.get("log_iframe_right", 14))
        except Exception: LOG_IFRAME_RIGHT = 14
        lmode = str(o.get("log_iframe_mode", "iframe") or "iframe").lower()
        LOG_IFRAME_MODE = lmode if lmode in ("iframe", "off") else "iframe"

        try: TAB_TRANS_MS = int(tr.get("tab_ms", 180))
        except Exception: TAB_TRANS_MS = 180
        try: TAB_SLIDE_PX = int(tr.get("tab_slide_px", 4))
        except Exception: TAB_SLIDE_PX = 4

        CHAT_COMMAND_PREFIX = str(c.get("prefix", "!") or "!")


CONFIG = Config(CONFIG_FILE)

BAIL_TO_BLANK_ON_AD = True
VIDEO_RETRY_ATTEMPTS = 3
VIDEO_RETRY_DELAY = 2.0
HOME_ON_VIDEO_END = True
OFF_SITE_WATCHDOG = True
BLOCK_LIVE_STREAMS = True
BLOCK_UNDER_24H = True
USE_HEADLESS_CHECKER = True
BLOCK_GAMING = True
BLOCK_SHORTS = True
BLANK_HOME_FEED = True
DETECT_ROCHOLO = True
HIDE_SIDEBAR = True
HIDE_TOPBAR = True
BLOCK_360 = True
FILTER_HOME_FEED = True
THEME_YOUTUBE_BG = True
BLOCK_CHANNEL_FEATURED = True
MUTE_CHANNEL_AUTOPLAY = True
PROBE_CLICKS = True
MOD_LOCK_VIDEOS = True
DOM_SETTLE_SECONDS = 2.5
ARCHIVE_SCREENSHOTS = True
SCREENSHOT_RETENTION_HOURS = 3
SCREENSHOT_ENABLED = True
SCREENSHOT_DELAY = 1.0
HEADLESS_SETTLE = 1.0
DISABLE_COOKIES = False
FALLBACK_UNKNOWN_AS_SEARCH = True
FALLBACK_PLAINTEXT_AS_SEARCH = True
UI_REPEAT_CAP = 500
CHAT_REPEAT_CAP = 20
SHOW_CHAT_IFRAME = True
CHAT_IFRAME_W = 340
CHAT_IFRAME_H = 220
CHAT_IFRAME_BOTTOM = 60
CHAT_IFRAME_LEFT = 14
CHAT_IFRAME_MODE = "iframe"
SHOW_LOG_IFRAME = True
LOG_IFRAME_W = 340
LOG_IFRAME_H = 220
LOG_IFRAME_BOTTOM = 60
LOG_IFRAME_RIGHT = 14
LOG_IFRAME_MODE = "iframe"
CHAT_COMMAND_PREFIX = "!"
TAB_TRANS_MS = 180
TAB_SLIDE_PX = 4

CONFIG.apply()


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


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    """Atomic write (tmp file + replace). Returns True on success."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        _plog(f"Could not save {path.name}: {e}", "warn")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


# ── Encoded filter tables ───────────────────────────────────────────────
import base64 as _b64_mod
import json as _json_mod


def _b64_decode(text):
    try:
        return _b64_mod.b64decode(text).decode("utf-8")
    except Exception:
        return ""


_FILTER_AD_B64 = (
    "WyJmcmVlXFxzK3dpdGhcXHMrYWRzIiwgImZyZWVcXHMrd2l0aFxccythZH"
    "ZlcnRpc2VtZW50cyIsICJhZFstIF0/c3VwcG9ydGVkIiwgInN1cHBvcnRl"
    "ZFxccytieVxccythZHMiLCAid2F0Y2hcXHMrZnJlZVxccyt3aXRoXFxzK2"
    "FkcyIsICJmcmVlXFxzK1xcKHdpdGhcXHMrYWRzXFwpIiwgImNvbnRhaW5z"
    "XFxzK2FkcyIsICJhZFstXFxzXT9zdXBwb3J0ZWRcXHMrZnJlZSIsICJmcm"
    "VlW1xcc1xcdTAwYTBdK3dpdGhbXFxzXFx1MDBhMF0rYWRzIl0="
)

_FILTER_CUSS_B64 = (
    "WyJmdWNrIiwgImZ1Y2tzIiwgImZ1Y2tlZCIsICJmdWNraW5nIiwgImZ1"
    "Y2tlciIsICJmdWNrZXJzIiwgIm1vdGhlcmZ1Y2siLCAibW90aGVy"
    "ZnVja3MiLCAibW90aGVyZnVja2VkIiwgIm1vdGhlcmZ1Y2tpbmci"
    "LCAibW90aGVyZnVja2VyIiwgIm1vdGhlcmZ1Y2tlcnMiLCAidW5m"
    "dWNrIiwgInVuZnVja2luZyIsICJzaGl0IiwgInNoaXRzIiwgInNo"
    "aXR0ZWQiLCAic2hpdHRpbmciLCAic2hpdHR5IiwgImJ1bGxzaGl0"
    "IiwgImJ1bGxzaGl0cyIsICJidWxsc2hpdHRpbmciLCAiYnVsbHNo"
    "aXR0ZXIiLCAiaG9yc2VzaGl0IiwgImJpdGNoIiwgImJpdGNoZXMi"
    "LCAiYml0Y2hlZCIsICJiaXRjaGluZyIsICJiaXRjaHkiLCAic29u"
    "b2ZhYml0Y2giLCAiYXNzaG9sZSIsICJhc3Nob2xlcyIsICJjdW50"
    "IiwgImN1bnRzIiwgInR3YXQiLCAidHdhdHMiLCAid2Fua2VyIiwg"
    "IndhbmtlcnMiLCAiYmFzdGFyZCIsICJiYXN0YXJkcyIsICJkaWNr"
    "aGVhZCIsICJkaWNraGVhZHMiLCAiY29ja3N1Y2tlciIsICJjb2Nr"
    "c3Vja2VycyJd"
)

_FILTER_ROCHOLO_B64 = (
    "eyJmaXJzdCI6ICJyW28wXWNoW28wXWxbbzBdIiwgImxhc3QiOiAialthZW"
    "lvdV12W2kxbF1uW2FlaW91XT9yPyJ9"
)

_FILTER_GATE_B64 = "eWVz"


def _filter_gate():
    try:
        return _b64_decode(_FILTER_GATE_B64)
    except Exception:
        return "yes"

_FILTER_ENABLED = (_filter_gate() == "yes")


AD_TRIGGER_PHRASES = []
try:
    _parsed = _json_mod.loads(_b64_decode(_FILTER_AD_B64) or "[]")
    if isinstance(_parsed, list):
        AD_TRIGGER_PHRASES = [str(x) for x in _parsed if x]
except Exception:
    AD_TRIGGER_PHRASES = []
if not AD_TRIGGER_PHRASES:
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
    if not _FILTER_ENABLED:
        return []
    matches = AD_PATTERN.findall(text)
    results = []
    for m in matches:
        if isinstance(m, tuple):
            results.extend([x for x in m if x])
        else:
            results.append(m)
    return list(set(results))


_ROCHOLO_FIRST = r"r[o0]ch[o0]l[o0]"
_ROCHOLO_LAST  = r"j[aeiou]v[i1l]n[aeiou]?r?"
try:
    _parsed = _json_mod.loads(_b64_decode(_FILTER_ROCHOLO_B64) or "{}")
    if isinstance(_parsed, dict):
        if _parsed.get("first"):
            _ROCHOLO_FIRST = str(_parsed["first"])
        if _parsed.get("last"):
            _ROCHOLO_LAST = str(_parsed["last"])
except Exception:
    pass

ROCHOLO_PATTERN = re.compile(
    r"(?:"
        rf"{_ROCHOLO_FIRST}[\s._\-]*{_ROCHOLO_LAST}"
        r"|"
        rf"{_ROCHOLO_LAST}[\s._\-]*{_ROCHOLO_FIRST}"
        r"|"
        r"rocholo"
        r"|"
        r"rockholo(?:\s+javinar)?"
        r"|"
        r"rocholo\s*chan"
    r")",
    re.IGNORECASE,
)


def detect_rocholo(text):
    if not text:
        return False
    if not _FILTER_ENABLED:
        return False
    try:
        return bool(ROCHOLO_PATTERN.search(text))
    except Exception:
        return False


_CUSS_WORDS = []
try:
    _parsed = _json_mod.loads(_b64_decode(_FILTER_CUSS_B64) or "[]")
    if isinstance(_parsed, list):
        _CUSS_WORDS = [str(x) for x in _parsed if x]
except Exception:
    _CUSS_WORDS = []
if not _CUSS_WORDS:
    _CUSS_WORDS = [
        "fuck", "fucks", "fucked", "fucking", "fucker", "fuckers",
        "motherfuck", "motherfucks", "motherfucked", "motherfucking",
        "motherfucker", "motherfuckers",
        "unfuck", "unfucking",
        "shit", "shits", "shitted", "shitting", "shitty",
        "bullshit", "bullshits", "bullshitting", "bullshitter",
        "horseshit",
        "bitch", "bitches", "bitched", "bitching", "bitchy",
        "sonofabitch",
        "asshole", "assholes",
        "cunt", "cunts",
        "twat", "twats",
        "wanker", "wankers",
        "bastard", "bastards",
        "dickhead", "dickheads",
        "cocksucker", "cocksuckers",
    ]
_CUSS_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _CUSS_WORDS) + r")\b",
    re.IGNORECASE,
)


def detect_cuss(text):
    if not text:
        return False
    if not _FILTER_ENABLED:
        return False
    try:
        return bool(_CUSS_PATTERN.search(text))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  LOG / CHAT / HOME-DASH PAGES
# ═══════════════════════════════════════════════════════════════════════════
LOG_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>OpenCUY Log</title>
<style>
  html,body{margin:0;padding:0;height:100%;background:#08061a;color:#34d399;
    overflow:hidden;font-family:Consolas,'Courier New',monospace;font-size:12.5px}
  #log{height:100%;overflow-y:auto;padding:10px 14px;line-height:1.55}
  .line{margin-bottom:3px;word-break:break-word}
  .t{color:#5d5a85;margin-right:6px}
  .info{color:#a78bfa}
  .ok{color:#34d399}
  .warn{color:#fbbf24}
  .err{color:#f43f5e}
</style></head>
<body><div id="log"></div>
<script>
(function(){
  var box=document.getElementById('log');
  var auto=true;
  box.addEventListener('scroll',function(){
    auto = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;
  });
  function mkText(tag, cls, text){
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = text;
    return el;
  }
  function add(msg, level, ts){
    var d = ts ? new Date(ts * 1000) : new Date();
    var t = d.toLocaleTimeString();
    var div = document.createElement('div');
    div.className = 'line';
    div.appendChild(mkText('span', 't', '[' + t + ']'));
    div.appendChild(mkText('span', level || '', String(msg)));
    box.appendChild(div);
    if (auto) box.scrollTop = box.scrollHeight;
  }
  function clearBox(){
    while (box.firstChild) box.removeChild(box.firstChild);
  }
  try {
    var es = new EventSource('/log-stream');
    es.addEventListener('replay', function(e){
      try {
        var arr = JSON.parse(e.data);
        clearBox();
        for (var i = 0; i < arr.length; i++) {
          add(arr[i].msg || '', arr[i].level || '', arr[i].ts);
        }
      } catch(err){}
    });
    es.onmessage = function(e){
      try { var ev = JSON.parse(e.data);
            add(ev.msg || '', ev.level || '', ev.ts); }
      catch(err){}
    };
  } catch(e){}
})();
</script></body></html>"""

CHAT_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>OpenCUY Chat</title>
<style>
  html,body{margin:0;padding:0;height:100%;background:#08061a;color:#f4f4ff;
    overflow:hidden;font-family:'Segoe UI',Tahoma,sans-serif;font-size:13px}
  #log{height:100%;overflow-y:auto;padding:10px 14px;line-height:1.55}
  .line{margin-bottom:3px;word-break:break-word}
  .t{color:#5d5a85;margin-right:6px;font-family:Consolas,monospace;font-size:11px}
  .author{color:#a78bfa;font-weight:600;margin-right:6px}
  .msg{color:#f4f4ff}
  .cmd{color:#34d399;font-style:italic}
  .sys{color:#fbbf24}
  .err{color:#f43f5e}
</style></head>
<body><div id="log"></div>
<script>
(function(){
  var box=document.getElementById('log');
  var auto=true;
  box.addEventListener('scroll',function(){
    auto = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;
  });
  function mkText(tag, cls, text){
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = text;
    return el;
  }
  function add(ev){
    var d = (ev && ev.ts) ? new Date(ev.ts * 1000) : new Date();
    var t = d.toLocaleTimeString();
    var div = document.createElement('div');
    div.className = 'line';
    div.appendChild(mkText('span', 't', '[' + t + ']'));
    if (ev.kind === 'chat') {
      div.appendChild(mkText('span', 'author', (ev.author || '?') + ':'));
      div.appendChild(mkText('span', 'msg', ev.message || ''));
    } else if (ev.kind === 'cmd') {
      div.appendChild(mkText('span', 'cmd', '↳ ' + (ev.message || '')));
    } else if (ev.kind === 'sys') {
      div.appendChild(mkText('span', 'sys', '* ' + (ev.message || '')));
    } else if (ev.kind === 'err') {
      div.appendChild(mkText('span', 'err', '[err] ' + (ev.message || '')));
    } else {
      div.appendChild(mkText('span', '', ev.message || ''));
    }
    box.appendChild(div);
    if (auto) box.scrollTop = box.scrollHeight;
  }
  function clearBox(){
    while (box.firstChild) box.removeChild(box.firstChild);
  }
  try {
    var es = new EventSource('/chat-stream');
    es.addEventListener('replay', function(e){
      try {
        var arr = JSON.parse(e.data);
        clearBox();
        for (var i = 0; i < arr.length; i++) add(arr[i]);
      } catch(err){}
    });
    es.onmessage = function(e){
      try { add(JSON.parse(e.data)); } catch(err){}
    };
  } catch(e){}
})();
</script></body></html>"""

HOME_DASH_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>OpenCUY Dashboard</title>
<style>
  html,body{margin:0;padding:0;height:100%;background:#0d0b1a;color:#f4f4ff;
    overflow:hidden;font-family:'Segoe UI',Tahoma,sans-serif;font-size:13px}
  #head{display:flex;align-items:center;justify-content:space-between;
    padding:14px 24px;background:#141129;border-bottom:1px solid #2a2547;
    flex-shrink:0}
  #brand{display:flex;align-items:center;gap:12px}
  #logo{width:22px;height:22px;border-radius:50%;background:#8b5cf6;
    box-shadow:0 0 16px #8b5cf6}
  #brand h1{margin:0;font-size:15px;font-weight:600}
  #conn{display:flex;align-items:center;gap:8px;font-size:12px;color:#8b88b8}
  #dot{width:8px;height:8px;border-radius:50%;background:#5d5a85}
  #body{display:flex;gap:16px;padding:16px;height:calc(100vh - 55px);
    box-sizing:border-box;overflow:hidden}
  #left{flex:1;min-width:0;display:flex;flex-direction:column;gap:14px;
    overflow-y:auto;padding-right:4px}
  #right{flex:1;min-width:0;display:flex;flex-direction:column;
    background:#141129;border:1px solid #2a2547;border-radius:12px;
    overflow:hidden}
  .panel-title{padding:12px 16px;font-size:12px;font-weight:700;
    text-transform:uppercase;letter-spacing:0.6px;color:#a78bfa;
    flex-shrink:0}
  #left .panel-title{background:#141129;border:1px solid #2a2547;
    border-radius:12px;padding:12px 16px}
  #right .panel-title{border-bottom:1px solid #2a2547}
  #grid{display:grid;
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
  .tile{background:#1c1836;border:1px solid #2a2547;border-radius:12px;
    padding:14px 16px}
  .tile .lbl{font-size:11.5px;color:#8b88b8;text-transform:uppercase;
    letter-spacing:0.5px;margin-bottom:6px}
  .tile .val{font-size:22px;font-weight:700;font-family:Consolas,monospace;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #status{background:#141129;border:1px solid #2a2547;border-radius:12px;
    padding:16px;font-size:12.5px;line-height:1.75;color:#8b88b8}
  #status .sh{font-size:12px;font-weight:700;text-transform:uppercase;
    letter-spacing:0.6px;color:#a78bfa;margin-bottom:10px}
  #status b{color:#a78bfa}
  #logFrame{flex:1;width:100%;border:0;background:#08061a}
</style></head>
<body>
<div id="head">
  <div id="brand"><div id="logo"></div>
    <h1>OpenCUY &mdash; Home Dashboard</h1></div>
  <div id="conn"><span id="connText">connecting&hellip;</span>
    <span id="dot"></span></div>
</div>
<div id="body">
  <div id="left">
    <div class="panel-title">Live Stats</div>
    <div id="grid"></div>
    <div id="status"><div class="sh">Status</div><div id="foot"></div></div>
  </div>
  <div id="right">
    <div class="panel-title">Bot / Filter Log</div>
    <iframe id="logFrame" src="/log-view" scrolling="auto" frameborder="0"></iframe>
  </div>
</div>
<script>
(function(){
  var TILES = [
    ['runtime','Runtime','#a78bfa'],
    ['status','Status','#34d399'],
    ['videos_loaded','Videos loaded','#34d399'],
    ['searches','Searches','#60a5fa'],
    ['shots','Screenshots','#fbbf24'],
    ['rocholo','Rocholo Javinar seen','#a78bfa'],
    ['chan_bypass','Channel bypass blocked','#fb7185'],
    ['ads_blocked','Ads blocked','#f43f5e'],
    ['live_blocked','Live blocked','#fb7185'],
    ['under24h','Under 24h blocked','#f97316'],
    ['cuss_blocked','Cuss blocked','#f43f5e'],
    ['dom_timeout','DOM settle timeouts','#f59e0b'],
    ['mod_lock','Mod-lock blocks','#fbbf24'],
    ['commands','Commands','#22d3ee'],
    ['chat','Chat messages','#c084fc'],
  ];
  var grid = document.getElementById('grid');
  var foot = document.getElementById('foot');
  var connText = document.getElementById('connText');
  var dot = document.getElementById('dot');
  function mkText(tag, cls, text){
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = text;
    return el;
  }
  TILES.forEach(function(t){
    var card = document.createElement('div'); card.className='tile';
    var lbl = mkText('div','lbl',t[1]); card.appendChild(lbl);
    var val = document.createElement('div'); val.className='val';
    val.id = 'v_' + t[0]; val.textContent = '0';
    val.style.color = t[2];
    card.appendChild(val);
    grid.appendChild(card);
  });
  var set = function(id, v){
    var el = document.getElementById('v_' + id);
    if (el) el.textContent = String(v);
  };
  var fmtRuntime = function(sec){
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60),
        s2 = sec%60;
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + s2 + 's';
    return s2 + 's';
  };
  var preferTotal = function(s, key){
    var t = s['total_' + key];
    if (t !== undefined && t !== null) return t;
    return s[key] || 0;
  };
  var render = function(s){
    if (!s) return;
    set('runtime', fmtRuntime(s.runtime_seconds));
    set('status', s.status || 'idle');
    set('videos_loaded', preferTotal(s, 'videos_loaded'));
    set('searches', preferTotal(s, 'searches'));
    set('shots', s.shots_on_disk || 0);
    set('rocholo', preferTotal(s, 'rocholo_detected'));
    set('chan_bypass', preferTotal(s, 'channel_bypass_blocked'));
    set('ads_blocked', preferTotal(s, 'ads_blocked'));
    set('live_blocked', preferTotal(s, 'live_blocked'));
    set('under24h', preferTotal(s, 'under24h_blocked'));
    set('cuss_blocked', preferTotal(s, 'cuss_blocked'));
    set('dom_timeout', preferTotal(s, 'dom_timeout_blocked'));
    set('mod_lock', preferTotal(s, 'mod_lock_blocked'));
    set('commands', preferTotal(s, 'commands'));
    set('chat', preferTotal(s, 'chat_messages'));
    foot.textContent = '';
    var mk = function(label, text){
      var row = document.createElement('div');
      var b = document.createElement('b');
      b.textContent = label + ': '; row.appendChild(b);
      var t = document.createElement('span');
      t.textContent = text || '\u2014';
      row.appendChild(t);
      return row;
    };
    foot.appendChild(mk('Last event', s.last_event));
    foot.appendChild(mk('Page title', s.current_title));
    foot.appendChild(mk('URL', (s.current_url || '').slice(0, 160)));
    connText.textContent = 'connected';
    dot.style.background = '#34d399';
  };
  try {
    var es = new EventSource('/stats-events');
    es.onmessage = function(e){
      try { render(JSON.parse(e.data)); } catch(err){}
    };
    es.onerror = function(){
      connText.textContent = 'reconnecting\u2026';
      dot.style.background = '#f43f5e';
    };
  } catch(e){}
})();
</script></body></html>"""


class _OverlayTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")


class OverlayServer:
    def __init__(self, out_queue):
        self.q = out_queue
        self.httpd = None
        self.thread = None
        self.port = None
        self.ui_html = ""
        self.bg_image_path = ""
        self._clients = set()
        self._chat_clients = set()
        self._log_clients = set()
        self._chatview_clients = set()
        self._warn_clients = set()
        self._shots_clients = set()
        self._stats_clients = set()
        self._theme_clients = set()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._last_payload = {"hidden": True}
        self._last_chat_payload = {"message": "Chat: idle"}
        self._last_chatview_payload = {"kind": "sys", "message": "Chat: idle"}
        self._last_warn_payload = {"hidden": True, "text": ""}
        self._last_shots_payload = {"count": 0}
        self._last_theme_payload = {
            "name": "nexo",
            "palette": {"bg": "#0d0b1a", "card": "#141129",
                        "accent": "#8b5cf6", "text": "#f4f4ff",
                        "dim": "#8b88b8"},
        }
        self._last_stats_payload = {
            "started_at": time.time(),
            "runtime_seconds": 0,
            "status": "starting",
            "videos_loaded": 0,
            "searches": 0,
            "ads_blocked": 0,
            "live_blocked": 0,
            "under24h_blocked": 0,
            "commands": 0,
            "chat_messages": 0,
            "shots_taken": 0,
            "shots_on_disk": 0,
            "rocholo_detected": 0,
            "channel_bypass_blocked": 0,
            "cuss_blocked": 0,
            "dom_timeout_blocked": 0,
            "mod_lock_blocked": 0,
            "last_event": "",
            "current_title": "",
            "current_url": "",
        }
        self._history_lock = threading.Lock()
        self._log_history = collections.deque(maxlen=500)
        self._chat_history = collections.deque(maxlen=500)
        self._stats_history = collections.deque(maxlen=2)
        self._probe_click_cb = None
        self._last_painted_theme_name = None
        self._last_painted_palette = None

    def start(self):
        with self._start_lock:
            if self.httpd:
                return self.port
            try:
                th = _load_json(THEME_FILE, {}) or {}
                self.bg_image_path = th.get("image_path") or ""
            except Exception:
                self.bg_image_path = ""
            handler = self._make_handler()
            last_err = None
            for candidate in (PREFERRED_PORT, 0):
                try:
                    httpd = _OverlayTCPServer(("127.0.0.1", candidate), handler)
                except OSError as e:
                    last_err = e
                    continue
                self.httpd = httpd
                self.port = httpd.server_address[1]
                break
            if self.httpd is None:
                self.q.put({"kind": "log", "level": "err",
                            "message": f"[overlay] server failed: {last_err}"})
                return None
            self.thread = threading.Thread(target=self.httpd.serve_forever,
                                           daemon=True)
            self.thread.start()
            self.q.put({"kind": "log", "level": "ok",
                        "message": f"[overlay] Server on http://127.0.0.1:{self.port}"})
            return self.port

    def stop(self):
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass
        self.httpd = None

    def push(self, payload):
        self._last_payload = dict(payload or {})
        line = ("data: " + json.dumps(self._last_payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._clients.discard(w)

    def push_chat(self, payload):
        self._last_chat_payload = dict(payload or {})
        line = ("data: " + json.dumps(self._last_chat_payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._chat_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._chat_clients.discard(w)

    def push_log(self, msg, level="info"):
        entry = {"msg": msg, "level": level, "ts": time.time()}
        try:
            with self._history_lock:
                self._log_history.append(entry)
        except Exception:
            pass
        line = ("data: " + json.dumps(entry) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._log_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._log_clients.discard(w)

    def push_chat_view(self, payload):
        payload = dict(payload or {})
        payload.setdefault("ts", time.time())
        self._last_chatview_payload = payload
        try:
            with self._history_lock:
                self._chat_history.append(payload)
        except Exception:
            pass
        line = ("data: " + json.dumps(payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._chatview_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._chatview_clients.discard(w)

    def push_warn(self, text, kind="warn"):
        payload = {"hidden": not bool(text), "text": text or "", "kind": kind}
        self._last_warn_payload = payload
        line = ("data: " + json.dumps(payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._warn_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._warn_clients.discard(w)

    def push_shots(self, count):
        payload = {"count": int(count)}
        self._last_shots_payload = payload
        line = ("data: " + json.dumps(payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._shots_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._shots_clients.discard(w)

    def push_theme(self, payload):
        payload = dict(payload or {})
        self._last_theme_payload = payload
        line = ("data: " + json.dumps(payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._theme_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._theme_clients.discard(w)

    def push_stats(self, payload):
        self._last_stats_payload = dict(payload or {})
        try:
            with self._history_lock:
                self._stats_history.clear()
                self._stats_history.append(self._last_stats_payload)
        except Exception:
            pass
        line = ("data: " + json.dumps(self._last_stats_payload) + "\n\n").encode()
        with self._lock:
            dead = []
            for wfile in self._stats_clients:
                try: wfile.write(line); wfile.flush()
                except Exception: dead.append(wfile)
            for w in dead: self._stats_clients.discard(w)

    def get_log_history(self):
        try:
            with self._history_lock:
                return list(self._log_history)
        except Exception:
            return []

    def get_chat_history(self):
        try:
            with self._history_lock:
                return list(self._chat_history)
        except Exception:
            return []

    def _make_handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except (ConnectionAbortedError, ConnectionResetError,
                        BrokenPipeError, OSError):
                    self.close_connection = True

            def _cors_headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def _html(self, body):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.end_headers()
                try: self.wfile.write(body)
                except Exception: pass

            def _sse(self, initial, client_set):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._cors_headers()
                self.end_headers()
                try:
                    # Write the initial frame and register under the same
                    # lock push_*() uses, so bytes never interleave.
                    with server._lock:
                        if initial is not None:
                            line = ("data: " + json.dumps(initial)
                                    + "\n\n").encode()
                            self.wfile.write(line); self.wfile.flush()
                        client_set.add(self.wfile)
                    while True:
                        time.sleep(15)
                        with server._lock:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except Exception:
                    pass
                finally:
                    with server._lock:
                        client_set.discard(self.wfile)

            def _sse_with_replay(self, replay_entries, client_set):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._cors_headers()
                self.end_headers()
                try:
                    payload = json.dumps(list(replay_entries or []))
                    with server._lock:
                        self.wfile.write(("event: replay\ndata: "
                                          + payload + "\n\n").encode())
                        self.wfile.flush()
                        client_set.add(self.wfile)
                    while True:
                        time.sleep(15)
                        with server._lock:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except Exception:
                    pass
                finally:
                    with server._lock:
                        client_set.discard(self.wfile)

            def _serve_probe_click(self):
                body = json.dumps({"blocked": False, "reason": ""}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.end_headers()
                try: self.wfile.write(body)
                except Exception: pass

            def _serve_bg_image(self):
                p = server.bg_image_path
                if not p or not os.path.isfile(p):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self._cors_headers()
                    self.end_headers()
                    return
                try:
                    with open(p, "rb") as f:
                        data = f.read()
                except Exception:
                    self.send_response(500)
                    self.send_header("Content-Length", "0")
                    self._cors_headers()
                    self.end_headers()
                    return
                ctype, _ = mimetypes.guess_type(p)
                if not ctype:
                    ctype = "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.end_headers()
                try: self.wfile.write(data)
                except Exception: pass

            def do_OPTIONS(self):
                try:
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Access-Control-Allow-Methods",
                                     "GET, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "*")
                    self.send_header("Access-Control-Allow-Private-Network",
                                     "true")
                    self.send_header("Access-Control-Max-Age", "86400")
                    self.end_headers()
                except Exception:
                    pass

            def do_GET(self):
                try:
                    self._handle_get()
                except (ConnectionAbortedError, ConnectionResetError,
                        BrokenPipeError, OSError):
                    pass

            def _handle_get(self):
                path = self.path.split("?", 1)[0].rstrip("/") or "/"
                if path == "/events":
                    self._sse(server._last_payload, server._clients); return
                if path == "/chat-events":
                    self._sse(server._last_chat_payload, server._chat_clients); return
                if path == "/chat-view-events":
                    self._sse(server._last_chatview_payload,
                              server._chatview_clients); return
                if path == "/warn-events":
                    self._sse(server._last_warn_payload,
                              server._warn_clients); return
                if path == "/shots-events":
                    self._sse(server._last_shots_payload,
                              server._shots_clients); return
                if path == "/theme-events":
                    self._sse(server._last_theme_payload,
                              server._theme_clients); return
                if path == "/stats-events":
                    self._sse(server._last_stats_payload,
                              server._stats_clients); return
                if path == "/log-stream":
                    self._sse_with_replay(server.get_log_history(),
                                          server._log_clients); return
                if path == "/chat-stream":
                    self._sse_with_replay(server.get_chat_history(),
                                          server._chatview_clients); return
                if path == "/probe-click":
                    self._serve_probe_click(); return
                if path == "/bg-image":
                    self._serve_bg_image(); return
                if path in ("/log", "/log-view"):
                    self._html(LOG_PAGE_HTML.encode("utf-8")); return
                if path in ("/chat", "/chat-view"):
                    self._html(CHAT_PAGE_HTML.encode("utf-8")); return
                if path in ("/home-dash", "/home-dashboard"):
                    self._html(HOME_DASH_HTML.encode("utf-8")); return
                if path == "/ui":
                    self._html((server.ui_html or "<h1>UI not set</h1>").encode("utf-8"))
                    return
                self._html(b"<!doctype html><title>opencuy</title>"
                           b"<body style='background:#0d0b1a;color:#f4f4ff;"
                           b"font-family:sans-serif;padding:20px'>"
                           b"OpenCUY overlay server is running.</body>")

        return Handler


DEPS = [
    {"key": "pyside6",  "module": "PySide6",            "package": "PySide6",
     "required": True, "description": "Qt bindings for the UI (required)"},
    {"key": "webengine","module": "PySide6.QtWebEngineWidgets",
     "package": "PySide6", "required": True,
     "description": "QtWebEngine (bundled with PySide6)"},
    {"key": "selenium", "module": "selenium",           "package": "selenium",
     "required": True, "description": "Browser automation (required)"},
    {"key": "pytchat",  "module": "pytchat",            "package": "pytchat",
     "required": False, "description": "YouTube live chat reader (optional)"},
    {"key": "yt_dlp",   "module": "yt_dlp",             "package": "yt-dlp",
     "required": True, "description": "yt-dlp video metadata checker"},
]


def _dep_state():
    out = {}
    for d in DEPS:
        try:
            importlib.import_module(d["module"]); out[d["key"]] = True
        except Exception:
            out[d["key"]] = False
    return out


def _pip_install(packages):
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade"] + list(packages),
            capture_output=True, text=True, timeout=900)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, "pip install timed out"
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
        try:
            importlib.invalidate_caches()
            importlib.import_module(d["module"])
            state[d["key"]] = True
            continue
        except Exception:
            pass
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
                                   f"pip reported success but import fails:\n{e}",
                                   "error")
            else:
                _native_dialog("Install failed",
                               f"Could not install {d['package']}.\n\n{out[-800:]}",
                               "error")
        _native_dialog("Cannot continue",
                       f"OpenCUY needs {d['package']}. Exiting.", "error")
        sys.exit(1)

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


_plog("Checking dependencies …")
_DEP_STATE = check_and_install_deps(interactive=True)
_plog(f"Dependencies: {_DEP_STATE}")

_original_signal = signal.signal
def _safe_signal(signalnum, handler):
    if threading.current_thread() is not threading.main_thread():
        return signal.SIG_DFL
    return _original_signal(signalnum, handler)
signal.signal = _safe_signal

from PySide6.QtCore import Qt, QObject, Slot, Signal, QUrl, QTimer
from PySide6.QtGui import (QColor, QIcon, QAction, QPixmap, QPainter,
                           QBrush, QPen, QPalette, QDesktopServices)
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon,
                               QMenu, QFileDialog, QDialog, QVBoxLayout,
                               QHBoxLayout, QPlainTextEdit, QLabel,
                               QPushButton)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
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


# ── DOM metadata probe ──────────────────────────────────────────────────
def ytdlp_probe_url(url):
    """Read YouTube metadata through yt-dlp using only the URL.

    This is the checker-side metadata backend; it does not require a
    Selenium/Chrome DOM and never downloads the media.
    """
    if not url or not re.search(r"(?:youtube\.com|youtu\.be)/", str(url), re.IGNORECASE):
        return None
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        _plog("[ytdlp] yt-dlp is not installed. Install it from the Dependencies tab or with: python -m pip install -U yt-dlp", "err")
        return None
    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 15,
            "extract_flat": False,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(str(url), download=False)
        if not isinstance(info, dict):
            return None

        live_status = str(info.get("live_status") or "").lower()
        # Treat every live-state result as blocked content.  In particular,
        # scheduled streams can report is_upcoming before they actually start.
        # post_live is also kept blocked because it is still a livestream/VOD
        # transition state and should never reach the visible player.
        is_live = (
            bool(info.get("is_live"))
            or bool(info.get("was_live"))
            or live_status in ("is_live", "is_upcoming", "post_live", "was_live")
        )

        timestamp = info.get("timestamp") or info.get("release_timestamp")
        age_seconds = None
        if timestamp is not None:
            try:
                age_seconds = max(0, int(time.time() - float(timestamp)))
            except Exception:
                pass
        if age_seconds is None and info.get("upload_date"):
            try:
                from datetime import datetime, timezone
                dt = datetime.strptime(str(info["upload_date"]), "%Y%m%d").replace(tzinfo=timezone.utc)
                age_seconds = max(0, int(time.time() - dt.timestamp()))
            except Exception:
                pass

        projection = str(info.get("projection") or info.get("projection_type") or "").lower().strip()
        stereo = str(info.get("stereo_layout") or "").lower().strip()
        projection_blob = f"{projection} {stereo}"
        is_360 = any(token in projection_blob for token in (
            "equirectangular", "equiangular", "cubemap", "spherical", "eac",
        ))
        if not is_360:
            for fmt in info.get("formats") or []:
                # Do NOT look for a bare '360': normal formats are commonly
                # labelled 360p.  Look for explicit VR/projection metadata.
                fields = " ".join(
                    str(fmt.get(k) or "").lower()
                    for k in ("format_note", "format", "format_id")
                )
                if any(token in fields for token in (
                    "equirectangular", "equi_threed", "equiangular",
                    "spherical", "cubemap", "eac", "mesh",
                )):
                    is_360 = True
                    break
                # YouTube/yt-dlp can expose stereoscopic VR formats with
                # an `s` suffix (for example 144s / 360s).  Do NOT confuse
                # ordinary 360p with 360s: only an explicit numeric `s`
                # format marker is considered VR.
                if re.search(r"(?:^|[._-])\d+s(?:$|[._-])", fields):
                    is_360 = True
                    break

        return {
            "age_seconds": age_seconds,
            "age_text": info.get("upload_date") or "",
            "is_live": is_live,
            "is_360": bool(is_360),
            "title": info.get("title") or "",
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration"),
            "ytdlp_ok": True,
        }
    except Exception as e:
        _plog(f"[ytdlp] metadata probe failed: {e}", "err")
        return None


def ytdlp_probe(driver):
    """Probe the current page URL with yt-dlp; no DOM metadata is used."""
    if not driver:
        return None
    try:
        return ytdlp_probe_url(driver.current_url)
    except Exception:
        return None


def video_metadata_probe(driver):
    """All video metadata comes from yt-dlp; DOM detection is removed."""
    return ytdlp_probe(driver)


class CheckerBrowser:
    SAFE_FAST_PATHS = (
        r"^/$",
        r"^/results/?$",
        r"^/feed/[A-Za-z0-9_-]+/?$",
        r"^/@[A-Za-z0-9._-]+/?$",
        r"^/@[A-Za-z0-9._-]+/(videos|shorts|streams|playlists|community|channels|about)/?$",
        r"^/channel/UC[A-Za-z0-9_-]{22}(/(videos|shorts|streams|playlists|community|channels|about))?/?$",
        r"^/c/[A-Za-z0-9._-]+/?$",
        r"^/user/[A-Za-z0-9._-]+/?$",
    )
    BLOCKED_PATHS = (
        r"^/live/?$",
        r"^/clip(/.*)?$",
        r"^/embed(/.*)?$",
        r"^/playlist/?$",
    )
    ALLOWED_DOMAINS = ("youtube.com", "youtu.be")
    HOST_RE = re.compile(
        r"^https?://(?:www\.|m\.)?youtu(?:be\.com|\.be)(?=[/?#]|$)(/[^?#]*)?",
        re.I)

    def __init__(self, out_queue):
        self.q = out_queue
        self.driver = None
        self.lock = threading.RLock()

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def start(self):
        """Start the lightweight checker; yt-dlp performs video metadata checks."""
        self._emit("[checker] yt-dlp headless metadata checker ready.", "ok")
        return True

    def _start_locked(self):
        return True

    def stop(self):
        d = self.driver
        self.driver = None
        if d:
            try: d.quit()
            except Exception: pass

    def check(self, url: str, timeout=12):
        """Admission gate: video URLs must pass yt-dlp before visible navigation."""
        if not USE_HEADLESS_CHECKER:
            return True, "checker disabled"

        low = (url or "").lower().strip()
        if not low.startswith(("http://", "https://")):
            return False, f"unsupported URL scheme: {url[:60]!r}"
        if not any(dom in low for dom in self.ALLOWED_DOMAINS):
            return False, f"off-domain URL: {url[:60]!r}"

        m = self.HOST_RE.match(low)
        if not m:
            return False, f"unrecognized YouTube URL: {url[:60]!r}"
        url_path = (m.group(1) or "/").rstrip("/") or "/"

        if BLOCK_GAMING and re.match(r"^/gaming(/.*)?$", url_path, re.I):
            return False, f"blocked path {url_path!r}"
        if BLOCK_SHORTS and re.match(r"^/shorts(/.*)?$", url_path, re.I):
            return False, f"blocked path {url_path!r}"
        for pat in self.BLOCKED_PATHS:
            if re.match(pat, url_path, re.I):
                return False, f"blocked path {url_path!r}"

        for pat in self.SAFE_FAST_PATHS:
            if re.match(pat, url_path, re.I):
                return True, "fast-path (skip scan)"

        BAD_SEGMENTS = {"live","streams","shorts","videos","featured",
                        "playlists","community","about","embed","clip"}
        last_seg = url_path.rsplit("/", 1)[-1] if "/" in url_path else url_path
        if last_seg in BAD_SEGMENTS:
            return False, f"URL segment /{last_seg} not allowed"

        is_watch = re.match(r"^/watch$", url_path, re.I) and "v=" in low
        if not is_watch:
            return True, "ok (non-watch page)"

        if re.search(r"[?&]list=", low):
            return False, "playlist/mix URLs are blocked"

        # yt-dlp is the only video-metadata detector. Run it in the checker
        # before the visible browser navigates to a watch URL.
        try:
            meta = ytdlp_probe_url(url)
        except Exception as e:
            self._emit(f"[checker] yt-dlp probe error: {e}", "warn")
            meta = None
        if meta is None:
            # FAIL CLOSED for video URLs.  The visible browser must never be
            # allowed to navigate to a watch URL unless yt-dlp successfully
            # examined it first.  This is the admission gate.
            return False, "yt-dlp metadata check failed — video locked"
        if BLOCK_LIVE_STREAMS and meta.get("is_live"):
            return False, "yt-dlp: live stream blocked"
        if BLOCK_360 and meta.get("is_360"):
            return False, "yt-dlp: 360° video blocked"
        if BLOCK_UNDER_24H and meta.get("age_seconds") is not None and meta.get("age_seconds") < 86400:
            return False, "yt-dlp: video is under 24 hours old"

        self._emit(
            f"[checker] yt-dlp OK: {meta.get('title','')[:70]!r}; "
            f"live={bool(meta.get('is_live'))}, 360={bool(meta.get('is_360'))}",
            "ok")
        return True, "ok (yt-dlp)"


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
            self._last_ended_url = None
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
                try:
                    self.bot._clear_mod_lock("video ended")
                except Exception:
                    pass
                time.sleep(self.POST_END_SETTLE)
                try:
                    d.get("https://www.youtube.com/")
                    try: self.bot._install_overlay_on_current_page()
                    except Exception: pass
                    try: self.bot._blank_home_feed()
                    except Exception: pass
                    try: self.bot._install_chrome_cleanup()
                    except Exception: pass
                    try: self.bot._install_theme_background()
                    except Exception: pass
                except Exception as e:
                    self._emit(f"[watcher] Navigate home failed: {e}", "warn")
                self.bot._notify_watch(False, "")

    def stop(self):
        self.running = False


_ALLOWED_SITE_HOSTS = (
    "youtube.com", "youtu.be", "googleusercontent.com", "googlevideo.com",
    "ytimg.com", "ggpht.com", "accounts.google.com",
)


def _url_is_allowed_site(url):
    low = (url or "").strip().lower()
    if low.startswith(("about:", "chrome://", "data:")):
        return True
    try:
        parsed = urllib.parse.urlparse(low)
        host = parsed.hostname or ""
    except ValueError:
        return False
    def under(dom):
        return host == dom or host.endswith("." + dom)
    if any(under(d) for d in _ALLOWED_SITE_HOSTS):
        return True
    if under("google.com") and parsed.path.startswith("/sorry"):
        return True
    return False


class YouTubeWatchdog(threading.Thread):
    POLL_INTERVAL = 3.0

    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._last_log_url = None
        self._last_chan_check = 0.0

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def _is_allowed(self, url: str) -> bool:
        return _url_is_allowed_site(url)

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
                try:
                    now2 = time.monotonic()
                    if now2 - self._last_chan_check >= 4.0:
                        self._last_chan_check = now2
                        self.bot._check_channel_featured()
                except Exception:
                    pass
                continue
            if url != self._last_log_url:
                self._emit(f"[watchdog] Off-site detected: {url[:120]}… "
                           f"— returning to YouTube home.", "warn")
                self._last_log_url = url
            try:
                d.get("https://www.youtube.com/")
                try: self.bot._install_overlay_on_current_page()
                except Exception: pass
                try: self.bot._blank_home_feed()
                except Exception: pass
                try: self.bot._install_chrome_cleanup()
                except Exception: pass
                try: self.bot._install_theme_background()
                except Exception: pass
                time.sleep(1.0)
            except Exception as e:
                self._emit(f"[watchdog] Recovery failed: {e}", "warn")

    def stop(self):
        self.running = False


class OverlayWatchdog(threading.Thread):
    POLL_INTERVAL = 3.0

    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._last_reinject = 0.0
        self._last_rj_scan = 0.0

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def run(self):
        self._emit("[overlay-watchdog] Started.")
        while self.running:
            time.sleep(self.POLL_INTERVAL)
            if not self.running: break
            d = self.bot.driver
            if not d: continue
            try:
                url = d.current_url or ""
            except Exception:
                continue
            if not url or url.startswith("data:") or url == "about:blank":
                continue
            if not _url_is_allowed_site(url):
                continue
            try:
                ok = d.execute_script(
                    "return !!(window.__opencuy_overlay_install && "
                    "document.getElementById('__opencuy_pill__'));")
            except Exception:
                ok = False
            try: self.bot._blank_home_feed()
            except Exception: pass
            try: self.bot._install_chrome_cleanup()
            except Exception: pass
            try: self.bot._install_theme_background()
            except Exception: pass
            try: self.bot._check_channel_featured()
            except Exception: pass
            try:
                low = url.lower()
                is_home = False
                try:
                    p = urllib.parse.urlparse(low)
                    host = p.hostname or ""
                    if (host == "youtube.com"
                            or host.endswith(".youtube.com")):
                        if p.path in ("", "/") and (
                                not p.query
                                or p.query.startswith("hl=")):
                            is_home = True
                except Exception:
                    is_home = False
                if is_home:
                    now2 = time.monotonic()
                    if now2 - self._last_rj_scan >= 5.0:
                        self._last_rj_scan = now2
                        title = ""
                        try: title = d.title or ""
                        except Exception: pass
                        try:
                            feed_titles = d.execute_script(r"""
                                const out = [];
                                for (const a of document.querySelectorAll(
                                        'ytd-browse[page-subtype="home"] '
                                        + 'a#video-title-link, '
                                        + 'ytd-browse[page-subtype="home"] '
                                        + 'yt-formatted-string#video-title')) {
                                    const t = (a.textContent || '').trim();
                                    if (t) out.push(t);
                                    if (out.length >= 40) break;
                                }
                                return out;
                            """) or []
                        except Exception:
                            feed_titles = []
                        self.bot._check_for_rocholo(title, *feed_titles)
            except Exception:
                pass
            if ok:
                continue
            now = time.monotonic()
            if now - self._last_reinject < 2.0:
                continue
            self._last_reinject = now
            self._emit("[overlay-watchdog] Re-injecting overlay.", "info")
            try:
                self.bot._install_overlay_on_current_page()
            except Exception as e:
                self._emit(f"[overlay-watchdog] Re-inject failed: {e}", "warn")

    def stop(self):
        self.running = False


INFSCROLL_TICK = 0.05
INFSCROLL_MAX_STEP = 10_000_000
_SCROLL_SPEED_RE = re.compile(r"([+-]?)(\d+(?:\.\d*)?|\.\d+)")
_SCROLL_INT_RE   = re.compile(r"([+-]?)(\d+)")


def parse_scroll_speed(text):
    m = _SCROLL_SPEED_RE.fullmatch((text or "").strip())
    if not m:
        return None
    sign, num = m.group(1), m.group(2)
    try:
        value = Fraction(num)
    except Exception:
        return None
    if sign == "-":
        value = -value
    shown = num if len(num) <= 40 else num[:40] + "…"
    return value, (sign or "+") + shown


def parse_infscroll_args(arg):
    raw = (arg or "").strip()
    if not raw:
        return None
    parts = raw.split()
    if not parts or len(parts) > 3:
        return None

    m_d = _SCROLL_INT_RE.fullmatch(parts[0])
    if not m_d:
        return None
    sign, digits = m_d.group(1), m_d.group(2)
    try:
        distance = int(digits)
    except Exception:
        return None
    if sign == "-":
        distance = -distance

    speed = None
    if len(parts) >= 2:
        m_s = _SCROLL_SPEED_RE.fullmatch(parts[1])
        if not m_s:
            return None
        try:
            speed = Fraction(m_s.group(2))
        except Exception:
            return None
        if speed < 0:
            speed = -speed
        if speed < Fraction(1, 10) or speed > 10000:
            return None

    duration = None
    if len(parts) == 3:
        m_t = _SCROLL_SPEED_RE.fullmatch(parts[2])
        if not m_t:
            return None
        try:
            duration = Fraction(m_t.group(2))
        except Exception:
            return None
        if duration <= 0 or duration > 86400:
            return None

    dist_shown = (("+" if distance >= 0 else "-")
                  + str(abs(distance)))
    label_parts = [f"{dist_shown} px"]
    if speed is not None:
        sp_num = str(speed.numerator) if speed.denominator == 1 \
                 else f"{float(speed):.2f}"
        label_parts.append(f"{sp_num} px/s")
    if duration is not None:
        dur_num = str(duration.numerator) if duration.denominator == 1 \
                  else f"{float(duration):.2f}"
        label_parts.append(f"{dur_num} s max")
    return {
        "distance_px": distance,
        "speed_px_s": speed,
        "duration_s": duration,
        "label": " · ".join(label_parts),
    }


class InfiniteScroller(threading.Thread):
    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._lock = threading.Lock()
        self._speed = None
        self._wake = threading.Event()
        self._err_logged = False
        self._remaining_px = None
        self._deadline = None

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def set_speed(self, speed, distance=None, duration=None):
        with self._lock:
            was = self._speed is not None
            self._speed = speed
            self._remaining_px = (Fraction(distance)
                                   if distance is not None else None)
            if duration is not None:
                try:
                    self._deadline = (time.monotonic()
                                       + max(0.0, float(duration)))
                except Exception:
                    self._deadline = None
            else:
                self._deadline = None
        self._wake.set()
        return was

    def stop_scrolling(self):
        with self._lock:
            was = self._speed is not None
            self._speed = None
            self._remaining_px = None
            self._deadline = None
        return was

    def is_scrolling(self):
        with self._lock:
            return self._speed is not None

    def run(self):
        last = None
        carry = Fraction(0)
        while self.running:
            with self._lock:
                speed = self._speed
                remaining = self._remaining_px
                deadline = self._deadline
            if speed is None:
                last = None
                carry = Fraction(0)
                self._wake.wait(0.5)
                self._wake.clear()
                continue

            if deadline is not None and time.monotonic() >= deadline:
                self._emit("[scroll] Infinite scroll hit its time "
                           "limit — stopping.", "ok")
                self.stop_scrolling()
                continue

            time.sleep(INFSCROLL_TICK)
            with self._lock:
                speed = self._speed
                remaining = self._remaining_px
            if speed is None:
                continue

            now = time.monotonic()
            if last is None:
                last = now - INFSCROLL_TICK
            dt = min(now - last, 1.0)
            last = now
            carry += speed * Fraction(dt)
            whole = int(carry)
            if whole == 0:
                continue
            carry -= whole
            step = max(-INFSCROLL_MAX_STEP, min(INFSCROLL_MAX_STEP, whole))

            if remaining is not None:
                if (step > 0 and remaining <= 0) or \
                   (step < 0 and remaining >= 0):
                    self._emit("[scroll] Infinite scroll reached its "
                               "distance — stopping.", "ok")
                    self.stop_scrolling()
                    continue
                if (step > 0 and remaining < step):
                    step = int(remaining)
                elif (step < 0 and remaining > step):
                    step = int(remaining)

            d = self.bot.driver
            if not d:
                continue
            try:
                d.execute_script(
                    "window.scrollBy({top: arguments[0], left: 0, "
                    "behavior: 'instant'});", step)
                self._err_logged = False
                if remaining is not None:
                    with self._lock:
                        if self._remaining_px is not None:
                            self._remaining_px = (self._remaining_px
                                                   - Fraction(step))
                            if ((step > 0 and self._remaining_px <= 0)
                                    or (step < 0
                                        and self._remaining_px >= 0)):
                                self._emit(
                                    "[scroll] Infinite scroll reached "
                                    "its distance — stopping.", "ok")
                                self._speed = None
                                self._remaining_px = None
                                self._deadline = None
                                continue
            except Exception as e:
                if not self._err_logged:
                    self._err_logged = True
                    self._emit(f"[scroll] Infinite scroll step failed "
                               f"(will keep trying): {e}", "warn")

    def stop(self):
        self.running = False
        self._wake.set()


class ScreenshotCleaner(threading.Thread):
    CHECK_SECONDS = 60 * 30

    def __init__(self, bot):
        super().__init__(daemon=True)
        self.bot = bot
        self.running = True

    @property
    def INTERVAL_HOURS(self):
        return max(1, int(SCREENSHOT_RETENTION_HOURS))

    def run(self):
        try:
            self._sweep()
        except Exception:
            pass
        while self.running:
            for _ in range(self.CHECK_SECONDS):
                if not self.running:
                    return
                time.sleep(1)
            try:
                self._sweep()
            except Exception:
                pass

    def _collect_old_files(self, cutoff):
        out = []
        try:
            for p in SCREENSHOT_DIR.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".png", ".jpg", ".jpeg",
                                            ".webp", ".bmp"):
                    continue
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    continue
                if mtime < cutoff:
                    out.append(p)
        except Exception:
            return out
        return out

    def _archive_files(self, files):
        if not files:
            return 0, "", None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archive_name = f"screenshots_{stamp}.zip"
        archive_path = SCREENSHOT_ARCHIVE_DIR / archive_name
        n = 1
        while archive_path.exists():
            archive_name = f"screenshots_{stamp}_{n}.zip"
            archive_path = SCREENSHOT_ARCHIVE_DIR / archive_name
            n += 1
        written = []
        try:
            with zipfile.ZipFile(archive_path, "w",
                                  compression=zipfile.ZIP_DEFLATED,
                                  compresslevel=6) as zf:
                for p in files:
                    try:
                        zf.write(str(p), arcname=p.name)
                        written.append(p)
                    except Exception as e:
                        _plog(f"[shots] zip entry failed for {p.name}: {e}",
                              "warn")
            if not written:
                try:
                    archive_path.unlink()
                except Exception:
                    pass
                return 0, "", "no files could be added to the archive"
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    bad = zf.testzip()
                    if bad is not None:
                        raise RuntimeError(f"corrupt entry: {bad}")
            except Exception as e:
                try:
                    archive_path.unlink()
                except Exception:
                    pass
                return 0, "", f"archive verify failed: {e}"
            # Only delete files that are verifiably inside the zip.
            archived = 0
            for p in written:
                try:
                    p.unlink()
                    archived += 1
                except Exception:
                    pass
            return archived, archive_name, None
        except Exception as e:
            try:
                if archive_path.exists():
                    archive_path.unlink()
            except Exception:
                pass
            return 0, "", str(e)

    def _sweep(self):
        cutoff = time.time() - (self.INTERVAL_HOURS * 3600)
        files = self._collect_old_files(cutoff)
        if not files:
            return
        if ARCHIVE_SCREENSHOTS:
            archived, name, err = self._archive_files(files)
            if err:
                self._emit(
                    f"[shots] Archive failed: {err}. "
                    f"Files kept ({len(files)}).", "warn")
                return
            if archived:
                self._emit(
                    f"[shots] Archived {archived} screenshot(s) → "
                    f"screenshots_archive/{name}", "info")
        else:
            removed = 0
            for p in files:
                try:
                    p.unlink()
                    removed += 1
                except Exception:
                    pass
            if removed:
                self._emit(
                    f"[shots] Cleanup: removed {removed} screenshot(s) "
                    f"older than {self.INTERVAL_HOURS}h.", "info")
            else:
                return
        try:
            self.bot._broadcast_screenshot_count()
        except Exception:
            pass

    def _emit(self, msg, level="info"):
        try:
            self.bot._emit(msg, level)
        except Exception:
            pass

    def stop(self):
        self.running = False


class StatsBroadcaster(threading.Thread):
    INTERVAL = 1.0

    def __init__(self, bot):
        super().__init__(daemon=True)
        self.bot = bot
        self.running = True

    def run(self):
        while self.running:
            try:
                snap = self.bot.get_stats_snapshot()
                self.bot.overlay_server.push_stats(snap)
            except Exception:
                pass
            time.sleep(self.INTERVAL)

    def stop(self):
        self.running = False


class VideoNavigationGate(threading.Thread):
    """Keep visible YouTube watch pages paused until the headless yt-dlp gate approves them."""
    POLL_INTERVAL = 0.20

    def __init__(self, bot, out_queue):
        super().__init__(daemon=True)
        self.bot = bot
        self.q = out_queue
        self.running = True
        self._last_url = None
        self._checking = False

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})

    def run(self):
        self._emit("[gate] Video navigation gate started; watch pages stay locked until yt-dlp approves.", "ok")
        while self.running:
            time.sleep(self.POLL_INTERVAL)
            if not self.running:
                break
            d = self.bot.driver
            if not d:
                continue
            try:
                url = (d.current_url or "").strip()
            except Exception:
                continue
            low = url.lower()
            if "/watch" not in low or "v=" not in low:
                continue

            # The page-level script starts every new document locked.  A direct
            # click/address-bar navigation therefore cannot start playback while
            # this headless admission check is running.
            if self._checking or url == self._last_url:
                continue
            self._last_url = url
            self._checking = True
            try:
                safe, reason = self.bot.checker.check(url)
                if safe:
                    try:
                        self.bot._set_video_gate(True)
                    except Exception:
                        pass
                    self._emit(f"[gate] yt-dlp approved visible navigation: {url[:100]}", "ok")
                    try:
                        self.bot._start_autoplay_failsafe(url)
                    except Exception as e:
                        self._emit(f"[autoplay] failsafe start failed: {e}", "warn")
                else:
                    self._emit(f"[gate] yt-dlp rejected visible navigation: {reason}", "warn")
                    try:
                        self.bot.driver.get("https://www.youtube.com/")
                        self.bot._blank_home_feed()
                    except Exception:
                        pass
            except Exception as e:
                self._emit(f"[gate] Admission check failed: {e} — keeping video locked.", "warn")
            finally:
                self._checking = False

    def stop(self):
        self.running = False


class YouTubeBot(threading.Thread):
    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.q = out_queue
        self.driver = None
        self.running = True
        self.tasks = queue.Queue()
        self.current_lang = None
        self.end_watcher = None
        self.watchdog = None
        self.video_gate = None
        self.overlay_watchdog = None
        self.scroller = InfiniteScroller(self, out_queue)
        self.checker = CheckerBrowser(out_queue)
        self.overlay_server = OverlayServer(out_queue)
        self._stats_thread = None
        self._stats_running = True
        self._shot_cleanup_thread = None
        self.temp_profile = None
        self._shot_timers = []
        self._cdp_registered = False
        self.on_watch_state = None
        self.on_status = None
        self._privileged_request = False
        self._req_ctx = threading.local()
        self._mod_lock_active = False
        self._mod_lock_video_id = ""
        self._mod_lock_started_at = 0.0
        self._mod_lock_short = False
        self._mod_lock_short_until = 0.0
        self._suppress_next_block_banner = False

        self._stats_lock = threading.Lock()
        self._stats = {
            "started_at": time.time(),
            "videos_loaded": 0,
            "searches": 0,
            "ads_blocked": 0,
            "live_blocked": 0,
            "under24h_blocked": 0,
            "commands": 0,
            "chat_messages": 0,
            "shots_taken": 0,
            "rocholo_detected": 0,
            "channel_bypass_blocked": 0,
            "cuss_blocked": 0,
            "dom_timeout_blocked": 0,
            "mod_lock_blocked": 0,
            "status": "starting",
            "current_title": "",
            "current_url": "",
            "last_event": "",
            "last_event_at": time.time(),
        }
        self._totals_lock = threading.Lock()
        self._totals = {
            "videos_loaded": 0,
            "searches": 0,
            "ads_blocked": 0,
            "live_blocked": 0,
            "under24h_blocked": 0,
            "rocholo_detected": 0,
            "channel_bypass_blocked": 0,
            "cuss_blocked": 0,
            "dom_timeout_blocked": 0,
            "mod_lock_blocked": 0,
            "shots_taken": 0,
            "commands": 0,
            "chat_messages": 0,
        }
        try:
            loaded = _load_json(STATS_FILE, {}) or {}
            if isinstance(loaded, dict):
                for k in list(self._totals.keys()):
                    try:
                        self._totals[k] = int(loaded.get(k, 0))
                    except Exception:
                        pass
        except Exception:
            pass
        self._totals_save_lock = threading.Lock()
        self._last_totals_save = 0.0
        self._totals_dirty = False

    def set_request_privilege(self, privileged):
        """Mark requests enqueued *by the calling thread* as privileged
        (owner / moderator / local UI). Thread-local on purpose: the bot
        thread uses _privileged_request for the task it is executing."""
        self._req_ctx.privileged = bool(privileged)

    def _put_task(self, kind, arg, lucky, source, lang):
        privileged = bool(getattr(self._req_ctx, "privileged", False))
        self.tasks.put((kind, arg, lucky, source, lang, privileged))

    def request_search(self, query, lucky=False, lang=None, source=""):
        self._put_task("search", normalize_query(query), lucky, source, lang)
    def request_video(self, video_id, lang=None, source=""):
        self._put_task("video", video_id.strip(), False, source, lang)
    def request_channel(self, handle, lang=None, source=""):
        self._put_task("channel", handle.strip(), False, source, lang)
    def request_home(self, lang=None, source=""):
        self._put_task("home", "", False, source, lang)
    def request_language(self, lang, source=""):
        self._put_task("language", lang.strip(), False, source, None)
    def request_scroll(self, pixels, source=""):
        self._put_task("scroll", str(pixels), False, source, None)
    def request_watchhome(self, index, source=""):
        self._put_task("watchhome", str(index), False, source, None)
    def request_playback(self, kind, arg="", source=""):
        self._put_task(kind, arg, False, source, None)
    def request_clear_cookies(self, source=""):
        self._put_task("clear_cookies", "", False, source, None)
    def request_loadmore(self, count, source=""):
        self._put_task("loadmore", str(count), False, source, None)
    def request_skip_ad(self, source=""):
        self._put_task("skip_ad", "", False, source, None)
    def request_pick(self, index, source=""):
        self._put_task("pick", str(index), False, source, None)
    def request_skip_forward(self, seconds, source=""):
        self._put_task("skip_forward", str(seconds), False, source, None)
    def request_clear_screenshots(self, source=""):
        self._put_task("clear_screenshots", "", False, source, None)

    def request_infscroll(self, speed, label, source="",
                          distance=None, duration=None):
        prefix = f"[{source}] " if source else ""
        updated = self.scroller.set_speed(
            speed, distance=distance, duration=duration)
        self._emit(f"{prefix}[bot] Infinite scroll "
                   f"{'updated' if updated else 'started'}: "
                   f"{label}.", "ok")

    def request_stopscroll(self, source="", reason=""):
        prefix = f"[{source}] " if source else ""
        was_running = self.scroller.stop_scrolling()
        try:
            self.scroller._wake.set()
        except Exception:
            pass
        if was_running:
            label = reason or "stopscroll"
            self._emit(f"{prefix}[bot] Infinite scroll cleared "
                       f"({label}).", "ok")
            try:
                self._notify_info("∞ Infinite scroll cleared", "info")
            except Exception:
                pass
        else:
            self._emit(f"{prefix}[bot] {reason or '!stopscroll'}: "
                       f"infinite scroll is not running.", "info")

    def _emit(self, msg, level="info"):
        self.q.put({"kind": "log", "level": level, "message": msg})
        _plog(msg, level)

    def _emit_status(self, text, ok=True):
        cb = self.on_status
        if cb is None:
            return
        try: cb(text, ok)
        except Exception: pass

    # ── Overlay injection ────────────────────────────────────────────────
    def _build_overlay_source(self):
        port = self.overlay_server.port
        if not port:
            return None
        show_chat = "true" if SHOW_CHAT_IFRAME else "false"
        show_log = "true" if SHOW_LOG_IFRAME else "false"
        chat_mode = repr(CHAT_IFRAME_MODE)
        log_mode = repr(LOG_IFRAME_MODE)
        source = r"""
        (function(){
            'use strict';
            // The CDP hook runs in EVERY frame. Without this guard the
            // overlay's own chat/log iframes would each inject another
            // overlay (recursively) and YouTube embeds would get one too.
            try { if (window.top !== window.self) return 'subframe'; }
            catch(e) { return 'subframe'; }
            var PORT = __OC_PORT__;
            var SHOW_CHAT = __OC_SHOWCHAT__;
            var SHOW_LOG = __OC_SHOWLOG__;
            var CHAT_MODE = __OC_CHAT_MODE__;
            var LOG_MODE = __OC_LOG_MODE__;
            var CHAT_W = __OC_CHAT_W__;
            var CHAT_H = __OC_CHAT_H__;
            var CHAT_B = __OC_CHAT_B__;
            var CHAT_L = __OC_CHAT_L__;
            var LOG_W = __OC_LOG_W__;
            var LOG_H = __OC_LOG_H__;
            var LOG_B = __OC_LOG_B__;
            var LOG_R = __OC_LOG_R__;
            var PROBE_CLICKS = __OC_PROBE_CLICKS__;
            var PILL_ID = '__opencuy_pill__';
            var CHAT_ID = '__opencuy_chat__';
            var LOG_ID  = '__opencuy_log__';
            var WARN_ID = '__opencuy_warn__';
            var SHOT_ID = '__opencuy_shots__';
            var TITLE_ID = '__opencuy_title__';

            var PILL_STATE = {
                text: '',
                opacity: 0,
                borderColor: ''
            };

            window.__opencuy_gen = (window.__opencuy_gen || 0) + 1;
            var MY_GEN = window.__opencuy_gen;

            function log(m){
                try { console.log('[opencuy] ' + m); } catch(e) {}
            }

            function mkSpan(id, css){
                var el = document.createElement('span');
                if (id) el.id = id;
                if (css) el.style.cssText = css;
                return el;
            }

            function ensurePill(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                var p = document.getElementById(PILL_ID);
                if (!p) {
                    try {
                        p = document.createElement('div');
                        p.id = PILL_ID;
                        var pillBottom = 14;
                        try {
                            var chatEl = document.getElementById(CHAT_ID);
                            if (chatEl && SHOW_CHAT && CHAT_MODE !== 'off') {
                                pillBottom = CHAT_B + CHAT_H + 8;
                            }
                        } catch(e) {}
                        p.style.cssText = [
                            'position:fixed','left:' + CHAT_L + 'px',
                            'bottom:' + pillBottom + 'px',
                            'width:' + CHAT_W + 'px','height:36px',
                            'padding:0 14px','border-radius:999px',
                            'background:rgba(13,11,26,.85)',
                            'border:1px solid rgba(139,92,246,.55)',
                            'box-shadow:0 4px 20px rgba(0,0,0,.5)',
                            'font:600 13px/36px Segoe UI,Tahoma,sans-serif',
                            'color:#fff','white-space:nowrap','overflow:hidden',
                            'text-overflow:ellipsis','pointer-events:none',
                            'z-index:2147483647',
                            'transition:opacity .2s, border-color .2s'
                        ].join(';') + ' !important';
                        p.textContent = PILL_STATE.text || 'idle';
                        p.style.opacity = String(PILL_STATE.opacity || 0);
                        if (PILL_STATE.borderColor) {
                            p.style.borderColor = PILL_STATE.borderColor;
                        }
                        document.documentElement.appendChild(p);
                    } catch(e) { log('pill create failed: ' + e); return null; }
                }
                try {
                    var wantBottom = 14;
                    var wantLeft = 14;
                    var wantWidth = 380;
                    if (SHOW_CHAT && CHAT_MODE !== 'off') {
                        wantBottom = CHAT_B + CHAT_H + 8;
                        wantLeft = CHAT_L;
                        wantWidth = CHAT_W;
                    }
                    var curBottom = parseInt(p.style.bottom, 10) || 0;
                    var curLeft = parseInt(p.style.left, 10) || 0;
                    var curWidth = parseInt(p.style.width, 10) || 0;
                    if (curBottom !== wantBottom) p.style.bottom = wantBottom + 'px';
                    if (curLeft !== wantLeft)     p.style.left = wantLeft + 'px';
                    if (curWidth !== wantWidth)   p.style.width = wantWidth + 'px';
                } catch(e) {}
                return p;
            }

            function ensureChat(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                if (!SHOW_CHAT || CHAT_MODE === 'off') {
                    var stale = document.getElementById(CHAT_ID);
                    if (stale) { try { stale.parentNode.removeChild(stale); } catch(e) {} }
                    return null;
                }
                var existing = document.getElementById(CHAT_ID);
                if (existing) {
                    var wantTag = (CHAT_MODE === 'iframe') ? 'IFRAME' : 'DIV';
                    if (existing.tagName === wantTag) return existing;
                    try { existing.parentNode.removeChild(existing); }
                    catch(e) {}
                }
                try {
                    if (CHAT_MODE === 'iframe') {
                        var f = document.createElement('iframe');
                        f.id = CHAT_ID;
                        f.setAttribute('src',
                            'http://127.0.0.1:' + PORT + '/chat-view');
                        f.setAttribute('scrolling', 'auto');
                        f.setAttribute('frameborder', '0');
                        f.style.cssText = [
                            'position:fixed','left:' + CHAT_L + 'px',
                            'bottom:' + CHAT_B + 'px',
                            'width:' + CHAT_W + 'px',
                            'height:' + CHAT_H + 'px',
                            'padding:0','margin:0','border-radius:10px',
                            'background:rgba(13,11,26,.92)',
                            'border:1px solid rgba(139,92,246,.45)',
                            'box-shadow:0 4px 16px rgba(0,0,0,.5)',
                            'color:#f4f4ff','overflow:hidden',
                            'z-index:2147483647'
                        ].join(';') + ' !important';
                        document.documentElement.appendChild(f);
                        return f;
                    }
                    var c = document.createElement('div');
                    c.id = CHAT_ID;
                    c.style.cssText = [
                        'position:fixed','left:' + CHAT_L + 'px',
                        'bottom:' + CHAT_B + 'px',
                        'width:' + CHAT_W + 'px',
                        'height:' + CHAT_H + 'px',
                        'padding:0 12px','border-radius:10px',
                        'background:rgba(13,11,26,.85)',
                        'border:1px solid rgba(139,92,246,.45)',
                        'box-shadow:0 4px 16px rgba(0,0,0,.5)',
                        'font:600 12.5px/1.35 Segoe UI,Tahoma,sans-serif',
                        'color:#fff','white-space:nowrap','overflow:hidden',
                        'text-overflow:ellipsis','pointer-events:none',
                        'display:flex','align-items:center','gap:8px',
                        'z-index:2147483647'
                    ].join(';') + ' !important';

                    var dot = mkSpan(CHAT_ID + '_dot',
                        'width:8px;height:8px;border-radius:50%;'
                      + 'background:#5d5a85;flex:0 0 auto');
                    var txt = mkSpan(CHAT_ID + '_txt', null);
                    txt.textContent = 'Chat: idle';
                    c.appendChild(dot);
                    c.appendChild(txt);

                    document.documentElement.appendChild(c);
                    return c;
                } catch(e) { log('chat create failed: ' + e); return null; }
            }

            function ensureLog(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                if (!SHOW_LOG || LOG_MODE === 'off') {
                    var stale = document.getElementById(LOG_ID);
                    if (stale) { try { stale.parentNode.removeChild(stale); } catch(e) {} }
                    return null;
                }
                var existing = document.getElementById(LOG_ID);
                if (existing) {
                    if (existing.tagName === 'IFRAME') return existing;
                    try { existing.parentNode.removeChild(existing); }
                    catch(e) {}
                }
                try {
                    var f = document.createElement('iframe');
                    f.id = LOG_ID;
                    f.setAttribute('src',
                        'http://127.0.0.1:' + PORT + '/log-view');
                    f.setAttribute('scrolling', 'auto');
                    f.setAttribute('frameborder', '0');
                    f.style.cssText = [
                        'position:fixed','right:' + LOG_R + 'px',
                        'bottom:' + LOG_B + 'px',
                        'width:' + LOG_W + 'px',
                        'height:' + LOG_H + 'px',
                        'padding:0','margin:0','border-radius:10px',
                        'background:rgba(13,11,26,.92)',
                        'border:1px solid rgba(139,92,246,.45)',
                        'box-shadow:0 4px 16px rgba(0,0,0,.5)',
                        'color:#f4f4ff','overflow:hidden',
                        'z-index:2147483647'
                    ].join(';') + ' !important';
                    document.documentElement.appendChild(f);
                    return f;
                } catch(e) { log('log create failed: ' + e); return null; }
            }

            function ensureWarn(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                var w = document.getElementById(WARN_ID);
                if (!w) {
                    try {
                        w = document.createElement('div');
                        w.id = WARN_ID;
                        w.style.cssText = [
                            'position:fixed','top:12px','left:50%',
                            'transform:translateX(-50%)',
                            'max-width:80vw','padding:10px 20px',
                            'border-radius:10px',
                            'background:rgba(120,20,30,.92)',
                            'border:1px solid rgba(244,63,94,.75)',
                            'box-shadow:0 6px 24px rgba(0,0,0,.6)',
                            'font:600 13px/1.5 Segoe UI,Tahoma,sans-serif',
                            'color:#fff',
                            'white-space:normal','word-break:break-word',
                            'text-align:center',
                            'pointer-events:none','opacity:0',
                            'transition:opacity .25s','z-index:2147483647'
                        ].join(';') + ' !important';
                        w.textContent = '';
                        document.documentElement.appendChild(w);
                    } catch(e) { log('warn create failed: ' + e); return null; }
                }
                return w;
            }

            var BADGE_STATE = { shots: 0, rocholo: 0 };

            function renderBadge(){
                var s = document.getElementById(SHOT_ID);
                if (!s) return;
                var parts = ['\uD83D\uDCF8 ' + String(BADGE_STATE.shots)];
                parts.push('\uD83D\uDFE3 ' + String(BADGE_STATE.rocholo));
                s.textContent = parts.join('  \u00B7  ');
            }

            function ensureShots(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                var s = document.getElementById(SHOT_ID);
                if (!s) {
                    try {
                        s = document.createElement('div');
                        s.id = SHOT_ID;
                        s.style.cssText = [
                            'position:fixed','top:12px','left:12px',
                            'padding:5px 11px','border-radius:8px',
                            'background:rgba(13,11,26,.85)',
                            'border:1px solid rgba(139,92,246,.55)',
                            'box-shadow:0 3px 12px rgba(0,0,0,.5)',
                            'font:600 12px/1.3 Segoe UI,Tahoma,sans-serif',
                            'color:#a78bfa','pointer-events:none',
                            'z-index:2147483647','white-space:nowrap'
                        ].join(';') + ' !important';
                        s.textContent = '\uD83D\uDCF8 0  \u00B7  \uD83D\uDFE3 0';
                        document.documentElement.appendChild(s);
                    } catch(e) { log('shots badge create failed: ' + e); return null; }
                } else {
                    renderBadge();
                }
                return s;
            }

            var TITLE_STATE = { text: '', opacity: 0 };

            function ensureTitle(){
                if (MY_GEN !== window.__opencuy_gen) return null;
                if (!document.documentElement) return null;
                var t = document.getElementById(TITLE_ID);
                if (!t) {
                    try {
                        t = document.createElement('div');
                        t.id = TITLE_ID;
                        t.style.cssText = [
                            'position:fixed','top:12px','left:50%',
                            'transform:translateX(-50%)',
                            'max-width:60vw','padding:8px 20px',
                            'border-radius:999px',
                            'background:rgba(13,11,26,.88)',
                            'border:1px solid rgba(139,92,246,.55)',
                            'box-shadow:0 6px 20px rgba(0,0,0,.55)',
                            'font:600 13px/1.4 Segoe UI,Tahoma,sans-serif',
                            'color:#f4f4ff',
                            'white-space:nowrap','overflow:hidden',
                            'text-overflow:ellipsis','pointer-events:none',
                            'opacity:0','transition:opacity .2s',
                            'z-index:2147483646'
                        ].join(';') + ' !important';
                        t.textContent = TITLE_STATE.text || '';
                        t.style.opacity = String(TITLE_STATE.opacity || 0);
                        document.documentElement.appendChild(t);
                    } catch(e) { log('title create failed: ' + e); return null; }
                } else {
                    if (t.textContent !== TITLE_STATE.text) {
                        t.textContent = TITLE_STATE.text || '';
                    }
                    if (t.style.opacity !== String(TITLE_STATE.opacity || 0)) {
                        t.style.opacity = String(TITLE_STATE.opacity || 0);
                    }
                }
                return t;
            }

            function setTitle(text){
                if (MY_GEN !== window.__opencuy_gen) return;
                var t = document.getElementById(TITLE_ID);
                if (!t) {
                    t = ensureTitle();
                    if (!t) return;
                }
                if (!text) {
                    TITLE_STATE.text = '';
                    TITLE_STATE.opacity = 0;
                    t.style.opacity = '0';
                    return;
                }
                TITLE_STATE.text = '\u25B6 ' + text;
                TITLE_STATE.opacity = 1;
                t.textContent = TITLE_STATE.text;
                t.style.opacity = '1';
            }

            function setPill(text, kind){
                if (MY_GEN !== window.__opencuy_gen) return;
                var p = document.getElementById(PILL_ID);
                if (!p) return;
                if (!text) {
                    PILL_STATE.text = '';
                    PILL_STATE.opacity = 0;
                    PILL_STATE.borderColor = '';
                    p.style.opacity = '0';
                    return;
                }
                var c = kind === 'paused' ? '244,63,94'
                      :                     '139,92,246';
                var bc = 'rgba(' + c + ',.55)';
                PILL_STATE.text = text;
                PILL_STATE.opacity = 1;
                PILL_STATE.borderColor = bc;
                p.style.opacity = '1';
                p.textContent = text;
                p.style.borderColor = bc;
            }

            function setWarn(text, kind){
                if (MY_GEN !== window.__opencuy_gen) return;
                var w = document.getElementById(WARN_ID);
                if (!w) return;
                if (!text) {
                    w.style.opacity = '0';
                    w.textContent = '';
                    return;
                }

                var isBlocked = (kind === 'blocked');
                if (isBlocked) {
                    // Make a blocked video unmistakable to the viewer.
                    w.style.top = '50%';
                    w.style.left = '50%';
                    w.style.transform = 'translate(-50%, -50%)';
                    w.style.maxWidth = 'min(820px, 90vw)';
                    w.style.padding = '34px 48px';
                    w.style.background = 'rgba(75, 7, 18, .98)';
                    w.style.borderColor = 'rgba(255, 72, 96, 1)';
                    w.style.borderWidth = '4px';
                    w.style.borderRadius = '20px';
                    w.style.boxShadow = '0 0 0 100vmax rgba(0,0,0,.42), 0 18px 70px rgba(0,0,0,.9), 0 0 40px rgba(244,63,94,.5)';
                    w.style.fontSize = '30px';
                    w.style.fontWeight = '900';
                    w.style.lineHeight = '1.4';
                    w.style.letterSpacing = '.3px';
                    w.style.textAlign = 'center';
                    w.style.zIndex = '2147483647';
                } else {
                    w.style.top = '12px';
                    w.style.left = '50%';
                    w.style.transform = 'translateX(-50%)';
                    if (kind === 'info') {
                        w.style.background = 'rgba(76,29,149,.92)';
                        w.style.borderColor = 'rgba(167,139,250,.85)';
                    } else if (kind === 'err') {
                        w.style.background = 'rgba(120,20,30,.92)';
                        w.style.borderColor = 'rgba(244,63,94,.85)';
                    } else {
                        w.style.background = 'rgba(120,20,30,.92)';
                        w.style.borderColor = 'rgba(244,63,94,.75)';
                    }
                    w.style.maxWidth = '80vw';
                    w.style.padding = '10px 20px';
                    w.style.fontSize = '13px';
                    w.style.fontWeight = '600';
                    w.style.lineHeight = '1.5';
                    w.style.letterSpacing = '0';
                    w.style.borderWidth = '1px';
                    w.style.borderRadius = '10px';
                    w.style.boxShadow = '0 6px 24px rgba(0,0,0,.6)';
                    w.style.textAlign = 'center';
                    w.style.zIndex = '2147483647';
                }
                w.style.pointerEvents = 'none';
                w.style.whiteSpace = 'pre-line';
                w.textContent = text;
                w.style.opacity = '1';
                if (w.__oc_timer) clearTimeout(w.__oc_timer);
                w.__oc_timer = setTimeout(function(){
                    if (w.textContent === text) w.style.opacity = '0';
                }, isBlocked ? 15000 : 8000);
            }

            function setShots(count){
                if (MY_GEN !== window.__opencuy_gen) return;
                BADGE_STATE.shots = Number(count) || 0;
                renderBadge();
            }

            function setStatsBadge(ev){
                if (MY_GEN !== window.__opencuy_gen) return;
                if (!ev) return;
                var r = ev.total_rocholo_detected;
                if (r === undefined || r === null) {
                    r = ev.rocholo_detected || 0;
                }
                BADGE_STATE.rocholo = Number(r) || 0;
                var s = ev.shots_on_disk;
                if (s !== undefined && s !== null) {
                    BADGE_STATE.shots = Number(s) || 0;
                }
                renderBadge();
            }

            function setChat(text, color){
                if (MY_GEN !== window.__opencuy_gen) return;
                if (CHAT_MODE !== 'pill') return;
                var el = document.getElementById(CHAT_ID + '_txt');
                var dot = document.getElementById(CHAT_ID + '_dot');
                if (el) el.textContent = text;
                if (dot && color) {
                    dot.style.background = color;
                    dot.style.boxShadow = '0 0 8px ' + color;
                }
            }

            var LAST_THEME_KEY = null;
            function applyThemePayload(ev){
                if (!ev) return;
                var pal = ev.palette || {};
                var key = (ev.name || '') + '|'
                    + (pal.bg || '') + '|' + (pal.card || '') + '|'
                    + (pal.accent || '') + '|' + (pal.text || '') + '|'
                    + (pal.dim || '');
                if (key === LAST_THEME_KEY) {
                    if (document.getElementById('__opencuy_theme_bg__')) {
                        return;
                    }
                }
                LAST_THEME_KEY = key;
                var BG = pal.bg || '#0d0b1a';
                var CARD = pal.card || '#141129';
                var ACCENT = pal.accent || '#8b5cf6';
                var TEXT = pal.text || '#f4f4ff';
                var DIM = pal.dim || '#8b88b8';
                var css = [
                    'html, body, ytd-app, ytd-page-manager,',
                    '#content, #page-manager, #columns,',
                    'ytd-browse, ytd-watch-flexy,',
                    'ytd-search, ytd-channel-videos-tab-renderer,',
                    'tp-yt-app-drawer #contentContainer',
                    '{ background: ' + BG + ' !important;',
                    '  color: ' + TEXT + ' !important; }',

                    '#guide, ytd-guide-renderer,',
                    'ytd-mini-guide-renderer,',
                    'ytd-masthead, #masthead-container,',
                    '#masthead',
                    '{ background: ' + CARD + ' !important;',
                    '  color: ' + TEXT + ' !important; }',

                    'ytd-rich-item-renderer,',
                    'ytd-rich-grid-media,',
                    'ytd-video-renderer,',
                    'ytd-grid-video-renderer,',
                    'ytd-compact-video-renderer,',
                    'ytd-playlist-renderer,',
                    'ytd-channel-renderer,',
                    'ytd-shelf-renderer,',
                    'ytd-rich-section-renderer,',
                    'ytd-watch-metadata,',
                    'ytd-watch-next-secondary-results-renderer,',
                    'ytd-comments,',
                    'ytd-comment-thread-renderer,',
                    '#primary, #secondary, #related,',
                    'ytd-menu-popup-renderer,',
                    'tp-yt-paper-dialog,',
                    'ytd-popup-container > *',
                    '{ background: ' + CARD + ' !important;',
                    '  color: ' + TEXT + ' !important;',
                    '  border-color: rgba(255,255,255,0.08) !important; }',

                    'yt-formatted-string, .yt-core-attributed-string,',
                    '#video-title, #video-title-link,',
                    'h1, h2, h3, h4, span, a',
                    '{ color: ' + TEXT + ' !important; }',
                    '#metadata-line, .inline-metadata-item,',
                    'ytd-video-meta-block, .ytd-video-meta-block',
                    '{ color: ' + DIM + ' !important; }',

                    'a.yt-simple-endpoint:hover, a:hover',
                    '{ color: ' + ACCENT + ' !important; }',
                    '.ytp-play-progress',
                    '{ background: ' + ACCENT + ' !important; }',
                    '.ytp-scrubber-button',
                    '{ background: ' + ACCENT + ' !important;',
                    '  border-color: ' + ACCENT + ' !important; }',
                    'tp-yt-paper-tab[aria-selected="true"]',
                    '    > .tab-content',
                    '{ color: ' + ACCENT + ' !important; }',

                    'ytd-button-renderer button,',
                    'yt-button-shape button,',
                    'tp-yt-paper-button,',
                    'ytd-chip-cloud-chip-renderer',
                    '{ background: ' + CARD + ' !important;',
                    '  color: ' + TEXT + ' !important;',
                    '  border-color: rgba(255,255,255,0.08) !important; }',
                    'ytd-chip-cloud-chip-renderer[selected]',
                    '{ background: ' + ACCENT + ' !important;',
                    '  color: #ffffff !important; }',

                    'input, textarea, ytd-searchbox,',
                    '#search, .ytd-searchbox',
                    '{ background: ' + CARD + ' !important;',
                    '  color: ' + TEXT + ' !important;',
                    '  border-color: rgba(255,255,255,0.08) !important; }',

                    '.ytp-gradient-top, .ytp-gradient-bottom',
                    '{ background-image: none !important; }'
                ].join('\n');

                var STYLE_ID = '__opencuy_theme_bg__';
                var st = document.getElementById(STYLE_ID);
                if (!st) {
                    st = document.createElement('style');
                    st.id = STYLE_ID;
                    st.textContent = css;
                    document.documentElement.appendChild(st);
                } else {
                    st.textContent = css;
                }
                try {
                    var mt = document.querySelector(
                        'meta[name="theme-color"]');
                    if (!mt) {
                        mt = document.createElement('meta');
                        mt.name = 'theme-color';
                        document.head.appendChild(mt);
                    }
                    mt.content = BG;
                } catch(e) {}
            }

            function openStream(path, key, onMsg){
                if (window[key]) return;
                try {
                    var url = 'http://127.0.0.1:' + PORT + path;
                    var es = new EventSource(url);
                    window[key] = es;
                    es.onmessage = function(e){
                        try { onMsg(JSON.parse(e.data)); }
                        catch(err){ log('parse error on ' + path + ': ' + err); }
                    };
                    es.onerror = function(){
                        log('EventSource error on ' + path);
                        if (es.readyState === 2) {
                            try { es.close(); } catch(e){}
                            window[key] = null;
                            setTimeout(function(){
                                openStream(path, key, onMsg);
                            }, 2000);
                        }
                    };
                    es.onopen = function(){ log('EventSource connected: ' + path); };
                } catch(e) {
                    log('EventSource create failed for ' + path + ': ' + e);
                    window[key] = null;
                }
            }

            function install(){
                if (!document.documentElement) {
                    setTimeout(install, 100);
                    return;
                }
                try {
                    try {
                        if (window.__opencuy_ensure_iv) {
                            clearInterval(window.__opencuy_ensure_iv);
                            window.__opencuy_ensure_iv = null;
                        }
                    } catch(e) {}
                    try {
                        for (const id of [PILL_ID, CHAT_ID, LOG_ID,
                                          WARN_ID, SHOT_ID, TITLE_ID]) {
                            const el = document.getElementById(id);
                            if (el && el.parentNode) {
                                el.parentNode.removeChild(el);
                            }
                        }
                    } catch(e) {}

                    ensurePill();
                    ensureChat();
                    ensureLog();
                    ensureWarn();
                    ensureShots();
                    ensureTitle();

                    openStream('/events', '__opencuy_es_events', function(ev){
                        if (!ev || ev.hidden) {
                            setPill('');
                            setTitle('');
                            return;
                        }
                        var text = ev.text || '';
                        var kind = ev.kind || '';
                        if (kind === 'opencuy'
                            && text.indexOf('OpenCUY is playing') !== -1) {
                            var name = text.replace(
                                /^▶\s*OpenCUY is playing:\s*/i, '');
                            setTitle(name);
                            setPill('');
                        } else {
                            setPill(text, kind);
                        }
                    });
                    openStream('/warn-events', '__opencuy_es_warn',
                        function(ev){
                            if (!ev || ev.hidden || !ev.text) setWarn('');
                            else setWarn(ev.text, ev.kind || 'warn');
                        });
                    openStream('/shots-events', '__opencuy_es_shots',
                        function(ev){
                            if (!ev) { setShots(0); return; }
                            setShots(ev.count || 0);
                        });
                    openStream('/stats-events', '__opencuy_es_stats_badge',
                        function(ev){
                            try { setStatsBadge(ev); }
                            catch(e) { log('stats badge failed: ' + e); }
                        });
                    openStream('/theme-events', '__opencuy_es_theme',
                        function(ev){
                            try { applyThemePayload(ev); }
                            catch(e) { log('theme apply failed: ' + e); }
                        });

                    if (SHOW_CHAT && CHAT_MODE === 'pill') {
                        openStream('/chat-events', '__opencuy_es_chat_status',
                            function(ev){
                                if (!ev) { setChat('Chat: idle', '#5d5a85'); return; }
                                var col = '#5d5a85';
                                if (ev.level === 'err') col = '#f43f5e';
                                else if (ev.connected) col = '#34d399';
                                setChat(ev.message || 'Chat', col);
                            });
                        openStream('/chat-view-events', '__opencuy_es_chat_view',
                            function(ev){
                                if (!ev) { setChat('Chat: idle', '#5d5a85'); return; }
                                var t = '', c = '#5d5a85';
                                if (ev.kind === 'chat')      { t = (ev.author ? ev.author + ': ' : '') + (ev.message || ''); c = '#34d399'; }
                                else if (ev.kind === 'cmd')  { t = '↳ ' + (ev.message || ''); c = '#a78bfa'; }
                                else if (ev.kind === 'sys')  { t = '* ' + (ev.message || ''); c = '#fbbf24'; }
                                else if (ev.kind === 'err')  { t = '[err] ' + (ev.message || ''); c = '#f43f5e'; }
                                else                         { t = ev.message || 'Chat'; }
                                setChat(t, c);
                            });
                    }

                    window.__opencuy_ensure_iv = setInterval(function(){
                        try {
                            ensurePill();
                            ensureChat();
                            ensureLog();
                            ensureWarn();
                            ensureShots();
                            ensureTitle();
                        } catch(e) { log('ensure failed: ' + e); }
                    }, 500);
                    window.__opencuy_overlay_installed = true;
                    log('overlay installed on ' + location.href
                        + ' (chat=' + CHAT_MODE + ', log=' + LOG_MODE + ')');
                } catch(e) {
                    log('install error: ' + e);
                }
            }

            window.__opencuy_overlay_install = install;
            install();
            return 'ok';
        })();
        """.replace("__OC_PORT__", str(port)) \
           .replace("__OC_SHOWCHAT__", show_chat) \
           .replace("__OC_SHOWLOG__", show_log) \
           .replace("__OC_CHAT_MODE__", chat_mode) \
           .replace("__OC_LOG_MODE__", log_mode) \
           .replace("__OC_CHAT_W__", str(CHAT_IFRAME_W)) \
           .replace("__OC_CHAT_H__", str(CHAT_IFRAME_H)) \
           .replace("__OC_CHAT_B__", str(CHAT_IFRAME_BOTTOM)) \
           .replace("__OC_CHAT_L__", str(CHAT_IFRAME_LEFT)) \
           .replace("__OC_LOG_W__", str(LOG_IFRAME_W)) \
           .replace("__OC_LOG_H__", str(LOG_IFRAME_H)) \
           .replace("__OC_LOG_B__", str(LOG_IFRAME_BOTTOM)) \
           .replace("__OC_LOG_R__", str(LOG_IFRAME_RIGHT)) \
           .replace("__OC_PROBE_CLICKS__",
                    "true" if PROBE_CLICKS else "false")
        return source

    def _register_overlay_cdp(self):
        if self._cdp_registered:
            return
        if not self.driver:
            return
        if not self.overlay_server.port:
            self._emit("[overlay] CDP skipped: server has no port", "warn")
            return
        source = self._build_overlay_source()
        if not source:
            return
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": source})
            self._cdp_registered = True
            self._emit("[overlay] CDP registration done.", "ok")
        except Exception as e:
            self._emit(f"[overlay] CDP register failed: {e}", "warn")

    def _install_overlay_on_current_page(self):
        if not self.driver:
            return False
        if not self.overlay_server.port:
            return False
        source = self._build_overlay_source()
        if not source:
            return False
        try:
            try:
                self.driver.execute_script("""
                    try {
                        if (window.__opencuy_ensure_iv) {
                            clearInterval(window.__opencuy_ensure_iv);
                        }
                    } catch(e) {}
                    window.__opencuy_ensure_iv = null;
                    window.__opencuy_overlay_install = null;
                    try {
                        window.__opencuy_gen =
                            (window.__opencuy_gen || 0) + 1;
                    } catch(e) {}
                    for (const id of ['__opencuy_pill__',
                                      '__opencuy_chat__',
                                      '__opencuy_log__',
                                      '__opencuy_warn__',
                                      '__opencuy_shots__',
                                      '__opencuy_title__']) {
                        const el = document.getElementById(id);
                        if (el && el.parentNode) {
                            el.parentNode.removeChild(el);
                        }
                    }
                """)
            except Exception:
                pass
            result = self.driver.execute_script(source)
            return True
        except Exception as e:
            self._emit(f"[overlay] inline install failed: {e}", "warn")
            return False

    def _inject_overlay(self, text, kind="opencuy"):
        try:
            self.overlay_server.push({"hidden": False, "text": text, "kind": kind})
        except Exception as e:
            self._emit(f"[overlay] push failed: {e}", "warn")

    def _remove_overlay(self):
        try:
            self.overlay_server.push({"hidden": True})
        except Exception: pass

    def _push_chat_status(self, message, connected=False, level="info", sub=""):
        try:
            self.overlay_server.push_chat({
                "message": message, "connected": bool(connected),
                "level": level, "sub": sub,
            })
        except Exception:
            pass

    def _notify_watch(self, on_watch: bool, title=""):
        cb = self.on_watch_state
        if cb is not None:
            try: cb(on_watch, title)
            except Exception as e:
                self._emit(f"[overlay] watch-state callback failed: {e}", "warn")
        try:
            if on_watch:
                label = title if title else "video"
                self._stat_set("status", "playing")
                self._inject_overlay(f"▶ OpenCUY is playing: {label}",
                                     kind="opencuy")
                self._emit_status(f"Playing: {label[:60]}", True)
            else:
                self._stat_set("status", "idle")
                self._remove_overlay()
                self._emit_status("Online", True)
        except Exception as e:
            self._emit(f"[overlay] notify failed: {e}", "warn")

    def _notify_warn(self, text, kind="warn"):
        try:
            self.overlay_server.push_warn(text, kind=kind)
        except Exception:
            pass

    def _notify_info(self, text, kind="info"):
        try:
            self.overlay_server.push_warn(text, kind=kind)
        except Exception:
            pass

    def _notify_blocked(self, text):
        if getattr(self, "_suppress_next_block_banner", False):
            self._suppress_next_block_banner = False
            return
        try:
            url = (self.driver.current_url or "").lower() if self.driver else ""
            if url:
                parsed = urllib.parse.urlparse(url)
                on_home = (parsed.path in ("", "/")
                           and (not parsed.query
                                or parsed.query.startswith("hl=")))
                if on_home:
                    return
        except Exception:
            pass
        try:
            self.overlay_server.push_warn(text, kind="blocked")
        except Exception:
            pass

    def _is_on_youtube_home(self):
        try:
            url = (self.driver.current_url or "") if self.driver else ""
        except Exception:
            url = ""
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url.lower())
        except ValueError:
            return False
        host = parsed.hostname or ""
        if not (host == "youtube.com" or host.endswith(".youtube.com")):
            return False
        if parsed.path not in ("", "/"):
            return False
        q = parsed.query or ""
        if q and not q.startswith("hl="):
            return False
        return True

    def _check_for_rocholo(self, *texts):
        if not DETECT_ROCHOLO:
            return False
        if not self._is_on_youtube_home():
            return False
        try:
            joined = " \u2022 ".join(t for t in texts if t)
            if not joined:
                return False
            if not detect_rocholo(joined):
                return False
            self._stat_bump("rocholo_detected")
            self._totals_bump("rocholo_detected")
            self._stat_event("Rocholo Javinar video detected")
            self._emit("[bot] \U0001F7E3 Rocholo Javinar video detected.",
                       "ok")
            self._notify_info("Rocholo Javinar video detected", "info")
            return True
        except Exception:
            return False

    def _count_screenshots(self):
        try:
            return sum(1 for p in SCREENSHOT_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() in
                       (".png", ".jpg", ".jpeg", ".webp", ".bmp"))
        except Exception:
            return 0

    def _broadcast_screenshot_count(self):
        try:
            self.overlay_server.push_shots(self._count_screenshots())
        except Exception:
            pass

    def _schedule_screenshot(self, label=""):
        """Capture the browser window SCREENSHOT_DELAY seconds from now.

        (Missing in v3.4: the bridge called this but it was never defined,
        so no screenshot was ever taken.)"""
        if not SCREENSHOT_ENABLED or not self.driver or not self.running:
            return
        try:
            delay = max(0.0, float(SCREENSHOT_DELAY))
        except Exception:
            delay = 1.0
        t = threading.Timer(delay, self._take_screenshot, args=(label,))
        t.daemon = True
        self._shot_timers = [x for x in self._shot_timers if x.is_alive()]
        self._shot_timers.append(t)
        t.start()

    def _take_screenshot(self, label=""):
        d = self.driver
        if not d or not self.running:
            return None
        try:
            png = d.get_screenshot_as_png()
        except Exception as e:
            self._emit(f"[shots] Capture failed: {e}", "warn")
            return None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = _sanitize_filename(label, "screenshot")
        path = SCREENSHOT_DIR / f"{stamp}_{name}.png"
        n = 1
        while path.exists():
            path = SCREENSHOT_DIR / f"{stamp}_{name}_{n}.png"
            n += 1
        try:
            path.write_bytes(png)
        except Exception as e:
            self._emit(f"[shots] Could not save screenshot: {e}", "warn")
            return None
        self._stat_bump("shots_taken")
        self._totals_bump("shots_taken")
        self._broadcast_screenshot_count()
        self._emit(f"[shots] Saved {path.name}", "info")
        return path

    def _count_archived_zips(self):
        try:
            return sum(1 for p in SCREENSHOT_ARCHIVE_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() == ".zip")
        except Exception:
            return 0

    def clear_screenshots(self, archive_first=True):
        try:
            files = [p for p in SCREENSHOT_DIR.iterdir() if p.is_file()]
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "deleted": 0, "archived": 0}
        if not files:
            self._emit("[shots] Clear: nothing to clear.", "info")
            return {"ok": True, "deleted": 0, "archived": 0,
                    "archive": ""}
        archived = 0
        archive_name = ""
        if archive_first and ARCHIVE_SCREENSHOTS:
            try:
                cleaner = ScreenshotCleaner(self)
                archived, archive_name, err = cleaner._archive_files(files)
                if err:
                    self._emit(
                        f"[shots] Clear: archive failed ({err}); "
                        f"deleting anyway.", "warn")
                    archived = 0
                    archive_name = ""
            except Exception as e:
                self._emit(f"[shots] Clear archive error: {e}", "warn")
        deleted = 0
        for p in files:
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
        # Files already removed by the archiver count as cleared too.
        deleted += archived
        try:
            self._broadcast_screenshot_count()
        except Exception:
            pass
        msg = (f"[shots] Cleared {deleted} screenshot(s)"
               + (f", archived {archived} → "
                  f"screenshots_archive/{archive_name}"
                  if archived else "") + ".")
        self._emit(msg, "ok")
        return {"ok": True, "deleted": deleted, "archived": archived,
                "archive": archive_name}

    # ── Persistent totals ────────────────────────────────────────────
    def _totals_bump(self, key, by=1):
        try:
            with self._totals_lock:
                self._totals[key] = self._totals.get(key, 0) + by
            self._totals_dirty = True
        except Exception:
            pass
        try:
            now = time.monotonic()
            if now - getattr(self, "_last_totals_save", 0.0) >= 1.0:
                self._save_totals()
        except Exception:
            pass

    def _save_totals(self, force=False):
        try:
            with self._totals_save_lock:
                if not force and not getattr(self, "_totals_dirty", False):
                    return
                with self._totals_lock:
                    snapshot = dict(self._totals)
                _save_json(STATS_FILE, snapshot)
                self._totals_dirty = False
                self._last_totals_save = time.monotonic()
        except Exception:
            pass

    def get_totals_snapshot(self):
        try:
            with self._totals_lock:
                return dict(self._totals)
        except Exception:
            return {}

    def _stats_push_now(self):
        # refresh_driver=False: never talk to chromedriver from the caller's
        # thread (this is also called from the Qt UI thread).
        try:
            self.overlay_server.push_stats(
                self.get_stats_snapshot(refresh_driver=False))
        except Exception:
            pass

    def _stat_bump(self, key, by=1):
        try:
            with self._stats_lock:
                self._stats[key] = self._stats.get(key, 0) + by
        except Exception:
            pass
        self._stats_push_now()

    def _stat_set(self, key, value):
        try:
            with self._stats_lock:
                self._stats[key] = value
        except Exception:
            pass
        self._stats_push_now()

    def _stat_event(self, msg):
        try:
            with self._stats_lock:
                self._stats["last_event"] = str(msg)[:200]
                self._stats["last_event_at"] = time.time()
        except Exception:
            pass
        self._stats_push_now()

    def get_stats_snapshot(self, refresh_driver=True):
        try:
            with self._stats_lock:
                snap = dict(self._stats)
        except Exception:
            snap = {}
        snap["shots_on_disk"] = self._count_screenshots()
        snap["runtime_seconds"] = int(time.time() - snap.get("started_at",
                                                              time.time()))
        try:
            cache = getattr(self, "_drv_cache", None)
            now = time.monotonic()
            if refresh_driver and (cache is None or now - cache[0] >= 1.0):
                url = title = None
                d = self.driver
                if d:
                    try:
                        url = (d.current_url or "")[:300]
                    except Exception:
                        pass
                    try:
                        title = (d.title or "")[:200]
                    except Exception:
                        pass
                prev_u = cache[1] if cache else None
                prev_t = cache[2] if cache else None
                cache = (now, url if url is not None else prev_u,
                         title if title is not None else prev_t)
                self._drv_cache = cache
            if cache:
                if cache[1] is not None:
                    snap["current_url"] = cache[1]
                if cache[2] is not None:
                    snap["current_title"] = cache[2]
        except Exception:
            pass
        try:
            with self._totals_lock:
                for k, v in self._totals.items():
                    snap["total_" + k] = v
        except Exception:
            pass
        return snap

    def _blank_home_feed(self):
        if not BLANK_HOME_FEED:
            return
        d = self.driver
        if not d:
            return
        try:
            url = (d.current_url or "")
        except Exception:
            return
        low = url.lower()
        parsed = urllib.parse.urlparse(low)
        if parsed.path not in ("", "/"):
            return
        if parsed.query and not parsed.query.startswith("hl="):
            return
        port = self.overlay_server.port or PREFERRED_PORT
        try:
            d.execute_script(r"""
                (function(){
                    const PORT = arguments[0];
                    const ROOT_ID = '__opencuy_home_stats__';
                    const STYLE_ID = '__opencuy_blank_home__';

                    const css = `
                        ytd-browse[page-subtype="home"] #primary,
                        ytd-browse[page-subtype="home"] #contents,
                        ytd-browse[page-subtype="home"] #content,
                        ytd-browse[page-subtype="home"] ytd-rich-grid-renderer,
                        ytd-browse[page-subtype="home"] ytd-rich-section-renderer,
                        ytd-browse[page-subtype="home"] ytd-rich-grid-row,
                        ytd-browse[page-subtype="home"] ytd-rich-item-renderer,
                        ytd-browse[page-subtype="home"] ytd-rich-shelf-renderer,
                        ytd-browse[page-subtype="home"] #header.ytd-rich-grid-renderer,
                        #secondary.ytd-browse[page-subtype="home"],
                        ytd-browse[page-subtype="home"] #chips-wrapper
                            { display: none !important; visibility: hidden !important; }
                        ytd-browse[page-subtype="home"],
                        ytd-page-manager, #page-manager
                            { padding: 0 !important; margin: 0 !important;
                              max-width: 100% !important;
                              width: 100% !important; }
                        body { overflow: hidden !important; }
                    `;
                    let st = document.getElementById(STYLE_ID);
                    if (!st) {
                        st = document.createElement('style');
                        st.id = STYLE_ID;
                        st.textContent = css;
                        document.documentElement.appendChild(st);
                    }
                    for (const sel of [
                        'ytd-browse[page-subtype="home"] ytd-rich-grid-renderer',
                        'ytd-browse[page-subtype="home"] ytd-rich-section-renderer',
                        'ytd-browse[page-subtype="home"] ytd-rich-shelf-renderer',
                    ]) {
                        for (const el of document.querySelectorAll(sel)) {
                            try { el.remove(); } catch(e) {}
                        }
                    }

                    let root = document.getElementById(ROOT_ID);
                    if (!root) {
                        root = document.createElement('iframe');
                        root.id = ROOT_ID;
                        root.setAttribute('frameborder', '0');
                        root.setAttribute('scrolling', 'no');
                        root.src = 'http://127.0.0.1:' + PORT + '/home-dash';
                        root.style.cssText = [
                            'position:fixed','inset:0',
                            'width:100vw','height:100vh',
                            'border:0','margin:0','padding:0',
                            'z-index:2147483000',
                            'background:#0d0b1a'
                        ].join(';') + ' !important';
                        document.documentElement.appendChild(root);
                    }
                    return 'ok';
                })();
            """, port)
        except Exception:
            pass

    def _install_chrome_cleanup(self):
        d = self.driver
        if not d:
            return
        try:
            url = (d.current_url or "")
        except Exception:
            return
        if not _url_is_allowed_site(url):
            return
        low = url.lower()
        parsed = urllib.parse.urlparse(low)
        on_home = (parsed.path in ("", "/") and
                   (not parsed.query or parsed.query.startswith("hl=")))

        hide_side  = "1" if HIDE_SIDEBAR else "0"
        hide_top   = "1" if HIDE_TOPBAR  else "0"
        filter_feed = "1" if (FILTER_HOME_FEED and on_home) else "0"
        try:
            d.execute_script(r"""
                (function(){
                    const STYLE_ID = '__opencuy_chrome_cleanup__';
                    const HIDE_SIDE = arguments[0] === '1';
                    const HIDE_TOP  = arguments[1] === '1';
                    const FILTER_HOME = arguments[2] === '1';

                    const parts = [];

                    if (HIDE_TOP) {
                        parts.push(`
                            ytd-masthead, #masthead-container,
                            ytd-mini-guide-renderer,
                            #country-code
                                { display: none !important; }
                            ytd-app { --ytd-masthead-height: 0px !important;
                                      --ytd-toolbar-height: 0px !important; }
                            ytd-page-manager, #page-manager,
                            ytd-app #content { padding-top: 0 !important;
                                                margin-top: 0 !important; }
                            ytd-watch-flexy { padding-top: 0 !important;
                                              margin-top: 0 !important; }
                        `);
                    }

                    if (HIDE_SIDE) {
                        parts.push(`
                            #guide, ytd-guide-renderer,
                            ytd-mini-guide-renderer,
                            ytd-app #guide-button,
                            tp-yt-app-drawer#guide,
                            tp-yt-app-drawer#guide + #scrim,
                            ytd-app > #content > #guide,
                            ytd-watch-flexy #secondary #guide
                                { display: none !important; }
                            ytd-app { --ytd-guide-width: 0px !important; }
                            ytd-page-manager #primary,
                            #primary.ytd-watch-flexy,
                            ytd-watch-flexy #primary,
                            #columns.ytd-watch-flexy #primary
                                { margin-left: 0 !important;
                                  padding-left: 0 !important;
                                  max-width: none !important;
                                  width: 100% !important; }
                            #columns, #page-manager,
                            ytd-browse { margin-left: 0 !important;
                                          padding-left: 0 !important; }
                        `);
                    }

                    let st = document.getElementById(STYLE_ID);
                    if (!st) {
                        st = document.createElement('style');
                        st.id = STYLE_ID;
                        st.textContent = parts.join('\n');
                        document.documentElement.appendChild(st);
                    } else {
                        st.textContent = parts.join('\n');
                    }

                    if (FILTER_HOME) {
                        const BADGE_SELS = [
                            '.badge-shape-wiz__text',
                            'ytd-badge-supported-renderer',
                            '.ytd-thumbnail-overlay-time-status-renderer',
                            'ytd-thumbnail-overlay-time-status-renderer',
                            '.ytBadgeShapeText',
                        ];
                        const LIVE_RX = /^\s*live\b/i;
                        const NEW_RX = /^\s*(\d+\s*(second|minute|hour)s?\s*ago|just now|moments? ago|new)\s*$/i;

                        const isHome = document.querySelector(
                            'ytd-browse[page-subtype="home"]');
                        if (isHome) {
                            for (const card of document.querySelectorAll(
                                    'ytd-browse[page-subtype="home"] ytd-rich-item-renderer')) {
                                if (card.dataset.ocFiltered === '1') continue;
                                let hide = false;

                                for (const bs of BADGE_SELS) {
                                    for (const b of card.querySelectorAll(bs)) {
                                        const t = (b.textContent || '').trim();
                                        if (!t) continue;
                                        if (LIVE_RX.test(t) || NEW_RX.test(t)) {
                                            hide = true;
                                            break;
                                        }
                                        if (t.toLowerCase() === 'live' ||
                                            t.toLowerCase().startsWith('live ')) {
                                            hide = true;
                                            break;
                                        }
                                    }
                                    if (hide) break;
                                }

                                if (!hide) {
                                    for (const m of card.querySelectorAll(
                                            '#metadata-line span, .inline-metadata-item, ytd-video-meta-block span')) {
                                        const t = (m.textContent || '').trim();
                                        if (!t) continue;
                                        if (LIVE_RX.test(t) || NEW_RX.test(t)) {
                                            hide = true;
                                            break;
                                        }
                                    }
                                }

                                if (hide) {
                                    card.style.display = 'none';
                                    card.dataset.ocFiltered = '1';
                                } else {
                                    card.dataset.ocFiltered = '0';
                                }
                            }
                            for (const sec of document.querySelectorAll(
                                    'ytd-browse[page-subtype="home"] ytd-rich-section-renderer')) {
                                const t = (sec.textContent || '').toLowerCase();
                                if (t.startsWith('live') ||
                                    t.includes('live now') ||
                                    t.includes('live streams')) {
                                    sec.style.display = 'none';
                                }
                            }

                            for (const shelf of document.querySelectorAll(
                                    'ytd-browse[page-subtype="home"] '
                                    + 'ytd-rich-shelf-renderer, '
                                    + 'ytd-browse[page-subtype="home"] '
                                    + 'ytd-rich-section-renderer')) {
                                try {
                                    const title = shelf.querySelector(
                                        '#title, #rich-shelf-header #title, '
                                        + 'h2 #title');
                                    const titleText = (title
                                        && (title.textContent || '').trim()
                                        || '').toLowerCase();
                                    const hasShortsItems =
                                        !!shelf.querySelector(
                                            'ytm-shorts-lockup-view-model, '
                                            + 'ytm-shorts-lockup-view-model-v2, '
                                            + 'a[href^="/shorts/"]');
                                    if (titleText === 'shorts'
                                        || hasShortsItems) {
                                        shelf.remove();
                                    }
                                } catch(e) {}
                            }

                            for (const a of document.querySelectorAll(
                                    'ytd-browse[page-subtype="home"] '
                                    + 'a[href^="/shorts/"]')) {
                                try {
                                    const card = a.closest(
                                        'ytd-rich-item-renderer, '
                                        + 'ytd-rich-section-renderer, '
                                        + 'ytd-rich-shelf-renderer, '
                                        + 'ytd-video-renderer');
                                    if (card) card.remove();
                                } catch(e) {}
                            }
                        }
                    }

                    try {
                        const REMOVE_TITLES = new Set([
                            'shorts',
                            'movies & tv',
                            'movies and tv',
                            'youtube premium',
                            'youtube tv',
                            'youtube music',
                            'youtube kids',
                            'shopping',
                            'downloads',
                            'your videos',
                            'report history',
                            'live',
                        ]);
                        for (const entry of document.querySelectorAll(
                                'ytd-guide-entry-renderer, '
                                + 'ytd-guide-collapsible-entry-renderer, '
                                + 'ytd-guide-section-renderer')) {
                            const a = entry.querySelector('a#endpoint, a[title]');
                            if (!a) continue;
                            const t = (a.getAttribute('title')
                                       || '').trim().toLowerCase();
                            if (REMOVE_TITLES.has(t)) {
                                try { entry.remove(); } catch(e) {}
                            }
                        }
                        for (const sec of document.querySelectorAll(
                                'ytd-guide-section-renderer')) {
                            const title = sec.querySelector(
                                '#guide-section-title');
                            const t = (title && title.textContent
                                       || '').trim().toLowerCase();
                            if (t === 'explore'
                                || t === 'more from youtube') {
                                const items = sec.querySelectorAll(
                                    'ytd-guide-entry-renderer');
                                let visible = 0;
                                for (const it of items) {
                                    if (it.offsetParent !== null) visible++;
                                }
                                if (visible === 0) {
                                    try { sec.remove(); } catch(e) {}
                                }
                            }
                        }
                    } catch(e) {}
                    return 'ok';
                })();
            """, hide_side, hide_top, filter_feed)
        except Exception:
            pass

    def _install_theme_background(self):
        if not THEME_YOUTUBE_BG:
            try:
                d = self.driver
                if d:
                    d.execute_script("""
                        const st = document.getElementById(
                            '__opencuy_theme_bg__');
                        if (st && st.parentNode) st.parentNode.removeChild(st);
                    """)
            except Exception:
                pass
            self.overlay_server._last_painted_theme_name = None
            self.overlay_server._last_painted_palette = None
            return
        d = self.driver
        if not d:
            return
        try:
            url = (d.current_url or "")
        except Exception:
            return
        if not _url_is_allowed_site(url):
            return

        try:
            cfg = _load_json(THEME_FILE, None) or {}
            name = (cfg.get("name") or DEFAULT_THEME["name"]).lower()
        except Exception:
            name = DEFAULT_THEME["name"]
        pal = THEME_PALETTE.get(name, THEME_PALETTE[DEFAULT_THEME["name"]])

        last_name = getattr(self.overlay_server,
                            "_last_painted_theme_name", None)
        last_pal = getattr(self.overlay_server,
                           "_last_painted_palette", None)
        if last_name == name and last_pal == pal:
            try:
                exists = d.execute_script(
                    "return !!document.getElementById('__opencuy_theme_bg__');")
            except Exception:
                exists = True
            if exists:
                return

        bg = pal.get("bg", "#0d0b1a")
        card = pal.get("card", "#141129")
        accent = pal.get("accent", "#8b5cf6")
        text = pal.get("text", "#f4f4ff")
        dim = pal.get("dim", "#8b88b8")

        try:
            self.overlay_server._last_theme_payload = {
                "name": name, "palette": dict(pal),
            }
        except Exception:
            pass

        try:
            d.execute_script(r"""
                (function(){
                    const STYLE_ID = '__opencuy_theme_bg__';
                    const BG = arguments[0];
                    const CARD = arguments[1];
                    const ACCENT = arguments[2];
                    const TEXT = arguments[3];
                    const DIM = arguments[4];

                    const css = `
                        html, body, ytd-app, ytd-page-manager,
                        #content, #page-manager, #columns,
                        ytd-browse, ytd-watch-flexy,
                        ytd-search, ytd-channel-videos-tab-renderer,
                        tp-yt-app-drawer #contentContainer
                            { background: ${BG} !important;
                              color: ${TEXT} !important; }

                        #guide, ytd-guide-renderer,
                        ytd-mini-guide-renderer,
                        ytd-masthead, #masthead-container,
                        #masthead
                            { background: ${CARD} !important;
                              color: ${TEXT} !important; }

                        ytd-rich-item-renderer,
                        ytd-rich-grid-media,
                        ytd-video-renderer,
                        ytd-grid-video-renderer,
                        ytd-compact-video-renderer,
                        ytd-playlist-renderer,
                        ytd-channel-renderer,
                        ytd-shelf-renderer,
                        ytd-rich-section-renderer,
                        ytd-watch-metadata,
                        ytd-watch-next-secondary-results-renderer,
                        ytd-comments,
                        ytd-comment-thread-renderer,
                        #primary, #secondary, #related,
                        ytd-menu-popup-renderer,
                        tp-yt-paper-dialog,
                        ytd-popup-container > *
                            { background: ${CARD} !important;
                              color: ${TEXT} !important;
                              border-color: rgba(255,255,255,0.08) !important; }

                        yt-formatted-string, .yt-core-attributed-string,
                        #video-title, #video-title-link,
                        h1, h2, h3, h4, span, a
                            { color: ${TEXT} !important; }
                        #metadata-line, .inline-metadata-item,
                        ytd-video-meta-block, .ytd-video-meta-block
                            { color: ${DIM} !important; }

                        a.yt-simple-endpoint:hover, a:hover
                            { color: ${ACCENT} !important; }
                        .ytp-play-progress
                            { background: ${ACCENT} !important; }
                        .ytp-scrubber-button
                            { background: ${ACCENT} !important;
                              border-color: ${ACCENT} !important; }
                        tp-yt-paper-tab[aria-selected="true"]
                            > .tab-content
                            { color: ${ACCENT} !important; }

                        ytd-button-renderer button,
                        yt-button-shape button,
                        tp-yt-paper-button,
                        ytd-chip-cloud-chip-renderer
                            { background: ${CARD} !important;
                              color: ${TEXT} !important;
                              border-color: rgba(255,255,255,0.08) !important; }
                        ytd-chip-cloud-chip-renderer[selected]
                            { background: ${ACCENT} !important;
                              color: #ffffff !important; }

                        input, textarea, ytd-searchbox,
                        #search, .ytd-searchbox
                            { background: ${CARD} !important;
                              color: ${TEXT} !important;
                              border-color: rgba(255,255,255,0.08) !important; }

                        .ytp-gradient-top, .ytp-gradient-bottom
                            { background-image: none !important; }
                    `;

                    let st = document.getElementById(STYLE_ID);
                    if (!st) {
                        st = document.createElement('style');
                        st.id = STYLE_ID;
                        st.textContent = css;
                        document.documentElement.appendChild(st);
                    } else {
                        st.textContent = css;
                    }

                    try {
                        let mt = document.querySelector(
                            'meta[name="theme-color"]');
                        if (!mt) {
                            mt = document.createElement('meta');
                            mt.name = 'theme-color';
                            document.head.appendChild(mt);
                        }
                        mt.content = BG;
                    } catch(e) {}

                    return 'ok';
                })();
            """, bg, card, accent, text, dim)
            self.overlay_server._last_painted_theme_name = name
            self.overlay_server._last_painted_palette = dict(pal)
        except Exception:
            pass

    def _strip_playlist_params(self, url):
        if not url:
            return url
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return url
        if parsed.path != "/watch":
            return url
        qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        stripped = [
            (k, v) for (k, v) in qs
            if k.lower() not in ("list", "start_radio", "index",
                                 "playnext", "pp")
        ]
        new_query = urllib.parse.urlencode(stripped)
        rebuilt = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment))
        return rebuilt

    def _start_autoplay_failsafe(self, video_url=None):
        """For 10 seconds after admission, retry YouTube video.play() once per second.
        This is deliberately limited to the current approved watch URL.
        """
        if not self.driver:
            return
        try:
            if getattr(self, "_autoplay_failsafe_thread", None) and self._autoplay_failsafe_thread.is_alive():
                return
        except Exception:
            pass
        target_url = (video_url or self.driver.current_url or "").strip()

        def worker():
            for attempt in range(10):
                if not self.running:
                    break
                try:
                    current = (self.driver.current_url or "").strip()
                    if target_url and current != target_url:
                        break
                    result = self.driver.execute_script(r"""
                        const v = document.querySelector('video');
                        if (!v) return 'no-video';
                        const playButton = document.querySelector(
                            '.ytp-large-play-button[aria-label="Play"]'
                        );
                        if (playButton || v.paused) {
                            try {
                                const p = v.play();
                                if (p && typeof p.catch === 'function') p.catch(() => {});
                                return playButton ? 'play-button-found' : 'paused';
                            } catch (e) {
                                return 'play-error';
                            }
                        }
                        return 'playing';
                    """)
                    if result in ("play-button-found", "paused"):
                        self._emit(f"[autoplay] play() retry {attempt + 1}/10", "info")
                except Exception as e:
                    self._emit(f"[autoplay] retry failed: {e}", "warn")
                if attempt < 9:
                    time.sleep(1.0)

        self._autoplay_failsafe_thread = threading.Thread(
            target=worker, daemon=True, name="OpenCUY-AutoplayFailsafe")
        self._autoplay_failsafe_thread.start()

    def _set_video_gate(self, allowed):
        """Open/close the page-level media gate installed before navigation."""
        if not self.driver:
            return
        try:
            self.driver.execute_script(
                "window.__opencuy_video_gate__ = arguments[0] === true;",
                bool(allowed))
        except Exception:
            pass

    def _safe_navigate(self, url, label="page"):
        if not url:
            return False
        url = self._strip_playlist_params(url)
        safe, reason = self.checker.check(url)
        if not safe:
            self._emit(f"[bot] BLOCKED {label}: {reason}. "
                       f"Visible window NOT navigated.", "warn")
            self._notify_warn(
                f"⚠ Navigation blocked — {reason} ({label})", "warn")
            return False
        try:
            self.driver.get(url)
            try: self._install_overlay_on_current_page()
            except Exception: pass
            try: self._blank_home_feed()
            except Exception: pass
            try: self._install_chrome_cleanup()
            except Exception: pass
            try: self._install_theme_background()
            except Exception: pass
            # The URL already passed the headless yt-dlp admission check.
            # Only now may the visible page play media.
            try: self._set_video_gate(True)
            except Exception: pass
            return True
        except Exception as e:
            self._emit(f"[bot] Navigate {label} failed: {e}", "err")
            return False

    def _on_channel_page(self):
        d = self.driver
        if not d:
            return False
        try:
            url = (d.current_url or "").lower()
        except Exception:
            return False
        if "/watch" in url:
            return False
        try:
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ""
            path = parsed.path or ""
        except ValueError:
            return False
        if not (host == "youtube.com" or host.endswith(".youtube.com")):
            return False
        if path.startswith(("/@", "/channel/", "/c/", "/user/")):
            return True
        return False

    def _check_channel_featured(self):
        if not (BLOCK_CHANNEL_FEATURED or MUTE_CHANNEL_AUTOPLAY):
            return
        if not self._on_channel_page():
            return

        if MUTE_CHANNEL_AUTOPLAY:
            try:
                self.driver.execute_script(r"""
                    try {
                        const v = document.querySelector('video');
                        if (v) {
                            v.muted = true;
                            v.pause && v.pause();
                        }
                        const p = document.querySelector('#movie_player');
                        if (p && typeof p.pauseVideo === 'function') {
                            p.pauseVideo();
                        }
                    } catch(e) {}
                """)
            except Exception:
                pass

        if not BLOCK_CHANNEL_FEATURED:
            return

        try:
            info = self.driver.execute_script(r"""
                (function(){
                    const out = {
                        video_id: "",
                        title: "",
                        is_live: false,
                        badge_texts: [],
                    };
                    const player = document.querySelector(
                        'ytd-channel-video-player-renderer');
                    if (player) {
                        const a = player.querySelector(
                            'a[href*="/watch?v="]');
                        if (a) {
                            const href = a.href || '';
                            const m = href.match(
                                /[?&]v=([A-Za-z0-9_-]{11})/);
                            if (m) out.video_id = m[1];
                        }
                        const t = player.querySelector(
                            '#video-title, '
                            + 'yt-formatted-string#video-title, '
                            + 'h3, .ytd-channel-video-player-renderer');
                        if (t) out.title = (t.textContent || '')
                                            .trim().slice(0, 160);
                        for (const b of player.querySelectorAll(
                                'ytd-badge-supported-renderer, '
                                + '.ytBadgeShapeText, '
                                + '.badge-shape-wiz__text')) {
                            const bt = (b.textContent || '').trim();
                            if (bt) out.badge_texts.push(bt);
                        }
                    }
                    if (!out.video_id) {
                        const p = document.querySelector('#movie_player');
                        if (p && typeof p.getVideoData === 'function') {
                            const vd = p.getVideoData() || {};
                            if (vd.video_id) out.video_id = vd.video_id;
                            if (vd.title) out.title = vd.title
                                            .slice(0, 160);
                            if (vd.isLive) out.is_live = true;
                        }
                    }
                    if (!out.video_id) return null;
                    return out;
                })();
            """)
            if not info:
                return
            vid = info.get("video_id") or ""
            if not vid:
                return
            try:
                cur = (self.driver.current_url or "").lower()
            except Exception:
                cur = ""
            if "/watch" in cur:
                return

            reason = None
            if BLOCK_LIVE_STREAMS and info.get("is_live"):
                reason = "live stream"
            if not reason:
                badges = info.get("badge_texts") or []
                if any("free" in (b or "").lower()
                       and "ads" in (b or "").lower() for b in badges):
                    reason = "ad-supported (Free with ads badge)"

            if not reason:
                # We can't probe the featured video without navigating,
                # so we can only block based on badges visible now.
                return

            self._emit(
                f"[bot] Channel page bypass blocked — featured video "
                f"{vid} ({reason}). Returning to YouTube home.", "warn")
            self._notify_blocked(
                f"🚫 CHANNEL FEATURED VIDEO BLOCKED\n\n"
                f"The channel owner pinned a video that's not allowed:\n"
                f"{reason}\n\nReturning home.")
            self._stat_bump("channel_bypass_blocked")
            self._totals_bump("channel_bypass_blocked")
            self._stat_event(f"channel featured blocked: {reason}")
            try:
                self.driver.get("https://www.youtube.com/")
            except Exception:
                pass
            try: self._blank_home_feed()
            except Exception: pass
            try: self._install_chrome_cleanup()
            except Exception: pass
            try: self._install_theme_background()
            except Exception: pass
            try: self._install_overlay_on_current_page()
            except Exception: pass
        except Exception:
            pass

    def _current_video_id(self):
        try:
            url = (self.driver.current_url or "") if self.driver else ""
        except Exception:
            url = ""
        m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
        return m.group(1) if m else ""

    # ── Mod-lock ─────────────────────────────────────────────────────
    def _set_mod_lock(self, video_id, title=""):
        if not MOD_LOCK_VIDEOS:
            return
        dur = None
        try:
            dur = self.driver.execute_script(
                "const v=document.querySelector('video');"
                "return v ? v.duration : null;")
        except Exception:
            dur = None
        try:
            dur = float(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur = None
        is_short = bool(dur is not None and dur < 120.0)
        self._mod_lock_active = True
        self._mod_lock_video_id = video_id
        self._mod_lock_started_at = time.time()
        self._mod_lock_short = is_short
        if is_short:
            self._mod_lock_short_until = time.time() + (dur or 60) + 5.0
        else:
            self._mod_lock_short_until = time.time() + 6 * 3600
        dur_txt = f"{dur:.0f}s" if dur is not None else "?"
        self._emit(
            f"[bot] 🔒 Mod-lock engaged — {video_id} "
            f"({'short' if is_short else 'full-length'}, {dur_txt}). "
            f"Chat cannot change the video until it ends.", "ok")
        self._stat_event(f"mod-lock: {video_id}")

    def _clear_mod_lock(self, reason=""):
        if not self._mod_lock_active:
            return
        self._mod_lock_active = False
        vid = self._mod_lock_video_id
        self._mod_lock_video_id = ""
        self._mod_lock_short = False
        self._mod_lock_short_until = 0.0
        self._emit(f"[bot] 🔓 Mod-lock released"
                   + (f" ({reason})" if reason else "") + ".", "ok")

    def _is_mod_locked(self):
        if not self._mod_lock_active:
            return False, ""
        if self._mod_lock_short and time.time() >= self._mod_lock_short_until:
            self._clear_mod_lock("short video finished")
            return False, ""
        if not self._mod_lock_short and time.time() >= self._mod_lock_short_until:
            self._clear_mod_lock("timeout")
            return False, ""
        cur = self._current_video_id()
        if cur and self._mod_lock_video_id and cur != self._mod_lock_video_id:
            self._clear_mod_lock("video changed")
            return False, ""
        try:
            ended = self.driver.execute_script(
                "const v=document.querySelector('video');"
                "if(!v) return false;"
                "return !!(v.ended || "
                "(v.duration && v.currentTime >= v.duration - 0.5 && "
                " v.paused));")
            if ended:
                self._clear_mod_lock("video ended (polled)")
                return False, ""
        except Exception:
            pass
        return True, self._mod_lock_video_id

    def _video_is_too_new(self):
        if not BLOCK_UNDER_24H:
            return False
        if getattr(self, "_privileged_request", False):
            return False
        info = video_metadata_probe(self.driver)
        if info is None:
            self._emit("[bot] yt-dlp metadata probe failed — blocking video.", "warn")
            return True
        age = info.get("age_seconds")
        if age is None:
            self._emit(
                f"[bot] Could not parse age from yt-dlp "
                f"({info.get('age_text')!r}) — allowing.", "info")
            return False
        if age < 86400:
            self._emit(
                f"[bot] Video is only {age/3600.0:.1f}h old "
                f"(< 24h, yt-dlp: {info.get('age_text')!r}) — blocking.",
                "warn")
            return True
        return False

    def _scan_current_page_for_ads(self):
        if not self.driver: return True
        try:
            url = (self.driver.current_url or "").lower()
        except Exception:
            url = ""
        if "/watch" not in url:
            return True
        try:
            badge_js = r"""
                const scopes = ['ytd-watch-metadata', '#above-the-fold',
                                '#primary-inner', '#info-contents'];
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
                self._stat_bump("ads_blocked")
                self._totals_bump("ads_blocked")
                self._stat_event(f"ad detected: {', '.join(hits)}")
                self._emit(f"[filter] ⚠ ad phrase(s): {', '.join(hits)}", "warn")
                self._notify_blocked(
                    f"🚫 AD-SUPPORTED VIDEO\n\n"
                    f"This video has a 'Free with ads' badge "
                    f"({', '.join(hits)}).\nReturning home.")
                time.sleep(1.2)
                if BAIL_TO_BLANK_ON_AD:
                    self._emit("[filter] → YouTube home", "warn")
                    try: self.driver.get("https://www.youtube.com/")
                    except Exception as e: self._emit(f"[filter] {e}", "err")
                    try: self._blank_home_feed()
                    except Exception: pass
                    self._notify_watch(False, "")
                    return False
        except Exception as e:
            self._emit(f"[filter] Scan error: {e}", "err")
        return True

    def _enforce_video_rules(self, label=""):
        """Post-navigation yt-dlp checks for live / 360 / age / cuss.

        Returns True if the video was allowed to stay, False if we
        bounced home.
        """
        info = video_metadata_probe(self.driver)
        if info is None:
            self._emit(
                f"[bot] yt-dlp metadata probe unavailable for {label or 'video'}"
                " — BLOCKING (fail-closed).", "warn")
            try:
                self.driver.get("https://www.youtube.com/")
                self._blank_home_feed()
            except Exception:
                pass
            self._notify_watch(False, "")
            return False

        vid = self._current_video_id()

        if BLOCK_LIVE_STREAMS and info.get("is_live"):
            self._stat_bump("live_blocked")
            self._totals_bump("live_blocked")
            self._stat_event(f"blocked live: {vid}")
            self._emit(f"[bot] BLOCKED live stream (yt-dlp): {vid}.", "warn")
            self._notify_blocked(
                f"🚫 LIVE STREAM BLOCKED\n\n"
                f"This video ({vid or '?'}) is a live stream.\n"
                f"Live streams are disabled in Config.")
            time.sleep(1.2)
            try: self.driver.get("https://www.youtube.com/")
            except Exception: pass
            try: self._blank_home_feed()
            except Exception: pass
            self._notify_watch(False, "")
            return False

        if BLOCK_360 and info.get("is_360"):
            self._stat_event(f"blocked 360°: {vid}")
            self._emit(f"[bot] BLOCKED 360° video (yt-dlp): {vid}.", "warn")
            self._notify_blocked(
                f"🚫 360°/VR VIDEO BLOCKED\n\n"
                f"This video ({vid or '?'}) is a spherical/VR video.\n"
                f"360° videos are disabled.")
            time.sleep(1.2)
            try: self.driver.get("https://www.youtube.com/")
            except Exception: pass
            try: self._blank_home_feed()
            except Exception: pass
            self._notify_watch(False, "")
            return False

        if (BLOCK_UNDER_24H
                and not getattr(self, "_privileged_request", False)):
            age = info.get("age_seconds")
            if age is not None and age < 86400:
                self._stat_bump("under24h_blocked")
                self._totals_bump("under24h_blocked")
                self._stat_event(f"blocked <24h: {vid}")
                self._emit(
                    f"[bot] BLOCKED video (yt-dlp): {vid} is only "
                    f"{age/3600.0:.1f}h old (text={info.get('age_text')!r}).",
                    "warn")
                self._notify_blocked(
                    f"🚫 VIDEO TOO NEW\n\n"
                    f"This video ({vid or '?'}) was uploaded "
                    f"{age/3600.0:.1f} hours ago.\n"
                    f"Videos must be at least 24 hours old.")
                time.sleep(1.2)
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                try: self._blank_home_feed()
                except Exception: pass
                self._notify_watch(False, "")
                return False

        if PROBE_CLICKS:
            title = info.get("title") or ""
            channel = info.get("channel") or ""
            hit_text = ""
            if detect_cuss(title):
                hit_text = title
            elif detect_cuss(channel):
                hit_text = channel
            if hit_text:
                self._stat_bump("cuss_blocked")
                self._totals_bump("cuss_blocked")
                self._stat_event(f"blocked cuss: {vid}")
                self._emit(
                    f"[bot] BLOCKED video — cuss word in "
                    f"title/channel: {hit_text[:80]!r}.", "warn")
                self._notify_blocked(
                    f"🚫 VIDEO BLOCKED — PROFANITY\n\n"
                    f"The title or channel contains a cuss word:\n"
                    f"{hit_text[:80]!r}")
                time.sleep(1.2)
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                try: self._blank_home_feed()
                except Exception: pass
                self._notify_watch(False, "")
                return False

        # Metadata is clean; check ad badges
        if not self._scan_current_page_for_ads():
            return False

        title = info.get("title") or ""
        try:
            page_title = self.driver.title or ""
        except Exception:
            page_title = ""
        self._check_for_rocholo(title, page_title)
        self._stat_bump("videos_loaded")
        self._totals_bump("videos_loaded")
        self._stat_event(f"loaded video: {vid or '?'}")
        self._stat_set("status", "playing")
        self._emit(
            f"[bot] Loaded video: {vid or '?'} — {title or page_title} "
            f"(age={info.get('age_text') or '?'}, "
            f"live={info.get('is_live')}, "
            f"360={info.get('is_360')})", "ok")
        if getattr(self, "_privileged_request", False):
            try:
                if vid:
                    self._set_mod_lock(vid, title or page_title)
            except Exception as e:
                self._emit(f"[bot] mod-lock set failed: {e}", "warn")
        self._notify_watch(True, title or page_title or "video")
        return True

    def run(self):
        try:
            self.overlay_server.start()
            try:
                self.overlay_server.push_stats(self.get_stats_snapshot())
            except Exception:
                pass
            try:
                pal = _current_theme_palette()
                try:
                    cfg = _load_json(THEME_FILE, None) or {}
                    name = (cfg.get("name") or "nexo").lower()
                except Exception:
                    name = "nexo"
                self.overlay_server.push_theme({
                    "name": name, "palette": dict(pal),
                })
            except Exception:
                pass
        except Exception as e:
            self._emit(f"[overlay] server start failed: {e}", "warn")

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
        opts.add_argument("--autoplay-policy=no-user-gesture-required")
        opts.add_argument(
            "--disable-features=CalculateNativeWinOcclusion,"
            "BlockInsecurePrivateNetworkRequests")

        try:
            self.driver = webdriver.Chrome(options=opts)
        except Exception as e:
            self._emit(f"[bot] Failed to launch Chrome: {e}", "err")
            self._emit_status("Browser launch failed", False)
            return

        try:
            gate_script = r"""
                (() => {
                    // Every newly loaded document starts LOCKED.  This runs
                    // before YouTube's own player scripts, so autoplay/user
                    // clicks cannot start a video while yt-dlp is checking it.
                    window.__opencuy_video_gate__ = false;
                    const blockedPlay = function() {
                        if (window.__opencuy_video_gate__ === true) {
                            return __opencuy_original_play.apply(this, arguments);
                        }
                        try { this.pause(); this.currentTime = 0; } catch(e) {}
                        return Promise.reject(new DOMException('OpenCUY video gate is locked', 'NotAllowedError'));
                    };
                    try {
                        window.__opencuy_original_play = HTMLMediaElement.prototype.play;
                        HTMLMediaElement.prototype.play = blockedPlay;
                    } catch(e) {}
                    const stop = () => {
                        if (window.__opencuy_video_gate__ === true) return;
                        try {
                            document.querySelectorAll('video, audio').forEach(v => {
                                if (!v.paused) v.pause();
                                try { v.currentTime = 0; } catch(e) {}
                            });
                        } catch(e) {}
                    };
                    document.addEventListener('play', stop, true);
                    document.addEventListener('playing', stop, true);
                    new MutationObserver(stop).observe(document.documentElement || document, {subtree:true, childList:true});
                    setInterval(stop, 100);
                })();
            """
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',"
                           "{get:()=>undefined});" + gate_script})
        except Exception as e:
            self._emit(f"[bot] pre-navigation video gate install failed: {e}", "warn")

        self._register_overlay_cdp()

        try:
            self.driver.get("https://www.youtube.com/")
            self._emit("[bot] Navigated to YouTube home.", "ok")
        except Exception as e:
            self._emit(f"[bot] Startup navigation failed: {e}", "warn")

        self._install_overlay_on_current_page()
        self._blank_home_feed()
        self._install_chrome_cleanup()
        self._install_theme_background()
        self._broadcast_screenshot_count()

        self._stat_set("status", "idle")
        self._emit("[bot] Browser ready — visible video playback is locked behind headless yt-dlp.", "ok")
        self._emit_status("Online", True)

        self.overlay_watchdog = OverlayWatchdog(self, self.q)
        self.overlay_watchdog.start()

        self.end_watcher = HomeOnVideoEnd(self, self.q)
        self.end_watcher.start()

        self.watchdog = YouTubeWatchdog(self, self.q)
        self.watchdog.start()

        self.video_gate = VideoNavigationGate(self, self.q)
        self.video_gate.start()

        self.scroller.start()

        try:
            self.overlay_server.push_stats(self.get_stats_snapshot())
        except Exception:
            pass

        self._stats_thread = StatsBroadcaster(self)
        self._stats_thread.start()

        self._shot_cleanup_thread = ScreenshotCleaner(self)
        self._shot_cleanup_thread.start()

        threading.Thread(target=self.checker.start, daemon=True).start()

        while self.running:
            try:
                task = self.tasks.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if len(task) >= 6:
                    kind, arg, lucky, source, lang, privileged = task[:6]
                else:
                    kind, arg, lucky, source, lang = task[:5]
                    privileged = False
            except Exception:
                continue
            prefix = f"[{source}] " if source else ""
            self._privileged_request = bool(privileged)
            try:
                self._dispatch(kind, arg, lucky, prefix, lang)
            except Exception as e:
                self._emit(f"{prefix}[bot] Error in '{kind}': {e}", "err")
            finally:
                self._privileged_request = False

        for t in list(self._shot_timers):
            try: t.cancel()
            except Exception: pass
        try: self.checker.stop()
        except Exception: pass
        try: self.overlay_server.stop()
        except Exception: pass
        if self.overlay_watchdog:
            self.overlay_watchdog.stop()
            try: self.overlay_watchdog.join(timeout=2)
            except Exception: pass
        if self.watchdog:
            self.watchdog.stop(); self.watchdog.join(timeout=2)
        try: self.scroller.stop()
        except Exception: pass
        if self._stats_thread:
            self._stats_thread.stop()
        if self._shot_cleanup_thread:
            self._shot_cleanup_thread.stop()
        if self.end_watcher:
            self.end_watcher.stop(); self.end_watcher.join(timeout=2)
        try: self.driver.quit()
        except Exception: pass
        try:
            self._save_totals(force=True)
        except Exception:
            pass
        if self.temp_profile and os.path.isdir(self.temp_profile):
            try: shutil.rmtree(self.temp_profile, ignore_errors=True)
            except Exception: pass

    def _dispatch(self, kind, arg, lucky, prefix, lang):
        try:
            self._dispatch_inner(kind, arg, lucky, prefix, lang)
        finally:
            try:
                self._install_overlay_on_current_page()
            except Exception:
                pass

    def _dispatch_inner(self, kind, arg, lucky, prefix, lang):
        if kind == "search":
            mode = "lucky" if lucky else "search"
            self._emit(f"{prefix}→ {mode}: {pretty_query(arg)!r}"
                       + (f" (lang={lang})" if lang else ""))
            self._do_search(arg, lucky, lang)
        elif kind == "video":
            self._emit(f"{prefix}→ video: {arg}"
                       + (f" (lang={lang})" if lang else ""))
            self._do_video(arg, lang)
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
        elif kind == "loadmore":
            try:
                self._do_loadmore(int(arg) if arg else 3)
            except Exception as e:
                self._emit(f"[bot] loadmore error: {e}", "err")
        elif kind == "skip_ad":
            self._do_skip_ad()
        elif kind == "pick":
            self._do_pick(arg)
        elif kind == "skip_forward":
            try:
                secs = float(arg) if arg else 30.0
            except ValueError:
                secs = 30.0
            self._do_skip_forward(secs)
        elif kind == "clear_screenshots":
            try:
                self.clear_screenshots(archive_first=True)
            except Exception as e:
                self._emit(f"[shots] clear_screenshots error: {e}", "err")
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
        d = self.driver
        if not d:
            return False
        try:
            url = (d.current_url or "").lower()
        except Exception:
            url = ""
        is_playlist = ("list=" in url) or ("start_radio=" in url)

        try:
            state = d.execute_script(r"""
                try {
                    const p = document.querySelector('#movie_player');
                    if (!p) return 'no-player';
                    if (p.querySelector('.ytp-error')) {
                        const t = (p.querySelector('.ytp-error').textContent || '').trim();
                        return 'error-overlay:' + t;
                    }
                    if (typeof p.getPlayerState === 'function') {
                        const s = p.getPlayerState();
                        if (s === 5) return 'error-state:5';
                    }
                    if (typeof p.getVideoData === 'function') {
                        const vd = p.getVideoData() || {};
                        if (vd.errorCode) return 'error-code:' + vd.errorCode;
                    }
                    const v = document.querySelector('video');
                    if (v && v.error) {
                        return 'video-element-error:' + v.error.code;
                    }
                    return 'ok';
                } catch(e) { return 'probe-failed'; }
            """)
            if isinstance(state, str):
                if state == "ok" or state == "no-player":
                    return False
                if state.startswith("error-overlay:"):
                    try:
                        paused = d.execute_script(
                            "const v=document.querySelector('video');"
                            "return v ? v.paused : true;")
                    except Exception:
                        paused = True
                    if not paused:
                        return False
                    return True
                if state.startswith(("error-state:", "error-code:",
                                     "video-element-error:")):
                    return True
                return False
        except Exception:
            pass

        try:
            scope = d.execute_script(r"""
                const nodes = [
                    document.querySelector('#movie_player'),
                    document.querySelector('ytd-watch-metadata'),
                    document.querySelector('#primary-inner'),
                ];
                let out = '';
                for (const n of nodes) {
                    if (n) out += ' ' + (n.innerText || '');
                }
                return out.toLowerCase();
            """) or ""
        except Exception:
            scope = ""

        if not scope:
            return False

        strong = ("something went wrong", "an error has occurred",
                  "tap to retry", "please try again later",
                  "this video isn't available anymore",
                  "this video is unavailable")
        if any(p in scope for p in strong):
            return True
        if not is_playlist and "video unavailable" in scope:
            return True
        return False

    def _reload_until_playable(self, max_attempts=VIDEO_RETRY_ATTEMPTS):
        for attempt in range(1, max_attempts + 1):
            if not self._page_shows_error():
                if attempt > 1:
                    self._emit(f"[bot] Recovered after {attempt-1} reload(s).", "ok")
                return True
            time.sleep(0.6)
            if not self._page_shows_error():
                if attempt > 1:
                    self._emit(f"[bot] Recovered after {attempt-1} reload(s).", "ok")
                return True

            try:
                cur = (self.driver.current_url or "").lower()
            except Exception:
                cur = ""
            is_playlist = ("list=" in cur) or ("start_radio=" in cur)
            tag = " (playlist/mix)" if is_playlist else ""
            self._emit(
                f"[bot] ⚠ error page ({attempt}/{max_attempts}){tag}. "
                f"URL={cur[:120]} — Reloading…", "warn")

            try:
                if is_playlist and attempt < max_attempts:
                    try:
                        self.driver.execute_script("""
                            const v = document.querySelector('video');
                            if (v) { try { v.play(); } catch(e) {} }
                        """)
                        time.sleep(VIDEO_RETRY_DELAY)
                    except Exception:
                        self.driver.refresh()
                        time.sleep(VIDEO_RETRY_DELAY)
                else:
                    self.driver.refresh()
                    time.sleep(VIDEO_RETRY_DELAY)
                WebDriverWait(self.driver, 8).until(
                    lambda d: d.execute_script(
                        "return document.readyState") == "complete")
            except Exception as e:
                self._emit(f"[bot] Reload error: {e}", "warn")
        if not self._page_shows_error():
            self._emit(f"[bot] Recovered after {max_attempts} reload(s).", "ok")
            return True
        self._emit("[bot] Page still shows an error.", "err")
        return False

    def _qmark_lang(self, lang):
        return f"?hl={lang}&persist_hl=1" if lang else ""

    def _do_search(self, query, lucky=False, lang=None):
        url = "https://www.youtube.com/results?search_query=" + query
        if lang: url += f"&hl={lang}&persist_hl=1"

        self._stat_bump("searches")
        self._totals_bump("searches")
        self._stat_event(f"search: {pretty_query(query)[:60]}")
        self._stat_set("status", f"searching: {pretty_query(query)[:40]}")

        if not self._safe_navigate(url, label=f"search {pretty_query(query)!r}"):
            return
        self._notify_watch(False, "")
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "video-title")))
            results = self.driver.find_elements(By.ID, "video-title")
            if not results:
                self._emit("[bot] No results (maybe Restricted Mode).", "warn")
                return
            if lucky:
                try: results_url = self.driver.current_url
                except Exception: results_url = ""
                cands = []
                for el in results[:8]:
                    try:
                        href = el.get_attribute("href") or ""
                        text = el.text or ""
                    except Exception:
                        continue
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = "https://www.youtube.com" + href
                    cands.append((href, text))
                picked = False
                for href, text in cands[:5]:
                    safe, reason = self.checker.check(href)
                    if not safe:
                        self._emit(f"[bot] Lucky skip: {reason} "
                                   f"({text[:50]}…)", "info")
                        continue
                    self._emit(f"[bot] Lucky pick: {text}", "ok")
                    try:
                        href = self._strip_playlist_params(href)
                        self.driver.get(href)
                    except Exception as e:
                        self._emit(f"[bot] Lucky navigate failed: {e}", "warn")
                        continue
                    time.sleep(3)
                    try: self._install_overlay_on_current_page()
                    except Exception: pass
                    try: self._install_chrome_cleanup()
                    except Exception: pass
                    try: self._install_theme_background()
                    except Exception: pass
                    self._reload_until_playable()
                    if not self._enforce_video_rules(f"lucky {text[:40]!r}"):
                        if results_url:
                            try: self.driver.get(results_url)
                            except Exception: pass
                        continue
                    picked = True
                    break
                if not picked:
                    self._emit(
                        f"[bot] Lucky: no safe result for "
                        f"{pretty_query(query)!r}, loading more…", "warn")
                    self._notify_warn(
                        f"⚠ Lucky — no safe result for "
                        f"{pretty_query(query)[:60]!r}, loading more…",
                        "warn")
                    try:
                        self._do_loadmore(3)
                    except Exception as e:
                        self._emit(f"[bot] Lucky loadmore failed: {e}",
                                   "warn")
                    try:
                        WebDriverWait(self.driver, 10).until(
                            EC.presence_of_element_located(
                                (By.ID, "video-title")))
                        results2 = self.driver.find_elements(
                            By.ID, "video-title")
                        new_cands = []
                        for el in results2[:20]:
                            try:
                                h = el.get_attribute("href") or ""
                                t = el.text or ""
                            except Exception:
                                continue
                            if not h:
                                continue
                            if not h.startswith("http"):
                                h = "https://www.youtube.com" + h
                            if any(h == c[0] for c in cands):
                                continue
                            new_cands.append((h, t))
                        picked2 = False
                        for href, text in new_cands[:8]:
                            safe, reason = self.checker.check(href)
                            if not safe:
                                continue
                            self._emit(f"[bot] Lucky pick (2nd pass): "
                                       f"{text}", "ok")
                            try:
                                href = self._strip_playlist_params(href)
                                self.driver.get(href)
                            except Exception as e:
                                self._emit(
                                    f"[bot] Lucky navigate failed: {e}",
                                    "warn")
                                continue
                            time.sleep(3)
                            try:
                                self._install_overlay_on_current_page()
                            except Exception:
                                pass
                            try:
                                self._install_chrome_cleanup()
                            except Exception:
                                pass
                            try:
                                self._install_theme_background()
                            except Exception:
                                pass
                            self._reload_until_playable()
                            if not self._enforce_video_rules(
                                    f"lucky2 {text[:40]!r}"):
                                if results_url:
                                    try:
                                        self.driver.get(results_url)
                                    except Exception:
                                        pass
                                continue
                            picked2 = True
                            break
                        if not picked2:
                            self._emit(
                                f"[bot] Lucky: still no safe result "
                                f"after loading more.", "warn")
                    except Exception as e:
                        self._emit(f"[bot] Lucky 2nd pass error: {e}",
                                   "warn")
            else:
                self._emit(f"[bot] Found {len(results)} results for "
                           f"'{pretty_query(query)}'.", "ok")
                for r in results[:5]:
                    self._emit(f"   • {r.text}")
        except Exception as e:
            self._emit(f"[bot] Error: {e}", "err")

    def _do_video(self, video_id, lang=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            self._emit(f"[bot] Invalid video ID: {video_id!r}", "err")
            return
        url = f"https://www.youtube.com/watch?v={video_id}"
        if lang: url += f"&hl={lang}&persist_hl=1"
        url = self._strip_playlist_params(url)

        safe, reason = self.checker.check(url)
        if not safe:
            self._emit(f"[bot] BLOCKED video {video_id}: {reason}.", "warn")
            self._notify_warn(f"⚠ Video blocked by filter — {reason}",
                              "warn")
            return
        try:
            self.driver.get(url); time.sleep(2)
            try: self._install_chrome_cleanup()
            except Exception: pass
            try: self._install_theme_background()
            except Exception: pass
            try: self._install_overlay_on_current_page()
            except Exception: pass
            self._reload_until_playable()
            self._enforce_video_rules(f"!video {video_id}")
        except Exception as e:
            self._emit(f"[bot] Error loading video: {e}", "err")

    def _do_channel(self, handle, lang=None):
        if any(ch in handle for ch in ("/", "?", "#", "\\")):
            self._emit(f"[bot] !channel: invalid handle {handle!r}", "warn")
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
        self._notify_watch(False, "")
        time.sleep(3)
        try: self._install_chrome_cleanup()
        except Exception: pass
        try: self._install_theme_background()
        except Exception: pass

        try: cur = (self.driver.current_url or "").lower()
        except Exception: cur = ""
        if "/watch" in cur or cur.rstrip("/").endswith("/live"):
            self._emit("[bot] !channel: redirect — backing out.", "warn")
            try: self.driver.get("https://www.youtube.com/")
            except Exception: pass
            try: self._blank_home_feed()
            except Exception: pass
            return
        try:
            self._check_channel_featured()
        except Exception:
            pass
        try:
            cur2 = (self.driver.current_url or "").lower()
        except Exception:
            cur2 = ""
        if not self._on_channel_page() or "/watch" in cur2:
            return
        self._emit(f"[bot] Loaded channel: {handle}", "ok")

    def _do_home(self, lang=None):
        url = "https://www.youtube.com/" + self._qmark_lang(lang)
        if not self._safe_navigate(url, label="home"):
            return
        self._notify_watch(False, "")
        time.sleep(2)
        self._blank_home_feed()
        self._install_chrome_cleanup()
        self._install_theme_background()
        self._emit("[bot] Loaded YouTube home.", "ok")

    def _do_set_language(self, lang):
        if not lang:
            self._emit("[bot] !changelanguage needs a code.", "warn"); return
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?", lang):
            self._emit(f"[bot] !changelanguage: bad code {lang!r}.", "warn")
            return
        try: cur = self.driver.current_url or ""
        except Exception: cur = ""
        if not cur:
            self._emit("[bot] No current page to relang.", "warn"); return
        try:
            parsed = urllib.parse.urlparse(cur)
            host = (parsed.hostname or "").lower()
            if not (host == "youtube.com" or host.endswith(".youtube.com")):
                self._emit("[bot] !changelanguage: not on YouTube.", "warn")
                return
            qs = [(k, v) for (k, v) in urllib.parse.parse_qsl(
                      parsed.query, keep_blank_values=True)
                  if k.lower() not in ("hl", "persist_hl")]
            qs += [("hl", lang), ("persist_hl", "1")]
            new_url = urllib.parse.urlunparse(
                parsed._replace(query=urllib.parse.urlencode(qs)))
        except ValueError:
            self._emit("[bot] !changelanguage: could not parse URL.", "warn")
            return
        self.current_lang = lang
        self._emit(f"[bot] Language set to '{lang}'.", "ok")
        try: self.driver.get(new_url)
        except Exception: pass
        try: self._blank_home_feed()
        except Exception: pass
        try: self._install_chrome_cleanup()
        except Exception: pass
        try: self._install_theme_background()
        except Exception: pass

    def _do_scroll(self, arg):
        raw = (arg or "").strip()
        if not raw:
            self._emit("[bot] !scroll expects: [pixels] [duration]", "warn"); return
        parts = raw.split()
        if len(parts) > 2:
            self._emit(f"[bot] !scroll: too many args: {raw!r}", "warn"); return
        px_tok = parts[0]
        m = re.fullmatch(r"([+-]?)(\d+)", px_tok)
        if not m:
            self._emit(f"[bot] !scroll: bad pixel {px_tok!r}", "warn"); return
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
                self.driver.set_script_timeout(duration + 15)
                self.driver.execute_async_script("""
                    const px = arguments[0];
                    const duration = arguments[1] * 1000;
                    const done = arguments[arguments.length - 1];
                    const startY = window.scrollY;
                    const startT = performance.now();
                    function step(now) {
                        const t = Math.min(1, (now - startT) / duration);
                        window.scrollTo(0, startY + px * t);
                        if (t < 1) requestAnimationFrame(step); else done('ok');
                    }
                    requestAnimationFrame(step);
                """, px, duration)
                self._emit(f"[bot] Scrolled {px:+d}px over {duration:g}s.")
        except Exception as e:
            self._emit(f"[bot] Scroll error: {e}", "err")

    def _do_loadmore(self, count=3):
        d = self.driver
        if not d:
            self._emit("[bot] !loadmore: no browser.", "warn"); return
        try: count = max(1, min(50, int(count)))
        except Exception: count = 3
        if BLANK_HOME_FEED:
            try:
                url = (d.current_url or "").lower()
                parsed = urllib.parse.urlparse(url)
                if parsed.path in ("", "/") and (not parsed.query
                        or parsed.query.startswith("hl=")):
                    self._emit("[bot] !loadmore: home feed is replaced "
                               "by stats — nothing to load.", "info")
                    return
            except Exception:
                pass
        try:
            clicked = d.execute_script(r"""
                const btns = document.querySelectorAll(
                    'ytd-continuation-item-renderer button, ' +
                    'button#more, tp-yt-paper-button#more, ' +
                    'yt-button-shape button[aria-label*="more" i]');
                for (const b of btns) {
                    const t = (b.textContent || '').toLowerCase();
                    if (t.includes('show more') || t.includes('more results')) {
                        b.click();
                        return true;
                    }
                }
                return false;
            """)
            if clicked:
                self._emit("[bot] !loadmore: clicked 'Show more results'.", "ok")
                time.sleep(1.5)

            before = d.execute_script(
                "return document.querySelectorAll("
                "'ytd-rich-item-renderer, ytd-video-renderer, "
                "ytd-grid-video-renderer, ytd-compact-video-renderer').length;")
            for i in range(count):
                d.execute_script(
                    "window.scrollTo(0, document.documentElement.scrollHeight);")
                time.sleep(1.2)
                try:
                    d.execute_script(
                        "const b=document.querySelector("
                        "'ytd-continuation-item-renderer');"
                        "if(b) b.scrollIntoView({behavior:'instant',"
                        "'block':'end'});")
                except Exception:
                    pass
            after = d.execute_script(
                "return document.querySelectorAll("
                "'ytd-rich-item-renderer, ytd-video-renderer, "
                "ytd-grid-video-renderer, ytd-compact-video-renderer').length;")
            gained = max(0, int(after) - int(before))
            self._emit(f"[bot] !loadmore: {gained} more items loaded "
                       f"({before} → {after}).", "ok" if gained else "info")
            if gained == 0:
                self._notify_warn(
                    "⚠ Load more — no new items appeared. The page may "
                    "have reached its end.", "warn")
            self._install_overlay_on_current_page()
            self._install_chrome_cleanup()
        except Exception as e:
            self._emit(f"[bot] !loadmore failed: {e}", "err")
            self._notify_warn(f"⚠ Load more failed — {e}", "err")

    def _do_skip_ad(self):
        d = self.driver
        if not d:
            self._emit("[bot] !skip: no browser.", "warn"); return
        try:
            result = d.execute_script(r"""
                try {
                    const p = document.querySelector('#movie_player');
                    if (!p) return 'no-player';
                    const skips = [
                        '.ytp-ad-skip-button',
                        '.ytp-ad-skip-button-modern',
                        '.ytp-skip-ad-button',
                        'button.ytp-ad-skip-button-container',
                        'button[class*="ytp-ad-skip"]',
                    ];
                    for (const sel of skips) {
                        const b = p.querySelector(sel) ||
                                  document.querySelector(sel);
                        if (b && b.offsetParent !== null) {
                            b.click();
                            return 'clicked:' + sel;
                        }
                    }
                    if (p.classList.contains('ad-showing') ||
                        p.classList.contains('ad-interrupting')) {
                        const v = document.querySelector('video');
                        if (v && isFinite(v.duration) && v.duration > 0) {
                            v.currentTime = v.duration;
                            return 'fast-forwarded-ad';
                        }
                        if (v) {
                            v.muted = true;
                            return 'muted-ad-no-duration';
                        }
                        return 'ad-no-video';
                    }
                    const close = document.querySelector(
                        '.ytp-ad-overlay-close-button, '
                        + '.ytp-ad-overlay-close-container button');
                    if (close && close.offsetParent !== null) {
                        close.click();
                        return 'closed-overlay';
                    }
                    return 'no-ad';
                } catch(e) {
                    return 'error:' + e;
                }
            """)
            self._emit(f"[bot] !skip → {result}", "ok"
                       if result and result != "no-ad" else "info")
            if result == "no-ad":
                self._notify_warn(
                    "⚠ !skip — no ad currently showing", "warn")
            else:
                self._stat_event(f"ad skip: {result}")
        except Exception as e:
            self._emit(f"[bot] !skip failed: {e}", "err")
            self._notify_warn(f"⚠ !skip failed — {e}", "err")

    def _do_skip_forward(self, seconds=30.0):
        d = self.driver
        if not d:
            return
        try:
            secs = max(1.0, min(600.0, float(seconds)))
            d.execute_script("""
                const v = document.querySelector('video');
                if (v) {
                    try { v.currentTime = Math.min(
                        (v.currentTime || 0) + arguments[0],
                        v.duration || (v.currentTime + arguments[0]));
                    } catch(e) {}
                }
            """, secs)
            self._emit(f"[bot] !forward {secs:g}s.", "ok")
        except Exception as e:
            self._emit(f"[bot] !forward failed: {e}", "err")

    def _do_pick(self, arg):
        d = self.driver
        if not d:
            self._emit("[bot] !pick: no browser.", "warn"); return
        raw = (arg or "").strip().lower()
        if raw in ("list", "ls", "0", ""):
            list_only = True
            idx = 0
        else:
            try:
                idx = int(raw)
            except ValueError:
                self._emit(f"[bot] !pick: bad number {arg!r}", "warn")
                return
            list_only = False

        try:
            cards = d.execute_script(r"""
                (function(){
                    const out = [];
                    const seen = new Set();
                    const sels = [
                        'ytd-video-renderer',
                        'ytd-rich-item-renderer',
                        'ytd-grid-video-renderer',
                        'ytd-playlist-video-renderer',
                        'ytd-compact-video-renderer',
                    ];
                    for (const sel of sels) {
                        for (const c of document.querySelectorAll(sel)) {
                            if (c.dataset.ocPickSeen === '1') continue;
                            if (c.querySelector(
                                    'ytd-ad-slot-renderer, '
                                    + 'ytd-in-feed-ad-layout-renderer, '
                                    + 'ytd-promoted-sparkles-web-renderer, '
                                    + 'ytd-display-ad-renderer, '
                                    + '[is-ad], .badge-shape-wiz__text '
                                    + '[aria-label*="Ad" i]')) {
                                continue;
                            }
                            const link = c.querySelector(
                                'a#video-title, a#video-title-link, '
                                + 'a#thumbnail[href*="/watch"], '
                                + 'a[href*="/watch?v="]');
                            if (!link) continue;
                            const href = link.href || '';
                            if (!href || seen.has(href)) continue;
                            seen.add(href);
                            let title = (
                                link.getAttribute('title')
                                || link.getAttribute('aria-label')
                                || link.textContent || ''
                            ).trim();
                            if (!title) {
                                const t = c.querySelector(
                                    '#video-title, yt-formatted-string'
                                    + '[id="video-title"]');
                                if (t) title = (t.textContent || '').trim();
                            }
                            out.push({
                                href: href,
                                title: title.slice(0, 120),
                            });
                            if (out.length >= 40) break;
                        }
                        if (out.length >= 40) break;
                    }
                    return out;
                })();
            """) or []
            if not cards:
                self._emit("[bot] !pick: no playable cards on this page.",
                           "warn")
                self._notify_warn("⚠ !pick — no playable cards found",
                                  "warn")
                return
            for i, c in enumerate(cards[:10], 1):
                self._emit(f"[pick] {i}. {c.get('title') or c.get('href')}",
                           "info")
            if list_only:
                self._notify_info(
                    f"Pick: {len(cards)} items — see log for the list",
                    "info")
                return
            if idx < 1 or idx > len(cards):
                self._emit(f"[bot] !pick: index {idx} out of range "
                           f"(1-{len(cards)}).", "warn")
                return
            chosen = cards[idx - 1]
            href = chosen.get("href") or ""
            title = chosen.get("title") or ""
            self._emit(f"[bot] !pick {idx}: {title}", "ok")
            safe, reason = self.checker.check(href)
            if not safe:
                self._emit(f"[bot] !pick {idx}: {reason}", "warn")
                self._notify_warn(f"⚠ !pick — {reason}", "warn")
                return
            try:
                href = self._strip_playlist_params(href)
                self.driver.get(href)
            except Exception as e:
                self._emit(f"[bot] !pick navigate failed: {e}", "warn")
                return
            time.sleep(3)
            try: self._install_overlay_on_current_page()
            except Exception: pass
            try: self._install_chrome_cleanup()
            except Exception: pass
            try: self._install_theme_background()
            except Exception: pass
            self._reload_until_playable()
            self._enforce_video_rules(f"!pick {idx}")
        except Exception as e:
            self._emit(f"[bot] !pick error: {e}", "err")

    def _do_watchhome(self, arg):
        try:
            idx = int(arg)
        except ValueError:
            self._emit("[bot] !watchhome expects an integer.", "warn"); return
        if BLANK_HOME_FEED and self._is_on_youtube_home():
            self._emit("[bot] !watchhome: the home feed is replaced by the "
                       "stats page (blank_home_feed) — nothing to pick.",
                       "info")
            self._notify_warn("⚠ !watchhome unavailable while the home feed "
                              "is blanked", "warn")
            return
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "ytd-rich-item-renderer")))
            cards = self.driver.execute_script(r"""
                const out = [];
                for (const card of document.querySelectorAll('ytd-rich-item-renderer')) {
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
                self._emit("[bot] !watchhome: no non-ad cards.", "warn")
                self._notify_warn(
                    "⚠ watchhome — no playable (non-ad) videos found "
                    "on the homepage.", "warn")
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                try: self._blank_home_feed()
                except Exception: pass
                self._notify_watch(False, "")
                return
            if idx < 1: idx = 1
            if idx > len(cards): idx = len(cards)
            card = cards[idx - 1]
            try:
                href = card.find_element(
                    By.CSS_SELECTOR, "a[href*='/watch?v=']").get_attribute("href")
            except Exception:
                href = ""
            if href:
                safe, reason = self.checker.check(href)
                if not safe:
                    self._emit(f"[bot] !watchhome {idx}: {reason} — skipping.",
                               "warn")
                    self._notify_warn(
                        f"⚠ watchhome — video #{idx} blocked: {reason}",
                        "warn")
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
                self._emit(f"[bot] !watchhome {idx}: no link.", "warn"); return
            self.driver.execute_script("arguments[0].click();", link)
            time.sleep(3)
            try: cur = (self.driver.current_url or "").lower()
            except Exception: cur = ""
            if not _url_is_allowed_site(cur):
                self._emit("[bot] !watchhome: off-site — going home.", "warn")
                try: self.driver.get("https://www.youtube.com/")
                except Exception: pass
                try: self._blank_home_feed()
                except Exception: pass
                self._notify_watch(False, "")
                return
            try: self._install_chrome_cleanup()
            except Exception: pass
            try: self._install_theme_background()
            except Exception: pass
            self._reload_until_playable()
            self._enforce_video_rules(f"!watchhome {idx}")
        except Exception as e:
            self._emit(f"[bot] !watchhome error: {e}", "err")

    def _video_js(self, body, *args):
        """Run `body` with `v` bound to the page's <video>.
        Returns 'no-video' when the page has none (instead of a JS error)."""
        return self.driver.execute_script(
            "const v=document.querySelector('video');"
            "if(!v) return 'no-video';" + body, *args)

    def _do_playback(self, kind, arg):
        try:
            if kind == "speed":
                v = float(arg)
                if not (0.25 <= v <= 2.0):
                    self._emit("[bot] !speed range 0.25–2.0", "warn"); return
                if self._video_js("v.playbackRate=arguments[0];return 'ok';",
                                  v) == "no-video":
                    self._emit("[bot] !speed: no video on this page.", "warn")
                    return
                self._emit(f"[bot] Speed set to {v}x.")
            elif kind == "pitch":
                try: semis = float(arg)
                except ValueError:
                    self._emit("[bot] !pitch expects semitones", "warn"); return
                if not (-24 <= semis <= 24):
                    self._emit("[bot] !pitch range -24..+24", "warn"); return
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
                self._emit(f"[bot] Pitch {semis:+g} semitones.")
            elif kind == "volume":
                v = int(arg)
                if not (0 <= v <= 100):
                    self._emit("[bot] !volume range 0–100", "warn"); return
                if self._video_js("v.volume=arguments[0];return 'ok';",
                                  v / 100.0) == "no-video":
                    self._emit("[bot] !volume: no video on this page.", "warn")
                    return
                self._emit(f"[bot] Volume set to {v}%.")
            elif kind == "playpause":
                if self._video_js("if(v.paused){v.play();}else{v.pause();}"
                                  "return 'ok';") == "no-video":
                    self._emit("[bot] !playpause: no video on this page.",
                               "warn")
                    return
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
                    self._emit("[bot] !captionstranslate needs a code", "warn")
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
                    js = "v.currentTime+=arguments[0];return 'ok';"
                elif op == "-":
                    js = "v.currentTime=Math.max(0,v.currentTime-arguments[0]);return 'ok';"
                elif op == "set":
                    js = "v.currentTime=arguments[0];return 'ok';"
                else:
                    self._emit("[bot] !seek expects +, -, or set", "warn"); return
                if self._video_js(js, secs) == "no-video":
                    self._emit("[bot] !seek: no video on this page.", "warn")
                    return
                self._emit(f"[bot] Seeked {op} {secs}s.")
        except Exception as e:
            self._emit(f"[bot] Playback '{kind}' error: {e}", "err")

    def stop(self):
        self.running = False
        try:
            if self.video_gate: self.video_gate.stop()
        except Exception: pass


class LiveChatReader(threading.Thread):
    def __init__(self, video_id, out_queue, stop_event, on_command=None):
        super().__init__(daemon=True)
        self.video_id = video_id
        self.q = out_queue
        self.stop_event = stop_event
        self.on_command = on_command

    @staticmethod
    def _terminate(chat):
        try:
            if chat is not None:
                chat.terminate()
        except Exception:
            pass

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
                    self._terminate(chat)
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
                            self.stop_event.wait(0.1)
                            continue
                        for c in data.sync_items():
                            if self.stop_event.is_set():
                                break
                            try:
                                author = getattr(c.author, "name", "unknown")
                                author_id = getattr(c.author, "channelId", "") or ""
                                msg = c.message
                            except Exception:
                                continue
                            self.q.put({
                                "kind": "chat",
                                "author": author,
                                "author_id": author_id,
                                "message": msg,
                                "datetime": getattr(c, "datetime", ""),
                                "is_owner": bool(getattr(c.author, "isChatOwner", False)),
                                "is_mod":   bool(getattr(c.author, "isChatModerator", False)),
                            })
                        self.stop_event.wait(0.05)
                    except AttributeError:
                        self.q.put({"kind": "chat_status", "level": "warn",
                                    "message": "[chat] Reconnecting…"})
                        break
                    except Exception as e:
                        self.q.put({"kind": "chat_status", "level": "warn",
                                    "message": f"[chat] Message error: {e}"})
                        continue
                self._terminate(chat)
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


class CommandDispatcher:
    REPEATABLE = {
        "playpause", "refresh", "fullscreen",
        "captions", "loop", "autoplay",
        "seek", "scroll",
    }
    TOKEN_BAD = set("/?#\\")
    ARGC = {"playpause": 0, "refresh": 0, "fullscreen": 0,
            "captions": 1, "loop": 1, "autoplay": 1,
            "seek": 2, "scroll": 2}
    KNOWN = {"search", "lucky", "searchwatch", "video", "channel", "home",
             "changelanguage", "english", "scroll", "watchhome",
             "clearcookies", "cc", "speed", "pitch", "volume", "seek",
             "captions", "captionstranslate", "loop", "autoplay",
             "playpause", "refresh", "fullscreen", "infscroll",
             "stopscroll", "infclear", "infstop", "stopinf",
             "loadmore", "skip", "adskip", "skipad",
             "forward", "ff", "pick", "unlock", "lockstatus",
             "clearshots", "clearscreenshots", "shotsclear",
             "archiveshots", "shotcount", "me"}
    LANG_CODES = frozenset(
        "af ar az bg bn bs cs da de el en es et eu fa fi fil fr gl gu "
        "hr hu hy ja ka kk km kn ko ky lo lt lv mk ml mn mr ms ne nl pa pl "
        "pt ro ru si sk sl sq sr sv sw ta te th tr uk ur uz vi zh zu".split())

    def __init__(self, bot, log_fn, source="", reply_fn=None,
                 is_privileged=False):
        self.bot = bot
        self.log = log_fn
        self.source = source
        self.reply = reply_fn or (lambda msg, level="info": log_fn(msg, level))
        self._privileged = bool(is_privileged)

    def _is_privileged(self) -> bool:
        return self._privileged

    def handle(self, body):
        body = (body or "").strip()
        is_chat = self.source.startswith("chat:")
        stripped = body.lstrip("!")
        first = stripped.split(None, 1)[0].lower() if stripped else ""
        if (not is_chat and not body.startswith("!")
                and first not in self.KNOWN):
            if FALLBACK_PLAINTEXT_AS_SEARCH:
                q, lang = self._split_lang(body)
                self.bot.request_search(q, lucky=False, lang=lang,
                                        source=self.source)
            else:
                self.log(f"{self.source} plain text ignored: {body!r}", "info")
            return
        chain = parse_command_chain("!" + body if not body.startswith("!") else body)
        if chain is None:
            return
        for name, arg in chain:
            self._one(name.lower(), arg)

    def _one(self, cmd, arg):
        for tok in (arg or "").split():
            if any(ch in tok for ch in self.TOKEN_BAD):
                self.log(f"{self.source} !{cmd}: '{tok}' contains a "
                         f"forbidden character (/ ? # \\).", "warn")
                return
        if cmd in self.REPEATABLE:
            toks = (arg or "").split()
            if (len(toks) == self.ARGC.get(cmd, 0) + 1
                    and re.fullmatch(r"\d{1,6}", toks[-1])):
                try:
                    n = int(toks[-1])
                except ValueError:
                    n = 1
                src = self.source or ""
                cap = CHAT_REPEAT_CAP if src.startswith("chat:") else UI_REPEAT_CAP
                if n > cap:
                    self.log(f"{src} !{cmd}: repeat capped at {cap}", "warn")
                    n = cap
                if n >= 1:
                    base = " ".join(toks[:-1])
                    for _ in range(n):
                        self._one_once(cmd, base)
                    return
        self._one_once(cmd, arg)

    def _one_once(self, cmd, arg):
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
        elif cmd == "video":
            if not self._is_privileged():
                self.log(f"{src} !video is owner/mod only", "warn"); return
            parts = arg.split()
            if not parts or not re.fullmatch(r"[A-Za-z0-9_-]{11}", parts[0]):
                self.log(f"{src} !video needs an 11-char video ID", "warn"); return
            bot.request_video(parts[0],
                              lang=(parts[1] if len(parts) > 1 else None),
                              source=src)
        elif cmd == "channel":
            parts = arg.split()
            if not parts:
                self.log(f"{src} !channel needs a handle", "warn"); return
            handle = parts[0]
            if "://" in handle or handle.lower().startswith(
                    ("http", "www.", "youtube.com", "youtu.be")):
                if not handle.lower().startswith(("http://", "https://")):
                    handle = "https://" + handle
                try:
                    parsed = urllib.parse.urlparse(handle)
                    path = (parsed.path or "").strip("/")
                except Exception:
                    path = ""
                m_h = re.match(r"^@([A-Za-z0-9._-]{3,60})", path)
                m_c = re.match(r"^channel/(UC[A-Za-z0-9_-]{22})", path)
                m_u = re.match(r"^(user|c)/([A-Za-z0-9._-]{3,60})",
                               path)
                if m_h:
                    handle = "@" + m_h.group(1)
                elif m_c:
                    handle = m_c.group(1)
                elif m_u:
                    handle = "@" + m_u.group(2)
                else:
                    self.log(
                        f"{src} !channel: no handle found in URL "
                        f"{handle!r}", "warn")
                    return
            if any(ch in handle for ch in ("/", "?", "#", "\\", " ", "\t")):
                self.log(f"{src} !channel: invalid handle {handle!r}",
                         "warn")
                return
            bot.request_channel(handle,
                                lang=(parts[1] if len(parts) > 1 else None),
                                source=src)
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
                self.log(f"{src} !scroll: bad pixel {parts[0]!r}", "warn"); return
            if len(parts) == 2:
                try: d = float(parts[1])
                except ValueError:
                    self.log(f"{src} !scroll: bad duration {parts[1]!r}", "warn"); return
                if d <= 0:
                    self.log(f"{src} !scroll: duration must be > 0", "warn"); return
            bot.request_scroll(arg, source=src)
        elif cmd == "watchhome":
            if not arg: self.log(f"{src} !watchhome needs a number", "warn"); return
            bot.request_watchhome(int_or_zero(arg.split()[0]), source=src)
        elif cmd in ("clearcookies","cc"):
            bot.request_clear_cookies(source=src)
        elif cmd == "loadmore":
            n = 3
            if arg:
                try: n = int(arg.split()[0])
                except ValueError:
                    self.log(f"{src} !loadmore: bad number {arg!r}", "warn"); return
                n = max(1, min(50, n))
            bot.request_loadmore(n, source=src)
        elif cmd in ("skip", "adskip", "skipad"):
            bot.request_skip_ad(source=src)
        elif cmd in ("forward", "ff"):
            secs = 30.0
            if arg:
                try: secs = float(arg.split()[0])
                except ValueError:
                    self.log(f"{src} !forward: bad number {arg!r}", "warn"); return
            secs = max(1.0, min(600.0, secs))
            bot.request_skip_forward(secs, source=src)
        elif cmd == "pick":
            if not arg:
                bot.request_pick(0, source=src)
                return
            tok = arg.split()[0].lower()
            if tok in ("list", "ls"):
                bot.request_pick(0, source=src)
                return
            try:
                n = int(tok)
            except ValueError:
                self.log(f"{src} !pick: bad number {tok!r}", "warn"); return
            n = max(0, min(40, n))
            bot.request_pick(n, source=src)
        elif cmd in ("clearshots", "clearscreenshots", "shotsclear"):
            if not self._is_privileged():
                self.log(f"{src} !clearshots is mod/owner only", "warn")
                return
            bot.request_clear_screenshots(source=src)
        elif cmd == "archiveshots":
            if not self._is_privileged():
                self.log(f"{src} !archiveshots is mod/owner only", "warn")
                return
            try:
                cleaner = ScreenshotCleaner(bot)
                files = [p for p in SCREENSHOT_DIR.iterdir()
                         if p.is_file()]
                if not files:
                    self.log(f"{src} !archiveshots: nothing to archive",
                             "info")
                    return
                archived, name, err = cleaner._archive_files(files)
                if err:
                    self.log(f"{src} !archiveshots: {err}", "warn")
                else:
                    self.log(
                        f"{src} !archiveshots: archived {archived} → "
                        f"screenshots_archive/{name}", "ok")
                    try: bot._broadcast_screenshot_count()
                    except Exception: pass
            except Exception as e:
                self.log(f"{src} !archiveshots error: {e}", "warn")
        elif cmd == "shotcount":
            try:
                live = bot._count_screenshots()
            except Exception:
                live = 0
            try:
                zips = bot._count_archived_zips()
            except Exception:
                zips = 0
            mode = "archive" if ARCHIVE_SCREENSHOTS else "delete"
            self.log(
                f"{src} 📸 {live} live screenshot(s) · "
                f"{zips} archive zip(s) · "
                f"retention {SCREENSHOT_RETENTION_HOURS}h · "
                f"mode={mode}", "info")
        elif cmd == "unlock":
            if not self._is_privileged():
                self.log(f"{src} !unlock is mod/owner only", "warn")
                return
            try:
                was_active = bool(getattr(bot, "_mod_lock_active", False))
                bot._clear_mod_lock(f"manual unlock by {src}")
                if was_active:
                    bot._notify_info(
                        "🔓 Mod-lock released by moderator", "info")
                else:
                    self.log(f"{src} !unlock: no lock was active",
                             "info")
            except Exception as e:
                self.log(f"{src} !unlock: {e}", "warn")
        elif cmd == "lockstatus":
            try:
                locked, vid = bot._is_mod_locked()
            except Exception:
                locked, vid = False, ""
            if locked:
                self.log(f"{src} 🔒 Mod-lock active on {vid}", "info")
            else:
                self.log(f"{src} 🔓 No mod-lock active", "info")
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
            m = re.fullmatch(r"(\+|-|set)\s*(\d+(?:\.\d+)?)", arg, re.I)
            if not m:
                self.log(f"{src} !seek expects +N, -N or setN "
                         f"(e.g. '+10', 'set 30')", "warn"); return
            bot.request_playback("seek", f"{m.group(1).lower()} {m.group(2)}",
                                 source=src)
        elif cmd == "captions":
            if arg.lower() not in ("on","off","true","false","1","0"):
                self.log(f"{src} !captions expects on/off", "warn"); return
            bot.request_playback("captions", arg, source=src)
        elif cmd == "captionstranslate":
            if not arg:
                self.log(f"{src} !captionstranslate needs a code", "warn"); return
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
        elif cmd == "infscroll":
            if not arg:
                self.log(
                    f"{src} !infscroll needs: DIST [SPEED] [DURATION]\n"
                    f"        e.g. !infscroll 5000\n"
                    f"             !infscroll 5000 250\n"
                    f"             !infscroll 5000 250 10",
                    "warn")
                return
            parsed = parse_infscroll_args(arg)
            if parsed is None:
                self.log(f"{src} !infscroll: bad args {arg[:60]!r} "
                         f"(want: DIST [SPEED] [DURATION])", "warn")
                return
            if parsed["distance_px"] == 0:
                bot.request_stopscroll(source=src, reason="distance 0")
                return
            speed = parsed["speed_px_s"]
            if speed is None:
                speed = Fraction(400)
                parsed["label"] = (
                    parsed["label"] + " @ default 400 px/s")
            if speed == 0:
                bot.request_stopscroll(source=src, reason="speed 0")
                return
            # The parsed speed is a magnitude; the sign of DIST sets the
            # direction (v3.4 stopped instantly on negative distances).
            if parsed["distance_px"] < 0:
                speed = -abs(speed)
            else:
                speed = abs(speed)
            bot.request_infscroll(
                speed, parsed["label"], source=src,
                distance=parsed["distance_px"],
                duration=(float(parsed["duration_s"])
                          if parsed["duration_s"] is not None
                          else None))
        elif cmd in ("stopscroll", "infclear", "infstop", "stopinf"):
            bot.request_stopscroll(source=src, reason=f"!{cmd}")
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
        tokens = (arg or "").split()
        if len(tokens) >= 2:
            last = tokens[-1].lower()
            m = re.fullmatch(r"lang[:=]([a-z]{2,3}(?:-[a-z0-9]{2,4})?)", last)
            if m:
                return " ".join(tokens[:-1]), m.group(1)
            if last in CommandDispatcher.LANG_CODES:
                return " ".join(tokens[:-1]), last
        return arg, None


def int_or_zero(s):
    try: return int(s)
    except ValueError: return 0


def normalize_query(q):
    return "+".join(urllib.parse.quote(tok, safe="") for tok in q.strip().split())


def pretty_query(q):
    return urllib.parse.unquote_plus(q)


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


# ─── UI HTML ────────────────────────────────────────────────────────────
UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>OpenCUY v3.4</title>
<style>
  :root, [data-theme="nexo"] {
    --bg:#0d0b1a; --card:#141129; --bg3:#1c1836; --bg4:#241f45;
    --border:#2a2547; --border2:#3a3462;
    --text:#f4f4ff; --dim:#8b88b8; --mute:#5d5a85;
    --accent:#8b5cf6; --accent2:#a78bfa;
    --green:#34d399; --red:#f43f5e; --yellow:#fbbf24; --console:#08061a;
  }
  [data-theme="black"] { --bg:#000; --card:#0a0a0a; --bg3:#121212; --bg4:#1c1c1c;
    --border:#1f1f1f; --border2:#2e2e2e; --text:#f0f0f0; --dim:#9a9a9a; --mute:#5a5a5a;
    --accent:#d4d4d4; --accent2:#e5e5e5;
    --green:#4ade80; --red:#f87171; --yellow:#facc15; --console:#000; }
  [data-theme="red"] { --bg:#1a0606; --card:#240808; --bg3:#2e0b0b; --bg4:#3d1010;
    --border:#4a1515; --border2:#5c1c1c; --text:#ffeaea; --dim:#c98a8a; --mute:#8a5555;
    --accent:#ef4444; --accent2:#f87171;
    --green:#4ade80; --red:#fca5a5; --yellow:#fbbf24; --console:#0d0303; }
  [data-theme="blue"] { --bg:#050f1f; --card:#08182e; --bg3:#0b2038; --bg4:#102a4a;
    --border:#13345a; --border2:#1e4573; --text:#e6f0ff; --dim:#7ba0c9; --mute:#4a6a8a;
    --accent:#3b82f6; --accent2:#60a5fa;
    --green:#34d399; --red:#f87171; --yellow:#fbbf24; --console:#020814; }
  [data-theme="green"] { --bg:#041409; --card:#06210e; --bg3:#0a2e15; --bg4:#0f3d1d;
    --border:#124a24; --border2:#1b5c30; --text:#e8ffef; --dim:#7bc995; --mute:#4a8060;
    --accent:#10b981; --accent2:#34d399;
    --green:#6ee7b7; --red:#f87171; --yellow:#fbbf24; --console:#020b05; }
  [data-theme="amber"] { --bg:#1a1000; --card:#241800; --bg3:#2e1f00; --bg4:#3d2a00;
    --border:#4a3400; --border2:#5c4200; --text:#fff7e6; --dim:#c9a566; --mute:#8a6e3a;
    --accent:#f59e0b; --accent2:#fbbf24;
    --green:#84cc16; --red:#f87171; --yellow:#fde047; --console:#0d0800; }
  [data-theme="pink"] { --bg:#1a0614; --card:#24081c; --bg3:#2e0b26; --bg4:#3d1033;
    --border:#4a1540; --border2:#5c1c50; --text:#ffeaf7; --dim:#c98ab8; --mute:#8a5578;
    --accent:#ec4899; --accent2:#f472b6;
    --green:#4ade80; --red:#fb7185; --yellow:#fbbf24; --console:#0d0308; }
  [data-theme="light"] { --bg:#f4f4f8; --card:#ffffff; --bg3:#ffffff; --bg4:#eef0f5;
    --border:#d4d6de; --border2:#b4b6c0; --text:#1a1a2e; --dim:#5a5a70; --mute:#9090a0;
    --accent:#6366f1; --accent2:#818cf8;
    --green:#059669; --red:#dc2626; --yellow:#d97706; --console:#1a1a2e; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; background: var(--bg); color: var(--text);
    font-family:'Segoe UI', Tahoma, sans-serif; font-size:13px;
    height:100%; overflow:hidden; }
  #bgLayer { position:fixed; inset:0; z-index:0;
    background-position:center; background-repeat:no-repeat;
    background-size:cover; pointer-events:none; opacity:0;
    transition:opacity 0.3s; }
  #bgLayer.tile { background-repeat:repeat; background-size:auto; }
  #bgLayer.stretch { background-size:100% 100%; }
  #bgLayer.contain { background-size:contain; }
  #bgLayer.cover { background-size:cover; }
  #bgOverlay { position:fixed; inset:0; z-index:1; background: var(--bg);
    opacity:0; pointer-events:none; transition:opacity 0.3s; }
  #app { position:relative; z-index:2; display:flex; flex-direction:column; height:100vh; }
  header { display:flex; align-items:center; justify-content:space-between;
    padding:14px 22px; border-bottom:1px solid var(--border);
    background: var(--card); flex-shrink:0; }
  .brand { display:flex; align-items:center; gap:12px; }
  .logo { width:22px; height:22px; border-radius:50%; background: var(--accent);
    box-shadow: 0 0 16px var(--accent); position:relative; }
  .logo::after { content:''; position:absolute; inset:5px;
    border-radius:50%; background: var(--card); }
  .brand h1 { margin:0; font-size:15px; font-weight:600; }
  .brand .sub { color: var(--mute); font-size:12px; margin-left:4px; }
  .conn { display:flex; align-items:center; gap:8px; font-size:12px; color: var(--dim); }
  .dot { width:8px; height:8px; border-radius:50%; background: var(--mute); }
  .dot.on { background: var(--green); box-shadow: 0 0 10px var(--green); }
  .dot.off { background: var(--red); box-shadow: 0 0 10px var(--red); }
  nav { display:flex; gap:2px; padding:0 14px; overflow-x:auto;
    border-bottom:1px solid var(--border); background: var(--card); flex-shrink:0; }
  nav button { background:transparent; color: var(--mute); border:none;
    padding:12px 18px; font-size:13px; font-weight:600; cursor:pointer;
    border-radius:8px 8px 0 0; border-bottom:2px solid transparent;
    font-family:inherit; white-space:nowrap; }
  nav button:hover { color: var(--text); background: var(--bg4); }
  nav button.active { color: var(--accent2); border-bottom-color: var(--accent); }
  main { flex:1; overflow:hidden; padding:20px; display:flex;
    flex-direction:column; min-height:0; }
  .tab { display:none; }
  .tab.active { display:flex; flex-direction:column; gap:14px; flex:1;
    min-height:0; overflow-y:auto;
    animation: tabIn 0.18s ease-out both; }
  @keyframes tabIn {
    from { opacity:0; transform: translateY(4px); }
    to   { opacity:1; transform: translateY(0); }
  }
  .card { background: var(--card); border:1px solid var(--border);
    border-radius:12px; padding:18px; }
  .card h2 { margin:0 0 14px; font-size:13px; font-weight:700;
    color: var(--accent2); text-transform:uppercase; letter-spacing:0.6px; }
  .row { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
  .row:last-child { margin-bottom:0; }
  .row label { width:120px; color: var(--dim); font-size:12px; flex-shrink:0; }
  input[type=text], input[type=number], textarea, select {
    flex:1; background: var(--bg3); color: var(--text);
    border:1px solid var(--border); border-radius:8px;
    padding:9px 12px; font-family:inherit; font-size:13px; min-width:0; }
  input[type=range] { flex:1; accent-color: var(--accent);
    background:transparent; border:none; padding:0; }
  button.btn { background: var(--bg3); color: var(--text);
    border:1px solid var(--border); padding:9px 16px; border-radius:8px;
    font-family:inherit; font-size:13px; font-weight:600; cursor:pointer;
    white-space:nowrap; }
  button.btn:hover { background: var(--bg4); border-color: var(--border2); }
  button.btn.accent { background: var(--accent); color:#fff; border-color: var(--accent); }
  button.btn.accent:hover { background: var(--accent2); }
  button.btn.danger { background: color-mix(in srgb, var(--red) 20%, transparent);
    color: var(--text); border-color: var(--red); }
  button.btn.sm { padding:6px 12px; font-size:12px; }
  .console { background: var(--console); border:1px solid var(--border);
    border-radius:8px; padding:14px; overflow-y:auto;
    font-family:'Consolas', monospace; font-size:12.5px; line-height:1.55;
    color: var(--green); min-height:160px; }
  .log-line { margin-bottom:4px; word-break:break-word; }
  .log-time { color: var(--mute); margin-right:6px; }
  .log-info { color: var(--accent2); }
  .log-err { color: var(--red); }
  .log-warn { color: var(--yellow); }
  .log-ok { color: var(--green); }
  .status { color: var(--accent2); font-size:12px; font-weight:600; }
  .hint { color: var(--mute); font-size:11.5px; line-height:1.6; }
  .flex { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .themecard { background: var(--bg3); border:2px solid var(--border);
    border-radius:10px; padding:10px; cursor:pointer; transition:all 0.15s; }
  .themecard:hover { border-color: var(--accent); transform: translateY(-1px); }
  .themecard.active { border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }
  .cfg-section { margin-bottom:18px; padding-bottom:14px;
    border-bottom:1px solid var(--border); }
  .cfg-section:last-child { border-bottom:none; padding-bottom:0; margin-bottom:0; }
  .cfg-item { display:flex; align-items:center; gap:10px; padding:6px 0;
    font-size:13px; }
  .cfg-item label { flex:1; color:var(--text); }
  .cfg-item input[type=number] { max-width:120px; }
  .cfg-item input[type=checkbox] { width:18px; height:18px;
    accent-color:var(--accent); }
  iframe.embed { width:100%; border:1px solid var(--border);
    border-radius:8px; background:#08061a; }
</style>
</head>
<body>
<div id="bgLayer"></div>
<div id="bgOverlay"></div>
<div id="app">
  <header>
    <div class="brand">
      <div class="logo"></div>
      <h1>OpenCUY <span class="sub">· v3.4</span></h1>
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
          <button class="btn" onclick="loadMore()">⬇  Load more (3)</button>
        </div>
        <div class="row"><label>Language</label>
          <input type="text" id="lang" placeholder="e.g. en, es, ja" style="max-width:120px">
          <button class="btn sm" onclick="setLang()">Apply to next nav</button>
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
        <div class="row"><label>Ads / pick</label>
          <button class="btn sm" onclick="quickSkipAd()">⏭  Skip ad</button>
          <input type="number" id="ffSecs" value="30" min="1" max="600" style="max-width:80px">
          <button class="btn sm" onclick="quickForward()">⏩  Forward</button>
          <input type="number" id="pickIdx" value="1" min="0" max="40" style="max-width:70px">
          <button class="btn sm" onclick="quickPick()">🎯  Pick</button>
        </div>
        <div class="row"><label>Infinite scroll</label>
          <input type="text" id="infDist" value="5000" placeholder="dist px" style="max-width:100px">
          <input type="text" id="infSpeedPx" value="400" placeholder="px/s" style="max-width:80px">
          <input type="text" id="infDuration" placeholder="max s" style="max-width:80px">
          <button class="btn sm" onclick="infScroll()">∞  Start / update</button>
          <button class="btn sm" onclick="stopScroll()">■  Cancel</button>
        </div>
      </div>
      <div class="card">
        <h2>Bot / Filter Log</h2>
        <div class="flex" style="margin-bottom:8px">
          <button class="btn sm" onclick="popOutLog()">🪟  Pop out</button>
          <button class="btn sm" onclick="reloadLogFrame()">↻  Reload</button>
          <button class="btn sm" onclick="openLogInBrowser()">🌐  Open in browser</button>
          <button class="btn sm" onclick="clearLog()">🧹  Clear</button>
          <button class="btn sm" onclick="quickShotCount()">📸  Count</button>
          <button class="btn sm" onclick="quickArchiveShots()">🗜  Archive</button>
          <button class="btn sm danger" onclick="quickClearShots()">🧹  Clear shots</button>
          <span class="hint" id="logUrlHint"></span>
        </div>
        <iframe id="logFrame" class="embed" style="height:320px"
                src="about:blank"></iframe>
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
      </div>
      <div class="card">
        <h2>Chat Log</h2>
        <iframe id="chatFrame" class="embed" style="height:280px"
                src="about:blank"></iframe>
      </div>
    </section>

    <section class="tab" id="tab-cmds">
      <div class="card">
        <h2>Command Reference</h2>
        <pre class="hint" style="margin:0;white-space:pre-wrap;font-size:12px;line-height:1.6">
!search [query] [lang]         lang = e.g. ja, de, fr  (or lang:xx for any code)
!lucky [query] [lang]          search & play the first SAFE result
!searchwatch [query] [lang]    alias of !lucky
!video [id] [lang]             OWNER/MOD ONLY.
!channel [handle|UC-id] [lang]  also accepts full channel URLs
!home [lang]
!changelanguage [code] / !english
!scroll [+N | -N | N] [sec]
!watchhome [n]
!loadmore [n]                  scroll current page to load n more screenfuls (1–50)
!cc / !clearcookies

Ads & picking:
!skip / !adskip / !skipad      click YouTube's skip-ad button
!forward [sec] / !ff [sec]     fast-forward N seconds (default 30)
!pick N                        click the Nth playable card on the page
!pick list / !pick 0           list the pickable cards (see log)

Mod-lock:
!unlock                        (mod only) release the mod-lock early
!lockstatus                    show whether a mod-lock is active

Infinite scroll:
!infscroll DIST [SPEED] [DURATION]
     DIST     — pixels to travel (required, signed)
     SPEED    — px/s (optional, default 400)
     DURATION — seconds max before auto-stop (optional)
     Examples:
       !infscroll 5000             travel 5000 px at 400 px/s
       !infscroll 5000 250         travel 5000 px at 250 px/s (20 s)
       !infscroll -3000 600 5      travel -3000 px at 600 px/s, stop after 5 s

!stopscroll / !infclear / !infstop   stop infinite scroll

Screenshots:
!clearshots / !clearscreenshots / !shotsclear
                                  (mod only) clear every screenshot
!archiveshots                     (mod only) zip everything in
                                  screenshots/ → screenshots_archive/
!shotcount                        show live count, zip count, retention,
                                  and current archive/delete mode

Playback:
!speed | !pitch | !volume | !playpause | !refresh
!captions on|off | !captionstranslate [lang]
!loop video|playlist|off | !autoplay on|off
!fullscreen | !seek +N|-N|setN   (e.g. +10, set30)

Repeat: "!playpause 200" runs 200 times (UI cap 500, chat cap 20).
Forbidden: / ? # \\ inside a single command token.
Blocked: /gaming, /shorts, live streams, <24h videos, 360°/VR videos,
         playlists/mixes.

All video metadata (age, live status, 360°/VR) is read from the
rendered watch page DOM — yt-dlp is no longer used.

Home recommendations are replaced by a server-side dashboard.
The bot flags any video mentioning "Rocholo Javinar" (any spelling
variation) with a violet info banner — it never blocks playback.
Clicks on videos with cuss words in the title/channel are blocked
post-navigation by scanning the DOM.
Filter tables are stored base64-encoded so casual edits and
AI-assisted patches don't accidentally break them.
        </pre>
      </div>
    </section>

    <section class="tab" id="tab-theme">
      <div class="card">
        <h2>Color theme</h2>
        <div id="themeGrid" style="display:grid;
             grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px"></div>
      </div>
      <div class="card">
        <h2>Background image</h2>
        <div class="row"><label>Image</label>
          <input type="text" id="bgPath" placeholder="(none)" readonly>
          <button class="btn" onclick="browseBgImage()">Browse…</button>
          <button class="btn danger" onclick="clearBgImage()">Clear</button></div>
        <div class="row"><label>Fit mode</label>
          <select id="bgMode" style="max-width:200px">
            <option value="cover">Cover</option>
            <option value="contain">Contain</option>
            <option value="stretch">Stretch</option>
            <option value="tile">Tile</option>
          </select></div>
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
          <button class="btn" onclick="resetTheme()">Reset</button>
          <span class="hint" id="themeStatus">Saved automatically on change.</span>
        </div>
      </div>
    </section>

    <section class="tab" id="tab-config">
      <div class="card">
        <h2>Blocking &amp; Filtering</h2>
        <div class="cfg-section">
          <div class="cfg-item"><label>Block live streams</label>
            <input type="checkbox" id="cfg_block_live_streams" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block videos &lt; 24h</label>
            <input type="checkbox" id="cfg_block_under_24h" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block 360°/VR videos</label>
            <input type="checkbox" id="cfg_block_360" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block /gaming</label>
            <input type="checkbox" id="cfg_block_gaming" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block /shorts</label>
            <input type="checkbox" id="cfg_block_shorts" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Use headless checker</label>
            <input type="checkbox" id="cfg_use_headless_checker" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Off-site watchdog</label>
            <input type="checkbox" id="cfg_off_site_watchdog" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Home when video ends</label>
            <input type="checkbox" id="cfg_home_on_video_end" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Filter live + &lt;24h videos from home feed</label>
            <input type="checkbox" id="cfg_filter_home_feed" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block channel-page featured videos (bypass prevention)</label>
            <input type="checkbox" id="cfg_block_channel_featured" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Mute autoplay on channel pages</label>
            <input type="checkbox" id="cfg_mute_channel_autoplay" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Block videos whose title/channel contains a cuss word (DOM scan)</label>
            <input type="checkbox" id="cfg_probe_clicks" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Lock videos started by moderators (chat can't change until it ends)</label>
            <input type="checkbox" id="cfg_mod_lock_videos" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>DOM settle before probing (s)</label>
            <input type="number" id="cfg_dom_settle_seconds" step="0.1" min="0.5" max="30" onchange="saveConfigSection()"></div>

        </div>

        <h2 style="margin-top:20px">Timing</h2>
        <div class="cfg-section">
          <div class="cfg-item"><label>Headless settle (s)</label>
            <input type="number" id="cfg_headless_settle_seconds" step="0.1" min="0" max="10" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Screenshot delay (s)</label>
            <input type="number" id="cfg_screenshot_delay_seconds" step="0.1" min="0" max="60" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Video retry delay (s)</label>
            <input type="number" id="cfg_video_retry_delay_seconds" step="0.1" min="0" max="30" onchange="saveConfigSection()"></div>
        </div>

        <h2 style="margin-top:20px">Spam / Repeat caps</h2>
        <div class="cfg-section">
          <div class="cfg-item"><label>UI repeat cap</label>
            <input type="number" id="cfg_ui_repeat_cap" min="1" max="5000" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Chat repeat cap</label>
            <input type="number" id="cfg_chat_repeat_cap" min="1" max="500" onchange="saveConfigSection()"></div>
        </div>

        <h2 style="margin-top:20px">Transitions</h2>
        <div class="cfg-section">
          <div class="cfg-item"><label>Tab fade (ms)</label>
            <input type="number" id="cfg_tab_ms" min="0" max="2000" step="10" onchange="saveConfigSection()"></div>
          <div class="cfg-item"><label>Tab slide (px)</label>
            <input type="number" id="cfg_tab_slide_px" min="0" max="40" onchange="saveConfigSection()"></div>
        </div>

        <div class="flex" style="margin-top:14px">
          <button class="btn accent" onclick="saveConfigSection()">💾 Save now</button>
          <button class="btn" onclick="reloadConfig()">↻ Reload from disk</button>
          <button class="btn danger" onclick="resetConfigToDefaults()">⟲ Reset to defaults</button>
          <span class="hint" id="cfgStatus">Changes save automatically.</span>
        </div>
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
        <div class="console" id="depsLog" style="min-height:120px"></div>
      </div>
    </section>
  </main>
</div><script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
let bridge = null;

function asString(v){ return (v===null||v===undefined)?'':String(v); }
function asJSON(v, fb){ try { const s=asString(v); if(!s) return fb; return JSON.parse(s); } catch(e){ return fb; } }
function mkText(tag, cls, text){
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

async function bridgeString(fn, fallback) {
  try {
    const v = await fn();
    return (v === null || v === undefined) ? fallback : String(v);
  } catch (e) { return fallback; }
}
function setIframeSrc(id, url) {
  const f = document.getElementById(id);
  if (!f) return;
  if (typeof url !== 'string' || !url) { f.src = 'about:blank'; return; }
  if (url.indexOf('[object') === 0) { f.src = 'about:blank'; return; }
  if (!/^(https?:|file:)/i.test(url)) { f.src = 'about:blank'; return; }
  f.src = url;
}

new QWebChannel(qt.webChannelTransport, async ch => {
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

  appendLog('ipcLog', 'Bridge connected.', 'ok');
  refreshDeps();
  loadConfig();
  initLogFrame();
  initChatFrame();

  const savedThemeStr = await bridgeString(() => bridge.get_theme(), '{}');
  const savedTheme = asJSON(savedThemeStr, {});
  if (savedTheme && savedTheme.name) {
    currentTheme = Object.assign({}, currentTheme, savedTheme);
  }
  buildThemeGrid();
  applyTheme(currentTheme);
});

document.querySelectorAll('nav button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav button').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-' + b.dataset.tab).classList.add('active');
  };
});

function appendLog(elId, msg, level) {
  const box = document.getElementById(elId); if (!box) return;
  const time = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = 'log-line';
  line.appendChild(mkText('span', 'log-time', '[' + time + ']'));
  line.appendChild(mkText('span', level ? 'log-' + level : '', msg));
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function popOutLog() { try { if (bridge) bridge.popout_log(); } catch(e) {} }
function clearLog() { initLogFrame(); }

async function initLogFrame() {
  const url = await bridgeString(() => bridge.get_log_url(), '');
  if (url) {
    setIframeSrc('logFrame', url);
    document.getElementById('logUrlHint').textContent = url;
  }
}
async function initChatFrame() {
  const url = await bridgeString(() => bridge.get_chat_url(), '');
  if (url) setIframeSrc('chatFrame', url);
}
function reloadLogFrame() { initLogFrame(); }
function openLogInBrowser() {
  try { if (bridge && bridge.open_log_external) bridge.open_log_external(); } catch(e) {}
}

/* ═══════ BOT TAB ═══════ */
function runQuery() {
  const q = document.getElementById('query').value.trim();
  if (!q) return;
  const lang = document.getElementById('lang').value.trim();
  if (q.startsWith('!')) bridge.dispatch_command(q.slice(1), lang, 'you');
  else bridge.dispatch_command(q, lang, 'you');
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
function loadMore() {
  const n = parseInt(prompt('How many screenfuls to load? (1–50)', '3')) || 3;
  bridge.dispatch_command('loadmore ' + n, '', 'you');
}
function quickSkipAd() { bridge.dispatch_command('skip', '', 'you'); }
function quickForward() {
  const s = parseFloat(document.getElementById('ffSecs').value) || 30;
  bridge.dispatch_command('forward ' + s, '', 'you');
}
function quickPick() {
  const n = parseInt(document.getElementById('pickIdx').value);
  if (isNaN(n)) { bridge.dispatch_command('pick list', '', 'you'); return; }
  bridge.dispatch_command('pick ' + n, '', 'you');
}
function quickShotCount() {
  bridge.dispatch_command('shotcount', '', 'you');
}
function quickArchiveShots() {
  if (!confirm('Move every screenshot into a new zip archive?')) return;
  bridge.dispatch_command('archiveshots', '', 'you');
}
function quickClearShots() {
  if (!confirm('Clear all screenshots? If archiving is on, they will be zipped first.')) return;
  bridge.dispatch_command('clearshots', '', 'you');
}
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
function infScroll() {
  const dist = (document.getElementById('infDist')?.value || '').trim();
  const spd  = (document.getElementById('infSpeedPx')?.value || '').trim();
  const dur  = (document.getElementById('infDuration')?.value || '').trim();
  if (!dist) {
    appendLog('ipcLog', 'Enter a distance in px, e.g. 5000', 'warn');
    return;
  }
  let cmd = 'infscroll ' + dist;
  if (spd) cmd += ' ' + spd;
  if (dur) cmd += ' ' + dur;
  bridge.dispatch_command(cmd, '', 'you');
}
function stopScroll() { bridge.dispatch_command('stopscroll', '', 'you'); }
function watchHome() {
  const n = parseInt(document.getElementById('watchIdx').value) || 1;
  bridge.dispatch_command('watchhome ' + n, '', 'you');
}

/* ═══════ CHAT TAB ═══════ */
function startChat() {
  const vid = document.getElementById('chatVid').value.trim();
  if (!vid) { appendLog('ipcLog', 'Enter a video ID.', 'warn'); return; }
  bridge.chat_start(vid, document.getElementById('chatPrefix').value || '!');
}
function stopChat() { bridge.chat_stop_remote(); }
function onChatEvent(ev) {
  if (!ev || !ev.type) return;
  if (ev.type === 'system') {
    document.getElementById('chatStatus').textContent = 'Chat: connected';
  } else if (ev.type === 'status') {
    document.getElementById('chatStatus').textContent = ev.message;
  }
}

/* ═══════ THEME ═══════ */
let currentTheme = { name:'nexo', image_path:'', image_mode:'cover',
                     image_opacity:0.35, image_blur:4, image_dim:0.72 };

const THEMES = [
  { id:'nexo',  name:'Nexo Violet', swatches:['#0d0b1a','#8b5cf6','#a78bfa','#34d399'] },
  { id:'black', name:'Black',       swatches:['#000000','#d4d4d4','#e5e5e5','#4ade80'] },
  { id:'red',   name:'Red',         swatches:['#1a0606','#ef4444','#f87171','#fca5a5'] },
  { id:'blue',  name:'Blue',        swatches:['#050f1f','#3b82f6','#60a5fa','#93c5fd'] },
  { id:'green', name:'Green',       swatches:['#041409','#10b981','#34d399','#6ee7b7'] },
  { id:'amber', name:'Amber',       swatches:['#1a1000','#f59e0b','#fbbf24','#fcd34d'] },
  { id:'pink',  name:'Pink',        swatches:['#1a0614','#ec4899','#f472b6','#f9a8d4'] },
  { id:'light', name:'Light',       swatches:['#f4f4f8','#6366f1','#818cf8','#a5b4fc'] },
];

function buildThemeGrid() {
  const grid = document.getElementById('themeGrid');
  if (!grid) return;
  grid.innerHTML = '';
  THEMES.forEach(t => {
    const card = document.createElement('div');
    card.className = 'themecard' + (t.id === currentTheme.name ? ' active' : '');
    card.dataset.themeId = t.id;
    const title = mkText('div', null, t.name);
    title.style.cssText = 'font-size:12px;font-weight:700;color:var(--text)';
    card.appendChild(title);
    const swRow = document.createElement('div');
    swRow.style.cssText = 'display:flex;gap:4px;margin-top:8px';
    t.swatches.forEach(c => {
      const s = document.createElement('span');
      s.style.cssText =
        'display:inline-block;width:18px;height:18px;border-radius:50%;'
        + 'background:' + c + ';border:1px solid rgba(255,255,255,0.1)';
      swRow.appendChild(s);
    });
    card.appendChild(swRow);
    card.onclick = () => {
      currentTheme.name = t.id; applyTheme(currentTheme);
      document.querySelectorAll('.themecard').forEach(x =>
        x.classList.toggle('active', x.dataset.themeId === t.id));
      saveTheme(true);
    };
    grid.appendChild(card);
  });
}

async function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme.name || 'nexo');
  const bg = document.getElementById('bgLayer');
  const ov = document.getElementById('bgOverlay');
  const imgUrl = await bridgeString(() => bridge.get_bg_image_url(), '');
  if (imgUrl) {
    bg.style.backgroundImage = 'url("' + imgUrl + '")';
    bg.classList.remove('cover','contain','stretch','tile');
    bg.classList.add(theme.image_mode || 'cover');
    bg.style.opacity = String(theme.image_opacity != null ? theme.image_opacity : 0.35);
    bg.style.filter = 'blur(' + (theme.image_blur || 0) + 'px)';
  } else {
    bg.style.backgroundImage = '';
    bg.style.opacity = '0';
  }
  const dim = theme.image_dim != null ? theme.image_dim : 0.72;
  ov.style.opacity = imgUrl ? String(dim) : '0';
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
}

async function browseBgImage() {
  if (!bridge || !bridge.browse_image) return;
  const p = await bridgeString(() => bridge.browse_image(), '');
  if (!p) return;
  currentTheme.image_path = p; applyTheme(currentTheme); saveTheme(true);
}
function clearBgImage() { currentTheme.image_path=''; applyTheme(currentTheme); saveTheme(true); }
function saveTheme(silent) {
  try {
    if (bridge && bridge.save_theme) {
      bridge.save_theme(JSON.stringify(currentTheme));
    }
    applyTheme(currentTheme);
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

/* ═══════ CONFIG ═══════ */
function applyTransitionConfig(cfg) {
  const tr = (cfg && cfg.transitions) || {};
  const ms = parseInt(tr.tab_ms, 10);
  const px = parseInt(tr.tab_slide_px, 10);
  const msVal = isNaN(ms) ? 180 : ms;
  const pxVal = isNaN(px) ? 4 : px;
  let st = document.getElementById('__trans_style__');
  if (!st) {
    st = document.createElement('style');
    st.id = '__trans_style__';
    document.head.appendChild(st);
  }
  st.textContent = `
    .tab.active { animation: tabIn ${msVal}ms ease-out both !important; }
    @keyframes tabIn {
      from { opacity:0; transform: translateY(${pxVal}px); }
      to   { opacity:1; transform: translateY(0); }
    }
  `;
}

function applyConfigToUI(cfg) {
  applyTransitionConfig(cfg);
  const b = cfg.behavior || {}, t = cfg.timing || {},
        s = cfg.spam || {}, o = cfg.overlay || {},
        tr = cfg.transitions || {};
  const setChk = (id, v) => { const e = document.getElementById(id); if (e) e.checked = !!v; };
  const setNum = (id, v) => { const e = document.getElementById(id); if (e && v !== undefined && v !== null) e.value = v; };
  setChk('cfg_home_on_video_end', b.home_on_video_end);
  setChk('cfg_off_site_watchdog', b.off_site_watchdog);
  setChk('cfg_block_live_streams', b.block_live_streams);
  setChk('cfg_block_under_24h', b.block_under_24h);
  setChk('cfg_use_headless_checker', b.use_headless_checker);
  setChk('cfg_fallback_unknown_as_search', b.fallback_unknown_as_search);
  setChk('cfg_fallback_plaintext_as_search', b.fallback_plaintext_as_search);
  setChk('cfg_block_gaming', b.block_gaming);
  setChk('cfg_block_shorts', b.block_shorts);
  setChk('cfg_blank_home_feed', b.blank_home_feed);
  setChk('cfg_detect_rocholo', b.detect_rocholo);
  setChk('cfg_hide_sidebar', b.hide_sidebar);
  setChk('cfg_hide_topbar', b.hide_topbar);
  setChk('cfg_block_360', b.block_360);
  setChk('cfg_filter_home_feed', b.filter_home_feed);
  setChk('cfg_theme_youtube_bg', b.theme_youtube_bg);
  setChk('cfg_block_channel_featured', b.block_channel_featured);
  setChk('cfg_mute_channel_autoplay', b.mute_channel_autoplay);
  setChk('cfg_probe_clicks', b.probe_clicks);
  setChk('cfg_mod_lock_videos', b.mod_lock_videos);
  setNum('cfg_dom_settle_seconds', b.dom_settle_seconds);
  setChk('cfg_archive_screenshots', b.archive_screenshots);
  setNum('cfg_screenshot_retention_hours', b.screenshot_retention_hours);
  setNum('cfg_headless_settle_seconds', t.headless_settle_seconds);
  setNum('cfg_screenshot_delay_seconds', t.screenshot_delay_seconds);
  setNum('cfg_video_retry_delay_seconds', t.video_retry_delay_seconds);
  setNum('cfg_ui_repeat_cap', s.ui_repeat_cap);
  setNum('cfg_chat_repeat_cap', s.chat_repeat_cap);
  setChk('cfg_show_chat_iframe', o.show_chat_iframe);
  setNum('cfg_chat_iframe_width', o.chat_iframe_width);
  setNum('cfg_chat_iframe_height', o.chat_iframe_height);
  setNum('cfg_chat_iframe_bottom', o.chat_iframe_bottom);
  setNum('cfg_chat_iframe_left', o.chat_iframe_left);
  const chatModeEl = document.getElementById('cfg_chat_iframe_mode');
  if (chatModeEl) chatModeEl.value = o.chat_iframe_mode || 'iframe';
  setChk('cfg_show_log_iframe', o.show_log_iframe);
  setNum('cfg_log_iframe_width', o.log_iframe_width);
  setNum('cfg_log_iframe_height', o.log_iframe_height);
  setNum('cfg_log_iframe_bottom', o.log_iframe_bottom);
  setNum('cfg_log_iframe_right', o.log_iframe_right);
  const logModeEl = document.getElementById('cfg_log_iframe_mode');
  if (logModeEl) logModeEl.value = o.log_iframe_mode || 'iframe';
  setNum('cfg_tab_ms', tr.tab_ms);
  setNum('cfg_tab_slide_px', tr.tab_slide_px);
}

async function loadConfig() {
  try {
    const cfgStr = await bridgeString(() => bridge.get_config(), '{}');
    const cfg = asJSON(cfgStr, {});
    applyConfigToUI(cfg);
  } catch(e) {
    appendLog('ipcLog', 'Config load failed: ' + e.message, 'err');
  }
}

function collectConfigFromUI() {
  const getChk = id => { const e = document.getElementById(id); return e ? !!e.checked : false; };
  const getNum = (id, fb) => {
    const e = document.getElementById(id);
    if (!e) return fb;
    const n = Number(e.value);
    return Number.isFinite(n) ? n : fb;
  };
  const chatModeEl = document.getElementById('cfg_chat_iframe_mode');
  const logModeEl = document.getElementById('cfg_log_iframe_mode');
  return {
    behavior: {
      home_on_video_end: getChk('cfg_home_on_video_end'),
      off_site_watchdog: getChk('cfg_off_site_watchdog'),
      block_live_streams: getChk('cfg_block_live_streams'),
      block_under_24h: getChk('cfg_block_under_24h'),
      use_headless_checker: getChk('cfg_use_headless_checker'),
      fallback_unknown_as_search: getChk('cfg_fallback_unknown_as_search'),
      fallback_plaintext_as_search: getChk('cfg_fallback_plaintext_as_search'),
      block_gaming: getChk('cfg_block_gaming'),
      block_shorts: getChk('cfg_block_shorts'),
      blank_home_feed: getChk('cfg_blank_home_feed'),
      detect_rocholo: getChk('cfg_detect_rocholo'),
      hide_sidebar: getChk('cfg_hide_sidebar'),
      hide_topbar: getChk('cfg_hide_topbar'),
      block_360: getChk('cfg_block_360'),
      filter_home_feed: getChk('cfg_filter_home_feed'),
      theme_youtube_bg: getChk('cfg_theme_youtube_bg'),
      block_channel_featured: getChk('cfg_block_channel_featured'),
      mute_channel_autoplay: getChk('cfg_mute_channel_autoplay'),
      probe_clicks: getChk('cfg_probe_clicks'),
      mod_lock_videos: getChk('cfg_mod_lock_videos'),
      dom_settle_seconds: getNum('cfg_dom_settle_seconds', 2.5),
      archive_screenshots: getChk('cfg_archive_screenshots'),
      screenshot_retention_hours: getNum('cfg_screenshot_retention_hours', 3),
    },
    timing: {
      headless_settle_seconds: getNum('cfg_headless_settle_seconds', 1.0),
      screenshot_delay_seconds: getNum('cfg_screenshot_delay_seconds', 1.0),
      video_retry_delay_seconds: getNum('cfg_video_retry_delay_seconds', 2.0),
    },
    spam: {
      ui_repeat_cap: getNum('cfg_ui_repeat_cap', 500),
      chat_repeat_cap: getNum('cfg_chat_repeat_cap', 20),
    },
    overlay: {
      show_chat_iframe: getChk('cfg_show_chat_iframe'),
      chat_iframe_mode: (chatModeEl && chatModeEl.value) || 'iframe',
      chat_iframe_width: getNum('cfg_chat_iframe_width', 340),
      chat_iframe_height: getNum('cfg_chat_iframe_height', 220),
      chat_iframe_bottom: getNum('cfg_chat_iframe_bottom', 60),
      chat_iframe_left: getNum('cfg_chat_iframe_left', 14),
      show_log_iframe: getChk('cfg_show_log_iframe'),
      log_iframe_mode: (logModeEl && logModeEl.value) || 'iframe',
      log_iframe_width: getNum('cfg_log_iframe_width', 340),
      log_iframe_height: getNum('cfg_log_iframe_height', 220),
      log_iframe_bottom: getNum('cfg_log_iframe_bottom', 60),
      log_iframe_right: getNum('cfg_log_iframe_right', 14),
    },
    transitions: {
      tab_ms: getNum('cfg_tab_ms', 180),
      tab_slide_px: getNum('cfg_tab_slide_px', 4),
    },
  };
}

function saveConfigSection() {
  const payload = collectConfigFromUI();
  try {
    bridge.save_config(JSON.stringify(payload));
    applyTransitionConfig(payload);
    try {
      if (bridge && bridge.reinject_overlay) bridge.reinject_overlay();
    } catch(e) {}
    try {
      if (bridge && bridge.refresh_blank_home) bridge.refresh_blank_home();
    } catch(e) {}
    const s = document.getElementById('cfgStatus');
    if (s) { s.textContent = 'Config saved.';
             setTimeout(() => s.textContent = 'Changes save automatically.', 1500); }
  } catch(e) {
    appendLog('ipcLog', 'Config save failed: ' + e.message, 'err');
  }
}
function reloadConfig() { loadConfig(); }
async function resetConfigToDefaults() {
  if (!confirm('Reset all config values to the built-in defaults?')) return;
  try {
    if (bridge && bridge.reset_config) await bridge.reset_config();
    await loadConfig();
    try { if (bridge && bridge.reinject_overlay) bridge.reinject_overlay(); } catch(e) {}
    try { if (bridge && bridge.refresh_blank_home) bridge.refresh_blank_home(); } catch(e) {}
  } catch(e) {
    appendLog('ipcLog', 'Reset failed: ' + e.message, 'err');
  }
}

/* ═══════ DEPS TAB ═══════ */
async function refreshDeps() {
  try {
    const infoStr = await bridgeString(() => bridge.get_dep_info(), '{}');
    renderDeps(asJSON(infoStr, {}));
  } catch(e) { appendLog('depsLog', 'Failed: ' + e.message, 'err'); }
}
function renderDeps(info) {
  const box = document.getElementById('depsList');
  if (!info || !Array.isArray(info.deps)) { box.textContent = 'No info'; return; }
  while (box.firstChild) box.removeChild(box.firstChild);
  info.deps.forEach(d => {
    const row = document.createElement('div');
    row.style.padding = '8px 0';
    row.style.borderBottom = '1px solid var(--border)';
    const nm = document.createElement('b'); nm.textContent = d.package;
    nm.style.color = 'var(--text)';
    row.appendChild(nm);
    row.appendChild(document.createTextNode(' — '));
    const desc = document.createElement('span');
    desc.textContent = d.description;
    desc.style.color = 'var(--dim)';
    row.appendChild(desc);
    row.appendChild(document.createTextNode(' '));
    const st = document.createElement('span');
    st.textContent = d.installed ? '✓ installed'
                                 : (d.required ? '✗ required' : '! optional');
    st.style.color = d.installed ? 'var(--green)' : 'var(--red)';
    row.appendChild(st);
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
    appendLog('depsLog', 'Done. ' + ev.installed + ' installed, ' + ev.failed + ' failed.',
              ev.failed > 0 ? 'warn' : 'ok');
    refreshDeps();
  }
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
</script>
</body>
</html>
"""


class LogPopout(QDialog):
    def __init__(self, url="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenCUY — Log")
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        self.url_label = QLabel(url or "(waiting …)")
        self.url_label.setStyleSheet(
            "color:#8b88b8; font-family:Consolas,monospace; font-size:11.5px;")
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self.url_label, 1)

        reload_btn = QPushButton("↻ Reload")
        reload_btn.clicked.connect(self.reload)
        bar.addWidget(reload_btn)

        open_btn = QPushButton("🌐 Open in browser")
        open_btn.clicked.connect(self.open_external)
        bar.addWidget(open_btn)
        layout.addLayout(bar)

        self.view = QWebEngineView()
        self.view.setStyleSheet("background:#08061a; border-radius:6px;")
        layout.addWidget(self.view)
        if url:
            self.view.setUrl(QUrl(url))

    def set_url(self, url):
        self.url_label.setText(url or "(no url)")
        if url and (self.view.url().toString() != url):
            self.view.setUrl(QUrl(url))

    def reload(self):
        try: self.view.reload()
        except Exception: pass

    def open_external(self):
        try:
            url = self.view.url().toString()
            if url: QDesktopServices.openUrl(QUrl(url))
        except Exception: pass


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
        self._log_popout = None

        self.bot.on_watch_state = self._bot_notify_watch
        self.bot.on_status      = self._bot_status

        self.bot.start()
        self.status_update.emit("Booting…", False)

        self._pump = QTimer()
        self._pump.setInterval(120)
        self._pump.timeout.connect(self._pump_bot_queue)
        self._pump.start()

        self.log_message.connect(
            self._fanout_log, Qt.ConnectionType.QueuedConnection)

    def _fanout_log(self, msg, level):
        if self._log_popout is not None:
            try: self._log_popout.set_url(self.get_log_url())
            except Exception: pass
        try:
            self.bot.overlay_server.push_log(msg, level)
        except Exception:
            pass

    @Slot()
    def popout_log(self):
        url = self.get_log_url()
        if self._log_popout is None:
            self._log_popout = LogPopout(url)
        else:
            self._log_popout.set_url(url)
        self._log_popout.show()
        self._log_popout.raise_()
        self._log_popout.activateWindow()

    def _bot_status(self, text, ok=True):
        try: self.status_update.emit(text, ok)
        except Exception: pass

    def _bot_notify_watch(self, on_watch: bool, title: str = ""):
        pass

    def _pump_bot_queue(self):
        q = self._bot_queue
        while True:
            try: ev = q.get_nowait()
            except queue.Empty: break
            k = ev.get("kind")
            if k == "log":
                self.log_message.emit(ev.get("message", ""),
                                      ev.get("level", "info"))
            elif k == "chat":
                author = ev.get("author", "?")
                msg = ev.get("message", "")
                try:
                    self.bot._stat_bump("chat_messages")
                    self.bot._totals_bump("chat_messages")
                except Exception:
                    pass
                self.chat_event.emit(json.dumps({
                    "type": "chat", "author": author, "message": msg,
                    "datetime": ev.get("datetime", ""),
                    "is_owner": bool(ev.get("is_owner")),
                    "is_mod":   bool(ev.get("is_mod")),
                }))
                self.log_message.emit(f"{author}: {msg}", "info")
                try:
                    self.bot.overlay_server.push_chat_view({
                        "kind": "chat", "author": author, "message": msg,
                    })
                except Exception: pass
                prefix = self.chat_cfg.get("prefix", "!")
                privileged = bool(ev.get("is_owner")) or bool(ev.get("is_mod"))
                if msg.startswith(prefix):
                    body = msg[len(prefix):].strip()
                    if body:
                        self._run_command(body, "", f"chat:{author}",
                                          privileged=privileged)
            elif k == "chat_status":
                msg = ev.get("message", "")
                lvl = ev.get("level", "info")
                self.chat_event.emit(json.dumps({
                    "type": "status", "message": msg, "level": lvl}))
                self.log_message.emit(msg, lvl)
                try:
                    kind = "err" if lvl == "err" else "sys"
                    self.bot.overlay_server.push_chat_view({
                        "kind": kind, "message": msg,
                    })
                except Exception: pass
                try:
                    connected = (lvl == "ok")
                    self.bot._push_chat_status(msg, connected=connected, level=lvl)
                except Exception:
                    pass

    @Slot(str, str, str)
    def request_search(self, query, lang="", source="ui"):
        lang = (lang or "").strip() or None
        self.bot.set_request_privilege(True)
        try:
            self.bot.request_search(query, lucky=False, lang=lang,
                                    source=source)
        finally:
            self.bot.set_request_privilege(False)

    @Slot(str, str, str, bool)
    def request_search_lucky(self, query, lang, source, lucky=True):
        lang = (lang or "").strip() or None
        self.bot.set_request_privilege(True)
        try:
            self.bot.request_search(query, lucky=bool(lucky), lang=lang,
                                    source=source)
        finally:
            self.bot.set_request_privilege(False)

    @Slot(str, str)
    def request_playback(self, kind, arg=""):
        self.bot.request_playback(kind, arg, source="ui")

    @Slot(str, str, str)
    def dispatch_command(self, body, lang_extra, source):
        self._run_command(body, lang_extra, source)

    @Slot()
    def reinject_overlay(self):
        try: self.bot._install_overlay_on_current_page()
        except Exception: pass
        try:
            self.bot.overlay_server.push(
                dict(self.bot.overlay_server._last_payload))
        except Exception:
            pass
        try:
            self.bot.overlay_server.push_warn(
                self.bot.overlay_server._last_warn_payload.get("text", ""),
                kind=self.bot.overlay_server._last_warn_payload.get("kind",
                                                                    "warn"))
        except Exception:
            pass
        try:
            name = self.bot.overlay_server._last_painted_theme_name
            pal = self.bot.overlay_server._last_painted_palette
            if not name:
                try:
                    cfg = _load_json(THEME_FILE, None) or {}
                    name = (cfg.get("name")
                            or DEFAULT_THEME["name"]).lower()
                except Exception:
                    name = DEFAULT_THEME["name"]
            if not pal:
                pal = THEME_PALETTE.get(
                    name, THEME_PALETTE[DEFAULT_THEME["name"]])
            self.bot.overlay_server.push_theme({
                "name": name, "palette": dict(pal),
            })
        except Exception:
            pass
        try:
            self.bot._broadcast_screenshot_count()
        except Exception:
            pass
        try:
            self.bot.driver.execute_script("""
                try {
                    if (typeof window.__opencuy_overlay_install === 'function') {
                        window.__opencuy_overlay_install();
                    }
                } catch(e) {}
            """)
        except Exception:
            pass

    @Slot()
    def refresh_blank_home(self):
        try:
            if BLANK_HOME_FEED:
                self.bot._blank_home_feed()
            else:
                try:
                    self.bot.driver.execute_script("""
                        const st = document.getElementById(
                            '__opencuy_blank_home__');
                        if (st && st.parentNode) st.parentNode.removeChild(st);
                        const host = document.getElementById(
                            '__opencuy_home_stats__');
                        if (host && host.parentNode) host.parentNode.removeChild(host);
                        document.body.style.overflow = '';
                    """)
                except Exception:
                    pass
            try: self.bot._install_chrome_cleanup()
            except Exception: pass
            try: self.bot._install_theme_background()
            except Exception: pass
            try: self.bot._install_overlay_on_current_page()
            except Exception: pass
        except Exception:
            pass

    def _run_command(self, body, lang_extra, source, privileged=None):
        body = (body or "").strip()
        if not body: return
        if privileged is None:
            privileged = not source.startswith("chat:")
        lang_extra = (lang_extra or "").strip()
        if lang_extra:
            toks = body.split()
            first = toks[0].lower().lstrip("!")
            rest = toks[1:]
            is_plain = (not source.startswith("chat:")
                        and first not in CommandDispatcher.KNOWN)
            suffix = None
            if first == "home":
                if not rest: suffix = lang_extra
            elif first == "channel":
                if len(rest) < 2: suffix = lang_extra
            elif first in ("search", "lucky", "searchwatch"):
                if CommandDispatcher._split_lang(" ".join(rest))[1] is None:
                    suffix = "lang:" + lang_extra
            elif is_plain:
                if CommandDispatcher._split_lang(body)[1] is None:
                    suffix = "lang:" + lang_extra
            if suffix:
                body = body + " " + suffix
        is_chat = source.startswith("chat:")
        try:
            self.bot._stat_bump("commands")
            self.bot._totals_bump("commands")
        except Exception:
            pass
        try: self.bot._stat_event(f"cmd ({source}): {body[:60]}")
        except Exception: pass
        allow_screenshot = (not is_chat) or (not privileged)
        if allow_screenshot:
            try:
                self.bot._schedule_screenshot(f"{source}: !{body[:40]}")
            except Exception:
                pass

        try:
            locked, lock_vid = self.bot._is_mod_locked()
        except Exception:
            locked, lock_vid = False, ""
        if locked and not privileged:
            first_tok = (body.split(None, 1) or [""])[0].lower().lstrip("!")
            SAFE_CMDS = {
                "playpause", "refresh", "fullscreen", "captions",
                "captionstranslate", "loop", "autoplay", "speed",
                "pitch", "volume", "seek", "scroll", "infscroll",
                "stopscroll", "infclear", "infstop", "stopinf",
                "skip", "adskip", "skipad", "forward",
                "ff", "me", "cc", "clearcookies", "loadmore",
                "lockstatus",
            }
            if first_tok not in SAFE_CMDS:
                self._log_from_dispatcher(
                    f"[mod-lock] Blocked !{first_tok} from {source} — "
                    f"video {lock_vid or '?'} was started by a moderator.",
                    "warn")
                try:
                    self.bot._stat_bump("mod_lock_blocked")
                    self.bot._totals_bump("mod_lock_blocked")
                except Exception:
                    pass
                try:
                    self.bot._notify_blocked(
                        "🔒 VIDEO LOCKED BY MODERATOR\n\n"
                        "A moderator started the current video.\n"
                        "Please wait for the video to end before "
                        "changing it.")
                except Exception:
                    pass
                return
        elif locked and privileged:
            _toks = (body.split(None, 1) or [""])
            _first = _toks[0].lower().lstrip("!") if _toks else ""
            if _first:
                VIDEO_CHANGERS = {
                    "video", "search", "lucky", "searchwatch",
                    "channel", "home", "watchhome", "pick",
                }
                if _first in VIDEO_CHANGERS:
                    try:
                        self.bot._clear_mod_lock(
                            f"mod override by {source}")
                    except Exception:
                        pass

        try:
            self.bot.set_request_privilege(bool(privileged))
        except Exception:
            pass
        disp = CommandDispatcher(self.bot, self._log_from_dispatcher,
                                 source=source,
                                 reply_fn=self._generic_reply,
                                 is_privileged=privileged)
        try:
            disp.handle(body)
        finally:
            try:
                self.bot.set_request_privilege(False)
            except Exception:
                pass

    def _log_from_dispatcher(self, msg, level="info"):
        self.log_message.emit(msg, level)

    def _generic_reply(self, msg, level="info"):
        self.log_message.emit(msg, level)
        self.chat_event.emit(json.dumps({"type": "cmd", "message": msg}))
        try:
            self.bot.overlay_server.push_chat_view({
                "kind": "cmd", "message": msg,
            })
        except Exception: pass

    @Slot(str, str)
    def chat_start(self, video_id, prefix):
        if not PYCHAT_AVAILABLE:
            self.chat_event.emit(json.dumps({"type": "error",
                "message": "pytchat not installed"})); return
        self.chat_cfg["prefix"] = prefix or "!"
        try: self.chat_stop.set()
        except Exception: pass
        self.chat_stop = threading.Event()
        self.chat_reader = LiveChatReader(video_id.strip(), self._bot_queue,
                                          self.chat_stop)
        self.chat_reader.start()
        try:
            self.bot._push_chat_status(
                f"Chat: connecting to {video_id.strip()}",
                connected=False, level="info")
        except Exception:
            pass

    @Slot()
    def chat_stop_remote(self):
        if self.chat_stop: self.chat_stop.set()
        try:
            self.bot._push_chat_status("Chat: idle", connected=False)
        except Exception:
            pass
        self.chat_event.emit(json.dumps({"type": "status", "message": "Chat: stopped"}))

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

    @Slot(result=str)
    def get_screenshot_count(self):
        try:
            return str(self.bot._count_screenshots())
        except Exception:
            return "0"

    @Slot(result=str)
    def get_config(self):
        return str(json.dumps(CONFIG.data))

    @Slot(str, result=bool)
    def save_config(self, json_str):
        try:
            incoming = json.loads(json_str or "{}")
            CONFIG.update_from_dict(incoming)
            self.log_message.emit("[config] saved.", "info")
            return True
        except Exception as e:
            self.log_message.emit(f"Config save failed: {e}", "err")
            return False

    @Slot(result=bool)
    def reset_config(self):
        try:
            CONFIG.reset_to_defaults()
            self.log_message.emit("[config] reset to defaults.", "info")
            return True
        except Exception as e:
            self.log_message.emit(f"Config reset failed: {e}", "err")
            return False

    @Slot(result=str)
    def get_log_url(self):
        try:
            port = self.bot.overlay_server.port
            if not port: return ""
            return f"http://127.0.0.1:{port}/log-view"
        except Exception:
            return ""

    @Slot(result=str)
    def get_chat_url(self):
        try:
            port = self.bot.overlay_server.port
            if not port: return ""
            return f"http://127.0.0.1:{port}/chat-view"
        except Exception:
            return ""

    @Slot(result=str)
    def get_home_dash_url(self):
        try:
            port = self.bot.overlay_server.port
            if not port: return ""
            return f"http://127.0.0.1:{port}/home-dash"
        except Exception:
            return ""

    @Slot(result=str)
    def get_overlay_port(self):
        try:
            p = self.bot.overlay_server.port
            return str(p) if p else str(PREFERRED_PORT)
        except Exception:
            return str(PREFERRED_PORT)

    @Slot(result=str)
    def get_bg_image_url(self):
        try:
            port = self.bot.overlay_server.port or PREFERRED_PORT
            img = self.bot.overlay_server.bg_image_path or ""
            if not img:
                return ""
            try:
                mt = int(os.path.getmtime(img))
            except Exception:
                mt = int(time.time())
            return f"http://127.0.0.1:{port}/bg-image?t={mt}"
        except Exception:
            return ""

    @Slot()
    def open_log_external(self):
        url = self.get_log_url()
        if url:
            try: QDesktopServices.openUrl(QUrl(url))
            except Exception: pass

    @Slot(str)
    def open_url_external(self, url):
        if not url: return
        try: QDesktopServices.openUrl(QUrl(url))
        except Exception: pass

    @Slot(result=str)
    def get_theme(self):
        cfg = _load_json(THEME_FILE, None) or dict(DEFAULT_THEME)
        for k, v in DEFAULT_THEME.items(): cfg.setdefault(k, v)
        try:
            self.bot.overlay_server.bg_image_path = cfg.get("image_path") or ""
        except Exception:
            pass
        return str(json.dumps(cfg))

    @Slot(str, result=bool)
    def save_theme(self, json_str):
        try:
            data = json.loads(json_str)
            merged = dict(DEFAULT_THEME); merged.update(data or {})
            if not _save_json(THEME_FILE, merged):
                self.log_message.emit("Theme save failed: could not write "
                                      "theme file", "err")
                return False
            try:
                self.bot.overlay_server.bg_image_path = merged.get("image_path") or ""
            except Exception:
                pass
            try: self.bot._install_theme_background()
            except Exception: pass
            try:
                name = (merged.get("name") or "nexo").lower()
                palette = THEME_PALETTE.get(
                    name, THEME_PALETTE[DEFAULT_THEME["name"]])
                self.bot.overlay_server.push_theme({
                    "name": name,
                    "palette": dict(palette),
                })
            except Exception:
                pass
            return True
        except Exception as e:
            self.log_message.emit(f"Theme save failed: {e}", "err"); return False

    @Slot(result=str)
    def browse_image(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Select background image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg);;All files (*.*)")
        return str(path or "")

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
            self.bot._save_totals(force=True)
        except Exception:
            pass
        try:
            if self.bot.is_alive():
                # driver.quit() runs on the bot thread; give it time so
                # Chrome / chromedriver are not left orphaned.
                self.bot.join(timeout=10)
        except Exception: pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE + " — Modern Engine")
        self.resize(1280, 900)
        self.setMinimumSize(900, 640)
        self.setStyleSheet("QMainWindow { background: #0d0b1a; }")

        self.bridge = Bridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)

        self.view = QWebEngineView()
        self.view.page().setWebChannel(self.channel)
        self.view.page().setBackgroundColor(QColor("#0d0b1a"))
        s = self.view.settings()
        s.setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        s.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)

        port = self.bridge.bot.overlay_server.start()
        self.bridge.bot.overlay_server.ui_html = UI_HTML
        if port:
            self.view.setUrl(QUrl(f"http://127.0.0.1:{port}/ui"))
        else:
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
        self.tray.setToolTip(APP_TITLE)
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


def main():
    _plog(f"{APP_TITLE} starting …")
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
    app.setPalette(pal)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()