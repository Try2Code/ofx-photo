#!/bin/sh
# Register Spektra Photo with the desktop: menu entry, icon, and "Open With".
# Everything goes under $HOME, so no root is needed and nothing is installed
# system-wide.  Run with --uninstall to take it all back out.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
ICONBASE=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor
ICONS=$ICONBASE/scalable/apps
DESKTOP=$APPS/ofx-photo.desktop
ICON=$ICONS/ofx-photo.svg
SIZES="16 24 32 48 64 128 256"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DESKTOP" "$ICON"
  for s in $SIZES; do rm -f "$ICONBASE/${s}x${s}/apps/ofx-photo.png"; done
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

# Bitmap fallbacks alongside the scalable one: some panels and file managers
# still prefer a PNG, and these are pre-rendered so installing needs no SVG
# renderer.
for s in $SIZES; do
  if [ -f "$HERE/desktop/icons/$s/ofx-photo.png" ]; then
    mkdir -p "$ICONBASE/${s}x${s}/apps"
    cp "$HERE/desktop/icons/$s/ofx-photo.png" "$ICONBASE/${s}x${s}/apps/ofx-photo.png"
  fi
done

# Absolute paths: the launcher must work whatever the working directory is.
sed -e "s|@EXEC@|$HERE/spektra-gui|" -e "s|@ICON@|ofx-photo|" \
    "$HERE/desktop/ofx-photo.desktop.in" > "$DESKTOP"
chmod 644 "$DESKTOP"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -qtf "$ICONBASE" 2>/dev/null || true

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DESKTOP" && echo "Desktop entry validates."
fi

echo "Installed: $DESKTOP"
echo "Icon     : $ICON"
echo "Bitmaps  : $ICONBASE/<size>/apps/ofx-photo.png  ($SIZES)"
echo
echo "It should appear in your applications menu as 'Spektra Photo', and in the"
echo "'Open With' list for photos.  To make it the default for a type:"
echo "  xdg-mime default ofx-photo.desktop image/x-nikon-nef"
