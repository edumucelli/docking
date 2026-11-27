const colors = {
  "Time & Productivity": "text-amber-100",
  "Wellness & Ambient": "text-emerald-100",
  "Launcher & Navigation": "text-blue-100",
  "Information and Environment": "text-slate-100",
  "System & Power": "text-rose-100",
};

// Category, icon, title, description in display order.
export const applets = [
  ["Time & Productivity", "aiusage", "AI Usage", "Track sessions, token usage, and estimated spend across Claude, Codex, and OpenCode."],
  ["Time & Productivity", "alarm", "Alarm", "Multiple local-time alarm presets with weekday repeats, snooze, and dismiss controls."],
  ["Wellness & Ambient", "ambient", "Ambient", "Play nature sounds, white noise, or pink noise on a loop."],
  ["Launcher & Navigation", "applications", "Applications", "Browse and launch installed desktop applications from a compact app menu."],
  ["Information and Environment", "apod", "Astronomy Picture of the Day", "NASA APOD thumbnails with title, credit, and explanation in the tooltip."],
  ["System & Power", "battery", "Battery", "Check the battery level, charging status, and estimated time remaining."],
  ["System & Power", "bluetooth", "Bluetooth", "Turn Bluetooth on or off and manage paired devices."],
  ["Time & Productivity", "bookmarks", "Bookmarks", "Pinned shortcuts for places, links, and resources you revisit often."],
  ["System & Power", "brightness", "Brightness", "Screen brightness control without leaving the dock."],
  ["System & Power", "caffeine", "Caffeine", "Keep the session awake with optional timers for focus blocks or long tasks."],
  ["Time & Productivity", "calculator", "Calculator", "Quick calculations without opening a larger application."],
  ["Time & Productivity", "calendar", "Calendar", "Open a pop-up calendar and browse dates."],
  ["System & Power", "camshield", "Cam Shield", "Camera privacy indicator that shows when a process holds a video device."],
  ["System & Power", "capslock", "Caps Lock", "Caps and Num Lock LED states for keyboards without physical indicators."],
  ["System & Power", "certwatch", "Cert Watch", "TLS certificate expiry monitor with a color-coded shield and days-remaining badge."],
  ["Time & Productivity", "clippy", "Clippy", "Clipboard history for recent snippets and repeated text."],
  ["Time & Productivity", "clock", "Clock", "Analog or digital clock display with optional seconds and alarm reminder support."],
  ["Time & Productivity", "colorpicker", "Color Picker", "Pick and inspect colors directly from the desktop."],
  ["Information and Environment", "crypto", "Crypto", "Track selected coins and NFT collections with live prices and compact sparklines."],
  ["Information and Environment", "currencyfx", "Currency FX", "Live exchange-rate pairs with a sparkline right on the dock icon."],
  ["Wellness & Ambient", "deskpresence", "Desk Presence", "Track time at desk versus away, with today's total in the badge."],
  ["Launcher & Navigation", "desktop", "Desktop", "Hide or restore all windows."],
  ["Launcher & Navigation", "devices", "Devices", "Browse every mounted device in a live stack that updates as mounts change."],
  ["System & Power", "docker", "Docker", "See running containers and stop or restart them from the dock menu."],
  ["Time & Productivity", "dragshare", "Drag Share", "Drop a file to upload it to tmpfiles.org and copy the temporary share URL."],
  ["Information and Environment", "hackernews", "Hacker News", "Browse up to 100 top Hacker News stories."],
  ["Wellness & Ambient", "hydration", "Hydration", "Set a timer to remind you to drink water."],
  ["System & Power", "kdeconnect", "KDE Connect", "See phone battery and charging status, ring your phone, and send files."],
  ["System & Power", "keyboardlayout", "Keyboard Layout", "See and switch your current keyboard layout."],
  ["Information and Environment", "lastfm", "Last.fm", "Recent Last.fm and Libre.fm scrobbles with album art and profile shortcuts."],
  ["System & Power", "micshield", "Mic Shield", "Microphone mute toggle with a privacy dot while an app captures input."],
  ["Information and Environment", "moon", "Moon", "Current lunar phase with a rendered moon disc and illumination shading."],
  ["System & Power", "music", "Music", "See the current album cover and control playback."],
  ["System & Power", "network", "Network", "Check your connection status and upload and download speeds."],
  ["Information and Environment", "news", "News", "Follow country-based RSS publications and browse their latest headlines from the dock."],
  ["System & Power", "notifications", "Notifications", "Notification status, Do Not Disturb toggle, and pending badge when available."],
  ["Wellness & Ambient", "pet", "Pet", "A playful dock companion that reacts to system activity."],
  ["Wellness & Ambient", "plantcare", "Plant Care", "Offline recurring care reminders for multiple plants, with due and overdue status."],
  ["Time & Productivity", "pomodoro", "Pomodoro", "Set a timer for work sessions and breaks."],
  ["System & Power", "powerprofiles", "Power Profiles", "Switch between available power and performance modes."],
  ["Time & Productivity", "quicknote", "Quick Note", "Capture short notes without opening a larger app."],
  ["Information and Environment", "quote", "Quote", "Rotating quote and joke applet with local fallback content and online refresh."],
  ["Information and Environment", "trivia", "Random Trivia", "Quick trivia questions with answer choices and result feedback."],
  ["Time & Productivity", "recentfiles", "Recent Files", "Reopen recently used files."],
  ["Launcher & Navigation", "runcommand", "Run Application", "Alt+F2-style command launcher with terminal mode and searchable app matching."],
  ["System & Power", "screenshot", "Screenshot", "Launch screenshot and portal capture actions directly from the dock."],
  ["Launcher & Navigation", "search", "Search", "Find applications, dock items, open windows, recent files, calculations, and paths."],
  ["System & Power", "session", "Session", "Lock the screen, log out, suspend, restart, or shut down."],
  ["System & Power", "speedtest", "Speedtest", "One-click internet speed test with a speedometer dial and Mbps badge."],
  ["Wellness & Ambient", "stretchcoach", "Stretch Coach", "Set stretch reminders and view stretch cards offline."],
  ["Information and Environment", "sunrise", "Sunrise", "Sunrise, sunset, twilight phases, and a solar dial for selected cities."],
  ["System & Power", "systemmonitor", "System Monitor", "Live CPU, memory, and system activity in one widget."],
  ["System & Power", "systemtray", "System Tray", "Legacy X11 tray area for status icons, with takeover and release controls."],
  ["Launcher & Navigation", "telegram", "Telegram", "Keep Telegram signed in, see unread unmuted chat counts, and receive desktop notifications."],
  ["System & Power", "thermals", "Thermals", "Hottest sensor temperature, fan RPM tooltip, and Celsius/Fahrenheit toggle."],
  ["Information and Environment", "todayinhistory", "Today in History", "One notable event at a time with online refresh and offline fallback data."],
  ["System & Power", "trash", "Trash", "See whether the trash is full, open it, or empty it."],
  ["Time & Productivity", "unitconverter", "Unit Converter", "Convert between units directly from the dock."],
  ["Time & Productivity", "urlshortener", "URL Shortener", "Shorten URLs via is.gd and copy the result to the clipboard."],
  ["System & Power", "usbwatch", "USB Watch", "See mounted USB drives and safely eject them."],
  ["System & Power", "volume", "Volume", "Adjust system volume or mute audio."],
  ["Information and Environment", "weather", "Weather", "Forecast, conditions, air quality, and Celsius/Fahrenheit display at a glance."],
  ["Launcher & Navigation", "whatsapp", "WhatsApp", "Persistent WhatsApp Web with desktop notifications and an unread-message badge."],
  ["System & Power", "windowkiller", "Window Killer", "Click the applet, then click any window to force-close it."],
  ["Launcher & Navigation", "workspaces", "Workspaces", "Switch and track workspaces from the dock."],
];

const escapes = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const escapeHtml = (value) => value.replace(/[&<>"]/g, (character) => escapes[character]);

export function renderAppletCards() {
  return applets.map(([category, icon, title, description]) => `          <article class="rounded-[1.4rem] border ${colors[category]} p-5 shadow-soft backdrop-blur-2xl" data-applet-card data-category="${escapeHtml(category)}">
            <div class="flex items-start gap-3">
              <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-[1rem] border bg-white/[0.08] p-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.18)]">
                <img src="/icons/${escapeHtml(icon)}.png" alt="" width="32" height="32" class="h-full w-full object-contain" loading="lazy" decoding="async">
              </div>
              <div class="min-w-0">
                <h3 class="text-base font-semibold leading-6 text-white">${escapeHtml(title)}</h3>
                <p class="mt-1 text-xs font-medium text-slate-300">${escapeHtml(category)}</p>
              </div>
            </div>
            <p class="mt-4 text-sm leading-7 text-slate-300">${escapeHtml(description)}</p>
          </article>`).join("\n");
}
