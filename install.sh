#!/bin/bash
# Cài DevDock cho người dùng hiện tại (không cần root).
#   ./install.sh              cài / cập nhật
#   ./install.sh --uninstall  gỡ (cài đặt của bạn ở ~/.config/devdock được giữ lại)
set -euo pipefail

APP_ID=vn.makeai.DevDock
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/devdock"
BIN="$HOME/.local/bin/devdock"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
ICONS="$HOME/.local/share/icons/hicolor"

refresh() {
  command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
}

# Dọn bản cũ (bản Tauri/webview dùng id vn.makeai.devdock viết thường) để không trùng lệnh `devdock` và mục menu.
remove_legacy() {
  rm -f "$HOME/.local/share/applications/vn.makeai.devdock.desktop" \
        "$ICONS/512x512/apps/vn.makeai.devdock.png" "$ICONS/128x128/apps/vn.makeai.devdock.png" "$ICONS/scalable/apps/vn.makeai.devdock.svg" \
        "$DEST/devdock" "$DEST/devdock.new" "$DEST/clean-env.sh"
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN" "$DESKTOP" "$ICONS/512x512/apps/$APP_ID.png" "$ICONS/scalable/apps/$APP_ID.svg"
  rm -rf "$DEST"
  refresh
  echo "Đã gỡ DevDock (cài đặt của bạn vẫn còn ở ~/.config/devdock)."
  exit 0
fi

# Kiểm tra thư viện GTK trước khi cài để báo lỗi rõ ràng.
if ! /usr/bin/python3 -c "
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, Adw
import psutil
assert Adw.MINOR_VERSION >= 5 or Adw.MAJOR_VERSION > 1, 'libadwaita cũ'
" 2>/dev/null; then
  echo "Thiếu GTK4/libadwaita (>= 1.5) hoặc psutil. Cài bằng:" >&2
  echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil" >&2
  exit 1
fi

remove_legacy
mkdir -p "$DEST/bin" "$DEST/data" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$ICONS/512x512/apps" "$ICONS/scalable/apps"
rm -rf "$DEST/devdock" "$DEST/data/icons"
cp -r "$SRC/devdock" "$DEST/devdock"
find "$DEST/devdock" -name __pycache__ -type d -prune -exec rm -rf {} +
cp -r "$SRC/data/icons" "$DEST/data/icons"
install -m 755 "$SRC/bin/devdock" "$DEST/bin/devdock"
ln -sf "$DEST/bin/devdock" "$BIN"
install -m 644 "$SRC/data/icons/hicolor/512x512/apps/$APP_ID.png" "$ICONS/512x512/apps/$APP_ID.png"
install -m 644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ICONS/scalable/apps/$APP_ID.svg"
sed "s|^Exec=.*|Exec=$BIN|" "$SRC/data/$APP_ID.desktop" > "$DESKTOP"
chmod 644 "$DESKTOP"
refresh
echo "Đã cài DevDock. Mở từ menu ứng dụng ('DevDock') hoặc chạy: devdock"
