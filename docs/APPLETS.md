# Applets

Docking applets are small dock widgets that live alongside application icons. Enable them from right-click on the dock background -> **Applets**.

Most applets support one or more of these actions:

- **Click** opens the primary action, popup, or control.
- **Scroll** changes the active item or adjusts a value when that makes sense.
- **Right click** opens applet-specific actions and preferences.
- **Tooltip** gives the current status without opening the applet.


### AI Usage

<img src="../docking/assets/icons/applets/aiusage.png" alt="AI Usage" width="48">

Tracks Claude Code, Codex CLI, and OpenCode usage from the dock.

**Scroll:** Cycle provider focus between Auto, Claude, Codex, and OpenCode
**Right-click options:**
- **Auto / Claude / Codex / OpenCode** -- filter the displayed provider
- **Reset Today** -- clear today’s tracked usage

**Tooltip:** Today/week cost summary plus per-model usage for the selected provider

**Refresh:** Updates when usage changes, plus a periodic refresh for providers that need polling


### Clock

<img src="../docking/assets/icons/applets/clock.png" alt="Clock" width="48">

Analog or digital clock face. Optional seconds display adds a red seconds hand in analog mode and `HH:MM:SS` in digital mode, and the applet can keep a simple one-shot alarm reminder.

**Click:** Acknowledge a ringing alarm
**Right-click options:**
- **Digital Clock** -- switch between analog and digital display
- **24-Hour Clock** -- toggle 12/24-hour format
- **Show Date** -- show date below time (digital mode only)
- **Show Seconds** -- refresh every second and show seconds on the icon
- **Set Alarm...** -- choose an hour/minute for the next one-shot reminder
- **Clear Alarm** -- remove a pending alarm
- **Acknowledge Alarm** -- clear the urgent reminder after it fires


### Trash

<img src="../docking/assets/icons/applets/trash.png" alt="Trash" width="48">

Shows the current state of the system trash. Icon switches between empty and full automatically.

**Click:** Open trash folder in file manager
**Right-click options:**
- **Open Trash** -- open in file manager
- **Empty Trash** -- permanently delete all trashed items

### Desktop

<img src="../docking/assets/icons/applets/desktop.png" alt="Desktop" width="48">

Toggle "show desktop" mode -- minimizes or restores all windows.

**Click:** Toggle show/hide all windows

### System Monitor

<img src="../docking/assets/icons/applets/systemmonitor.png" alt="System Monitor" width="48">

Circular gauge showing real-time CPU and memory usage. The fill color shifts from green (idle) to red (busy). A white arc around the edge shows memory usage.

**Tooltip:** `CPU: 23.5% | Mem: 67.2% | Temp: 54.0°C` when CPU temperature is available

**Refresh:** 1 second

### Thermals

<img src="../docking/assets/icons/applets/thermals.png" alt="Thermals" width="48">

Shows the hottest temperature sensor and fastest fan speed. The icon includes a compact temperature label, and the tooltip gives the current sensor and fan details.

**Right-click options:**
- **Temperature Unit** -- Celsius or Fahrenheit
- **Refresh Now**

**Tooltip:** `Hot: coretemp Package 72.4C` and `Fan: thinkpad fan1 2987 RPM`

**Refresh:** 5 seconds

### Battery

<img src="../docking/assets/icons/applets/battery.png" alt="Battery" width="48">

Shows battery charge level using standard icons. The icon changes based on charge level and charging state.

**Right-click options:**
- **Power Settings** -- open the desktop power settings or power management screen when available

**Tooltip:** Shows percentage and, when the system exposes a battery rate, the estimated time left or time until full. If no estimate is available, it keeps the tooltip simple.

**Refresh:** 60 seconds

### Brightness

<img src="../docking/assets/icons/applets/brightness.png" alt="Brightness" width="48">

Screen brightness control with a live level indicator.

**Click:** Reset brightness to 100%
**Scroll:** Adjust brightness by small steps
**Right-click options:**
- **Show Level** -- toggle percentage text overlay on icon

