#!/bin/sh
# Chụp ảnh các trang DevDock (tách biệt, không đụng cấu hình thật).
#   scripts/shot.sh dashboard,git:select 5000     -> ảnh ở $OUT (mặc định /tmp/devdock-shots)
#   NAME=x scripts/shot.sh ...                    -> thư mục riêng /tmp/devdock-shots_x
cd "$(dirname "$0")/.." || exit 1
B="${OUT:-/tmp/devdock-shots}${NAME:+_$NAME}"; mkdir -p "$B/img" "$B/cfg" "$B/data"
exec env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/local/bin DISPLAY="$DISPLAY" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
  DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" XDG_SESSION_TYPE="$XDG_SESSION_TYPE" XDG_CONFIG_HOME="$B/cfg" XDG_DATA_HOME="$B/data" \
  DEVDOCK_PLAN="$1" DEVDOCK_WAIT="${2:-2500}" DEVDOCK_SHOTS="$B/img" timeout 180 ./bin/devdock
