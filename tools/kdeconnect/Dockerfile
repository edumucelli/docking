FROM debian:13
RUN apt-get update && apt-get install -y --no-install-recommends kdeconnect dbus-x11 xvfb xauth python3 python3-gi python3-gi-cairo python3-cairo python3-pytest python3-psutil gir1.2-gtk-3.0 gir1.2-wnck-3.0 gettext && rm -rf /var/lib/apt/lists/*
ENV QT_QPA_PLATFORM=offscreen
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
