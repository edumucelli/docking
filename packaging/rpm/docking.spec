Name:           docking
Version:        %{?pkg_version}%{!?pkg_version:1.9.10}
Release:        1%{?dist}
Summary:        A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo

License:        GPL-3.0-or-later
URL:            https://github.com/edumucelli/docking
Source0:        %{name}-%{version}.tar.gz

Requires:       python3
BuildRequires:  gettext

%description
Docking is a lightweight, feature-rich dock for Linux written in Python
with GTK 3 and Cairo. Inspired by Plank and Cairo-Dock, it provides
pinned launchers, window indicators, previews, autohide, drag-and-drop,
and an extensible applet system.

%prep
%autosetup -n %{name}-%{version}

%build
# No build step required; Python package install is done in %install.

%install
rm -rf %{buildroot}

bash tools/i18n.sh --compile

mkdir -p %{buildroot}/usr/lib/docking/python
python3 -m pip install --no-compile --no-deps \
  --target %{buildroot}/usr/lib/docking/python .
rm -rf %{buildroot}/usr/lib/docking/python/*.dist-info
rm -rf %{buildroot}/usr/lib/docking/python/bin

mkdir -p %{buildroot}/usr/lib/docking/vendor
python3 -m pip install --no-compile --target %{buildroot}/usr/lib/docking/vendor \
  openmeteo-requests requests-cache retry-requests
rm -rf %{buildroot}/usr/lib/docking/vendor/*.dist-info
rm -rf %{buildroot}/usr/lib/docking/vendor/bin

install -Dm755 /dev/stdin %{buildroot}/usr/bin/docking << 'EOF'
#!/bin/sh
set -eu
export PYTHONPATH="/usr/lib/docking/python:/usr/lib/docking/vendor${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m docking.app "$@"
EOF

install -Dm644 packaging/shared/org.docking.Docking.desktop \
  %{buildroot}/usr/share/applications/org.docking.Docking.desktop

install -Dm755 packaging/shared/refresh-desktop-caches.sh \
  %{buildroot}/usr/lib/docking/refresh-desktop-caches

if [ -d packaging/deb/icons/hicolor ]; then
  mkdir -p %{buildroot}/usr/share/icons/hicolor
  cp -a packaging/deb/icons/hicolor/. %{buildroot}/usr/share/icons/hicolor/
fi

%post
if [ -x /usr/lib/docking/refresh-desktop-caches ]; then
  /usr/lib/docking/refresh-desktop-caches
fi

%postun
if [ -x /usr/lib/docking/refresh-desktop-caches ]; then
  /usr/lib/docking/refresh-desktop-caches
fi

%files
%license LICENSE
/usr/bin/docking
/usr/lib/docking/python
/usr/lib/docking/vendor
/usr/lib/docking/refresh-desktop-caches
/usr/share/applications/org.docking.Docking.desktop
/usr/share/icons/hicolor

%changelog
* Fri Apr 10 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.10-1
- Release 1.9.10.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.9-1
- Release 1.9.9.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.8-1
- Release 1.9.8.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.7-1
- Release 1.9.7.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.6-1
- Release 1.9.6.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.5-1
- Release 1.9.5.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.4-1
- Release 1.9.4.

* Wed Apr 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.3-1
- Release 1.9.3.

* Tue Apr 07 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.9.2-1
- Release 1.9.2.

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.6.0-1
- Release 1.6.0.

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.5.0-1
- Release 1.5.0.

* Fri Apr 03 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.4.1-1
- Release 1.4.1.

* Thu Apr 02 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.4.0-1
- Release 1.4.0.

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.3.1-1
- Release 1.3.1.

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.3.0-1
- Release 1.3.0.

* Tue Mar 31 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.9-1
- Release 1.2.9.

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.8-1
- Release 1.2.8.

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.7-1
- Release 1.2.7.

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.6-1
- Release 1.2.6.

* Sun Mar 29 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.5-1
- Release 1.2.5.

* Sat Mar 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.4-1
- Release 1.2.4.

* Sat Mar 28 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.3-1
- Release 1.2.3.

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.2-1
- Release 1.2.2.

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.1-1
- Release 1.2.1.

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.2.0-1
- Release 1.2.0.

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.2-1
- Release 1.1.2.

* Fri Mar 27 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.1-1
- Release 1.1.1.

* Thu Mar 26 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.1.0-1
- Release 1.1.0.

* Thu Mar 26 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 1.0.0-1
- Release 1.0.0.

* Sat Mar 14 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.43-1
- Release 0.1.43.

* Sat Mar 14 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.42-1
- Release 0.1.42.

* Sat Mar 14 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.41-1
- Release 0.1.41.

* Wed Mar 11 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.40-1
- Release 0.1.40.

* Tue Mar 10 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.39-1
- Release 0.1.39.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.37-1
- Release 0.1.37.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.36-1
- Release 0.1.36.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.35-1
- Release 0.1.35.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.34-1
- Release 0.1.34.

* Sun Mar 08 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.33-1
- Release 0.1.33.

* Thu Mar 05 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.18-1
- Release 0.1.18.

* Sun Mar 01 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.1-1
- Initial RPM packaging.
