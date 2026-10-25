{ pkgs ? import <nixpkgs> { } }:

let
  pyPkgs = pkgs.python3Packages;
  # The CI channel predates the authentication SDKs required by Gmail. Package
  # the same upstream pure-Python wheels used by the other release formats,
  # retaining runtime dependency and import checks instead of relaxing bounds.
  googleAuth = pyPkgs.buildPythonPackage {
    pname = "google-auth";
    version = "2.49.1";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/e9/eb/c6c2478d8a8d633460be40e2a8a6f8f429171997a35a96f81d3b680dec83/google_auth-2.49.1-py3-none-any.whl";
      sha256 = "195ebe3dca18eddd1b3db5edc5189b76c13e96f29e73043b923ebcf3f1a860f7";
    };
    propagatedBuildInputs = with pyPkgs; [
      cryptography
      pyasn1-modules
      requests
    ];
    pythonImportsCheck = [ "google.auth" ];
  };
  googleAuthOauthlib = pyPkgs.buildPythonPackage {
    pname = "google-auth-oauthlib";
    version = "1.3.1";
    format = "wheel";
    src = pkgs.fetchurl {
      url = "https://files.pythonhosted.org/packages/2a/e0/cb454a95f460903e39f101e950038ec24a072ca69d0a294a6df625cc1627/google_auth_oauthlib-1.3.1-py3-none-any.whl";
      sha256 = "1a139ef23f1318756805b0e95f655c238bffd29655329a2978218248da4ee7f8";
    };
    propagatedBuildInputs = [ googleAuth pyPkgs.requests-oauthlib ];
    pythonImportsCheck = [ "google_auth_oauthlib.flow" ];
  };
in
pyPkgs.buildPythonApplication rec {
  pname = "docking";
  version = "2.27.0";
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
    webkitgtk_4_1
    librsvg
    libsecret
  ];

  propagatedBuildInputs = with pyPkgs; [
    pycairo
    pygobject3
    pywayland
    googleAuth
    googleAuthOauthlib
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
