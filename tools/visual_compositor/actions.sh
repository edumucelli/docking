# Real input and output changes, with intermediate captures for host assertions.
capture_phase() { capture_until_stable "$EVIDENCE_DIR/$name.$1.png"; }

run_lab_action() {
    local action="$1" edge="$2" geometry x y width height ax ay anchor
    if [ "$action" = stack ]; then
        export LAB_STACK_CASE="$name"
        PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 \
            "$LAB_SCRIPTS/probes/stack_action.py"
        return
    fi
    geometry="$(adapter_geometry | jq '.outputs[0]')"
    x="$(echo "$geometry" | jq '.x')"; y="$(echo "$geometry" | jq '.y')"
    width="$(echo "$geometry" | jq '.width')"; height="$(echo "$geometry" | jq '.height')"
    lab_pointer "$((x+width/2))" "$((y+height/2))"
    sleep 1.5
    if [ "$action" = window-switching ]; then
        PYTHONPATH="$(docking_source_pythonpath)" /usr/bin/python3 \
            "$LAB_SCRIPTS/probes/cinnamon_window_probe.py" "$EVIDENCE_DIR" "$name"
    elif [ "$action" = autohide ]; then
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
