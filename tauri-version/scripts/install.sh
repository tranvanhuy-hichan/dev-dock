#!/bin/bash
# Cài DevDock cho người dùng hiện tại (không cần root).
#   ./scripts/install.sh              cài / cập nhật (cần đã build: npm run app:build:bin)
#   ./scripts/install.sh --uninstall  gỡ (dữ liệu ở ~/.local/share/vn.makeai.devdock được giữ lại)
set -euo pipefail

APP_ID=vn.makeai.devdock
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.local/share/devdock"
LAUNCH="$HOME/.local/bin/devdock"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
ICONS="$HOME/.local/share/icons/hicolor"

refresh() {
  command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$LAUNCH" "$DESKTOP" "$ICONS/512x512/apps/$APP_ID.png" "$ICONS/128x128/apps/$APP_ID.png" "$ICONS/scalable/apps/$APP_ID.svg"
  rm -rf "$DEST"
  refresh
  echo "Đã gỡ DevDock (dữ liệu của bạn vẫn còn ở ~/.local/share/$APP_ID)."
  exit 0
fi

BIN="$SRC/target/release/devdock"
if [ ! -x "$BIN" ]; then
  echo "Chưa có bản build: $BIN" >&2
  echo "Chạy trước: npm run app:build:bin" >&2
  exit 1
fi

mkdir -p "$DEST" "$(dirname "$LAUNCH")" "$(dirname "$DESKTOP")" "$ICONS/512x512/apps" "$ICONS/128x128/apps" "$ICONS/scalable/apps"
install -m 755 "$BIN" "$DEST/devdock.new" && mv -f "$DEST/devdock.new" "$DEST/devdock"   # thay nguyên tử, không lỗi khi app đang chạy
install -m 755 "$SRC/scripts/clean-env.sh" "$DEST/clean-env.sh"
install -m 644 "$SRC/src-tauri/icons/icon.png" "$ICONS/512x512/apps/$APP_ID.png"
install -m 644 "$SRC/src-tauri/icons/128x128.png" "$ICONS/128x128/apps/$APP_ID.png"
install -m 644 "$SRC/assets/logo.svg" "$ICONS/scalable/apps/$APP_ID.svg"

# Trình chạy: gỡ biến của VS Code snap (nếu mở từ terminal của nó) rồi chạy app.
cat > "$LAUNCH" <<LAUNCHER
#!/bin/sh
export WEBKIT_DISABLE_DMABUF_RENDERER=1   # tránh cửa sổ trắng với card NVIDIA
exec bash "$DEST/clean-env.sh" "$DEST/devdock" "\$@"
LAUNCHER
chmod 755 "$LAUNCH"

cat > "$DESKTOP" <<ENTRY
[Desktop Entry]
Type=Application
Name=DevDock
GenericName=Developer Toolbox
Comment=Công cụ dev, giám sát hệ thống, snippet, Docker và dọn đĩa trong một app
Exec=$LAUNCH
Icon=$APP_ID
Terminal=false
Categories=Development;
Keywords=developer;docker;json;jwt;snippet;monitor;disk;cleaner;
StartupWMClass=devdock
ENTRY

refresh
echo "Đã cài DevDock. Mở từ menu ứng dụng hoặc chạy: devdock"
