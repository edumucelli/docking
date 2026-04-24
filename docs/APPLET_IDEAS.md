# Applet Ideas

A brainstorm pool for new Docking applets. Mix of original ideas and concepts
surveyed from other docks/panels/widget systems (Cairo-Dock, Plank, KDE Latte,
Waybar, Polybar, Conky, Rainmeter, Ubersicht, GNOME Shell, macOS menu bar apps,
Stream Deck plugins).

Items already implemented are listed at the bottom for gap analysis; each new
idea below is deliberately non-duplicative.

---

## System and hardware

- **micmute** - toggle mic + privacy indicator dot
- **camshield** - red dot when any process holds /dev/video*
- **gpumon** - nvidia-smi / radeontop utilization, VRAM, temp
- **diskspace** - per-mount bar with SMART degraded warn
- **thermals** - hottest lm-sensors reading + fan RPM
- **sinkswitch** - click-to-cycle PipeWire output sinks
- **audioinput** - switch active mic/source
- **usbwatch** - safe-remove list for mounted USB devices
- **vpn** - wg/openvpn up-down toggle, country flag
- **bluetoothbattery** - upower battery % for paired BT peripherals
- **capslock** - Caps/Num lock state LED for keyboards without one
- **nightshift** - redshift/gammastep schedule + nudge
- **darkmode** - gtk/qt theme + wallpaper pair toggle
- **display** - xrandr preset switcher (laptop-only, mirror, extend)
- **gamemode** - Feral gamemode status + toggle
- **idleinhibit** - caffeine mode, blocks screensaver and blanker
- **systray** - StatusNotifier / legacy XEmbed tray host
- **penguin** - animated mascot character at dock end (Cairo-Penguin homage)
- **mousespotlight** - highlight cursor for presentations (show-mouse)
- **systemdfailed** - count of failed systemd units, click to list
- **pkgupdate** - apt/dnf/pacman pending update count
- **extensionlist** - meta applet that lists/enables/disables other applets

## Dev and ops

- **gitrepo** - branch + dirty/ahead/behind for a watched dir
- **ghnotify** - GitHub unread notifications and PR review queue
- **gitlabci** - pipeline status green/yellow/red
- **buildstatus** - generic CI status aggregator
- **pingmon** - heartbeat for N endpoints, latency tooltip
- **certwatch** - SSL cert days-until-expiry for N domains
- **publicip** - WAN IP + country, flips when VPN flips
- **speedtest** - one-click librespeed run, last result in tooltip
- **sshagent** - loaded keys count, lifetime, add/remove
- **totp** - 2FA codes with countdown ring, per-account popup
- **dockerstats** - running containers, click to stop/restart
- **logtail** - tail a file, flash on ERROR
- **wakatime** - today coding time, top language
- **taskwarrior** - task cli bridge, next task in tooltip
- **timetracker** - toggl/clockify running timer
- **githeatmap** - daily commit heatmap thumbnail
- **dailystandup** - yesterday/today/blockers quick note
- **webhook** - push-button HTTP trigger to configured URLs
- **mqtt** - pub/sub bridge for home automation
- **dragshare** - drop file onto applet, uploads to 0x0.st and copies URL

## Communication and focus

- **mailcount** - IMAP/mbsync/maildir unread, click opens client
- **rssreader** - feedparser unread + latest titles
- **mastodon** - unread DMs + mentions
- **matrix** - Element/matrix unread
- **meetingnext** - next calendar event countdown from local ics
- **dnd** - unified focus mode (notifications + dimmed colors + ambient pause)
- **mutedeck** - cross-platform meeting mute/cam/share (Zoom/Teams/Meet)
- **dictation** - push-to-talk speech-to-text
- **notifhistory** - review past notifications
- **kdeconnect** - phone battery, SMS preview, send file, ping phone

## Time and world

- **worldclocks** - N timezones, Cairo multi-face or cycling digital
- **tzhelper** - aligned meeting grid across configured TZs
- **countdown** - days/hours to a date (holiday, trip, deadline)
- **stopwatch** - with laps, sibling of timer
- **timer** - generic countdown with named presets (tea 3m, nap 20m)
- **alarm** - alarm clock, multiple presets, rings at time
- **reminders** - deadline popups with snooze
- **sunrise** - sunrise/sunset countdown, twilight band indicator
- **astro** - ISS passes, meteor showers, aurora Kp
- **apod** - NASA Astronomy Picture of the Day thumbnail
- **spacex** - next launch countdown
- **transit** - public transport next departures (KDE style)
- **tides** - NOAA tide station height, next high/low

## Productivity

- **scratchpad** - bigger multi-line markdown sibling of quicknote
- **snippets** - clickable snippet library with search
- **cmdpalette** - fuzzy command runner popup
- **currencyfx** - live FX pair (unitconverter is static)
- **emojipicker** - search + recents, insert via xdotool
- **unicodepicker** - gucharmap-lite
- **dictionary** - word lookup on hotkey from clipboard
- **translator** - libretranslate / google / deepl from clipboard
- **mount** - NAS/SSHFS/cifs quick mount toggles
- **places** - unified filesystem bookmarks + mounts + network (GNOME style)
- **folderstack** - Cairo-Dock Folders-style nested folder drill-down popup
- **desklet** - detach an applet as a standalone desktop widget
- **recall** - search recent files + commands + windows
- **wifiqr** - current WiFi password as QR for quick guest share
- **qr** - generate QR from clipboard
- **qrread** - scan QR from screen region
- **ocr** - screen region to clipboard text
- **wolframalpha** - compute popup
- **ai** - LLM chat popup (Claude/OpenAI)
- **ollama** - local LLM chat via Ollama

