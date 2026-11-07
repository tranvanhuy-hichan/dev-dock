#!/bin/sh
# Chạy test giao diện thật (cần màn hình) trong môi trường sạch, tách biệt với cửa sổ/cấu hình thật.
#   scripts/ui-test.sh                  chạy toàn bộ
#   UI_ONLY=ui_git scripts/ui-test.sh   chỉ chạy tests/ui_git.py (nhiều file: ui_git,ui_db)
cd "$(dirname "$0")/.." || exit 1
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/local/bin DISPLAY="$DISPLAY" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" XDG_SESSION_TYPE="$XDG_SESSION_TYPE" UI_ONLY="$UI_ONLY" timeout 900 /usr/bin/python3 -B tests/ui_smoke.py
