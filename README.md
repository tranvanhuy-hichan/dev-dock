# DevDock

Trung tâm cho developer, viết bằng **Python + GTK4 + libadwaita** (giao diện GNOME native, cùng công nghệ với Disk Cleaner).

| Module | Làm gì |
|---|---|
| **Projects** | Quét `~/Projects`: mọi repo cùng nhánh, số file chưa commit, commit chưa push / cần pull, commit cuối, dung lượng thư mục build. Mở nhanh VS Code / terminal / thư mục, `git fetch`, dọn `node_modules`/`target`/`.venv` bằng một nút. |
| **Dev Services** | Khai báo server dev (`npm run dev`, `cargo run`, `docker compose up`…): bật/tắt/chạy lại, xem log trực tiếp, thấy cổng nào đang bị process khác chiếm và giải phóng. Tự gợi ý lệnh từ `package.json`/`Cargo.toml`/compose. |
| **Env Manager** | Mọi file `.env*` trong project: xem/sửa biến (giá trị bí mật được che), so sánh giữa các file, cảnh báo thiếu biến so với `.env.example`, thêm biến thiếu. Sửa file giữ nguyên comment và thứ tự. |
| **Log Viewer** | Log trực tiếp từ container Docker, dev service, journal và file; lọc chữ/regex, lọc theo mức (lỗi/cảnh báo/info/debug, nhận cả log JSON), tô màu, tạm dừng, lưu. |
| **SysMonitor** | CPU/RAM/GPU NVIDIA/mạng realtime, process (dừng process), cổng mạng, ổ đĩa, thống kê container. |
| **DockerDesk** | Container (start/stop/restart/xoá, xem log, mở shell), image (pull/xoá), Docker Compose (tự tìm file trong project). |
| **DiskCleaner** | Tìm và dọn rác: thư mục build, cache, Docker, tệp lớn, tệp trùng, apt/journal. Treemap dung lượng, xuất CSV. Lõi quét dùng chung với app Disk Cleaner. |

Phím tắt: `Ctrl+1..7` đổi module · `Ctrl+K` lệnh nhanh · `Ctrl+Q` thoát. Giao diện sáng/tối/theo hệ thống ở menu.

## Cài đặt
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil   # nếu thiếu
./install.sh              # cài cho user hiện tại, không cần root
./install.sh --uninstall  # gỡ
```
Sau đó mở **DevDock** từ menu ứng dụng hoặc chạy `devdock`. Chạy thẳng từ mã nguồn: `./bin/devdock`.
(Bộ khởi chạy tự gỡ biến môi trường của VS Code bản snap — nếu không, GTK sẽ lỗi khi mở từ terminal của nó.)

## An toàn
- **Projects**: git chạy với `core.fsmonitor=false` và hook tắt, nên repo lạ không chạy được lệnh qua `.git/config`.
- **DiskCleaner**: quét chỉ đọc; xoá phải qua hộp xác nhận; từ chối mọi đường dẫn ngoài `$HOME`; lệnh root/Docker là lệnh cố định.
- **Env Manager**: bí mật che mặc định, không ghi log giá trị; ghi file nguyên tử và giữ quyền file.
- **Dev Services**: chạy trong session riêng, dừng cả cây tiến trình; tự dừng khi đóng DevDock.

## Phát triển
```
devdock/lib/      logic thuần Python, test độc lập (projects, services, envfiles, logsrc, dockercli, sysmon)
devdock/pages/    giao diện từng module (GTK4/libadwaita)
devdock/cleaner/  lõi quét của Disk Cleaner (scanner, treemap, ring)
tests/            test_*.py (unit)   ui_smoke.py (điều khiển giao diện thật, cần màn hình)
tauri-version/    bản cũ viết bằng Tauri (webview) — giữ để tham khảo
```
```bash
python3 -m unittest discover -s tests -v    # unit test
python3 tests/ui_smoke.py                   # test giao diện thật (cần màn hình, dùng thư mục tạm)
DEVDOCK_SHOTS=/tmp/shots DEVDOCK_PLAN=projects,env ./bin/devdock   # chụp ảnh trang rồi thoát
```
Cài đặt lưu ở `~/.config/devdock/settings.json`.
