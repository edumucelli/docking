set pagination off
set debuginfod enabled off
set $node = (void **) ((void * (*)()) gtk_window_list_toplevels)()
while $node != 0
    set $window = $node[0]
    printf "GTK window=%p title=%s visible=%d mapped=%d realized=%d allocated=%dx%d\n", $window, (char *) gtk_window_get_title($window), (int) gtk_widget_get_visible($window), (int) gtk_widget_get_mapped($window), (int) gtk_widget_get_realized($window), (int) gtk_widget_get_allocated_width($window), (int) gtk_widget_get_allocated_height($window)
    set $gwindow = (void *) gtk_widget_get_window($window)
    if $gwindow != 0
        printf "GDK window=%p xid=%lu visible=%d viewable=%d state=%u\n", $gwindow, (unsigned long) gdk_x11_window_get_xid($gwindow), (int) gdk_window_is_visible($gwindow), (int) gdk_window_is_viewable($gwindow), (unsigned int) gdk_window_get_state($gwindow)
        set $clock = (void *) gtk_widget_get_frame_clock($window)
        if $clock != 0
            printf "GDK frame clock=%p counter=%lld time=%lld\n", $clock, (long long) gdk_frame_clock_get_frame_counter($clock), (long long) gdk_frame_clock_get_frame_time($clock)
        end
    end
    set $node = (void **) $node[1]
end
thread apply all bt
