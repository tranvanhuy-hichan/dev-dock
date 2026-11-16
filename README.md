# DevDock

**DevDock** là ứng dụng desktop native cho Linux (GNOME), gom công cụ hằng ngày của developer vào **một cửa sổ**: quản lý project và Git, dev service và cổng mạng, file `.env`, log, Docker, database, API tester, quét bí mật (secrets), theo dõi RAM thật của từng tiến trình, giám sát và cảnh báo hệ thống, chỉnh chế độ hiệu năng/quạt, dọn đĩa và quản lý model Ollama. Viết bằng **Python + GTK4 + libadwaita**, chạy hoàn toàn cục bộ, không gửi dữ liệu đi đâu.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![GTK4](https://img.shields.io/badge/GTK-4%20%2B%20libadwaita-green) ![Platform](https://img.shields.io/badge/Linux-GNOME-orange) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

Sidebar có 7 mục; các chức năng liên quan được gộp và chuyển qua tab ở thanh tiêu đề:
**Tổng quan** · **Dự án** (Projects, Git, Dependency, Secrets, Env) · **Dev Services** (Services, Cổng mạng, Log) · **DockerDesk** · **Dữ liệu & API** (Database, API Tester) · **Hệ thống** (Tài nguyên, Bộ nhớ, Cảnh báo, Dọn đĩa) · **Ollama AI**.

| Nhóm | Module | Làm gì |
|---|---|---|
| Trang chủ | **Tổng quan** | Dashboard mở đầu tiên: lời chào + đồng hồ, gauge CPU/RAM/ổ đĩa/GPU, mục "Cần chú ý" (RAM/ổ đĩa đầy, service lỗi, repo chưa push, container lỗi, cảnh báo giám sát), project gần đây, dev service bật/tắt nhanh, Docker/Ollama, công tắc tự khởi động. |
| Dự án | **Projects** | Quét `~/Projects`: mọi repo cùng nhánh, thay đổi chưa commit, commit chưa push/cần pull, dung lượng build; mở VS Code/terminal, `git fetch`, dọn `node_modules`/`target`/`.venv`. |
| | **Git** | Stage/unstage, diff tô màu, commit (và commit + push), pull `--ff-only`, push (không force), lịch sử có tìm kiếm, nhánh (checkout/tạo/xoá nhánh đã merge), stash, "Push tất cả" các repo chưa push. |
| | **Dependency** | Thư viện từng project (npm/pnpm/yarn/pip): đã cài chưa, lạc hậu major/minor/patch, lỗ hổng bảo mật; không tự cài/cập nhật, chỉ mở terminal chạy lệnh. |
| | **Secrets** | Quét mọi repo tìm khoá API/token (AWS, GitHub, Stripe, OpenAI/Anthropic, private key, mật khẩu trong URL, chuỗi gán cứng entropy cao) và file `.env` đã commit / chưa nằm trong `.gitignore` / từng bị commit trong lịch sử. Giá trị luôn được che; thêm vào `.gitignore` bằng một nút; "Bỏ qua" hoặc `devdock:ignore` cuối dòng cho mục giả. Kết quả hiện ở "Cần chú ý" của Tổng quan. |
| | **Env Manager** | Mọi file `.env*`: xem/sửa (che bí mật), so sánh, cảnh báo thiếu biến so với `.env.example`. |
| Dev Services | **Services** | Khai báo server dev, bật/tắt, log trực tiếp, xung đột cổng. **Tìm trong project** tự phát hiện dev server (npm, compose, Makefile, FastAPI, Spring…). |
| | **Cổng mạng** | Mọi cổng đang nghe kèm chủ sở hữu (process, dev service, container Docker, project), lọc theo dev/Docker/mở ra mạng; "Giải phóng cổng" gửi SIGTERM có xác nhận (SIGKILL chỉ khi bạn đồng ý, không dừng process của user khác/hệ thống/chính DevDock); cảnh báo trùng cổng ngay trước khi chạy dev service. |
| | **Log Viewer** | Log Docker/service/journal/file; lọc chữ/regex/mức lỗi, tô màu, tạm dừng, lưu. |
| DockerDesk | **DockerDesk** | Tổng quan + dọn dẹp, container (chi tiết/logs/thống kê/biến môi trường), image (chạy container), volume, network, Compose. |
| Dữ liệu & API | **Database** | PostgreSQL, MySQL/MariaDB, Redis (trong Docker) và SQLite: duyệt bảng, cấu trúc, SQL, xuất CSV; **mặc định chỉ đọc**. |
| | **API Tester** | Gọi HTTP, lưu request theo project, biến `{{base_url}}`, import OpenAPI, copy curl. |
| Hệ thống | **Tài nguyên** | CPU/RAM/GPU/mạng theo thời gian thực, ổ đĩa, network. |
| | **Bộ nhớ** | RAM thực sự của từng tiến trình bằng PSS (`smaps_rollup`, chia đều bộ nhớ dùng chung nên không đếm trùng như RSS), gom theo ứng dụng (VS Code, Chrome, Firefox…), cột CPU và cột tăng RAM sau 5 phút để bắt rò rỉ; dừng tiến trình/cả nhóm có xác nhận (SIGTERM, SIGKILL chỉ khi bạn đồng ý; không dừng process hệ thống, của user khác hay DevDock). |
| | **Hiệu năng** | Nhiệt độ CPU, tốc độ quạt, xung CPU, pin; đổi chế độ nguồn (power-profiles-daemon), Turbo Boost, xung CPU tối đa, ưu tiên năng lượng; trên laptop MSI có driver `msi-ec`: chế độ quạt, Cooler Boost, shift mode, super battery, giới hạn sạc pin. 4 chế độ nhanh (Hiệu năng tối đa / Cân bằng / Êm & mát / Làm mát khẩn cấp). Chỉ hiện điều khiển máy hỗ trợ; thay đổi cần quyền quản trị gom thành **một** lần hỏi mật khẩu. |
| | **Cảnh báo (Giám sát)** | Cảnh báo desktop khi RAM/ổ đĩa thấp, tiến trình ngốn tài nguyên, dev service/container chết, cổng bị chiếm; thêm: rò rỉ bộ nhớ (RAM một tiến trình tăng đều không giảm), kernel OOM-kill, tải CPU quá cao, nhiệt độ CPU/GPU, pin yếu, tiến trình zombie. Mỗi luật chỉnh ngưỡng riêng; không báo `llama-server`/`ollama`. Tuỳ chọn chạy nền và tự khởi động. |
| | **DiskCleaner** | Dọn thư mục build, cache, Docker, tệp lớn/trùng; treemap, xuất CSV. |
| AI | **Ollama AI** | Quản lý model (tải/xoá/chi tiết/dỡ khỏi RAM) và chat trực tiếp với model cục bộ. |

Phím tắt: `Ctrl+1..7` đổi mục sidebar (Ctrl+1 = Tổng quan, luôn mở đầu tiên); từng trang con (Git, Cổng mạng, Bộ nhớ…) mở nhanh qua `Ctrl+K` · `Ctrl+K` lệnh nhanh · `Ctrl+Q` thoát. Giao diện sáng/tối/theo hệ thống ở menu.

## Nguồn project
DevDock quét **thư mục** (không quét cả ổ đĩa, không theo dõi thời gian thực): một thư mục là project nếu có `.git` hoặc file như `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`… Mặc định quét `~/Projects`, sâu tối đa 3 cấp, bỏ qua thư mục ẩn, symlink, `node_modules`, `venv`, `target`, `build`.
Nút thư mục ở trang **Projects** mở hộp thoại **Nguồn project**: thêm/bớt **nhiều thư mục gốc**, thêm **project lẻ** nằm ở bất kỳ đâu, chỉnh **độ sâu** (1–6), có đếm trước số project sẽ tìm thấy. Cấu hình dùng chung cho Projects, Git, Dependency, Secrets, Env, Cổng mạng, Docker và Dev Services.

## Cài đặt
**Cách 1: gói .deb** (Ubuntu/Debian, tải ở trang [Releases](https://github.com/tranvanhuy-hichan/dev-dock/releases)):
```bash
sudo apt install ./devdock_1.1.0_all.deb     # tự cài các thư viện GTK4/libadwaita cần thiết
sudo apt remove devdock                      # gỡ
```
**Cách 2: từ mã nguồn**, cài cho user hiện tại, không cần root:
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil   # nếu thiếu
./install.sh              # cài
./install.sh --uninstall  # gỡ
```
Sau đó mở **DevDock** từ menu ứng dụng hoặc chạy `devdock`. Chạy thẳng từ mã nguồn: `./bin/devdock`.
(Bộ khởi chạy tự gỡ biến môi trường của VS Code bản snap — nếu không, GTK sẽ lỗi khi mở từ terminal của nó.)
Yêu cầu: Python 3.11+, GTK 4, libadwaita 1.5+. Tuỳ chọn: `git`, `docker`, `power-profiles-daemon` (chế độ nguồn), `pkexec` (đổi thiết lập hệ thống), `ollama`.

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
- **Hiệu năng**: chỉ ghi vào một danh sách file sysfs cố định với giá trị đã kiểm tra, qua một lệnh `pkexec` duy nhất (bạn thấy hộp thoại mật khẩu); không ghi thanh ghi EC thô, không ép nạp driver; sau khi ghi sẽ đọc lại để chắc hệ thống đã chấp nhận. Thiết lập không giữ sau khi khởi động lại máy.
- **Dev Services**: chạy trong session riêng, dừng cả cây tiến trình; tự dừng khi đóng DevDock.

## Phát triển
```
devdock/lib/      logic thuần Python, test độc lập (projects, services, envfiles, logsrc, dockercli, sysmon)
devdock/pages/    giao diện từng module (GTK4/libadwaita)
devdock/cleaner/  lõi quét của Disk Cleaner (scanner, treemap, ring)
scripts/          ui-test.sh (chạy test giao diện thật), shot.sh (chụp ảnh các trang), build-deb.sh (đóng gói .deb vào dist/)
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