**Tooltip:** `Brightness: N%`

**Refresh:** 5 seconds

### Weather

<img src="../docking/assets/icons/applets/weather.png" alt="Weather" width="48">

Shows current weather and air quality for a selected city with a 5-day forecast.

**Click:** Open city search and add/switch the active city
**Right-click options:**
- **Show Temperature** -- toggle temperature overlay on icon
- **Temperature Unit** -- Celsius or Fahrenheit
- **Remove {city}** -- remove active city when multiple cities are configured

**Scroll:** Cycle through configured cities

**Tooltip:** Bold city header + current conditions + air quality + daily forecast with icons:
```
Contagem, Brazil
29°C, Clear sky
Air: Good
Mon: 25/29°C, Partly cloudy
Tue: 28/32°C, Rain
```


**Refresh:** 5 minutes

### Moon

<img src="../docking/assets/icons/applets/moon.png" alt="Moon" width="48">

Moon phase applet with a rendered moon disc and illumination shading.

**Click:** Refresh moon data now
**Right-click options:**
- **Show Phase Name** -- toggle phase label overlay on icon
- **Refresh** -- force a refresh

**Tooltip:** Multi-line phase summary with illumination percentage and description

**Refresh:** 6 hours

### Clippy

<img src="../docking/assets/icons/applets/clippy.png" alt="Clippy" width="48">

Clipboard history manager. Monitors the system clipboard and stores the last 15 text entries.

**Click:** Copy the currently selected clip back to the clipboard
**Scroll:** Cycle through clipboard history (tooltip updates instantly)
**Right-click:** List of all clips (newest first), click to copy. "Clear" to empty history.


### Bookmarks

<img src="../docking/assets/icons/applets/bookmarks.png" alt="Bookmarks" width="48">

Bookmarks launcher for pinned URLs.

**Click:** Open the first saved bookmark in the default browser
**Right-click options:**
- **Add Bookmark...** -- save a name + URL pair
- individual bookmark entries -- open that bookmark directly
- **Remove All** -- clear the saved bookmark list

**Tooltip:** summary of the saved bookmark set

### Quick Note

<img src="../docking/assets/icons/applets/quicknote.png" alt="Quick Note" width="48">

Sticky note applet for a single quick text note.

**Click:** Open the note editor dialog
**Right-click options:**
- **Edit Note** -- open the editor
- **Clear Note** -- empty the note

**Tooltip:** note preview or empty-note fallback

### Recent Files

<img src="../docking/assets/icons/applets/recentfiles.png" alt="Recent Files" width="48">

Launcher for the most recently opened files.

**Click:** Open the newest recent file
**Right-click options:**
- recent file entries -- open the selected file
- **Clear Recent Files** -- purge the recent-files list

**Tooltip:** most recent file name or empty-state fallback

### Color Picker

<img src="../docking/assets/icons/applets/colorpicker.png" alt="Color Picker" width="48">

Eyedropper color picker. Click enters fullscreen pick mode, samples a pixel color, copies hex value to clipboard, and updates the icon swatch.

**Click:** Start pick mode and sample next clicked pixel
**Right-click options:**
- **Copy #RRGGBB** -- copy current sampled value
- **Show Hex** -- toggle hex label overlay on icon

**Tooltip:** Current sampled hex value


### Applications

<img src="../docking/assets/icons/applets/applications.png" alt="Applications" width="48">

Categorized application launcher. Groups all installed `.desktop` applications by FreeDesktop category (Multimedia, Development, Internet, etc.) with icons.

**Click:** Open the categorized launcher menu. The top of the menu includes a search field that filters applications as you type.

### Keyboard Layout

<img src="../docking/assets/icons/applets/keyboardlayout.png" alt="Keyboard Layout" width="48">

Keyboard layout switcher with a compact keyboard icon and active layout code overlay.

**Click:** Cycle to the next available layout
**Scroll:** Move forward/backward through available layouts
**Right-click options:**
- **Keyboard Settings** -- open the desktop keyboard settings screen when available
- **Show Current Layout** -- open the current keyboard layout dialog when available
- direct selection of each detected layout

