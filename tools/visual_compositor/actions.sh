# Real input and output changes, with intermediate captures for host assertions.
capture_phase() { capture_until_stable "$EVIDENCE_DIR/$name.$1.png"; }

run_lab_action() {
    local action="$1" edge="$2" geometry x y width height ax ay anchor
    if [[ "$action" = popup-* ]]; then
        run_popup_action "${action#popup-}"
        return
    fi
    if [[ "$action" = window-actions || "$action" = workspace-switch || "$action" = bridge-recovery ]]; then
        PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 \
            "$LAB_SCRIPTS/probes/backend_actions.py" "$action" "$name"
        return
    fi
    if [ "$action" = window ]; then
        # Exercise native toplevel events independently of dodge support.
        record_window_items before || return 1
        local lifecycle_ok=true
        /usr/bin/python3 "$LAB_SCRIPTS/probes/client_probe.py" overlap /tmp/unused \
            >"$LAB_DIR/$name.window.log" 2>&1 &
        ADAPTER_PROBE_PID=$!
        wait_window_item true || lifecycle_ok=false
        record_window_items opened || lifecycle_ok=false
        local tracked=false
        for _ in $(seq 40); do
            gdbus call --session --dest org.docking.Docking --object-path /org/docking/Docking \
                --method org.docking.Docking.Items1.ListTransientIds \
                >"$EVIDENCE_DIR/$name.opened.items.txt"
            if grep -Fq "'lab-probe.desktop'" "$EVIDENCE_DIR/$name.opened.items.txt"; then
                tracked=true
                break
            fi
            sleep 0.25
        done
        terminate_pid "$ADAPTER_PROBE_PID"
        ADAPTER_PROBE_PID=""
        wait_window_item false || lifecycle_ok=false
        record_window_items closed || lifecycle_ok=false
        [ "$tracked" = true ] && [ "$lifecycle_ok" = true ]
        return
    fi
    geometry="$(adapter_geometry | jq '.outputs[0]')"
    x="$(echo "$geometry" | jq '.x')"; y="$(echo "$geometry" | jq '.y')"
    width="$(echo "$geometry" | jq '.width')"; height="$(echo "$geometry" | jq '.height')"
    lab_pointer "$((x+width/2))" "$((y+height/2))"
    sleep 1.5
    if [ "$action" = autohide ]; then
        # Exercise a real enter/leave transition before measuring hiding.
        case "$edge" in
            bottom) lab_pointer "$((x+width/2))" "$((y+height-1))" ;;
            top) lab_pointer "$((x+width/2))" "$y" ;;
            left) lab_pointer "$x" "$((y+height/2))" ;;
            right) lab_pointer "$((x+width-1))" "$((y+height/2))" ;;
        esac
        sleep 1
        lab_pointer "$((x+width/2))" "$((y+height/2))"
        sleep 1.5
        capture_phase hidden
        case "$edge" in
            bottom) lab_pointer "$((x+width/2))" "$((y+height-1))" ;;
            top) lab_pointer "$((x+width/2))" "$y" ;;
            left) lab_pointer "$x" "$((y+height/2))" ;;
            right) lab_pointer "$((x+width-1))" "$((y+height/2))" ;;
        esac
        sleep 1.5
        capture_phase revealed
        lab_pointer "$((x+width/2))" "$((y+height/2))"
        sleep 1.5
        capture_phase hidden-again
        case "$edge" in
            bottom) lab_pointer "$((x+width/2))" "$((y+height-1))" ;;
            top) lab_pointer "$((x+width/2))" "$y" ;;
            left) lab_pointer "$x" "$((y+height/2))" ;;
            right) lab_pointer "$((x+width-1))" "$((y+height/2))" ;;
        esac
        sleep 1
    elif [ "$action" = dodge ]; then
        capture_phase revealed
        /usr/bin/python3 "$LAB_SCRIPTS/probes/client_probe.py" overlap /tmp/unused \
            >"$LAB_DIR/$name.overlap.log" 2>&1 &
        ADAPTER_PROBE_PID=$!
        sleep 1
        lab_pointer "$((x+width/2))" "$((y+height/2))" 272
        sleep 1.5
        capture_phase hidden
        terminate_pid "$ADAPTER_PROBE_PID"
        ADAPTER_PROBE_PID=""
        sleep 1.5
        capture_phase restored
    else
        capture_phase resting
        anchor="$(gdbus call --session --dest org.docking.Docking \
            --object-path /org/docking/Docking \
            --method org.docking.Docking.Items1.GetHoverAnchor lab-alpha.desktop)"
        read -r ax ay < <(/usr/bin/python3 -c \
            'import re,sys; print(*re.findall(r"-?[0-9]+",sys.argv[1])[:2])' "$anchor")
        [ -n "$ax" ] && [ -n "$ay" ] || return 1
        # GetHoverAnchor is the icon's outer corner, not its hit-test center.
        # These interaction cases use a bottom dock with 48-pixel icons.
        ax=$((ax+24)); ay=$((ay+24))
        lab_pointer "$ax" "$ay"
        sleep 0.2
        # Deliver motion after the enter event, as a user moving onto an icon does.
        lab_pointer "$((ax+2))" "$ay"
        if [ "$action" = menu ]; then lab_pointer "$ax" "$ay" 273
        else lab_pointer "$ax" "$ay"; fi
        sleep 1.5
        capture_phase effect
    fi
}

