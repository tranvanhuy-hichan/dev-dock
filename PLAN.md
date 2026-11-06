# DevDock — Kế hoạch

App desktop native (Tauri 2 + React/TS) gồm 4 module:
1. **DevDock Tools** – JSON/XML, JWT, Base64, Hash, Regex, UUID, Cron, URL, Timestamp (frontend thuần)
2. **SysMonitor** – CPU/RAM/GPU, network, process manager, port inspector, disk, Docker containers
3. **CodeVault** – snippet manager (SQLite), CodeMirror, tìm kiếm, folder/tag/favorite, sync GitHub Gist
4. **DockerDesk** – containers, images, logs, start/stop/restart, Compose, terminal tích hợp

## Kiến trúc
- `crates/core` (devdock-core): logic thuần Rust, không phụ thuộc Tauri → test được độc lập
  - `sysmon` (sysinfo, listeners, nvidia-smi), `vault` (rusqlite + Gist qua reqwest),
    `docker` (bollard + docker compose CLI), `pty` (portable-pty)
- `src-tauri`: wrapper mỏng, đăng ký command + state + event terminal
- `src`: React + Tailwind v4, mỗi module một thư mục, `lib/api.ts` bọc `invoke`

## Giai đoạn
1. Khung workspace, build config  2. core crate + test  3. Tauri wrapper
4. UI shell (sidebar, theme, Ctrl+K)  5. 4 module UI  6. build/typecheck, tài liệu
