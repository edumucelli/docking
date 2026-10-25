# Applets

Docking includes 66 built-in applets that live alongside
application launchers, files, and folders. Applets can launch workflows,
show live information, adjust system settings, or provide small tools
without opening a full application.

## Add and Manage Applets

- Right-click the dock background and open **Add Applet**, then choose a
  category and applet.
- Open right-click -> **Preferences** -> **Applets** to enable or disable
  applets from the complete visual catalog.
- Right-click an applet and choose **Remove from Dock** to remove it.
- Drag applets to reorder them unless **Lock Positions** is enabled.
- Use an applet's context menu for its preferences and actions. Many
  applets also respond to clicking, scrolling, or drag-and-drop.

Separators are added through the separate **Add Separator** action because
multiple independently sized separators can be used at once.

Some applets need an internet connection, compatible desktop software, or a
connected device. Features that are unavailable show a status message or stay
hidden from the menu.

## Catalog

- **Launcher & Navigation:** [Applications](#applications), [Desktop](#desktop),
  [Run Application](#run-application), [Search](#search), [Telegram](#telegram), [WhatsApp](#whatsapp),
  [Workspaces](#workspaces)
- **Time & Productivity:** [AI Usage](#ai-usage), [Alarm](#alarm),
  [Bookmarks](#bookmarks), [Calculator](#calculator), [Calendar](#calendar),
  [Clippy](#clippy), [Clock](#clock), [Color Picker](#color-picker),
  [Drag Share](#drag-share), [Pomodoro](#pomodoro), [Quick Note](#quick-note),
  [Recent Files](#recent-files), [Unit Converter](#unit-converter),
  [URL Shortener](#url-shortener)
- **System & Power:** [Battery](#battery), [Bluetooth](#bluetooth),
  [Brightness](#brightness), [Caffeine](#caffeine), [Cam Shield](#cam-shield),
  [Caps Lock](#caps-lock), [Cert Watch](#cert-watch), [Devices](#devices),
  [Docker](#docker), [Keyboard Layout](#keyboard-layout), [Mic Shield](#mic-shield),
  [Music](#music), [Network](#network), [Notifications](#notifications),
  [Power Profiles](#power-profiles), [Screenshot](#screenshot), [Session](#session),
  [Speedtest](#speedtest), [System Monitor](#system-monitor),
  [System Tray](#system-tray), [Thermals](#thermals), [Trash](#trash),
  [USB Watch](#usb-watch), [Volume](#volume), [Window Killer](#window-killer)
- **Wellness & Ambient:** [Ambient](#ambient), [Desk Presence](#desk-presence),
  [Hydration](#hydration), [Pet](#pet), [Plant Care](#plant-care),
  [Stretch Coach](#stretch-coach)
- **Information and Environment:** [Astronomy Picture of the Day](#astronomy-picture-of-the-day),
  [Crypto](#crypto), [Currency FX](#currency-fx), [Hacker News](#hacker-news),
  [Last.fm](#lastfm), [Moon](#moon), [News](#news), [Quote](#quote),
  [Random Trivia](#random-trivia), [Reddit](#reddit), [Sunrise](#sunrise),
  [Today in History](#today-in-history), [Weather](#weather)
- **Other:** [Separator](#separator)

## Launcher & Navigation

### Applications

Browse installed applications by category, such as Multimedia, Development,
and Internet.

**Click:** Open the categorized launcher menu. Its search field filters
applications as you type.

**Drag:** Drag an application from the menu directly into the dock to pin it.
The application icon remains beside the pointer during the drag.

### Search

Search for applications, files, open windows, and more from the dock.

**Click:** Open the search window. Find applications, dock items, open windows,
recent files, calculations, unit/currency conversions, dates, time zones,
websites, and file or folder paths. Press `Ctrl+Right` to explore an app's
actions, windows, and recent documents. Use `Ctrl+P` for previews or `Ctrl+J`
for more actions.

Removing the applet leaves your global search keyboard shortcut available.

### Desktop

Toggle "show desktop" mode -- minimizes or restores all windows.

**Click:** Toggle show/hide all windows

### Run Application

Alt+F2-style launcher for applications and commands. Typing filters the list
of installed applications, while selecting an application fills in its launch
command and description.

**Click:** Open the Run Application dialog

Recent commands remain available to run again.

**Dialog options:**

- Type a command or select a known application
- **Run in terminal** -- launch the command in an available terminal emulator
- **Run with file...** -- append a selected file to the command
- **Show list of known applications** -- browse installed desktop applications

### Workspaces

Workspace switcher with a visual grid icon. Active workspace is highlighted in blue.

**Click:** Cycle to next workspace
**Scroll:** Switch workspace up/down
**Right-click options:** Radio list of all workspaces

**Tooltip:** Active workspace name

### Telegram

Use Telegram in its own Docking window. Sign in with a QR code or your phone
number. Closing the window hides it while messages and notifications continue
to arrive. Your sign-in is remembered when Docking restarts.

**Click:** Show or hide the Telegram window.

**Right-click:** Open Telegram / Hide Telegram, Reload, Open in Browser, and
Disconnect Telegram. Disconnect signs you out of Telegram inside Docking;
Telegram on your other devices stays signed in.

**Badge:** Number of unread unmuted chats. When an unread count is unavailable,
the badge shows recent notifications and clears when you open the window.

**Status:** The icon and tooltip show loading, sign-in, synchronization, offline,
ready, and error states.

**Notifications:** Click a notification to open the Telegram window. You will
be asked before Telegram can use the camera or microphone.

### WhatsApp

Use WhatsApp in its own Docking window. Scan the QR code to sign in. Closing
the window hides it while messages and notifications continue to arrive.
Your sign-in is remembered when Docking restarts.

**Click:** Show or hide the WhatsApp window.

**Right-click options:**

- **Open WhatsApp** / **Hide WhatsApp**
- **Reload** -- refresh the WhatsApp window
- **Open in Browser** -- open WhatsApp Web in your default browser
- **Disconnect WhatsApp** -- sign out of WhatsApp inside Docking and return to
  the QR login page

**Badge:** Unread message count. When an unread count is unavailable, the badge
shows recent notifications and clears when you open the window.

**Status:** The icon and tooltip show loading, QR sign-in, synchronization,
offline, ready, and error states.

**Notifications:** Click a notification to open the WhatsApp window. You will
be asked before WhatsApp can use the camera or microphone.


## Time & Productivity

### AI Usage

Tracks Claude Code, Codex CLI, and OpenCode usage from the dock.

**Scroll:** Cycle provider focus between Auto, Claude, Codex, and OpenCode
**Right-click options:**

- **Auto / Claude / Codex / OpenCode** -- filter the displayed provider
- **Reset Today** -- clear today’s tracked usage

**Tooltip:** Today/week cost summary plus per-model usage for the selected provider

### Alarm

Multiple alarm presets with local-time scheduling, weekday repeats, one-shot alarms, snooze, and dismiss controls. The icon shows a rounded alarm clock with a compact next-alarm countdown, and switches to a ringing label when an alarm fires.

**Click:** Open the alarm editor, or dismiss the current ringing alarm
**Right-click options:**

- **Add Alarm...** -- create a new alarm preset
- **Snooze** -- move the current ringing alarm forward by its preset snooze duration
- **Dismiss** -- stop the current ringing alarm
- **Alarm preset rows** -- enable or disable saved presets from the menu
- **Edit {label}...** -- edit or remove a saved preset

**Tooltip:** Next enabled alarm with local time, or the currently ringing alarm label.

### Bookmarks

Bookmarks launcher for pinned URLs.

**Click:** Open the first saved bookmark in the default browser
**Right-click options:**

- **Add Bookmark...** -- save a name + URL pair
- individual bookmark entries -- open that bookmark directly
- **Remove All** -- clear the saved bookmark list

**Tooltip:** summary of the saved bookmark set

### Calculator

Basic four-function calculator with a popup interface. Supports +, -, *, /, parentheses, and decimal numbers.

**Click:** Toggle calculator popup
**Keyboard:** Type expression, press Enter to evaluate

### Calendar

Shows today's date as a calendar page icon with red header (weekday) and day number.

**Click:** Toggle a calendar popup
**Tooltip:** Full date (e.g. "Tuesday, February 25")

### Clippy

Clipboard history manager. Monitors the system clipboard and stores the last 15 text entries.

**Click:** Copy the currently selected clip back to the clipboard
**Scroll:** Cycle through clipboard history (tooltip updates instantly)
**Right-click:** List of all clips (newest first), click to copy. "Clear" to empty history.

### Clock

Choose an analog or digital clock, optionally show seconds, and set a one-time
alarm reminder.

**Click:** Open or close the calendar. A ringing alarm is acknowledged before
the calendar opens.
**Right-click options:**

- **Digital Clock** -- switch between analog and digital display
- **24-Hour Clock** -- toggle 12/24-hour format
- **Show Date** -- show date below time (digital mode only)
- **Show Seconds** -- show seconds on the icon
- **Set Alarm...** -- choose an hour/minute for the next one-shot reminder
- **Clear Alarm** -- remove a pending alarm
- **Acknowledge Alarm** -- clear the urgent reminder after it fires

### Color Picker

Eyedropper color picker. Click enters fullscreen pick mode, samples a pixel color, copies hex value to clipboard, and updates the icon swatch.

**Click:** Start pick mode and sample next clicked pixel
**Right-click options:**

- **Copy #RRGGBB** -- copy current sampled value
- **Show Hex** -- toggle hex label overlay on icon

**Tooltip:** Current sampled hex value

### Drag Share

Drop a local file onto the applet to upload it to tmpfiles.org and copy the returned URL to the clipboard. Files are temporary and expire automatically.

**Drop:** Upload file and copy URL
**Click:** Copy last uploaded URL again

### Pomodoro

Alternate between timed work and breaks. Choose the duration of each phase;
the tomato icon bounces and glows when it is time to switch.

**Click:** Start/pause toggle
**Right-click options:**

- **Reset** -- back to idle
- **Work duration** -- 15/25/30/45 min presets
- **Break duration** -- 5/10 min presets
- **Long break duration** -- 15/20/30 min presets

### Quick Note

Sticky note applet for a single quick text note.

**Click:** Open the note editor dialog
**Right-click options:**

- **Edit Note** -- open the editor
- **Clear Note** -- empty the note

**Tooltip:** Note preview, or a message when the note is empty

### Recent Files

Launcher for the most recently opened files.

**Click:** Open the newest recent file
**Right-click options:**

- recent file entries -- open the selected file
- **Clear Recent Files** -- purge the recent-files list

**Tooltip:** Most recent file name, or a message when there are no recent files

### Unit Converter

Convert between units directly from the dock popup. Supports length, weight, temperature, volume, speed, and data categories.

**Click:** Toggle converter popup

### URL Shortener

Shorten URLs with one click. Paste a URL, hit Shorten, and copy the result to the clipboard.

**Click:** Toggle URL shortener dialog
**Keyboard:** Paste URL, press Enter to shorten


## System & Power

### Battery

Shows battery charge level using standard icons. The icon changes based on charge level and charging state.

**Right-click options:**

- **Power Settings** -- open the desktop power settings or power management screen when available

**Tooltip:** Battery percentage, with estimated time left or time until full
when available.

### Bluetooth

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

### Brightness

Screen brightness control with a live level indicator.

**Click:** Reset brightness to 100%
**Scroll:** Adjust brightness by small steps
**Right-click options:**

- **Show Level** -- toggle percentage text overlay on icon

**Tooltip:** `Brightness: N%`

### Caffeine

Keeps the computer awake for a selected duration or indefinitely.

**Click:** Toggle keeping the computer awake
**Right-click:** Duration presets and status

### Cam Shield

Camera privacy indicator. The icon shows a red dot while an app is using a camera.

**Right-click options:**

- Active app list when available
- Lock Camera / Unlock Camera
- Refresh Now

**Tooltip:** Whether the camera is idle, active, or unavailable, plus the apps
using it when they can be identified

Locking blocks new camera access. Apps that are already using the camera may need to be closed first.

### Caps Lock

Caps Lock and Num Lock indicators for keyboards without physical lights. The icon shows which locks are currently active.

**Click:** Refresh lock state immediately
**Right-click options:**

- Current Caps Lock and Num Lock states
- Refresh Now

**Tooltip:** Whether Caps Lock and Num Lock are on, off, or unavailable

### Cert Watch

Monitor certificate expiry for a list of domains. The shield color highlights the most urgent domain, and the icon shows the lowest days remaining so expiring certificates are easy to spot.

**Click:** Add domain dialog (accepts `example.com`, `example.com:8443`, or a full URL)

**Right-click menu:**

- Per-domain status with days remaining
- Add domain
- Remove submenu
- Refresh Now

### Devices

Browse drives and network folders that are available in your file manager.
Selecting a device opens it. The list updates as devices are connected or
removed.

**Open:** Show devices on hover or click. Choose which under **Preferences**
-> **Behavior**.
**Right-click option:**

- **Refresh Devices** -- update the device list

### Docker

Shows Docker availability and container status when Docker is installed.

**Click:** Refresh container state
**Right-click:** Container actions when available

### Keyboard Layout

Keyboard layout switcher with a compact keyboard icon and active layout code overlay.

**Click:** Cycle to the next available layout
**Scroll:** Move forward/backward through available layouts
**Right-click options:**

- **Keyboard Settings** -- open the desktop keyboard settings screen when available
- **Show Current Layout** -- open the current keyboard layout dialog when available
- direct selection of each detected layout

**Tooltip:** Current keyboard layout, or a message when none is available

### Mic Shield

Microphone privacy indicator and mute toggle. The icon shows a red dot while an app is using microphone input, and clicking the applet quickly mutes or unmutes the microphone.

**Click:** Toggle microphone mute
**Right-click options:**

- Active app list when available
- Mute Microphone / Unmute Microphone
- Refresh Now

**Tooltip:** Whether the microphone is muted, idle, or active, plus the apps
using it when they can be identified

### Music

Control music playback from an icon that shows the current album art.

**Click:** Play/pause
**Scroll:** Player volume ±5%
**Right-click options:**

- **Previous**
- **Play** / **Pause**
- **Next**
- **Volume Up** / **Volume Down**

**Tooltip:** multiline summary, e.g. `Artist - Title`, `Album: ...`, `Vol N%`

### Network

Shows WiFi signal strength or wired connection status, with live upload/download speed overlay.

**Tooltip:**
```
WiFi: MyNetwork (82%)
IP: 192.168.1.42
down-arrow 1.2 MB/s  up-arrow 350 KB/s
```

**Right-click options:**

- **Available Networks** -- choose a visible Wi-Fi network to connect to
- **Connect to Hidden Wi-Fi Network...** -- open the desktop network editor/settings flow for hidden Wi-Fi setup
- **Create New Wi-Fi Network...** -- open the desktop network editor/settings flow for creating a new Wi-Fi network
- **VPN Connections** -- open a submenu of saved VPN profiles and toggle them on or off
- **Connection Information** -- open the desktop network settings or information screen when available
- **Edit Connections...** -- open the connection editor when available
- **Enable Networking** -- turn network connections on or off
- **Enable Wi-Fi** -- toggle Wi-Fi radio on/off when a wireless device is present
- **Show Download / Show Upload / Hide Speeds** -- control the speed overlay on the icon

### Notifications

Notification center applet with a compact status icon, Do Not Disturb toggle, and pending badge when available.

**Click:** Toggle Do Not Disturb on/off
**Right-click options:**

- **Do Not Disturb** -- toggle notification pause state
- **Pending: N** -- pending notifications (when available)
- **Clear Notifications** -- clear notification history (when available)

### Power Profiles

Power profile applet for quick laptop/handheld mode switching.

**Click:** Cycle to next available profile
**Right-click options:**

- **Select Profile** -- radio selector for available profiles
- **Power Saver / Balanced / Performance** -- set active profile

**Tooltip:** current profile and available profiles

### Screenshot

Capture screenshots with the available screenshot tool on your system.

**Click:** Full-screen capture
**Right-click options:**

- **Full Screen** -- capture entire screen
- **Window** -- capture active window
- **Region** -- interactive area selection
- **Full Screen in 3s/5s/7s/9s** -- delayed full-screen capture

### Session

Lock, log out, suspend, restart, or shut down from the dock.

**Click:** Lock screen
**Right-click options:**

- **Lock Screen**
- **Log Out**
- **Suspend**
- **Restart**
- **Shut Down**

### Speedtest

Check your internet connection's speed. The gauge and badge show the last
download speed; the tooltip includes upload speed, connection quality, test
server, and the time of the test.

**Click:** Run a speed test (about 20 seconds). Results stay visible until you
run another test.

**Right-click menu:**

- Summary header (Down / Up)
- Run Test (disabled while running)
- Copy Last Result (to clipboard)

### System Monitor

Circular gauge showing real-time CPU and memory usage. The fill color shifts from green (idle) to red (busy). A white arc around the edge shows memory usage.

**Tooltip:** `CPU: 23.5% | Mem: 67.2% | Temp: 54.0°C` when CPU temperature is available

### System Tray

Access tray icons for chat clients, sync tools, and other background apps.

**Click:** Show tray applications
**Right-click options:**

- Actions and menus provided by each app, when available
- Refresh Now

### Thermals

Shows the highest available temperature and fastest fan speed. The thermometer
icon displays the temperature; the tooltip identifies the readings.

**Right-click options:**

- **Temperature Unit** -- Celsius or Fahrenheit
- **Refresh Now**

**Tooltip:** Highest temperature, fastest fan speed, and sensor names

### Trash

Shows the current state of the system trash. Icon switches between empty and full automatically.

**Click:** Open trash folder in file manager
**Right-click options:**

- **Open Trash** -- open in file manager
- **Empty Trash** -- permanently delete all trashed items

### USB Watch

View USB storage devices and safely remove them without opening a file manager.

**Tooltip:** Connected storage-device count and folder locations
**Right-click options:**

- **Safely Remove _device_** -- prepare a USB drive for unplugging

### Volume

System volume control. The icon switches between muted, low, medium, and high based on level.

**Click:** Toggle mute
**Scroll:** Adjust volume ±5%
**Right-click options:**

- **Volume Settings** -- open the desktop volume or sound settings screen when available
**Tooltip:** `Volume: 75%` or `Muted`

### Window Killer

Click the applet, then click any window to force-close it.

**Click:** Enter kill mode (cursor changes to crosshair)


## Wellness & Ambient

### Ambient

Looping ambient soundscape player with 7 bundled nature sounds plus white and pink noise.

**Click:** Toggle play/stop
**Scroll:** Adjust volume ±10%
**Right-click:** Sound selection (Birds, Boat, Coffee Shop, Fireplace, Stream, Summer Night, Wind, White Noise, Pink Noise)

### Desk Presence

Tracks time at your desk versus away. The icon shows whether you are currently active or away, the bottom label shows today's at-desk hours, and the tooltip summarizes the recent daily breakdown.

**Right-click menu:**

- Status header (At desk / Away / Status unknown)
- Idle Threshold submenu (1 / 2 / 5 / 10 min presets)
- Reset Today

### Hydration

The water drop drains as your next drink reminder approaches and bounces when
it is time to drink. Click to refill it.

**Click:** Refill (log a drink)
**Right-click options:**

- **Show Timer** -- toggle countdown overlay on icon
- **Interval presets** -- 15/30/45/60/90 min

### Pet

Animated companion applet that reacts to system activity with different moods.

**Click:** reset the pet back to a happy state
**Tooltip:** current mood and CPU percentage

### Plant Care

Offline care reminders for multiple plants. The sprout icon shows whether care
is on schedule, due today, or overdue, with a compact count when tasks need
attention.

**Click:** Open the care manager with due and upcoming tasks
**Right-click options:**

- due task actions -- mark done or snooze for one day
- **Add Plant...** -- configure a plant and its recurring schedules
- **Manage Plants...** -- review, edit, or remove configured plants
- **Refresh Now**

Set reminders for watering, fertilizing, misting, rotating, pruning, repotting,
and pest checks. Reminders follow your local calendar and do not measure the
plant or soil.

### Stretch Coach

Periodic micro-break reminder applet with offline stretch cards. Reminders stay inside the dock: the icon becomes urgent when a break is due, and clicking acknowledges the reminder and restarts the timer.

**Click:** Trigger a break immediately when idle, or acknowledge the active reminder
**Right-click options:**

- **Take Break Now** / **Acknowledge Break**
- **Show Random Stretch**
- **Random Stretch Cards** -- include a stretch card with each reminder
- **Interval presets** -- 15/30/45/60/90 min


## Information and Environment

### Astronomy Picture of the Day

Shows NASA's Astronomy Picture of the Day as a dock thumbnail. The tooltip includes the date, title, credit, and a short explanation, and the applet keeps showing a graceful placeholder if the image is unavailable.

**Click:** Open today's page on apod.nasa.gov in the default browser

**Right-click menu:**

- Title header (date + title)
- Open on apod.nasa.gov
- Copy Explanation
- Refresh Now

### Crypto

Tracks selected cryptocurrency prices with compact dock display and refresh actions.

**Click:** Add an asset
**Scroll:** Switch tracked assets
**Right-click:** Refresh, chart interval, switch, add, or remove assets

### Currency FX

Live currency pair monitor with a sparkline icon. Add the pairs you care about, cycle between them from the dock, and choose the chart range that fits your glance.

**Click:** Add FX pair
**Scroll:** Cycle added pairs
**Right-click:** Refresh, swap pair, add pair, chart interval, switch/remove added pair

### Hacker News

Browse up to 100 top Hacker News stories. The tooltip shows the selected
title, points, and comment count.

**Click:** Open the current story
**Scroll:** Cycle headlines
**Right-click options:**

- **Open Story**
- **Open Comments**
- **Next Headline**
- **Refresh Now**

### Last.fm

Shows recent Last.fm listening activity for a configured user.

**Click:** Configure the applet, or open the current track when available
**Right-click:** Recent tracks, profile link, refresh, and configuration

### Moon

Shows the current moon phase and how much of the moon is illuminated.

**Click:** Update the moon display
**Right-click options:**

- **Show Phase Name** -- toggle phase label overlay on icon
- **Refresh** -- force a refresh

**Tooltip:** Multi-line phase summary with illumination percentage and description

### News

Choose a country and publication, add up to 20 news sources, and switch
between their headlines. Publications with several editions or sections appear
as separate choices with language and section details.

**Click:** Choose a source, refresh an empty configured feed, or open the
current headline

**Scroll:** Move through headlines from the active publication

**Right-click options:**

- **Open Headline** / **Open Publication**
- **Previous Headline** / **Next Headline**
- **Source** -- switch between configured publications
- **Add News Source...** / **Remove Current News Source**
- **Refresh Now**

### Quote

Read quotes and jokes, copy a favorite, or refresh from an online source.
A selection of quotes is also available without an internet connection.

**Click:** Show next quote
**Right-click options:**

- **Next Quote**
- **Copy Quote** -- copy current quote to clipboard
- **Refresh from Web**
- **Source** -- switch source (Quotationspage, Qdb, Danstonchat, Viedemerde, Fmylife, Vitadimerda, Chucknorrisfactsfr)

### Random Trivia

Quick trivia applet with local and online questions. The tooltip shows the current question and answer state, the menu exposes answer choices plus refresh/next actions, and the icon displays a small result pill after you answer: green for correct, red for wrong. The pill clears on the next trivia question.

**Click:** Show the next trivia question
**Right-click options:**

- **Answer choices** -- pick an answer from the current question
- **Next Trivia**
- **Refresh from Web**

### Reddit

Browse posts from your favorite subreddits. Choose one or more subreddits,
switch between Hot/New/Top/Rising feeds, and open the selected Reddit thread in
the default browser.

**Click:** Open the current Reddit post, or add a subreddit when no post is loaded
**Scroll:** Cycle through fetched posts
**Right-click options:**

- **Open Post**
- **Previous Headline** / **Next Headline**
- **Subreddit** -- switch between configured sources
- **Sort** -- Hot, New, Top, or Rising
- **Top Period** -- day, week, month, year, or all time
- **Add Subreddit...** / **Remove r/{subreddit}**
- **Refresh Now**

### Sunrise

Count down to sunrise, sunset, and twilight for a selected city. The 24-hour
dial shows daylight, twilight, and night, with a marker for the current time.

**Click:** Open city search and add/switch the active city
**Right-click options:**

- **Label Mode** -- switch between next-event countdown, current phase, and sunrise/sunset times
- **Remove {city}** -- remove active city when multiple cities are configured

**Scroll:** Cycle through configured cities

**Tooltip:** Selected city, current solar phase, next event countdown, and
today's sunrise and sunset times, shown in your system timezone.

### Today in History

Browse notable historical events for today in the tooltip and menu. A
selection is available without an internet connection.

**Click:** Show the next historical event for today
**Right-click options:**

- **Next Event**
- **Refresh from Web**
- **Open Article** -- open the current event's Wikipedia page when available

### Weather

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


## Other

### Separator

Transparent gap divider between dock items. Supports multiple instances -- each with independent, persistent size.

**Scroll:** Adjust gap width (±2px, range 2–48px)
**Right-click options:**

- **Increase Gap** / **Decrease Gap**
- **Remove from Dock**

Added via right-click on dock background -> **Add Separator** (inserts at click position).
