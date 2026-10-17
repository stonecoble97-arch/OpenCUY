# OpenCUY — YouTube Command List

<p align="center">
  <img src="OpenCUYlogo.png" alt="OpenCUY logo" width="220">
</p>

This is the full list of chat commands OpenCUY understands. Commands can
be typed in the **Live Chat tab** of the Qt window, sent from the
**YouTube live chat** (if `pytchat` is installed and the chat reader is
connected), or typed into the **Query box** on the Bot tab.

The default prefix is `!`. You can change it in the Live Chat tab.

---

## How commands work

- Commands start with the prefix (`!` by default).
- Arguments are separated by spaces.
- **Chain multiple commands** in one message by writing them one after
  another, each starting with the prefix. Example:
  `!speed 1.5 !volume 40 !playpause`
- **Repeat a command** by adding a number at the end. Example:
  `!playpause 5` runs it 5 times. The repeat cap is 500 from the local
  UI and 20 from live chat (configurable).
- **Language suffix**: most search and channel commands accept an
  optional language code as the last word, or as `lang:xx`. Example:
  `!search cats ja` or `!search cats lang:ja`.
- **Forbidden characters**: a single command token may not contain
  `/`, `?`, `#`, or `\`. This blocks URL-suffix injection attempts.
- **Unknown commands**: if `fallback_unknown_as_search` is ON, an
  unknown command is treated as a search. If `fallback_plaintext_as_search`
  is ON, plain text with no prefix is also treated as a search.

---

## Search and navigation

### `!search [query] [lang]`
Search YouTube and list the top results in the log.

```
!search lofi hip hop
!search cats ja
!search news lang:de
```

### `!lucky [query] [lang]`
Search YouTube and play the **first safe result**. Safe means: not a
live stream, not under 24 hours old, not 360°/VR, no ad badge, and no
cuss word in the title or channel. Blocked results are skipped and the
next candidate is tried.

```
!lucky minecraft
!lucky music es
```

### `!searchwatch [query] [lang]`
Alias for `!lucky`. Same behavior.

```
!searchwatch rain sounds
```

### `!video [id] [lang]` — *owner/mod only (v3.5)*
Load a specific video by its 11-character video ID. In v3.5, this is
restricted to the channel owner or a moderator.

```
!video dQw4w9WgXcQ
!video dQw4w9WgXcQ ja
```

### `!channel [handle|UC-id] [lang]`
Load a YouTube channel page. Accepts `@handle`, a raw `UC…` channel ID,
or (in v3.5) a full channel URL. Live streams and redirects to `/watch`
are backed out of automatically.

```
!channel @MrBeast
!channel UCX6OQ3DkcsbYNE6H8uQQuVA
!channel https://www.youtube.com/@MrBeast
```

### `!home [lang]`
Go to `https://www.youtube.com/`.

```
!home
!home ja
```

### `!changelanguage [code]`
Set the `hl=` language parameter for future navigations on the current
page. The language is not persisted across restarts.

```
!changelanguage ja
!changelanguage de
```

### `!english`
Shortcut for `!changelanguage en`.

```
!english
```

---

## Page and scroll control

### `!scroll [pixels] [duration]`
Scroll the page by a number of pixels. A negative number scrolls up.
An optional duration in seconds turns it into a smooth linear animation
(max 120 seconds).

```
!scroll 500
!scroll -300
!scroll 500 3
!scroll +1200 12.5
```

### `!watchhome [n]`
Click the **nth non-ad video card** on the YouTube home feed. Ads and
sponsored cards are skipped automatically. Blocked videos (live,
under-24h, 360°, ad-badged, cuss-word) are rejected before clicking.

```
!watchhome 1
!watchhome 5
```

### `!loadmore [n]` — *v3.5 only*
Scroll the current page down `n` times to trigger YouTube's infinite
scroll and load more results. `n` is clamped to 1–50 (default 3).

