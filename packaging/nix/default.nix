{ pkgs ? import <nixpkgs> { } }:

let
  pyPkgs = pkgs.python3Packages;
in
pyPkgs.buildPythonApplication rec {
  pname = "docking";
  version = "1.2.3";
  format = "pyproject";

  src = ../..;

  nativeBuildInputs = with pyPkgs; [
    setuptools
    wheel
    pkgs.gettext
  ];

  buildInputs = with pkgs; [
    gtk3
    libwnck
    networkmanager
    gdk-pixbuf
    pango
    cairo
    gobject-introspection
    gst_all_1.gstreamer
    librsvg
  ];

  propagatedBuildInputs = with pyPkgs; [
    pycairo
    pygobject3
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
    install -Dm644 ${../shared/org.docking.Docking.desktop} \
      "$out/share/applications/org.docking.Docking.desktop"
    substituteInPlace "$out/share/applications/org.docking.Docking.desktop" \
      --replace-fail "Exec=docking" "Exec=$out/bin/docking"

    mkdir -p "$out/share/icons/hicolor"
    cp -a ${../deb/icons/hicolor}/. "$out/share/icons/hicolor/"
  '';

  meta = with pkgs.lib; {
    description = "A lightweight, feature-rich dock for Linux written in Python with GTK 3 and Cairo";
    homepage = "https://github.com/edumucelli/docking";
    license = licenses.gpl3Plus;
    platforms = platforms.linux;
    mainProgram = "docking";
  };
}
