#!/usr/bin/env bash
# Chạy lệnh với môi trường đã gỡ biến của VS Code bản snap.
# Terminal tích hợp của VS Code (snap) để lại GSETTINGS_SCHEMA_DIR, GTK_PATH, LOCPATH… trỏ vào /snap/code,
# làm GTK abort ("Settings schema ... antialiasing") và WebKit lỗi ("__libc_pthread_init").
# Khôi phục giá trị gốc mà snap đã lưu lại
[ -n "$XDG_DATA_DIRS_VSCODE_SNAP_ORIG" ] && export XDG_DATA_DIRS="$XDG_DATA_DIRS_VSCODE_SNAP_ORIG"
[ -n "$XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG" ] && export XDG_CONFIG_DIRS="$XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG"
for v in $(env | grep -oE '^(SNAP[A-Z_]*|GTK_PATH|GTK_EXE_PREFIX|GTK_IM_MODULE_FILE|GTK_MODULES|GIO_MODULE_DIR|GIO_LAUNCHED_[A-Z_]+|GDK_PIXBUF_[A-Z_]+|LOCPATH|GSETTINGS_SCHEMA_DIR|XDG_DATA_HOME|XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG)=' | tr -d '='); do
  unset "$v"
done
exec "$@"
