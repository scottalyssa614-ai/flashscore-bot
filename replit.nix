{ pkgs }: {
  deps = [
    pkgs.gut
    pkgs.python311Full

    # Browser runtime deps
    pkgs.nspr
    pkgs.nss
    pkgs.glib
    pkgs.gtk3
    pkgs.pango
    pkgs.cairo
    pkgs.cups
    pkgs.dbus
    pkgs.at-spi2-atk
    pkgs.at-spi2-core
    pkgs.libdrm
    pkgs.mesa
    pkgs.libxkbcommon
    pkgs.alsa-lib
    pkgs.fontconfig
    pkgs.freetype

    # X11 libs
    pkgs.xorg.libX11
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXrandr
    pkgs.xorg.libxcb
    pkgs.xorg.libxshmfence
    pkgs.xorg.libXtst
  ];
}