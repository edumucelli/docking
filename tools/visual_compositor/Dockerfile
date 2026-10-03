# Lab image for visual testing of Docking against real Wayland compositors.
#
# The container only PRODUCES evidence: screenshot PNGs and JSON probed from the
# compositor. All comparison (geometry assertions, SSIM/PSNR against baselines)
# runs on the host, where the imaging stack already exists in the dev extra.
# That keeps this image free of Pillow/numpy/scikit-image.
#
# Targets are separate rather than one image because the compositors differ
# wildly in size -- Cinnamon alone pulls ~500 packages and ~1GB, which would make
# iterating on the sway lane unusable. Build the one you need:
#
#   docker build --target sway     -t docking-lab:sway     tools/visual_compositor
#   docker build --target cinnamon -t docking-lab:cinnamon tools/visual_compositor
#   docker build --target kwin     -t docking-lab:kwin     tools/visual_compositor
#   docker build --target labwc    -t docking-lab:labwc    tools/visual_compositor
#   docker build --target wayfire  -t docking-lab:wayfire  tools/visual_compositor
#   docker build --target cage     -t docking-lab:cage     tools/visual_compositor
#   docker build --target all      -t docking-lab:all      tools/visual_compositor
#
# tools/visual_compositor_matrix.sh does this for you.

# ---------------------------------------------------------------------------
# Stage: build pywayland from source.
#
# Debian's python3-pywayland ships only the core protocol bindings; Docking's
# Wayland runtime needs staging/unstable ones it does not include, and fails
# with ModuleNotFoundError: pywayland.protocol.ext_idle_notify_v1. That is
# exactly why packaging/deb/build-wayland-vendors.sh builds it with
# --no-binary=pywayland. This mirrors that script, and installs into the same
# /usr/lib/docking/vendor-python<minor> layout the real launcher uses, so source
# mode resolves imports the way the packaged build does.
# ---------------------------------------------------------------------------
ARG DEBIAN_IMAGE=debian:trixie@sha256:9cc080028c43b27d2074d63a5f9caf7166d731494965616c1a6d2827a004585c
ARG DEBIAN_SNAPSHOT=20261002T000000Z
FROM ${DEBIAN_IMAGE} AS pywayland-builder
ARG DEBIAN_SNAPSHOT
RUN sed -i -e "s|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/|g" \
    -e "s|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/|g" /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Check-Valid-Until "false";\n' >/etc/apt/apt.conf.d/99snapshot

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        python3-pip \
        libffi-dev \
        libwayland-dev \
        wayland-protocols \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade --ignore-installed \
        --break-system-packages pip \
    && python3 -m pip install --no-compile --no-binary=pywayland \
        --break-system-packages \
        --target="/vendors/vendor-python$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
        "pywayland==0.4.19" \
    && rm -rf /vendors/vendor-python*/bin

# The same import contract build-wayland-vendors.sh:19-20 enforces.
RUN PYTHONPATH="$(ls -d /vendors/vendor-python*)" python3 -c \
        "from pywayland.client import Display; from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1; from pywayland.protocol.ext_image_copy_capture_v1 import ExtImageCopyCaptureManagerV1"


# ---------------------------------------------------------------------------
# Stage: shared base. The GTK/GI stack Docking needs, capture tooling, and the
# session plumbing. No compositor here.
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS base
ARG DEBIAN_SNAPSHOT
RUN sed -i -e "s|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}/|g" \
    -e "s|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}/|g" /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Check-Valid-Until "false";\n' >/etc/apt/apt.conf.d/99snapshot

ENV DEBIAN_FRONTEND=noninteractive
# Mesa software rendering: correctness-representative, not performance-representative.
ENV LIBGL_ALWAYS_SOFTWARE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        grim \
        jq \
        dbus \
        dbus-x11 \
        libglib2.0-bin \
        python3 \
        python3-cffi-backend \
        python3-gi \
        python3-gi-cairo \
        gir1.2-gtk-3.0 \
        gir1.2-gdk-3.0 \
        gir1.2-gdkpixbuf-2.0 \
        gir1.2-gtklayershell-0.1 \
        gir1.2-wnck-3.0 \
        gir1.2-pango-1.0 \
        libwayland-client0 \
        libgl1-mesa-dri \
        mesa-utils \
        adwaita-icon-theme \
        hicolor-icon-theme \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pywayland from the builder, into the same layout the packaged launcher uses.
COPY --from=pywayland-builder /vendors/ /usr/lib/docking/

# Build-time self-check for the pieces every lane depends on. Each of these was
# a real gap found while writing the harness: `gdbus` comes from
# libglib2.0-bin (not pulled in by python3-gi), and Docking's Wayland protocol
# runtime needs pywayland. Failing here is far cheaper than failing 10 minutes
# into a container run.
RUN set -eu; \
    missing=""; \
    for tool in gdbus dbus-run-session grim jq python3; do \
        command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"; \
    done; \
    if [ -n "$missing" ]; then echo "missing tools:$missing" >&2; exit 1; fi; \
    python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk, Gdk, Gio; import cairo; print("gi stack ok")'; \
    python3 -c 'from gi.repository import GtkLayerShell; print("layer-shell GIR importable")' 2>/dev/null \
        || echo "note: GtkLayerShell GIR needs a display to use (expected)"; \
    vendor="$(ls -d /usr/lib/docking/vendor-python*)"; \
    PYTHONPATH="$vendor" python3 -c "from pywayland.client import Display; from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1; from pywayland.protocol.ext_foreign_toplevel_list_v1 import ExtForeignToplevelListV1; print('vendored pywayland ok')"

