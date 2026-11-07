# DevDock

Trung tâm cho developer, viết bằng **Python + GTK4 + libadwaita** (giao diện GNOME native, cùng công nghệ với Disk Cleaner).

| Nhóm | Module | Làm gì |
|---|---|---|
| Trang chủ | **Tổng quan** | Dashboard mở đầu tiên: lời chào + đồng hồ, gauge CPU/RAM/ổ đĩa/GPU, mục "Cần chú ý" (RAM/ổ đĩa đầy, service lỗi, repo chưa push, container lỗi, cảnh báo giám sát), project gần đây, dev service bật/tắt nhanh, Docker/Ollama, công tắc tự khởi động. |
| Project | **Projects** | Quét `~/Projects`: mọi repo cùng nhánh, thay đổi chưa commit, commit chưa push/cần pull, dung lượng build; mở VS Code/terminal, `git fetch`, dọn `node_modules`/`target`/`.venv`. |
| | **Secrets** | Quét mọi repo tìm khoá API/token (AWS, GitHub, Stripe, OpenAI/Anthropic, private key, mật khẩu trong URL, chuỗi gán cứng entropy cao) và file `.env` đã commit / chưa nằm trong `.gitignore` / từng bị commit trong lịch sử. Giá trị luôn được che; thêm vào `.gitignore` bằng một nút; "Bỏ qua" hoặc `devdock:ignore` cuối dòng cho mục giả. Kết quả hiện ở "Cần chú ý" của Tổng quan. |
| | **Git** | Stage/unstage, diff tô màu, commit (và commit + push), pull `--ff-only`, push (không force), lịch sử có tìm kiếm, nhánh (checkout/tạo/xoá nhánh đã merge), stash, "Push tất cả" các repo chưa push. |
| | **Dev Services** | Khai báo server dev, bật/tắt, log trực tiếp, xung đột cổng. **Tìm trong project** tự phát hiện dev server (npm, compose, Makefile, FastAPI, Spring…). |
| | **Env Manager** | Mọi file `.env*`: xem/sửa (che bí mật), so sánh, cảnh báo thiếu biến so với `.env.example`. |
| | **Dependency** | Thư viện từng project (npm/pnpm/yarn/pip): đã cài chưa, lạc hậu major/minor/patch, lỗ hổng bảo mật; không tự cài/cập nhật, chỉ mở terminal chạy lệnh. |
| Chạy & dữ liệu | **DockerDesk** | Tổng quan + dọn dẹp, container (chi tiết/logs/thống kê/biến môi trường), image (chạy container), volume, network, Compose. |
| | **Port Manager** | Mọi cổng đang nghe kèm chủ sở hữu (process, dev service, container Docker, project), lọc theo dev/Docker/mở ra mạng; "Giải phóng cổng" gửi SIGTERM có xác nhận (SIGKILL chỉ khi bạn đồng ý, không dừng process của user khác/hệ thống/chính DevDock); cảnh báo trùng cổng ngay trước khi chạy dev service. |
| | **Database** | PostgreSQL, MySQL/MariaDB, Redis (trong Docker) và SQLite: duyệt bảng, cấu trúc, SQL, xuất CSV; **mặc định chỉ đọc**. |
| | **API Tester** | Gọi HTTP, lưu request theo project, biến `{{base_url}}`, import OpenAPI, copy curl. |
| | **Log Viewer** | Log Docker/service/journal/file; lọc chữ/regex/mức lỗi, tô màu, tạm dừng, lưu. |
| Hệ thống | **SysMonitor** | CPU/RAM/GPU/mạng realtime, process, cổng mạng, ổ đĩa, container. |
| | **Giám sát** | Cảnh báo desktop khi RAM/ổ đĩa thấp, tiến trình ngốn tài nguyên, dev service/container chết, cổng bị chiếm; tuỳ chọn chạy nền và tự khởi động. |
| | **DiskCleaner** | Dọn thư mục build, cache, Docker, tệp lớn/trùng; treemap, xuất CSV. |
| AI | **Ollama AI** | Quản lý model (tải/xoá/chi tiết/dỡ khỏi RAM) và chat trực tiếp với model cục bộ. |

Phím tắt: `Ctrl+1` = Tổng quan (luôn mở đầu tiên), `Ctrl+1..9` đổi module (module còn lại qua `Ctrl+K`) · `Ctrl+K` lệnh nhanh · `Ctrl+Q` thoát. Giao diện sáng/tối/theo hệ thống ở menu.

## Cài đặt
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil   # nếu thiếu
./install.sh              # cài cho user hiện tại, không cần root
./install.sh --uninstall  # gỡ
```
Sau đó mở **DevDock** từ menu ứng dụng hoặc chạy `devdock`. Chạy thẳng từ mã nguồn: `./bin/devdock`.
(Bộ khởi chạy tự gỡ biến môi trường của VS Code bản snap — nếu không, GTK sẽ lỗi khi mở từ terminal của nó.)

## Tự khởi động
Bật ở trang **Tổng quan** (công tắc cuối trang) hoặc trang Giám sát; tạo `~/.config/autostart/vn.makeai.DevDock.desktop` chạy `devdock --hidden`. Chạy khi đăng nhập desktop; cửa sổ chỉ ẩn nếu đã bật chế độ chạy nền ở Giám sát.

## An toàn
- **Projects**: git chạy với `core.fsmonitor=false` và hook tắt, nên repo lạ không chạy được lệnh qua `.git/config`.
- **DiskCleaner**: quét chỉ đọc; xoá phải qua hộp xác nhận; từ chối mọi đường dẫn ngoài `$HOME`; lệnh root/Docker là lệnh cố định.
- **Env Manager**: bí mật che mặc định, không ghi log giá trị; ghi file nguyên tử và giữ quyền file.
- **Git**: lệnh đọc tắt hook/fsmonitor; không bao giờ force-push; lệnh phá huỷ (hoàn tác, drop stash, xoá nhánh) đều hỏi xác nhận.
- **Database**: mặc định chỉ đọc, ép ở cả tầng DB và tầng Python; mật khẩu lấy từ môi trường container, không lưu, không log.
- **API Tester**: không tự gửi request; token lưu file quyền 0600 và được che.
- **Giám sát**: chỉ cảnh báo, không tự dừng tiến trình nào.
- **Dev Services**: chạy trong session riêng, dừng cả cây tiến trình; tự dừng khi đóng DevDock.

## Phát triển
```
devdock/lib/      logic thuần Python, test độc lập (projects, services, envfiles, logsrc, dockercli, sysmon)
devdock/pages/    giao diện từng module (GTK4/libadwaita)
devdock/cleaner/  lõi quét của Disk Cleaner (scanner, treemap, ring)
scripts/          ui-test.sh (chạy test giao diện thật), shot.sh (chụp ảnh các trang)
tests/            test_*.py (unit)   ui_smoke.py + ui_*.py (điều khiển giao diện thật, cần màn hình)
```
```bash
python3 -m unittest discover -s tests -v    # unit test
python3 tests/ui_smoke.py                   # test giao diện thật (cần màn hình, dùng thư mục tạm)
UI_ONLY=ui_git python3 tests/ui_smoke.py    # chỉ chạy test của một module (tests/ui_<tên>.py)
bin/devdock --add-services                  # thêm dev server phát hiện được vào Dev Services (đóng DevDock trước)
DEVDOCK_SHOTS=/tmp/shots DEVDOCK_PLAN=projects,env ./bin/devdock   # chụp ảnh trang rồi thoát
```
Cài đặt lưu ở `~/.config/devdock/settings.json`.
