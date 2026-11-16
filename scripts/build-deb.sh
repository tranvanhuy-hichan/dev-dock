#!/bin/bash
# Đóng gói DevDock thành file .deb (kiến trúc all, thuần Python) để đưa lên GitHub Releases.
#   scripts/build-deb.sh           → dist/devdock_<phiên bản>_all.deb
# Cài thử:  sudo apt install ./dist/devdock_<phiên bản>_all.deb      Gỡ:  sudo apt remove devdock
set -euo pipefail
cd "$(dirname "$0")/.."

APP_ID=vn.makeai.DevDock
VERSION="$(sed -n "s/^__version__ = '\(.*\)'/\1/p" devdock/__init__.py)"
[ -n "$VERSION" ] || { echo "Không đọc được phiên bản từ devdock/__init__.py" >&2; exit 1; }
PKG="devdock_${VERSION}_all"
OUT="dist/${PKG}.deb"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/pkg"
LIB="$ROOT/usr/lib/devdock"
MAINTAINER="tranvanhuy-hichan <tranvanhuy-hichan@users.noreply.github.com>"

mkdir -p "$ROOT/usr/share/man/man1" "$LIB/bin" "$LIB/data" "$ROOT/usr/bin" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/devdock" \
         "$ROOT/usr/share/icons/hicolor/512x512/apps" "$ROOT/usr/share/icons/hicolor/scalable/apps" "$ROOT/DEBIAN"

cp -r devdock "$LIB/devdock"
find "$LIB/devdock" -name __pycache__ -type d -prune -exec rm -rf {} +
cp -r data/icons "$LIB/data/icons"
install -m 755 bin/devdock "$LIB/bin/devdock"
ln -s ../lib/devdock/bin/devdock "$ROOT/usr/bin/devdock"
install -m 644 "data/$APP_ID.desktop" "$ROOT/usr/share/applications/$APP_ID.desktop"
install -m 644 "data/icons/hicolor/512x512/apps/$APP_ID.png" "$ROOT/usr/share/icons/hicolor/512x512/apps/$APP_ID.png"
install -m 644 "data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 644 README.md "$ROOT/usr/share/doc/devdock/README.md"
cat > "$ROOT/usr/share/doc/devdock/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: DevDock
Source: https://github.com/tranvanhuy-hichan/dev-dock

Files: *
Copyright: $(date +%Y) tranvanhuy-hichan
License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy of this
 software and associated documentation files (the "Software"), to deal in the Software
 without restriction, including without limitation the rights to use, copy, modify, merge,
 publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
 to whom the Software is furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all copies or
 substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
 PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
 FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
 OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 DEALINGS IN THE SOFTWARE.
COPY
chmod 644 "$ROOT/usr/share/doc/devdock/copyright"
cat <<'MAN' | gzip -9n > "$ROOT/usr/share/man/man1/devdock.1.gz"
.TH DEVDOCK 1 "November 2026" "DevDock" "User Commands"
.SH NAME
devdock \- developer hub (GTK4/libadwaita)
.SH SYNOPSIS
.B devdock
[\fB\-\-hidden\fR]
.br
.B devdock \-\-add\-services
[\fB\-\-all\fR] [\fB\-\-dry\-run\fR]
.SH DESCRIPTION
One window for projects, Git, dev services, Docker, system monitoring and more.
.SH OPTIONS
.TP
.B \-\-hidden
Start without showing the window when background mode is enabled (used by autostart).
.TP
.B \-\-add\-services
Add the dev servers found in your projects to Dev Services, then exit. Close DevDock first.
.SH FILES
.TP
.I ~/.config/devdock/
Settings and monitoring configuration.
.SH SEE ALSO
https://github.com/tranvanhuy-hichan/dev-dock
MAN
chmod 644 "$ROOT/usr/share/man/man1/devdock.1.gz"
printf 'devdock (%s) stable; urgency=low\n\n  * Bản phát hành %s.\n\n -- %s  %s\n' "$VERSION" "$VERSION" "$MAINTAINER" "$(date -R)" | gzip -9n > "$ROOT/usr/share/doc/devdock/changelog.gz"
chmod 644 "$ROOT/usr/share/doc/devdock/changelog.gz"
gzip -9nf "$ROOT/usr/share/doc/devdock/README.md"

# quyền: thư mục 755, file 644, trừ lệnh khởi động
find "$ROOT" -type d -exec chmod 755 {} +
find "$LIB" -type f -exec chmod 644 {} +
chmod 755 "$LIB/bin/devdock"

SIZE="$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)"
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: devdock
Version: $VERSION
Section: devel
Priority: optional
Architecture: all
Installed-Size: $SIZE
Maintainer: $MAINTAINER
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), python3-psutil
Recommends: git, python3-yaml, power-profiles-daemon, pkexec | policykit-1
Suggests: docker.io | docker-ce-cli, ollama
Homepage: https://github.com/tranvanhuy-hichan/dev-dock
Description: Trung tâm cho developer: project, Git, dev service, Docker, giám sát hệ thống
 DevDock là ứng dụng GTK4/libadwaita (GNOME) gom công cụ hằng ngày
 của developer vào một cửa sổ: project và Git, dev service và cổng
 mạng, file .env, log, Docker, database, API tester, quét bí mật
 (secrets), kiểm tra thư viện, RAM thật của từng tiến trình, giám sát
 và cảnh báo hệ thống, chế độ hiệu năng và quạt, dọn đĩa, quản lý
 model Ollama. Chạy hoàn toàn cục bộ.
CTRL
cat > "$ROOT/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  # biên dịch trước bytecode để khởi động nhanh (thư mục cài đặt không ghi được khi chạy bằng người dùng thường)
  python3 -m compileall -q /usr/lib/devdock/devdock >/dev/null 2>&1 || true
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
POST
cat > "$ROOT/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
case "$1" in remove|purge|upgrade|deconfigure) find /usr/lib/devdock -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true ;; esac
exit 0
PRERM
cat > "$ROOT/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
POSTRM
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/prerm" "$ROOT/DEBIAN/postrm"

mkdir -p dist
rm -f "$OUT"
fakeroot dpkg-deb --build --root-owner-group "$ROOT" "$OUT" >/dev/null
echo "Đã tạo: $(pwd)/$OUT ($(du -h "$OUT" | cut -f1))"