# wlroots compositors refuse to run as root. Create the same unprivileged user
# the packaged smoke test uses (packaging/deb/runtime-smoke.sh:102) so both lanes
# share one convention.
RUN useradd --create-home --shell /bin/bash docking-smoke

LABEL org.docking.purpose="headless Wayland compositor lab for visual testing"


# ---------------------------------------------------------------------------
# Target: sway -- wlroots, full layer-shell. The reference lane.
# ---------------------------------------------------------------------------
FROM base AS sway
RUN apt-get update && apt-get install -y --no-install-recommends sway \
    && rm -rf /var/lib/apt/lists/*
RUN command -v sway && command -v swaymsg


# ---------------------------------------------------------------------------
# Target: labwc -- wlroots, layer-shell.
# ---------------------------------------------------------------------------
FROM base AS labwc
RUN apt-get update && apt-get install -y --no-install-recommends labwc \
    && rm -rf /var/lib/apt/lists/*
RUN command -v labwc


# ---------------------------------------------------------------------------
# Target: wayfire -- wlroots plus its own IPC plugin.
# ---------------------------------------------------------------------------
FROM base AS wayfire
# Debian trixie ships wayfire 0.9.0. There is no wayfire-plugins-extra package
# in Debian at any version (checked), so the lane relies only on what the main
# package provides -- which is fine, because geometry here comes from pixels via
# grim rather than from wayfire's IPC.
RUN apt-get update && apt-get install -y --no-install-recommends wayfire \
    && rm -rf /var/lib/apt/lists/*
RUN command -v wayfire && wayfire --version


# ---------------------------------------------------------------------------
# Target: cage -- wlroots kiosk compositor. Kept as a NEGATIVE compatibility
# lane: Docking is expected to land on the documented `reduced` tier here, and
# the harness asserts that honestly rather than expecting placement.
# ---------------------------------------------------------------------------
FROM base AS cage
RUN apt-get update && apt-get install -y --no-install-recommends cage \
    && rm -rf /var/lib/apt/lists/*
RUN command -v cage


# ---------------------------------------------------------------------------
# Target: cinnamon -- Muffin nested inside Xvfb. The #347 lane.
#
# Xvfb is required because Muffin's --nested mode needs an outer display to nest
# into; Muffin itself then provides the Wayland server the dock connects to.
# ---------------------------------------------------------------------------
FROM base AS cinnamon
RUN apt-get update && apt-get install -y --no-install-recommends \
        cinnamon \
        muffin \
        xvfb \
        xauth \
    && rm -rf /var/lib/apt/lists/*
RUN command -v cinnamon && command -v muffin && command -v Xvfb
# --nested and --wayland come from Muffin's option context, which cinnamon adds
# to its own (cinnamon's src/main.c defines only --version). Assert they exist
# rather than discovering it 10 minutes into a run.
RUN cinnamon --help 2>&1 | grep -q -- '--nested' \
    && cinnamon --help 2>&1 | grep -q -- '--wayland'


# ---------------------------------------------------------------------------
# Target: kwin -- the virtual backend. No X, no GPU.
# ---------------------------------------------------------------------------
FROM sway AS kwin
RUN apt-get update && apt-get install -y --no-install-recommends \
        kwin-wayland at-spi2-core xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
# Only assert what is verifiable here. An earlier version of this target
# asserted `kwin_wayland --help | grep -q -- --virtual`, which fails for two
# independent reasons: Debian's binary carries cap_sys_nice=ep so direct
# execution is denied, and it does not implement --help at all (it tries to
# start). Binary string inspection was also unreliable across two attempts --
# Qt stores option names without the leading dashes.
#
# So: assert the files exist, and let the adapter determine viability by
# actually starting it and reporting the compositor's own error. That is a real
# answer rather than a proxy, and it is the same pattern used for niri/cosmic.
RUN setcap -r /usr/bin/kwin_wayland; test -x /usr/bin/kwin_wayland && test -x /usr/bin/kwin_wayland_wrapper


# ---------------------------------------------------------------------------
# Target: all -- every compositor, for a full matrix run.
# ---------------------------------------------------------------------------
FROM base AS all
RUN apt-get update && apt-get install -y --no-install-recommends \
        sway \
        labwc \
        wayfire \
        wayfire-plugins-extra \
        cage \
        cinnamon \
        muffin \
        xvfb \
        xauth \
        kwin-wayland at-spi2-core \
    && rm -rf /var/lib/apt/lists/*
RUN for tool in sway labwc wayfire cage cinnamon muffin kwin_wayland Xvfb; do \
        command -v "$tool" >/dev/null 2>&1 || { echo "missing $tool" >&2; exit 1; }; \
    done

# GNOME/Mutter without the Docking bridge: negative compatibility lane.
FROM cinnamon AS gnome
RUN apt-get update && apt-get install -y --no-install-recommends gnome-shell \
    && rm -rf /var/lib/apt/lists/*
RUN command -v gnome-shell

# Same pinned Shell image, with the bridge installed only into the run directory.
FROM gnome AS gnome-bridge
