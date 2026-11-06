# DevDock

App desktop native (Tauri 2 + Rust + React/TypeScript) gồm 5 module:

| Module | Chức năng |
|---|---|
| **DevDock Tools** | JSON/XML formatter, JWT decoder (+verify HMAC), Base64, Hash (MD5/SHA/HMAC, file), Regex tester, UUID v4/v7/ULID, Cron parser, URL encode/parse, Timestamp |
| **SysMonitor** | CPU/RAM/GPU (NVIDIA)/network realtime, process manager (dừng process), port inspector, ổ đĩa, thống kê container Docker |
| **CodeVault** | Snippet manager (SQLite), CodeMirror highlight, tìm kiếm, thư mục/tag/yêu thích, đồng bộ GitHub Gist |
| **DiskCleaner** | Quét & dọn rác trên đĩa: thư mục build (`node_modules`, `target`, `.venv`…), cache, Docker, tệp lớn, tệp trùng lặp, apt/journal (qua polkit); treemap dung lượng, xuất CSV, ẩn mục, thống kê |
| **DockerDesk** | Containers (start/stop/restart/xoá, logs), Images (pull/xoá), Docker Compose, terminal tích hợp (shell máy + exec vào container) |

Phím tắt: `Ctrl+K` command palette · `Ctrl+1..5` đổi module · `Ctrl+S` lưu snippet.

## Yêu cầu (Ubuntu/Debian)
```bash
sudo apt install build-essential pkg-config libwebkit2gtk-4.1-dev libxdo-dev libssl-dev libdbus-1-dev librsvg2-dev
# Node >= 20, Rust (rustup), Docker (tuỳ chọn, cho DockerDesk)
npm install
```

## Chạy / build / cài
```bash
npm run app            # dev (hot reload)
npm run app:build:bin  # build bản release (target/release/devdock)
npm run install:app    # cài cho user: ~/.local/bin/devdock + mục menu "DevDock" + icon (không cần root)
bash scripts/install.sh --uninstall   # gỡ (dữ liệu được giữ lại)
npm run app:build      # (tuỳ chọn) đóng gói .deb / .AppImage / .rpm
```
> `npm run app` đi qua `scripts/clean-env.sh` để gỡ biến môi trường của **VS Code bản snap**
> (nếu không, GTK/WebKit sẽ lỗi khi chạy từ terminal tích hợp). Chạy ở terminal thường thì dùng `npm run tauri dev` cũng được.
> DockerDesk cần user thuộc nhóm `docker`.

## Cấu trúc
```
crates/core   logic thuần Rust, test độc lập:  cargo test -p devdock-core
  sysmon.rs   sysinfo + listeners + nvidia-smi
  vault.rs    SQLite + GitHub Gist (reqwest)
  docker.rs   bollard + `docker compose`
  pty.rs      portable-pty cho terminal
  cleaner.rs  port của Disk Cleaner (scanner Python → Rust), có test an toàn khi xoá
src-tauri     Tauri commands (wrapper mỏng) + cấu hình
src           React UI, mỗi module một thư mục trong src/modules
```
### DiskCleaner — an toàn khi xoá
- Quét chỉ đọc. Lệnh dọn **chỉ nhận id** rồi tra trong kết quả quét lưu ở backend (giao diện không gửi được đường dẫn tuỳ ý).
- Từ chối mọi đường dẫn ngoài `$HOME`, đường dẫn có `..`, hoặc đi qua symlink thoát khỏi home. Docker/root chỉ chạy các lệnh cố định.
- Cache/build xoá vĩnh viễn; tệp lớn & tệp trùng mặc định vào thùng rác (`gio trash`).
- So với bản Python gốc: chưa có khay hệ thống / tự khởi động / nhắc dọn định kỳ (app Disk Cleaner gốc vẫn dùng được song song).

Dữ liệu (snippets, token Gist, danh sách compose) lưu ở `~/.local/share/vn.makeai.devdock/vault.db` (kèm cài đặt DiskCleaner).
Token Gist lưu dạng thường trong DB cục bộ (chưa dùng keyring của OS).
