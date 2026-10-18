# OpenCUY — Requirements

> **⚠️ IMPORTANT — Put the script in its own folder first.**
>
> On first boot, OpenCUY creates several files and directories **next to
> the script**: `opencuy_config.json`, `opencuy_theme.json`,
> `opencuy_deps.json`, `screenshots/`, and (in newer builds)
> `opencuy_stats.json` and `screenshots_archive/`.
>
> If you drop the script into your Downloads folder, Desktop, or home
> directory, it will litter that location with these files. Create a
> dedicated folder (for example `OpenCUY/`) and put the script inside it
> **before** running it for the first time.

---

## Disclaimer

**OpenCUY is provided "as is", without warranty of any kind.**

**I am not responsible for any consequences of using this script.**
That includes, but not limited to:

- **YouTube account bans, suspensions, or terminations**
- **Community Guidelines strikes**
- **Copyright strikes or Content ID claims**
- **Loss of monetization, channel features, or upload privileges**
- **Loss of Google account access**
- **IP blocks, CAPTCHA walls, or rate limiting**
- **Any other action taken by YouTube, Google, or any third party**

OpenCUY uses Selenium to drive an automated Chrome browser and reads
YouTube pages and live chat. This kind of automation **may violate
YouTube's Terms of Service** and **may be detected by YouTube's
anti-bot systems**. Automated browsing patterns, live-chat polling, and
injected scripts can all be flagged.

### Moderation can break, and it can break in dangerous ways

The blocking and filtering system in OpenCUY (live-stream blocking,
under-24h blocking, 360°/VR blocking, cuss-word blocking, ad blocking,
Rocholo detection, and the various toggles in the Config tab) **is not
guaranteed to work**. It can fail in two ways:

**Bad way — moderation over-blocks.**

The script may block content it shouldn't. Videos may refuse to play,
navigation may be rejected, or the bot may sit on a blank page when it
should be playing something. This is annoying but safe — nothing gets
through that shouldn't.

**Super bad way — moderation under-blocks.**

The script may fail to block content it should. Videos that are live,
under 24 hours old, 360°/VR, ad-supported, or otherwise flagged may
**play anyway**. The filters can silently stop working entirely, and
the bot will act as if nothing is wrong.

**This has already caused one of my own channels to be terminated while
livestreaming.** When moderation fails in the "super bad" direction
during a livestream, the bot can play content that violates YouTube's
rules or your own viewing policy, and YouTube can and will act on it.
Live content is watched more closely than regular uploads, so the risk
is higher and the consequences can come faster.

**I am not responsible if moderation fails and you get banned, struck,
or terminated as a result.** You are responsible for watching what the
bot is doing and for making sure it is behaving the way you expect. If
you cannot monitor it, do not run it.

### Why the Rocholo Javinar filter exists

OpenCUY includes a detector that looks for videos mentioning **Rocholo
Javinar** (any spelling variation) and flags them with a violet info
banner. It does not block playback — it just warns you that one showed
up.

The reason this filter exists: **Rocholo Javinar is a fan of my main
account.** He makes videos about how many videos are on my livestream
of his account. The filter is there so I (and anyone running OpenCUY)
can see when one of his videos shows up in the feed or search results,
without having to scan for it manually.

The filter is purely informational. It is controlled by the
`detect_rocholo` toggle in the Config tab, and it can be turned off if
you do not want it.

### How to be safe when using this script

There is no way to make automation on YouTube completely safe, but you
can dramatically reduce the damage if something goes wrong.

**Never sign into a Google account in the Selenium window.**

The Selenium-controlled Chrome window is fully automated and is
controlled by this script. **Do not sign into any Google account in
that window — not your main account, not a secondary account, not a
throwaway account.** Anything that happens in that browser session is
tied to whatever account is signed in, and YouTube can and will act on
that account if the automation trips their anti-bot systems.

Logging into a Google account inside the Selenium window is the single
biggest way to get that account banned, suspended, or terminated. If
you never sign in, YouTube has no account to punish — at worst you get
an IP-level block, which is temporary and does not touch your real
Google account.

**Stay signed out.**

- Do not sign in to browse YouTube in the Selenium window.
- Do not sign in to watch age-restricted content.
- Do not sign in to use a paid subscription or Premium features.
- Do not sign in to post, comment, subscribe, or like anything.
- Do not sign in to access YouTube Studio, your channel, or your
  uploads.

