#!/bin/sh
# Register Spektra Photo with the desktop: menu entry, icon, and "Open With".
# Everything goes under $HOME, so no root is needed and nothing is installed
# system-wide.  Run with --uninstall to take it all back out.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
ICONS=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps
DESKTOP=$APPS/ofx-photo.desktop
ICON=$ICONS/ofx-photo.svg

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DESKTOP" "$ICON"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
  echo "Removed the menu entry and icon."
  exit 0
fi

if [ ! -x "$HERE/spektra-gui" ]; then
  echo "install-desktop.sh: $HERE/spektra-gui is missing or not executable" >&2
  exit 1
fi
if [ ! -x "$HERE/build/spektra-render" ]; then
  echo "install-desktop.sh: build the host first:" >&2
  echo "  cmake -S '$HERE' -B '$HERE/build' && cmake --build '$HERE/build'" >&2
  exit 1
fi

mkdir -p "$APPS" "$ICONS"
cp "$HERE/desktop/ofx-photo.svg" "$ICON"

# Absolute paths: the launcher must work whatever the working directory is.
sed -e "s|@EXEC@|$HERE/spektra-gui|" -e "s|@ICON@|ofx-photo|" \
    "$HERE/desktop/ofx-photo.desktop.in" > "$DESKTOP"
chmod 644 "$DESKTOP"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -qtf "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DESKTOP" && echo "Desktop entry validates."
fi

echo "Installed: $DESKTOP"
echo "Icon     : $ICON"
echo
echo "It should appear in your applications menu as 'Spektra Photo', and in the"
echo "'Open With' list for photos.  To make it the default for a type:"
echo "  xdg-mime default ofx-photo.desktop image/x-nikon-nef"