```
!loadmore
!loadmore 10
```

### `!infscroll DIST [SPEED] [DURATION]` — *v3.5 only*
Start or update **continuous infinite scroll**.

- `DIST` — pixels to travel. Required. Signed: positive scrolls down,
  negative scrolls up.
- `SPEED` — pixels per second. Optional, default 400. Must be between
  0.1 and 10000.
- `DURATION` — maximum seconds before auto-stop. Optional.

```
!infscroll 5000
!infscroll 5000 250
!infscroll 5000 250 10
!infscroll -3000 600 5
```

### `!stopscroll` / `!infclear` / `!infstop` / `!stopinf` — *v3.5 only*
Stop infinite scroll. All four names do the same thing.

```
!stopscroll
```

### `!cc` / `!clearcookies`
Delete all cookies, clear `localStorage` and `sessionStorage`, and
refresh the page. Useful when YouTube gets into a bad state.

```
!cc
!clearcookies
```

---

## Ad handling — *v3.5 only*

### `!skip` / `!adskip` / `!skipad`
Click YouTube's skip-ad button if one is showing. If the skip button
isn't there but an ad is playing, it fast-forwards the ad or mutes it
as a fallback.

```
!skip
!adskip
!skipad
```

### `!forward [sec]` / `!ff [sec]`
Fast-forward the current video by `sec` seconds. Clamped to 1–600
(default 30).

```
!forward
!forward 60
!ff 120
```

---

## Picking a video — *v3.5 only*

### `!pick N`
Click the **Nth playable card** on the current page. Ads, promoted
cards, and already-seen cards are skipped. The chosen URL is run through
the headless checker first, so blocked videos are rejected.

```
!pick 1
!pick 4
```

### `!pick list` / `!pick 0` / `!pick ls`
List the pickable cards in the log without clicking any of them.

```
!pick list
```

---

## Playback control

### `!speed [0.25–2.0]`
Set the video playback rate.

```
!speed 1.5
!speed 0.75
```

### `!pitch [-24 to +24]`
Shift the pitch by semitones. This also changes playback speed, because
browsers don't have a separate pitch control. The script disables
`preservesPitch` so the pitch actually shifts.

```
!pitch +3
!pitch -5
```

### `!volume [0–100]`
Set the video volume.

```
!volume 50
!volume 0
!volume 100
```

### `!playpause`
Toggle play/pause.

```
!playpause
```

### `!refresh`
Reload the current page.

```
!refresh
```

### `!captions on|off`
Turn captions on or off.

```
!captions on
!captions off
```

### `!captionstranslate [lang]`
Load captions and translate them to a language code.

```
!captionstranslate en
!captionstranslate ja
```

### `!loop video|playlist|off`
Set loop mode.

```
!loop video
!loop playlist
!loop off
```

### `!autoplay on|off`
Toggle YouTube's autoplay.

```
!autoplay on
!autoplay off
```

### `!fullscreen`
Click YouTube's fullscreen button.

```
!fullscreen
```

### `!seek + N` / `!seek - N` / `!seek set N`
Seek relative to the current time, or jump to an absolute time.

```
!seek +10
!seek -5
!seek set 90
```

---

## Screenshots

### `!shotcount`
Show how many screenshots are on disk, how many archive ZIPs exist, the
current retention window, and whether the cleaner is in archive or
delete mode.

```
!shotcount
```

### `!archiveshots` — *owner/mod only (v3.5)*
Zip every file in `screenshots/` into a new archive in
`screenshots_archive/`.

```
!archiveshots
```

### `!clearshots` / `!clearscreenshots` / `!shotsclear` — *owner/mod only (v3.5)*
Clear every screenshot. If archiving is on, they are zipped first.

```
!clearshots
!clearscreenshots
!shotsclear
```

---

## Mod-lock — *v3.5 only*

### `!unlock` — *owner/mod only*
Release the mod-lock early so chat can change the video.