If you need to use YouTube signed in, do it in your **normal browser**
on your **normal Google account** — not in the Selenium window.

**What happens if you do sign in:**

If YouTube flags the automated session, the account that was signed in
at the time can be:

- Suspended or permanently terminated
- Stripped of monetization or channel features
- Issued Community Guidelines strikes
- Locked out of Google services entirely (Gmail, Drive, Photos, etc.)

**There is no safe account to sign in with.** Not your main account,
not a secondary one, not a brand-new one. The only safe move is to
never sign in at all.

**Be extra careful when livestreaming.**

Livestreaming with this script carries more risk than running it
off-stream. Live content is moderated more aggressively, viewers can
report what they see in real time, and a moderation failure is visible
to everyone watching instead of just you. **The channel termination
mentioned above happened while livestreaming.** If you are going to
livestream with this, assume the risk is higher and watch the bot
constantly.

**Monitor the bot while it runs.**

Moderation can fail silently in the "super bad" direction. Do not leave
the bot unattended. Watch what it is doing and stop it if you see it
playing content that should have been blocked.

**You use this script entirely at your own risk.** If you are not
comfortable with the possibility of losing your YouTube or Google
account, **do not run it**.

---

## Available Releases

OpenCUY ships as two separate single-file scripts. Pick one — you do not
need both.

### v2.0 — stable release (recommended for most users)

- **File:** `OpenCUY.v2.0.py`
- **Status:** Older but **more stable**. Fewer moving parts, well-tested,
  and the version most people should start with.

### v3.6 — newer release (more features, less stable)

- **File:** `OpenCUY.v3.6.py`
- **Status:** Newer build with more features, but **less stable** than
  v2.0. Expect rougher edges and more frequent changes.

> **Which one should I run?** If you want something that just works, use
> **v2.0** (`OpenCUY.v2.0.py`). If you want the latest features and don't
> mind occasional bugs, use **v3.6** (`OpenCUY.v3.6.py`). You can keep
> both files in the same folder and run whichever you prefer — they share
> the same config, theme, and screenshot files.

---

## Changes Between Releases

This section summarizes what changed between the stable v2.0 build
(`OpenCUY.v2.0.py`) and the newer v3.6 build (`OpenCUY.v3.6.py`). It is
not a full changelog — just the differences that matter for setup and
day-to-day use.

### UI and architecture

**v2.0 — embedded UI**

The HTML/CSS/JS interface is baked into the Python file and rendered
directly in the Qt window with `setHtml()`. There is no network server.
The UI can't be opened in an external browser, and the log/chat panels
are plain scrolling `<div>`s inside the Qt window.

**v3.6 — overlay server UI**

The HTML/CSS/JS interface is served over a local HTTP server on
`127.0.0.1:7000`. The Qt window loads it as a URL. Extra pages are
available at `/log-view`, `/chat-view`, and `/home-dash`, and the log
and chat panels can be popped out into separate windows or opened in a
real browser. The same server streams live stats and theme events back
into the overlay.

### Video metadata backend

**v2.0 — DOM-based detection**

Age, live status, and ad badges are read from the rendered watch page
using Selenium JavaScript probes. There is no yt-dlp dependency.
Detection depends on YouTube's DOM staying stable.

**v3.6 — yt-dlp backend**

All video metadata (age, live status, 360°/VR projection) is fetched
from yt-dlp **before** the visible browser is allowed to navigate to a
watch URL. yt-dlp is a required dependency. If yt-dlp fails, the video
is blocked (fail-closed).

### Navigation safety

**v2.0 — headless pre-flight checker**

Every user-driven navigation is routed through a headless Chrome
instance that loads the target URL, inspects it, and returns
`(safe, reason)`. The visible window is only navigated if the check
passes. URL-shape guards also reject obvious suffix-injection attempts
like `@handle/live`.

**v3.6 — yt-dlp check + page-level video gate**

The headless Chrome checker is gone. Every watch URL is checked with
yt-dlp before the visible browser navigates to it, and v3.6 adds a
second layer: a CDP script injects a media gate into every new page load
that keeps `<video>` elements paused until yt-dlp approves the video.
This means even if a watch page slips past the URL check, playback
cannot start on its own.

### New blocking and filtering features

**v2.0 has:**

