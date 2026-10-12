#!/bin/bash
# The complete Phosh shell, distinct from bare Phoc: shell protocol ownership
# and real panel reservations can change which services a second client gets.
source "$LAB_SCRIPTS/adapters/phoc.sh"

adapter_start() {
    # The shell must retain its private singleton even when a developer left
    # the bare-compositor diagnostic opt-in exported in the parent terminal.
    unset DOCKING_PHOC_PRIVATE_PREVIEWS
    local shell
    shell="$(command -v phosh || true)"
    if [ -z "$shell" ]; then shell=/usr/libexec/phosh; fi
    [ -x "$shell" ] || { log_adapter "Phosh shell executable missing"; return 1; }
    phoc "${PHOC_LAB_ARGS[@]}" -S -C "$XDG_CONFIG_HOME/phoc.ini" -E "$shell -U" \
        >"$COMPOSITOR_LOG" 2>&1 &
    ADAPTER_COMPOSITOR_PID=$!
    wait_for_wayland_socket 80
}

adapter_wait_ready() {
    # Screenshot success alone would race the shell's private protocol bind.
    for _ in $(seq "${1:-80}"); do
        kill -0 "$ADAPTER_COMPOSITOR_PID" 2>/dev/null || { report_compositor_failure; return 1; }
        if gdbus call --session --dest org.freedesktop.DBus \
            --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.GetNameOwner \
            org.gnome.Shell >/dev/null 2>&1; then
            sleep 2
            return 0
        fi
        sleep 0.25
    done
    report_compositor_failure
    log_adapter "Phosh shell did not become ready"
    return 1
}

adapter_capabilities() {
    session_probe_json | jq '{compositor:"phosh", expected_backend:"wayland-layer-shell",
        placement:.layer_shell_supported, native_geometry:false, pointer:false,
        screenshot_method:"grim", gtk_display_is_wayland, layer_shell_supported}'
}
