"""NetBSD native suspend and power-off commands."""

SUSPEND_COMMAND = ("sysctl", "-w", "hw.acpi.sleep.state=3")
POWEROFF_COMMAND = ("shutdown", "-p", "now")