- Block live streams
- Block videos under 24 hours old
- Block "Free with ads" badges
- Off-site watchdog
- Home-on-video-end watcher
- Mouse bot (anti-idle jitter)
- `!lucky` and `!watchhome` skip blocked videos

**v3.6 adds:**

- Block 360°/VR videos (`block_360`)
- Block `/gaming` paths (`block_gaming`)
- Block `/shorts` paths (`block_shorts`)
- Block channel-page featured videos (`block_channel_featured`)
- Mute autoplay on channel pages (`mute_channel_autoplay`)
- Detect "Rocholo Javinar" videos by spelling-variant pattern
  (`detect_rocholo`) — **see the note below for why this filter
  exists**
- Filter live and under-24h cards out of the home feed DOM
  (`filter_home_feed`)
- Blank the home feed entirely and replace it with a dashboard
  (`blank_home_feed`)
- Cuss-word title/channel blocking (`probe_clicks`)
- Mod-lock videos started by moderators (`mod_lock_videos`)

> **About the Rocholo Javinar filter:** Rocholo Javinar is a fan of my
> main account. He makes videos about how many videos are on my
> livestream of his account. The filter is purely informational — it
> flags videos that mention him (any spelling) with a violet banner so
> I can see when one shows up in the feed or search results. It does
> not block playback, and it can be turned off with the
> `detect_rocholo` toggle in the Config tab.

### Chat and moderation

**v2.0 — basic chat**

Chat commands are read from live streams and routed through the same
dispatcher as the local UI. Owner/mod flags are captured but only used
for logging. No command is restricted by role.

**v3.6 — privilege-aware chat**

Owner and moderator messages are marked as privileged. A handful of
commands (`!video`, `!clearshots`, `!archiveshots`, `!unlock`, `!cheatmode`) are
**owner/mod only** and rejected from regular chat users. A mod-lock
system lets moderators start a video that chat can't change until it
ends, with a `!unlock` escape hatch for mods.

### Overlay features

**v2.0**

No overlay. The Qt window is the only UI.

**v3.6**

Floating chat panel, log panel, warning banner, screenshot counter, and
now-playing title badge are injected into the YouTube page itself. Each
element's position, size, and mode (native / pill / off) is
configurable from the Config tab. In v3.6 the chat and log panels are
drawn directly in the page instead of in iframes, and a red BLOCKED
banner appears whenever a video or page is blocked.

### Screenshots

**v2.0**

A screenshot is taken 10 seconds after each command, saved to
`screenshots/` as a PNG. Files accumulate until manually deleted.

**v3.6**

Screenshot delay is configurable. An automatic cleaner runs every 30
minutes and either archives screenshots older than N hours into
`screenshots_archive/` as ZIPs or deletes them, depending on
`archive_screenshots`. `!shotcount`, `!archiveshots`, and
`!clearshots` commands manage them from chat. A screenshot is skipped
if the command that triggered it ended in a blocked video.

### Theme system

**v2.0**

Nine color themes, background image with fit mode / opacity / blur /
dim, saved to `opencuy_theme.json`.

**v3.6**

Same theme system, plus the theme palette is pushed to the overlay
server so the injected YouTube-page elements (chat panel, log panel,
badges) match the chosen theme. The YouTube page background can also be
recolored to match (`theme_youtube_bg`).

### Playback and scroll

**v2.0**

`!speed`, `!pitch`, `!volume`, `!playpause`, `!refresh`, `!captions`,
`!captionstranslate`, `!loop`, `!autoplay`, `!fullscreen`, `!seek`,
`!scroll`.

**v3.6 adds:**

- `!infscroll DIST [SPEED] [DURATION]` — continuous scroll with signed
  distance, px/s speed, and optional time cap
- `!stopscroll` / `!infclear` / `!infstop` — stop infinite scroll
- `!loadmore [n]` — scroll to load n more screenfuls
- `!skip` / `!adskip` / `!skipad` — click YouTube's skip-ad button
- `!forward [sec]` / `!ff [sec]` — fast-forward N seconds
- `!pick N` / `!pick list` — click the Nth playable card on the page
- `!forcewatchhome [n]` — like `!watchhome`, but also works on
  playlist/Mix cards (the playlist parameters are removed)
- `!watchsearch [n]` / `!watchupnext [n]` — open the Nth video on a
  search results page or in the Up Next sidebar
- `!cheatmode` — owner/mod only; runs the cheat-mode script on the
  current page