**Tooltip:** active layout code or no-layout fallback

### Caps Lock

<img src="../docking/assets/icons/applets/capslock.png" alt="Caps Lock" width="48">

Caps Lock and Num Lock indicators for keyboards without physical lights. The icon shows which locks are currently active.

**Click:** Refresh lock state immediately
**Right-click options:**
- Current Caps Lock and Num Lock states
- Refresh Now

**Tooltip:** Caps Lock and Num Lock on/off state, or an unavailable-state fallback

**Refresh:** 1 second

### Network

<img src="../docking/assets/icons/applets/network.png" alt="Network" width="48">

Shows WiFi signal strength or wired connection status, with live upload/download speed overlay.

**Tooltip:**
```
WiFi: MyNetwork (82%)
IP: 192.168.1.42
down-arrow 1.2 MB/s  up-arrow 350 KB/s
```

**Right-click options:**
- **Available Networks** -- open a submenu of visible Wi-Fi networks; clicking one starts the connection flow
- **Connect to Hidden Wi-Fi Network...** -- open the desktop network editor/settings flow for hidden Wi-Fi setup
- **Create New Wi-Fi Network...** -- open the desktop network editor/settings flow for creating a new Wi-Fi network
- **VPN Connections** -- open a submenu of saved VPN profiles and toggle them on or off
- **Connection Information** -- open the desktop network settings or information screen when available
- **Edit Connections...** -- open the connection editor when available
- **Enable Networking** -- toggle networking on/off
- **Enable Wi-Fi** -- toggle Wi-Fi radio on/off when a wireless device is present
- **Show Download / Show Upload / Hide Speeds** -- control the speed overlay on the icon

**Refresh:** 2 seconds

### Bluetooth

<img src="../docking/assets/icons/applets/bluetooth.png" alt="Bluetooth" width="48">

Bluetooth manager applet for quick adapter and device control from the dock.

**Click:** Toggle Bluetooth power for the active adapter
**Right-click options:**
- **Turn Bluetooth On / Turn Bluetooth Off** -- power toggle for the active adapter
- **Disconnect {device}** -- quick disconnect action for connected devices
- **Send Files to Device...** -- open the desktop Bluetooth file sender when available
- **Recent Connections** -- reopen recently connected paired devices
- **Devices...** -- open the desktop Bluetooth devices/settings screen when available
- **Adapters...** -- open the desktop Bluetooth adapter/settings screen when available
- **Local Services...** -- open the desktop Bluetooth local-services screen when available
- **Continuous Discovery** -- keeps discovery active while enabled
- **Adapter** -- switch active adapter on multi-adapter systems
- **Connected / Paired / Discovered Devices** -- per-device actions:
  connect/disconnect, pair, remove pairing, trust toggle

**Tooltip:** adapter state, connected/paired counts, discovery status, optional battery line
**Badge:** connected device count

**Refresh:** 2 seconds

### Cam Shield

<img src="../docking/assets/icons/applets/camshield.png" alt="Cam Shield" width="48">

Camera privacy indicator. The icon shows a red dot while an app is using a camera.

**Right-click options:**
- Active app list when available
- Lock Camera / Unlock Camera
- Refresh Now

**Tooltip:** Shows whether the camera is idle, active, or unavailable, plus active holders when detected

Locking blocks new camera sessions. Apps that are already using the camera may need to be closed first.

**Refresh:** 2 seconds

### Mic Shield

<img src="../docking/assets/icons/applets/micshield.png" alt="Mic Shield" width="48">

Microphone privacy indicator and mute toggle. The icon shows a red dot while an app is using microphone input, and clicking the applet quickly mutes or unmutes the microphone.

**Click:** Toggle microphone mute
**Right-click options:**
- Active app list when available
- Mute Microphone / Unmute Microphone
- Refresh Now

**Tooltip:** Shows mute state, idle/active state, and active capture streams when detected

**Refresh:** 2 seconds

### Power Profiles

