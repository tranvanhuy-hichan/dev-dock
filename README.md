# DevDock

**DevDock** is a native desktop app for Linux (GNOME) that puts a developer's everyday tools in **one window**: projects and Git, dev services and ports, `.env` files, logs, Docker, databases, an API tester, secrets scanning, real per-process memory usage, system monitoring and alerts, performance/fan controls, disk cleanup and Ollama model management. Built with **Python + GTK4 + libadwaita**. It runs entirely on your machine and sends no data anywhere.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![GTK4](https://img.shields.io/badge/GTK-4%20%2B%20libadwaita-green) ![Platform](https://img.shields.io/badge/Linux-GNOME-orange) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

> The interface is currently in Vietnamese. Names in parentheses below are the labels you will see in the app.

The sidebar has 7 entries; related tools are grouped together and switched with tabs in the header bar:
**Overview** (Tổng quan) · **Projects** (Dự án: Projects, Git, Dependency, Secrets, Env) · **Dev Services** (Services, Ports, Logs) · **DockerDesk** · **Data & API** (Dữ liệu & API: Database, API Tester) · **System** (Hệ thống: Resources, Memory, Performance, Alerts, Disk cleaner) · **Ollama AI**.

| Group | Module | What it does |
|---|---|---|
| Home | **Overview** (Tổng quan) | The dashboard, always opened first: greeting and clock, CPU/RAM/disk/GPU gauges, an "Attention" list (full RAM/disk, failed services, unpushed repos, unhealthy containers, high CPU temperature, secrets found, monitoring alerts), recent projects, quick start/stop for dev services, Docker/Ollama status, and the autostart switch. |
| Projects | **Projects** | Scans your project folders: branch of every repo, uncommitted changes, unpushed commits / commits to pull, build-folder size; opens VS Code or a terminal, runs `git fetch`, cleans `node_modules`/`target`/`.venv`. |
| | **Git** | Stage/unstage, coloured diffs, commit (and commit + push), `pull --ff-only`, push (never force), searchable history, branches (checkout/create/delete merged), stash, and "Push all" for every repo with unpushed commits. |
| | **Dependency** | Libraries of each project (npm/pnpm/yarn/pip): installed or not, outdated by major/minor/patch, known vulnerabilities. It never installs or updates anything; it only opens a terminal with the command. |
| | **Secrets** | Scans every repo for API keys and tokens (AWS, GitHub, Stripe, OpenAI/Anthropic, private keys, passwords in URLs, high-entropy hard-coded strings) and for `.env` files that are committed, not in `.gitignore`, or were committed in history. Values are always masked; add to `.gitignore` with one button; "Ignore" or a trailing `devdock:ignore` comment handles false positives. |
| | **Env Manager** (Env) | Every `.env*` file: view/edit (secrets masked), compare, and warnings for variables missing compared with `.env.example`. |
| Dev Services | **Services** | Define dev servers, start/stop them, live logs, port conflicts. **Find in projects** auto-detects dev servers (npm, compose, Makefile, FastAPI, Spring…). |
| | **Ports** (Cổng mạng) | Every listening port with its owner (process, dev service, Docker container, project), filtered by dev/Docker/network-exposed; "Free port" sends SIGTERM after confirmation (SIGKILL only if you agree; never touches other users' or system processes, or DevDock itself); warns about port clashes right before a dev service starts. |
| | **Log Viewer** (Log) | Docker/service/journal/file logs; text/regex/level filters, colouring, pause, save. |
| DockerDesk | **DockerDesk** | Overview and cleanup, containers (details/logs/stats/environment), images (run a container), volumes, networks, Compose. |
| Data & API | **Database** | PostgreSQL, MySQL/MariaDB, Redis (in Docker) and SQLite: browse tables, schema, run SQL, export CSV. **Read-only by default.** |
| | **API Tester** | HTTP requests saved per project, `{{base_url}}` variables, OpenAPI import, copy as curl. |
| System | **Resources** (Tài nguyên) | Real-time CPU/RAM/GPU/network, disks, network interfaces. |
| | **Memory** (Bộ nhớ) | Real RAM per process using PSS (`smaps_rollup`, which splits shared memory fairly instead of double-counting like RSS), grouped by application (VS Code, Chrome, Firefox…), with CPU and 5-minute growth columns to spot leaks; stop a process or a whole group with confirmation (SIGTERM; SIGKILL only if you agree; never system processes, other users' processes or DevDock). |
| | **Performance** (Hiệu năng) | CPU temperature, fan speed, CPU frequency, battery; switch the power profile (power-profiles-daemon), Turbo Boost, max CPU frequency and energy preference; on MSI laptops with the `msi-ec` driver also fan mode, Cooler Boost, shift mode, super battery and battery charge limit. 4 quick presets (Max performance / Balanced / Quiet & cool / Emergency cooling). Only controls your machine supports are shown; all changes that need admin rights are applied with **one** password prompt. |
| | **Alerts** (Cảnh báo) | Desktop notifications for low RAM/disk, resource-hungry processes, dead dev services/containers and port clashes; plus memory leaks (a process whose RAM keeps growing), kernel OOM kills, very high CPU load, CPU/GPU temperature, low battery and zombie processes. Each rule has its own threshold; `llama-server`/`ollama` are never flagged. Optional background mode and autostart. |
| | **Disk cleaner** (Dọn đĩa) | Cleans build folders, caches, Docker, large/duplicate files; treemap, CSV export. |
| AI | **Ollama AI** | Manage models (pull/delete/details/unload from RAM) and chat with local models. |

Shortcuts: `Ctrl+1..7` switch sidebar entries (Ctrl+1 = Overview); open any single tool (Git, Ports, Memory…) with `Ctrl+K` · `Ctrl+K` quick command palette · `Ctrl+Q` quit. Light/dark/system theme is in the menu.

## Where projects come from
DevDock scans **folders**: it does not scan the whole disk and does not watch the file system in real time. A folder counts as a project if it contains `.git` or a file such as `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`… By default it scans `~/Projects` up to 3 levels deep and skips hidden folders, symlinks, `node_modules`, `venv`, `target` and `build`.
The folder button on the **Projects** page opens the **Project sources** dialog: add or remove **several root folders**, add **standalone projects** that live anywhere, set the **depth** (1–6), and see how many projects will be found before saving. The same settings are used by Projects, Git, Dependency, Secrets, Env, Ports, Docker and Dev Services.

## Install
**Option 1: `.deb` package** (Ubuntu/Debian; download it from the [Releases](https://github.com/tranvanhuy-hichan/dev-dock/releases) page):
```bash
sudo apt install ./devdock_1.1.0_all.deb     # also installs the GTK4/libadwaita libraries it needs
sudo apt remove devdock                      # uninstall
```
**Option 2: from source**, for the current user, no root needed:
```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-psutil   # if missing
./install.sh              # install
./install.sh --uninstall  # uninstall
```
Then open **DevDock** from the application menu or run `devdock`. To run straight from the source tree: `./bin/devdock`.
(The launcher clears environment variables left behind by the snap build of VS Code; otherwise GTK aborts when started from its terminal.)
Requirements: Python 3.11+, GTK 4, libadwaita 1.5+. Optional: `git`, `docker`, `power-profiles-daemon` (power profiles), `pkexec` (changing system settings), `ollama`.

## Autostart
Turn it on from the **Overview** page (switch at the bottom) or from the Alerts tab. It creates `~/.config/autostart/vn.makeai.DevDock.desktop` running `devdock --hidden`. It starts when you log in to the desktop; the window is only hidden if background mode is enabled in Alerts.

## Safety
- **Projects**: git runs with `core.fsmonitor=false` and hooks disabled, so an untrusted repo cannot run commands through `.git/config`.
- **Disk cleaner**: read-only scan; deletion always asks for confirmation; every path outside `$HOME` is refused; root/Docker commands are fixed commands.
- **Env Manager**: secrets are masked by default and never logged; files are written atomically and keep their permissions.
- **Git**: read commands disable hooks/fsmonitor; it never force-pushes; destructive commands (revert, drop stash, delete branch) always ask first.
- **Database**: read-only by default, enforced both in the database session and in Python; passwords come from the container environment, are never stored and never logged.
- **API Tester**: never sends a request on its own; tokens are stored in a `0600` file and masked.
- **Alerts**: they only notify; nothing is ever stopped automatically.
- **Performance**: it writes only to a fixed whitelist of sysfs files with validated values, through a single `pkexec` call (you see the password prompt); no raw EC register writes and no forced driver loading; after writing it reads the values back to confirm the system accepted them. Settings do not persist across a reboot.
- **Dev Services**: run in their own session, the whole process tree is stopped; they stop when DevDock quits.

## Development
```
devdock/lib/      pure-Python logic, tested on its own (projects, services, envfiles, logsrc, dockercli, sysmon, perf, memwatch, ports, secrets)
devdock/pages/    one GTK4/libadwaita page per tool
devdock/cleaner/  scanning core of the disk cleaner (scanner, treemap, ring)
scripts/          ui-test.sh (real-GUI tests), shot.sh (page screenshots), build-deb.sh (builds the .deb into dist/)
tests/            test_*.py (unit)   ui_smoke.py + ui_*.py (drive the real GUI, need a display)
```
```bash
python3 -m unittest discover -s tests -v    # unit tests
scripts/ui-test.sh                          # real-GUI tests (needs a display; uses temporary config folders)
UI_ONLY=ui_git scripts/ui-test.sh           # run only one module's tests (tests/ui_<name>.py)
scripts/build-deb.sh                        # build dist/devdock_<version>_all.deb
bin/devdock --add-services                  # add detected dev servers to Dev Services (close DevDock first)
DEVDOCK_SHOTS=/tmp/shots DEVDOCK_PLAN=projects,env ./bin/devdock   # screenshot pages, then exit
```
Settings are stored in `~/.config/devdock/settings.json`.

## License
[MIT](LICENSE)
