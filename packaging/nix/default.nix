{ pkgs ? import <nixpkgs> { } }:

let
  pyPkgs = pkgs.python3Packages;
in
pyPkgs.buildPythonApplication rec {
  pname = "docking";
  version = "2.7.0";
  format = "pyproject";

  src = ../..;

  nativeBuildInputs = with pyPkgs; [
    setuptools
    wheel
    pkgs.gettext
    pkgs.gobject-introspection
    pkgs.wrapGAppsHook3
  ];

  buildInputs = with pkgs; [
    gtk3
    gtk-layer-shell
    libwnck
    networkmanager
    gdk-pixbuf
    pango
    cairo
    gst_all_1.gstreamer
    librsvg
  ];

  propagatedBuildInputs = with pyPkgs; [
    pycairo
    pygobject3
    pywayland
  ];

  # Weather client deps are not consistently available in nixpkgs channels.
  # Keep build reproducible in CI by removing them from Nix metadata.
  pythonRemoveDeps = [
    "openmeteo-requests"
    "requests-cache"
    "retry-requests"
  ];

  doCheck = false;

  preBuild = ''
    bash tools/i18n.sh --compile
  '';

  postInstall = ''
    mv "$out/bin/docking" "$out/bin/docking-real"
    cat > "$out/bin/docking" <<EOF
#!/bin/sh
set -eu
exec "$out/bin/docking-real" "\$@"
EOF
    chmod 0755 "$out/bin/docking"

    install -Dm644 ${../shared/org.docking.Docking.desktop} \
      "$out/share/applications/org.docking.Docking.desktop"
    substituteInPlace "$out/share/applications/org.docking.Docking.desktop" \
      --replace-fail "Exec=docking" "Exec=$out/bin/docking"

    install -Dm644 ${../shared/org.docking.camshield.policy} \
      "$out/share/polkit-1/actions/org.docking.camshield.policy"

    mkdir -p "$out/share/icons/hicolor"
    cp -a ${../deb/icons/hicolor}/. "$out/share/icons/hicolor/"

    install -Dm644 ${../../docking/platform/backends/gnome/extension/metadata.json} \
      "$out/share/gnome-shell/extensions/docking-bridge@docking.org/metadata.json"
    install -Dm644 ${../../docking/platform/backends/gnome/extension/extension.js} \
      "$out/share/gnome-shell/extensions/docking-bridge@docking.org/extension.js"
  '';

  meta = with pkgs.lib; {
    description = "A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo";
    homepage = "https://github.com/edumucelli/docking";
    license = licenses.gpl3Plus;
    platforms = platforms.linux;
    mainProgram = "docking";
  };
}