<img src="../docking/assets/icons/applets/powerprofiles.png" alt="Power Profiles" width="48">

Power profile applet for quick laptop/handheld mode switching.

**Click:** Cycle to next available profile
**Right-click options:**
- **Select Profile** -- radio selector for available profiles
- **Power Saver / Balanced / Performance** -- set active profile

**Tooltip:** current profile and available profiles

### Notifications

<img src="../docking/assets/icons/applets/notifications.png" alt="Notifications" width="48">

Notification center applet with a compact status icon, Do Not Disturb toggle, and pending badge when available.

**Click:** Toggle Do Not Disturb on/off
**Right-click options:**
- **Do Not Disturb** -- toggle notification pause state
- **Pending: N** -- pending notifications (when available)
- **Clear Notifications** -- clear notification history (when available)

**Refresh:** 2 seconds

### Session

<img src="../docking/assets/icons/applets/session.png" alt="Session" width="48">

Lock, log out, suspend, restart, or shut down from the dock.

**Click:** Lock screen
**Right-click options:**
- **Lock Screen**
- **Log Out**
- **Suspend**
- **Restart**
- **Shut Down**

### Calendar

<img src="../docking/assets/icons/applets/calendar.png" alt="Calendar" width="48">

Shows today's date as a calendar page icon with red header (weekday) and day number.

**Click:** Toggle a calendar popup
**Tooltip:** Full date (e.g. "Tuesday, February 25")

**Refresh:** 30 seconds (refreshes icon at midnight)

### Workspaces

<img src="../docking/assets/icons/applets/workspaces.png" alt="Workspaces" width="48">

Workspace switcher with a visual grid icon. Active workspace is highlighted in blue.

**Click:** Cycle to next workspace
**Scroll:** Switch workspace up/down
**Right-click options:** Radio list of all workspaces

**Tooltip:** Active workspace name

### Screenshot

<img src="../docking/assets/icons/applets/screenshot.png" alt="Screenshot" width="48">

Capture screenshots with the available screenshot tool on your system.

**Click:** Full-screen capture
**Right-click options:**
- **Full Screen** -- capture entire screen
- **Window** -- capture active window
- **Region** -- interactive area selection
- **Full Screen in 3s/5s/7s/9s** -- delayed full-screen capture

### Volume

<img src="../docking/assets/icons/applets/volume.png" alt="Volume" width="48">

System volume control. The icon switches between muted, low, medium, and high based on level.

**Click:** Toggle mute
**Scroll:** Adjust volume ±5%
**Right-click options:**
- **Volume Settings** -- open the desktop volume or sound settings screen when available
**Tooltip:** `Volume: 75%` or `Muted`

**Refresh:** 1 second (refreshes only on change)

### Music

<img src="../docking/assets/icons/applets/music.png" alt="Music" width="48">

Media controller applet with album-art icon rendering.

**Click:** Play/pause
**Scroll:** Player volume ±5%
**Right-click options:**
- **Previous**
- **Play** / **Pause**
- **Next**
- **Volume Up** / **Volume Down**

**Tooltip:** multiline summary, e.g. `Artist - Title`, `Album: ...`, `Vol N%`

### Pomodoro

<img src="../docking/assets/icons/applets/pomodoro.png" alt="Pomodoro" width="48">

Pomodoro timer with a flat tomato icon. Auto-cycles through work/break phases with configurable durations. Triggers urgent bounce+glow on phase transitions.

**Click:** Start/pause toggle
**Right-click options:**
- **Reset** -- back to idle
- **Work duration** -- 15/25/30/45 min presets
- **Break duration** -- 5/10 min presets
- **Long break duration** -- 15/20/30 min presets


### Pet

<img src="../docking/assets/icons/applets/pet.png" alt="Pet" width="48">

Animated companion applet that reacts to system activity with different moods.

**Click:** reset the pet back to a happy state
**Tooltip:** current mood and CPU percentage


### Separator

Transparent gap divider between dock items. Supports multiple instances -- each with independent, persistent size.