## Wellness

- **eyebreak** - 20-20-20 reminder with soft fade overlay
- **breathe** - 4-7-8 breathing animation on click
- **meditate** - silent sit with start/end chime
- **posture** - interval nag or webcam-based posture check
- **habits** - tiny daily habit check-in grid
- **caffeine** - mg tracker + screensaver inhibit above threshold
- **moodlog** - one-tap mood entry, weekly histogram
- **gratitude** - daily gratitude journal prompt
- **dailyreflection** - evening journaling prompt
- **deskpresence** - time at desk vs away from idle signals

## Information and ambient curiosity

- **stocks** - ticker list, sparkline popup
- **crypto** - crypto ticker list
- **news** - HN / Reddit / RSS headline cycler
- **xkcd** - latest strip with alt text
- **wordofday** - Merriam-Webster / Wiktionary
- **earthquake** - USGS recent M4.5+ nearby
- **solarflare** - NOAA Kp + flare class
- **uvindex** - UV index for sun exposure
- **pollen** - daily pollen forecast
- **ohm** - electricity spot price per hour
- **airquality** - promote AQI out of weather for quick glance
- **onthismap** - random geo coordinate to street view link

## Creative

- **screencast** - start/stop with timer badge
- **magnifier** - Cairo zoom lens toggle
- **palette** - sample pixels under cursor into named palettes
- **wallpaper** - cycle a directory, random/schedule
- **fontviewer** - preview installed fonts with sample string
- **asciicam** - webcam to ASCII popup
- **audioviz** - spectrum/waveform visualizer (Cava/Impulse)
- **huecontrol** - Philips Hue / Tasmota light control
- **rgboff** - master kill for keyboard/mouse/room RGB
- **smartlight** - unified Home Assistant toggle
- **torrent** - transmission-daemon session stats + add link
- **earthcam** - scheduled-refresh still from a public webcam

## Charm and ambient fun

- **fish** - sprite swims across dock background
- **dice** - d4/d6/d20, hold to reroll
- **magic8** - yes/no/maybe with animation
- **fortune** - BSD fortune + cowsay tooltip
- **catsdogs** - random image from thecatapi / dog.ceo
- **forest** - passive tree grows while you avoid switching apps
- **breathecircle** - ambient pulse you can sync breath to
- **fireplace** - Cairo flames loop
- **candle** - ambient flicker, companion to fireplace
- **wavebar** - gentle ocean wave animation across the dock
- **rain** - particle rain synced to weather applet
- **plasma** - demoscene effect background
- **matrixrain** - green rain when idle, vanishes on hover
- **bongocat** - reacts to keystrokes via xinput
- **tama** - low-maintenance tamagotchi sibling to `pet`
- **spinwheel** - pick one item from a list at random
- **gradienttime** - Cairo gradient that shifts with hour of day
- **hud** - single-line always-on glance (time + weather + battery)

---

## Survey sources

- Cairo-Dock plugins (stable + extras)
- Plank
- KDE Latte Dock and Plasma plasmoids
- Waybar modules
- Polybar built-ins and community scripts
- Conky scripts and Lua themes
- Rainmeter popular skins (Mond, Stellar, Monstercat)
- Ubersicht community widgets
- GNOME Shell extensions
- macOS menu bar apps (iStat Menus, Raycast, Ice, Vanilla, Bartender presets)
- Elgato Stream Deck plugins (MuteDeck, Windows Gizmos, SuperMacro)

Notable cross-dock standouts inspiring picks above:
- Cairo-Dock: showDesklets (detach applet to desktop), Folders (nested
  filesystem drill), Status-Notifier (tray host), Impulse (audio viz),
  Recent-Events, dnd2share, Cairo-Penguin
- Waybar: Gamemode, Idle Inhibitor, Privacy, Systemd Failed Units, Tray,
  WirePlumber
- Polybar: GitHub, XWindow
- KDE: Public Transport, Device Notifier, Dictee (voice), Hoppla (Hue),
  arch update notifier, MacOS-style control center
- GNOME: Place Status Indicator, Extension List (meta), Blur my shell
- Stream Deck: MuteDeck for unified meeting control
- Conky: arbitrary scripted display with rings/graphs (inspires a generic
  `graphdash` applet)

---

## Shortlist if implementing only a few

High utility, clean visuals, fit current patterns:

1. **timer** - single-session add, reuses pomodoro/hydration/stretchcoach bones
2. **micmute** + **camshield** - privacy pair, small, widely loved
3. **ghnotify** + **gitrepo** - matches dev profile of the project
4. **worldclocks** + **meetingnext** - remote-team utility
5. **totp** - Cairo countdown ring looks great next to clock
6. **apod** - best visual payoff of any ambient-info applet
7. **systray** - unlocks every third-party app that still uses StatusNotifier
8. **idleinhibit** - small, universal, one-toggle
9. **breathe** or **eyebreak** - fits the wellness cluster already present

---

## Current applets (40, for reference)

aiusage, ambient, applications, battery, bluetooth, bookmarks, brightness,
calculator, calendar, clippy, clock, colorpicker, desktop, hydration,
keyboardlayout, moon, music, network, notifications, pet, pomodoro,
powerprofiles, quicknote, quote, recentfiles, screenshot, separator, session,
stretchcoach, systemmonitor, todayinhistory, trash, trivia, unitconverter,
urlshortener, volume, weather, windowkiller, workspaces
