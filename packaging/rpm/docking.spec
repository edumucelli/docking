Name:           docking
Version:        %{?pkg_version}%{!?pkg_version:0.1.1}
Release:        1%{?dist}
Summary:        Lightweight Linux dock inspired by Plank and Cairo-Dock

License:        GPL-3.0-or-later
URL:            https://github.com/edumucelli/docking
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3

%description
Docking is a lightweight, feature-rich dock for Linux written in Python
with GTK 3 and Cairo. It provides pinned launchers, window indicators,
previews, autohide, drag-and-drop, and an extensible applet system.

%prep
%autosetup -n %{name}-%{version}

%build
# No build step required; Python package install is done in %install.

%install
rm -rf %{buildroot}

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

install -Dm644 packaging/deb/org.docking.Docking.desktop \
  %{buildroot}/usr/share/applications/org.docking.Docking.desktop

if [ -d packaging/deb/icons/hicolor ]; then
  mkdir -p %{buildroot}/usr/share/icons/hicolor
  cp -a packaging/deb/icons/hicolor/. %{buildroot}/usr/share/icons/hicolor/
fi

%files
%license LICENSE
/usr/bin/docking
/usr/lib/docking/python
/usr/lib/docking/vendor
/usr/share/applications/org.docking.Docking.desktop
/usr/share/icons/hicolor

%changelog
* Sun Mar 01 2026 Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com> - 0.1.1-1
- Initial RPM packaging.