**Scroll:** Adjust gap width (±2px, range 2–48px)
**Right-click options:**
- **Increase Gap** / **Decrease Gap**
- **Remove from Dock**

Added via right-click on dock background -> **Add Separator** (inserts at click position).

### Hydration

<img src="../docking/assets/icons/applets/hydration.png" alt="Hydration" width="48">

Water drop icon that drains over a configurable interval, reminding you to drink water. Click to refill. Triggers urgent bounce when empty.

**Click:** Refill (log a drink)
**Right-click options:**
- **Show Timer** -- toggle countdown overlay on icon
- **Interval presets** -- 15/30/45/60/90 min


### Stretch Coach

<img src="../docking/assets/icons/applets/stretchcoach.png" alt="Stretch Coach" width="48">

Periodic micro-break reminder applet with offline stretch cards. Reminders stay inside the dock: the icon becomes urgent when a break is due, and clicking acknowledges the reminder and restarts the timer.

**Click:** Trigger a break immediately when idle, or acknowledge the active reminder
**Right-click options:**
- **Take Break Now** / **Acknowledge Break**
- **Show Random Stretch**
- **Random Stretch Cards** -- toggle offline card attachment on reminders
- **Interval presets** -- 15/30/45/60/90 min


### Quote

<img src="../docking/assets/icons/applets/quote.png" alt="Quote" width="48">

Quote/joke applet inspired by the original Cairo-Dock Quote plugin. Ships with local fallback quotes and supports online refresh from active sources.

**Click:** Show next quote
**Right-click options:**
- **Next Quote**
- **Copy Quote** -- copy current quote to clipboard
- **Refresh from Web**
- **Source** -- switch source (Quotationspage, Qdb, Danstonchat, Viedemerde, Fmylife, Vitadimerda, Chucknorrisfactsfr)


### Random Trivia

<img src="../docking/assets/icons/applets/trivia.png" alt="Random Trivia" width="48">

Quick trivia applet with local and online questions. The tooltip shows the current question and answer state, the menu exposes answer choices plus refresh/next actions, and the icon displays a small result pill after you answer: green for correct, red for wrong. The pill clears on the next trivia question.

**Click:** Show the next trivia question
**Right-click options:**
- **Answer choices** -- pick an answer from the current question
- **Next Trivia**
- **Refresh from Web**

### Today in History

<img src="../docking/assets/icons/applets/todayinhistory.png" alt="Today in History" width="48">

One-event-at-a-time history applet with online refresh and offline fallback data. It keeps the current event compact in the tooltip/menu, refreshes for the local date, and lets you step through notable events without leaving the dock.

**Click:** Show the next historical event for today
**Right-click options:**
- **Next Event**
- **Refresh from Web**
- **Open Article** -- open the current event's Wikipedia page when available

### Hacker News

<img src="../docking/assets/icons/applets/hackernews.png" alt="Hacker News" width="48">

Hacker News headline viewer. It shows top stories, keeps recent headlines available, loads more as you browse, and shows the selected title plus points/comments in the tooltip. Paging continues up to 100 loaded headlines.

**Click:** Open the current story
**Scroll:** Cycle headlines
**Right-click options:**
- **Open Story**
- **Open Comments**
- **Next Headline**
- **Refresh Now**

**Refresh:** 10 minutes. Additional pages load on demand when you reach the last loaded headline, up to 100 stories.


### Ambient

<img src="../docking/assets/icons/applets/ambient.png" alt="Ambient" width="48">

Looping ambient soundscape player with 7 bundled nature sounds plus white and pink noise.

**Click:** Toggle play/stop
**Scroll:** Adjust volume ±10%
**Right-click:** Sound selection (Birds, Boat, Coffee Shop, Fireplace, Stream, Summer Night, Wind, White Noise, Pink Noise)


### Calculator

<img src="../docking/assets/icons/applets/calculator.png" alt="Calculator" width="48">

Basic four-function calculator with a popup interface. Supports +, -, *, /, parentheses, and decimal numbers.

**Click:** Toggle calculator popup
**Keyboard:** Type expression, press Enter to evaluate