```
!unlock
```

### `!lockstatus`
Show whether a mod-lock is currently active and on which video.

```
!lockstatus
```

---

## Misc

### `!me`
Ignored. Present so users typing `!me` don't accidentally trigger a
search fallback.

```
!me
```

---

## Repeat syntax

Any command can be repeated by adding a number at the end. This only
works for commands that are safe to repeat (playback, scroll, etc.).

```
!playpause 5
!scroll 200 10
!volume 30 3
```

The repeat cap is:
- **500** from the local Qt UI (configurable as `ui_repeat_cap`)
- **20** from live chat (configurable as `chat_repeat_cap`)

Numbers above the cap are clamped down to the cap and a warning is
logged.

---

## What gets blocked

OpenCUY will refuse to load or play:

- **Live streams** (`block_live_streams`)
- **Videos under 24 hours old** (`block_under_24h`)
- **360°/VR videos** (`block_360`)
- **`/gaming` paths** (`block_gaming`, v3.5)
- **`/shorts` paths** (`block_shorts`, v3.5)
- **Playlists and mixes** (`list=` / `start_radio=` in the URL)
- **Videos with a "Free with ads" badge**
- **Videos whose title or channel contains a cuss word** (`probe_clicks`, v3.5)
- **Channel-page featured videos that would bypass the filters** (`block_channel_featured`, v3.5)
- **Off-site URLs** (the watchdog returns to YouTube home)

Every navigation is pre-flighted in a headless Chrome instance. If the
check fails, the visible browser is not navigated.

---

## Informational filters (no blocking)

### Rocholo Javinar detection
When `detect_rocholo` is on (v3.5), OpenCUY scans the home feed titles
and the current page title for "Rocholo Javinar" in any spelling
variation. If it matches, it shows a violet info banner and bumps the
`rocholo_detected` counter. It does **not** block playback.

---

## Which commands exist in which release

| Command | v2.0 | v3.5 |
|---|---|---|
| `!search` | ✅ | ✅ |
| `!lucky` / `!searchwatch` | ✅ | ✅ |
| `!video` | ✅ (anyone) | ✅ (owner/mod only) |
| `!channel` | ✅ | ✅ |
| `!home` | ✅ | ✅ |
| `!changelanguage` / `!english` | ✅ | ✅ |
| `!scroll` | ✅ | ✅ |
| `!watchhome` | ✅ | ✅ |
| `!cc` / `!clearcookies` | ✅ | ✅ |
| `!speed` / `!pitch` / `!volume` | ✅ | ✅ |
| `!playpause` / `!refresh` / `!fullscreen` | ✅ | ✅ |
| `!captions` / `!captionstranslate` | ✅ | ✅ |
| `!loop` / `!autoplay` / `!seek` | ✅ | ✅ |
| `!me` | ✅ | ✅ |
| `!loadmore` | ❌ | ✅ |
| `!infscroll` / `!stopscroll` | ❌ | ✅ |
| `!skip` / `!adskip` / `!skipad` | ❌ | ✅ |
| `!forward` / `!ff` | ❌ | ✅ |
| `!pick` | ❌ | ✅ |
| `!shotcount` / `!archiveshots` / `!clearshots` | ❌ | ✅ |
| `!unlock` / `!lockstatus` | ❌ | ✅ |

---

## Getting help

If a command doesn't work:

1. Check the **Bot / Filter Log** in the Qt window — every command
   prints a `[source]` tag showing where it came from (`you`, `you-lucky`,
   or `chat:AuthorName`).
2. Make sure the command starts with the prefix (`!` by default).
3. Check that arguments don't contain `/`, `?`, `#`, or `\`.
4. For owner/mod-only commands, confirm you are the channel owner or a
   moderator.
5. If the command is silently ignored, `fallback_unknown_as_search` may
   have turned it into a search — check the log.