app_probe() {
    /usr/bin/python3 "$LAB_SCRIPTS/probes/app_probe.py" --call "$LAB_APP_SOCKET" "$1"
}

run_popup_action() {
    local kind="$1" target ax ay popup_ready=false
    target="$(echo "$case_json" | jq -r 'if .popup_target == "last" then .overrides.pinned[-1] else .overrides.pinned[0] end')"
    if [ "$kind" = preview ]; then
        target=lab-probe.desktop
        /usr/bin/python3 "$LAB_SCRIPTS/probes/client_probe.py" overlap /tmp/unused >"$EVIDENCE_DIR/$name.client.log" 2>&1 &
        ADAPTER_PROBE_PID=$!
        for _ in $(seq 40); do
            if app_probe '{}' | jq -e '.windows | length > 0' >/dev/null; then break; fi
            sleep 0.25
        done
        app_probe '{"action":"minimize"}' >"$EVIDENCE_DIR/$name.minimize.json"
        sleep 1
    fi
    lab_pointer 100 100
    sleep 0.5
    capture_phase resting || return 1
    read -r ax ay < <(app_probe "{\"desktop_id\":\"$target\"}" | jq -r '.pointer | @tsv')
    [ -n "$ax" ] && [ -n "$ay" ] || return 1
    lab_pointer "$ax" "$ay"
    sleep 0.2
    lab_pointer "$((ax+1))" "$ay"
    lab_pointer "$ax" "$ay"
    if [ "$kind" = menu ]; then lab_pointer "$ax" "$ay" 273; fi
    for _ in $(seq 40); do
        app_probe "{\"popup_kind\":\"$kind\",\"capture_popup\":\"$EVIDENCE_DIR/$name.popup-template.png\"}" >"$EVIDENCE_DIR/$name.popup.json"
        if jq -e --arg kind "$kind" '.popups | any(.[]; if $kind == "preview" then .class == "PreviewPopup" else (.class != "PreviewPopup" and .kind == (if $kind == "menu" then "popup-menu" else "tooltip" end)) end)' \
            "$EVIDENCE_DIR/$name.popup.json" >/dev/null; then popup_ready=true; break; fi
        sleep 0.25
    done
    capture_phase effect || return 1
    if [ -n "$ADAPTER_PROBE_PID" ]; then terminate_pid "$ADAPTER_PROBE_PID"; ADAPTER_PROBE_PID=""; fi
    [ "$popup_ready" = true ]
}

record_window_items() {
    adapter_windows >"$EVIDENCE_DIR/$name.$1.windows.json"
}

wait_window_item() {
    local expected="$1" result present
    for _ in $(seq 40); do
        result="$(adapter_windows)" || return 1
        present="$(echo "$result" | jq 'any(.[]; .title == "Lab probe")')"
        if [ "$present" = "$expected" ]; then return 0; fi
        sleep 0.25
    done
    log_adapter "test window tracking did not become $expected: $result"
    return 1
}