### Unit Converter

<img src="../docking/assets/icons/applets/unitconverter.png" alt="Unit Converter" width="48">

Convert between units directly from the dock popup. Supports length, weight, temperature, volume, speed, and data categories.

**Click:** Toggle converter popup


### Currency FX

<img src="../docking/assets/icons/applets/currencyfx.png" alt="Currency FX" width="48">

Live currency pair monitor with a sparkline icon. Add the pairs you care about, cycle between them from the dock, and choose the chart range that fits your glance.

**Click:** Add FX pair
**Scroll:** Cycle added pairs
**Right-click:** Refresh, swap pair, add pair, chart interval, switch/remove added pair

**Refresh:** 15 minutes. Day charts use local samples collected on each successful refresh; week and month charts use remote daily history plus the current rate.


### URL Shortener

<img src="../docking/assets/icons/applets/urlshortener.png" alt="URL Shortener" width="48">

Shorten URLs with one click. Paste a URL, hit Shorten, and copy the result to the clipboard.

**Click:** Toggle URL shortener dialog
**Keyboard:** Paste URL, press Enter to shorten


### Drag Share

<img src="../docking/assets/icons/applets/dragshare.png" alt="Drag Share" width="48">

Drop a local file onto the applet to upload it to tmpfiles.org and copy the returned URL to the clipboard. Files are temporary and expire automatically.

**Drop:** Upload file and copy URL
**Click:** Copy last uploaded URL again


### Window Killer

<img src="../docking/assets/icons/applets/windowkiller.png" alt="Window Killer" width="48">

Click the applet, then click any window to force-close it.

**Click:** Enter kill mode (cursor changes to crosshair)

### Cert Watch

<img src="../docking/assets/icons/applets/certwatch.png" alt="Cert Watch" width="48">

Monitor certificate expiry for a list of domains. The shield color highlights the most urgent domain, and the icon shows the lowest days remaining so expiring certificates are easy to spot.

**Click:** Add domain dialog (accepts `example.com`, `example.com:8443`, or a full URL)

**Right-click menu:**
- Per-domain status with days remaining
- Add domain
- Remove submenu
- Refresh Now

**Refresh:** 1 hour. Failed certificate checks retry after 5 minutes.


### Speedtest

<img src="../docking/assets/icons/applets/speedtest.png" alt="Speedtest" width="48">

One-click internet speed test. The dial is painted as a classic four-band speedometer (red, orange, yellow, green from left to right); the needle points at the last download speed and takes its color from the current tier. The badge shows Mbps (e.g. `250Mb`, `1.2Gb`). Tooltip shows download, upload, ping, jitter, server, and timestamp.

**Click:** Run one test (~20 seconds: ping + 10s download + 10s upload)

**Right-click menu:**
- Summary header (Down / Up)
- Run Test (disabled while running)
- Copy Last Result (to clipboard)

**Refresh:** Manual. Results update only when you run a test.


### Desk Presence

<img src="../docking/assets/icons/applets/deskpresence.png" alt="Desk Presence" width="48">

Tracks time at your desk versus away. The icon shows whether you are currently active or away, the bottom label shows today's at-desk hours, and the tooltip summarizes the recent daily breakdown.

**Right-click menu:**
- Status header (At desk / Away / Status unknown)
- Idle Threshold submenu (1 / 2 / 5 / 10 min presets)
- Reset Today


### Astronomy Picture of the Day

<img src="../docking/assets/icons/applets/apod.png" alt="APOD" width="48">

Shows NASA's Astronomy Picture of the Day as a dock thumbnail. The tooltip includes the date, title, credit, and a short explanation, and the applet keeps showing a graceful placeholder if the image is unavailable.

**Click:** Open today's page on apod.nasa.gov in the default browser

**Right-click menu:**
- Title header (date + title)
- Open on apod.nasa.gov
- Copy Explanation
- Refresh Now

**Refresh:** 1 hour. The applet fetches again when the APOD date changes and retries errors after 10 minutes.
