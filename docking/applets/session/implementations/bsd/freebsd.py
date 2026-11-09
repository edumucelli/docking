"""FreeBSD native suspend and power-off commands."""

SUSPEND_COMMAND = ("acpiconf", "-s", "3")
POWEROFF_COMMAND = ("shutdown", "-p", "now")