### Config and stats

**v2.0**

Config is a flat set of keys in `opencuy_config.json`. There is no stats
tracking and no lifetime counters.

**v3.6**

Config is split into nested sections (`behavior`, `timing`, `spam`,
`overlay`, `transitions`, `chat`). A live stats dashboard shows runtime,
videos loaded, searches, ads blocked, live blocked, under-24h blocked,
cuss blocked, mod-lock blocks, commands, chat messages, screenshots, and
more. Lifetime totals persist in `opencuy_stats.json`.

### Reliability fixes in v3.6

The v3.6 build also fixes a number of bugs that exist in v2.0:

- Screenshots were silently never taken in early v3.4 builds
- Privileged requests were being mis-queued, so mod-lock never engaged
- `!infscroll` with a negative distance stopped instantly
- Filter tables had invalid JSON and pattern typos
- `block_gaming` / `block_shorts` toggles had no effect
- A second unused headless Chrome instance was launched per URL check
- UI thread could freeze when querying chromedriver
- SSE stream races on keepalive vs push
- Overlay script was injected into its own iframes recursively
- `!changelanguage` stacked `hl=` params instead of replacing them
- Various playback commands threw JS errors when no `<video>` existed

v3.6 also fixes:

- Warning and blocked-video banners stopped appearing after the overlay
  was re-injected (the overlay's event streams were never reopened)
- Old banners were replayed on later pages and when saving the config
- Gate rejections and yt-dlp failures blocked a video without showing a
  banner
- A video the gate rejected could stay locked if you opened it again
- Screenshots were still taken for commands that ended in a block

### Known issues in v3.6

> **🛠 These are open bugs. A fix will be released when I have time to
> track them down — I do not currently know why they happen.**

**Theme injection into YouTube can break the page.**

When `theme_youtube_bg` is enabled, v3.6 injects a `<style>` tag into
the YouTube page to recolor the background, cards, and player chrome.
On some YouTube layouts, this breaks the page — elements may disappear,
the player may fail to render, or the page may refuse to scroll. The
exact trigger is unknown. It seems to depend on which YouTube UI variant
you get served, but it is not reproducible on demand.

**Iframe overlay windows can break.**

In v3.5 the floating chat and log panels were injected as `<iframe>`
elements into the YouTube page. On some pages, these iframes failed to
render, rendered as blank white boxes, overlapped the player controls in
a way that couldn't be dismissed, or caused the parent page to freeze.
The exact trigger is unknown. v3.6 draws the chat and log panels
directly in the page instead of in iframes, which should avoid the
iframe-specific failures, but the home dashboard still uses one iframe
and overlay problems can still happen.

**Moderation can over-block or under-block.**

The blocking and filtering system is not guaranteed to work reliably.
It can fail in the "bad" direction (videos that should play won't) or in
the **"super bad" direction (videos that should be blocked play
anyway)**. The super-bad failure mode has already caused one of my own
channels to be terminated while livestreaming. See the Disclaimer at
the top of this document for details.

**Workaround for the theme and iframe issues:**

Turn off the affected feature in the Config tab:

- `theme_youtube_bg` — disable to stop the theme `<style>` injection
- `show_chat_iframe` — disable to remove the chat overlay
- `show_log_iframe` — disable to remove the log overlay

Setting `chat_iframe_mode` or `log_iframe_mode` to `off` also removes
the panel while keeping the config entry.

**Workaround for moderation failures:**

There is no reliable workaround. Monitor the bot while it is running and
stop it if you see it playing content that should have been blocked.
Do not leave it unattended, especially while livestreaming.

The v2.0 build does not have the theme or iframe issues, because it does
not inject anything into the YouTube page. Its moderation can still
fail, but it has fewer moving parts and is generally more predictable.

### Which to choose

**Pick v2.0 if you want:**

- The most stable, well-tested build
- No yt-dlp dependency
- A simpler codebase with fewer moving parts
- No local HTTP server / no open ports
- No risk of theme or iframe injection breaking YouTube

**Pick v3.6 if you want:**

- The overlay UI injected into YouTube itself
- yt-dlp-based metadata checks before navigation
- 360°/VR, gaming, shorts, and cuss-word blocking
- Mod-lock and privilege-aware chat commands
- Infinite scroll, skip-ad, pick, and loadmore commands
- `!forcewatchhome`, `!watchsearch`, `!watchupnext`, and `!cheatmode`
  commands
- A live stats dashboard

Both releases read and write the same `opencuy_config.json`,
`opencuy_theme.json`, and `screenshots/` folder, so you can switch
between them without losing settings. Just be aware that v3.6 writes
extra keys into the config that v2.0 will ignore, and v2.0 does not
create `opencuy_stats.json` or `screenshots_archive/`.

---

This document lists the Python version, third-party packages, and external
runtime requirements needed to run either release.

---

## 1. Python Version

- **Minimum:** Python 3.9
- **Recommended:** Python 3.11 or 3.12
- **Tested on:** Python 3.10 – 3.12

Both scripts use only standard-library modules for their core logic
(`pathlib`, `fractions`, `urllib.parse`, `socketserver`, `http.server`,
`zipfile`, `importlib`, `threading`, `queue`, `subprocess`).

Python **3.8 is not recommended** because PySide6 6.5 and newer dropped
3.8 wheels.

---

## 2. Required Python Packages

These are hard dependencies. Both scripts will prompt to install any
that are missing and will exit if a required one cannot be installed.

### PySide6

- **Import name:** PySide6
- **Version:** 6.5 or newer recommended
- **Purpose:** Qt bindings for the native window, system tray icon, and
  the embedded Chromium UI. `QtWebEngineWidgets` and `QtWebChannel` are
  bundled with the main wheel.
- **Notes:** The v2.0 script checks for both `PySide6` and
  `PySide6.QtWebEngineWidgets` as separate dependencies, but they come
  from the same pip package. No extra install needed.

**Install:**

```
pip install PySide6
```

### selenium

- **Import name:** selenium
- **Version:** 4.15 or newer recommended
- **Purpose:** Drives the visible Google Chrome instance. v2.0 also uses
  it for headless pre-flight safety checks; v3.6 checks videos with
  yt-dlp instead.
- **Notes:** Uses only stable WebDriver APIs.

**Install:**

```
pip install selenium
```

### yt-dlp

- **Import name:** yt_dlp
- **Version:** 2024.x or newer
- **Purpose:** Headless video metadata backend — detects age, live
  status, and 360°/VR projection.
- **Notes:** **Required for v3.6** (`OpenCUY.v3.6.py`), where it is the
  only metadata backend. **Not used by v2.0** (`OpenCUY.v2.0.py`), which
  does DOM-based metadata detection instead. Installing it is still
  recommended so both scripts work.

**Install:**

```
pip install yt-dlp
```

**Install all three at once:**

```
pip install PySide6 selenium yt-dlp
```

---

## 3. Optional Python Packages

### pytchat

- **Import name:** pytchat
- **Purpose:** Reads YouTube live chat so viewers can drive the bot with
  `!commands`.
- **If missing:** Non-fatal. The Live Chat tab reports an error and
  disables itself. Everything else works normally.

**Install:**

```
pip install pytchat
```

Both scripts record "don't ask again" preferences in
`opencuy_deps.json` after the first prompt.

---

## 4. External (Non-Python) Requirements

**Google Chrome**
Required. Selenium attaches to a real Chrome install — no bundled
browser. Selenium Manager fetches a matching chromedriver
automatically.

**Outbound HTTPS to YouTube**
Required. Needed for browsing, chat, and (in v3.6) yt-dlp metadata
(youtube.com, youtu.be, googlevideo.com, ytimg.com, etc.).

**tkinter** (usually bundled with Python)
Optional. Only used for the native "install missing dependencies?"
dialogs. If unavailable, the scripts fall back to a console input()
prompt.

**Supported operating systems:**
Windows 10/11, macOS 12+, and Linux with a desktop session
(X11 or Wayland). Headless-only servers will **not** work — the bot
opens a visible Chrome window by design.

> **Note:** The v3.6 release also runs a local overlay HTTP server on
> `127.0.0.1:7000`. The v2.0 release does **not** — it embeds the UI
> directly in the Qt window via `setHtml()`, so no port is needed.

---

## 5. Minimal `requirements.txt`

Save this as `requirements.txt` inside the same folder as the scripts:

```
PySide6>=6.5
selenium>=4.15
yt-dlp>=2024.1.1

# Optional — only needed for live-chat command support
pytchat>=0.5.4
```

**Install everything from the file:**

```
pip install -r requirements.txt
```

---

## 6. Installing on Linux

Many modern Linux distributions (Debian 12+, Ubuntu 23.04+, Fedora 38+,
Arch, etc.) ship Python with **PEP 668** protections enabled. A plain
pip install into the system Python is refused with an error like:

```text
error: externally-managed-environment
```

You have three supported options.

### Option A — Use a virtual environment (recommended)

This is the safe, portable way and works on every distro.

**Create and activate a venv inside the OpenCUY folder:**

```
python3 -m venv .venv && source .venv/bin/activate
```

**Then install everything:**

```
python -m pip install --upgrade pip && python -m pip install -r requirements.txt
```

Remember to run `source .venv/bin/activate` again in every new shell
where you launch a script.

### Option B — Install globally with `--break-system-packages`

Only do this if you understand the risk of modifying your system
Python. It can conflict with distro-managed packages.

**Per-user install (preferred over sudo):**

```
python3 -m pip install --user --break-system-packages -r requirements.txt
```

**System-wide install (use with care):**

```
sudo python3 -m pip install --break-system-packages -r requirements.txt
```

The `--user` form is strongly preferred over sudo — it installs into
`~/.local` and avoids fighting with your package manager.

### Option C — Use your distro packages instead

Some distros provide the needed modules directly:

**Debian / Ubuntu:**

```
sudo apt install python3-pyside6 python3-selenium python3-yt-dlp
```

**Arch:**

```
sudo pacman -S pyside6 python-selenium yt-dlp
```

**Fedora:**

```
sudo dnf install python3-pyside6 python3-selenium yt-dlp
```

Package names and available versions vary; check pip show after
install if something is out of date.

If you launch a script and it tries to auto-install dependencies on
its own, on a PEP 668 system you may still need to allow that:

```
export PIP_BREAK_SYSTEM_PACKAGES=1
```

---

## 7. Runtime Files Created

Both scripts write these files **next to themselves** on first run. They
are not dependencies, but the app expects to be able to create them —
make sure the script's folder is **writable**.

This is why you should keep the scripts in their own dedicated folder
rather than in Downloads, Desktop, or your home directory.

- `opencuy_config.json` — main configuration (behavior, fallbacks,
  blocking toggles, screenshot settings).
- `opencuy_theme.json` — selected color theme and background image path.
- `opencuy_deps.json` — remembers "don't ask again" for optional deps.
- `screenshots/` — PNG screenshots captured after commands.

The v3.6 release additionally creates:

- `opencuy_stats.json` — persistent lifetime counters.
- `screenshots_archive/` — ZIP archives created by the screenshot
  cleaner.

Because both releases read and write the same config, theme, and
screenshot files, you can switch between `OpenCUY.v2.0.py` and
`OpenCUY.v3.6.py` freely — your settings carry over.

---

## 8. Ports

- **v2.0 (`OpenCUY.v2.0.py`)** — no network ports. The UI is embedded
  directly in the Qt window.
- **v3.6 (`OpenCUY.v3.6.py`)** — binds `127.0.0.1:7000/tcp` for its
  overlay HTTP + SSE server (serves /ui, /log-view, /chat-view,
  /home-dash, /stats-events, /theme-events, and related endpoints). If
  7000 is busy, the OS assigns an ephemeral port and the UI uses that
  instead.
- Only one v3.6 instance can bind port 7000 at a time. Do not run two
  v3.6 copies simultaneously.

---

## 9. Known Environment Caveats

- **Put the scripts in their own folder first.** They generate config,
  theme, and screenshot files in the directory they live in.
- **Prefer v2.0 for stability.** The v2.0 release (`OpenCUY.v2.0.py`)
  is the more stable, well-tested build. The v3.6 release
  (`OpenCUY.v3.6.py`) has more features but is rougher.
- **Never sign into a Google account in the Selenium window.** The
  Selenium-controlled Chrome window is fully automated. Anything that
  happens in that session is tied to whatever account is signed in, and
  YouTube can act on it. Do not sign in — not your main account, not a
  secondary account, not a throwaway account. If you need to use
  YouTube signed in, use your normal browser on your normal account.
  See "How to be safe when using this script" in the Disclaimer at the
  top of this document.
- **Livestreaming with this script is riskier than running it
  off-stream.** Live content is moderated more aggressively, viewers
  can report what they see in real time, and a moderation failure is
  visible to everyone watching. **The channel termination mentioned in
  the Disclaimer happened while livestreaming.** Watch the bot
  constantly if you livestream with it.
- **The Rocholo Javinar filter is informational only.** Rocholo Javinar
  is a fan of my main account and makes videos about how many videos
  are on my livestream of his account. The filter flags videos that
  mention him with a violet banner — it does not block playback. It can
  be turned off with the `detect_rocholo` toggle in the Config tab. See
  "Why the Rocholo Javinar filter exists" in the Disclaimer at the top
  of this document.
- **v3.6 theme injection into YouTube can break the page.** When
  `theme_youtube_bg` is enabled, the injected `<style>` tag may cause
  elements to disappear, the player to fail to render, or the page to
  stop scrolling. **I do not know why this happens.** A fix will be
  released when I find out what is causing it. In the meantime, turn
  off `theme_youtube_bg` in the Config tab.
- **v3.6 overlay windows can break.** In v3.5 the chat and log panels
  were iframes that could fail to render, show up blank, overlap the
  player controls in a way that can't be dismissed, or freeze the parent
  page. v3.6 draws them directly in the page, but the home dashboard
  still uses an iframe. **I do not know why the original problem
  happens.** A fix will be released when I find out
  what is causing it. In the meantime, set `chat_iframe_mode` and/or
  `log_iframe_mode` to `off` in the Config tab, or turn off
  `show_chat_iframe` / `show_log_iframe` entirely.
- **Moderation can fail in two directions, and one of them is
  dangerous.** The blocking system may over-block (videos that should
  play won't — annoying but safe) or under-block (videos that should be
  blocked play anyway — dangerous). **The under-blocking failure mode
  has already caused one of my own channels to be terminated while
  livestreaming.** Monitor the bot while it runs and stop it if you see
  it playing content that should have been blocked.
- **You may get banned or struck by YouTube.** Using automation on
  YouTube may violate their Terms of Service and can result in account
  suspension, termination, or Community Guidelines strikes. **I am not
  responsible for any bans, strikes, or other account actions taken
  against you.** See the Disclaimer at the top of this document.
- **First launch is interactive.** The dependency checker may open a
  tkinter dialog asking to install missing packages. Run once with a
  terminal attached.
- **Linux PEP 668 systems** (Debian 12+, Ubuntu 23.04+, Fedora 38+, and
  similar) block global pip install by default. Use a virtual
  environment, or add --break-system-packages if installing globally.
  See section 6 above.
- **Chrome must be installed separately.** Selenium Manager downloads
  chromedriver but **not** Chrome itself.
- **Antivirus / SmartScreen** may flag the unsigned Python scripts or a
  freshly downloaded chromedriver.exe on Windows.
- **Corporate proxies** can break Selenium Manager's chromedriver
  download and (in v3.6) yt-dlp metadata extraction.
- **Wayland users** may need QT_QPA_PLATFORM=xcb for reliable
  QtWebEngine rendering:

  ```
  export QT_QPA_PLATFORM=xcb
  ```

- **macOS Gatekeeper** may require allowing the Python interpreter (or
  the packaged app) to open network connections.

---

## 10. Verification Checklist

Before anything else, confirm you are inside the OpenCUY folder:

```
cd path/to/OpenCUY
```

**Check core imports:**

```
python -c "import PySide6, selenium, yt_dlp; print('core OK')"
```

**Check the optional chat module:**

```
python -c "import pytchat; print('chat OK')"
```

If you installed into a venv on Linux, activate it first:

```
source .venv/bin/activate
```

**Run the stable release (recommended):**

```
python OpenCUY.v2.0.py
```

**Or run the newer release:**

```
python OpenCUY.v3.6.py
```

Expected startup sequence in the log (v2.0 shown; v3.6 is similar):

```text
[hh:mm:ss] [INFO] OpenCUY v2.0 — Modern Engine starting …
[hh:mm:ss] [INFO] [checker] Headless checker ready.
[hh:mm:ss] [ OK ] [bot] Browser ready.
```

v3.6 startup:

```text
[hh:mm:ss] [INFO] OpenCUY v3.6 starting …
[hh:mm:ss] [ OK ] [checker] yt-dlp headless metadata checker ready.
[hh:mm:ss] [ OK ] [bot] Browser ready — visible video playback is locked behind headless yt-dlp.
```

If you see the checker-ready message and a visible Chrome window opens,
all runtime requirements are satisfied.
