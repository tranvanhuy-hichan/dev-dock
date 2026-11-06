use base64::{engine::general_purpose::STANDARD, Engine};
use devdock_core::{
    cleaner::{self, CleanReport, Ctl, ScanResult, Settings as CleanSettings, Tree},
    docker::{self, ContainerInfo, ContainerStats, DockerStatus, ImageInfo},
    pty::PtyManager,
    sysmon::{self, Monitor, PortInfo, ProcInfo, Snapshot},
    vault::{self, Facets, Filter, Snippet, SnippetInput, Vault},
};
use serde::Serialize;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager, State};

type Res<T> = Result<T, String>;

#[derive(Default)]
struct CleanerState {
    items: Mutex<Vec<cleaner::Item>>, // kết quả quét gần nhất: lệnh dọn chỉ nhận id rồi tra ở đây
    ctl: Mutex<Option<Ctl>>,
}

struct AppState {
    mon: Mutex<Monitor>,
    vault: Mutex<Vault>,
    pty: PtyManager,
    cleaner: Arc<CleanerState>,
}

fn lock<'a, T>(m: &'a Mutex<T>) -> Res<std::sync::MutexGuard<'a, T>> {
    m.lock().map_err(|_| "state bị khoá lỗi".to_string())
}

// ---------- SysMonitor ----------
#[tauri::command]
fn sys_snapshot(st: State<AppState>) -> Res<Snapshot> {
    Ok(lock(&st.mon)?.snapshot())
}
#[tauri::command]
fn sys_processes(st: State<AppState>) -> Res<Vec<ProcInfo>> {
    Ok(lock(&st.mon)?.processes())
}
#[tauri::command]
fn sys_kill(st: State<AppState>, pid: u32, force: bool) -> Res<()> {
    lock(&st.mon)?.kill(pid, force)
}
#[tauri::command]
fn sys_ports() -> Res<Vec<PortInfo>> {
    sysmon::list_ports()
}

// ---------- Docker ----------
#[tauri::command]
async fn docker_status() -> DockerStatus {
    docker::status().await
}
#[tauri::command]
async fn docker_containers(all: bool) -> Res<Vec<ContainerInfo>> {
    docker::list_containers(all).await
}
#[tauri::command]
async fn docker_action(id: String, action: String) -> Res<()> {
    docker::container_action(&id, &action).await
}
#[tauri::command]
async fn docker_logs(id: String, tail: u32) -> Res<String> {
    docker::container_logs(&id, tail).await
}
#[tauri::command]
async fn docker_images() -> Res<Vec<ImageInfo>> {
    docker::list_images().await
}
#[tauri::command]
async fn docker_remove_image(id: String, force: bool) -> Res<()> {
    docker::remove_image(&id, force).await
}
#[tauri::command]
async fn docker_pull(image: String) -> Res<String> {
    docker::pull_image(&image).await
}
#[tauri::command]
async fn docker_stats() -> Res<Vec<ContainerStats>> {
    docker::stats_all().await
}

// ---------- Compose ----------
const COMPOSE_KEY: &str = "compose_files";

fn compose_files(v: &Vault) -> Res<Vec<String>> {
    Ok(v.get_setting(COMPOSE_KEY)?
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default())
}

#[tauri::command]
fn compose_list(st: State<AppState>) -> Res<Vec<String>> {
    compose_files(&*lock(&st.vault)?)
}
#[tauri::command]
fn compose_add(st: State<AppState>, path: String) -> Res<Vec<String>> {
    let v = lock(&st.vault)?;
    let mut list = compose_files(&v)?;
    if !list.contains(&path) {
        list.push(path);
    }
    v.set_setting(COMPOSE_KEY, &serde_json::to_string(&list).map_err(|e| e.to_string())?)?;
    Ok(list)
}
#[tauri::command]
fn compose_remove(st: State<AppState>, path: String) -> Res<Vec<String>> {
    let v = lock(&st.vault)?;
    let mut list = compose_files(&v)?;
    list.retain(|p| p != &path);
    v.set_setting(COMPOSE_KEY, &serde_json::to_string(&list).map_err(|e| e.to_string())?)?;
    Ok(list)
}
#[tauri::command]
async fn compose_run(file: String, action: String) -> Res<String> {
    docker::compose_run(&file, &action).await
}

// ---------- Terminal ----------
#[derive(Serialize, Clone)]
struct PtyData {
    id: String,
    data: String, // base64
}

#[tauri::command]
fn pty_open(app: AppHandle, st: State<AppState>, id: String, cmd: Vec<String>, cols: u16, rows: u16) -> Res<()> {
    st.pty.spawn(id, cmd, cols, rows, move |id, bytes| {
        let _ = app.emit("pty://data", PtyData { id: id.to_string(), data: STANDARD.encode(bytes) });
    })
}
#[tauri::command]
fn pty_write(st: State<AppState>, id: String, data: String) -> Res<()> {
    st.pty.write(&id, data.as_bytes())
}
#[tauri::command]
fn pty_resize(st: State<AppState>, id: String, cols: u16, rows: u16) -> Res<()> {
    st.pty.resize(&id, cols, rows)
}
#[tauri::command]
fn pty_close(st: State<AppState>, id: String) {
    st.pty.close(&id)
}

// ---------- CodeVault ----------
#[tauri::command]
fn vault_list(st: State<AppState>, filter: Filter) -> Res<Vec<Snippet>> {
    lock(&st.vault)?.list(&filter)
}
#[tauri::command]
fn vault_facets(st: State<AppState>) -> Res<Facets> {
    lock(&st.vault)?.facets()
}
#[tauri::command]
fn vault_save(st: State<AppState>, input: SnippetInput) -> Res<Snippet> {
    lock(&st.vault)?.save(input)
}
#[tauri::command]
async fn vault_delete(st: State<'_, AppState>, id: i64, delete_remote: bool) -> Res<()> {
    let (gist, token) = {
        let v = lock(&st.vault)?;
        (v.get(id)?.and_then(|s| s.gist_id), v.get_setting("gist_token")?)
    };
    if let (true, Some(g), Some(t)) = (delete_remote, gist, token) {
        vault::gist_delete(&t, &g).await?;
    }
    lock(&st.vault)?.delete(id)
}
#[tauri::command]
fn vault_favorite(st: State<AppState>, id: i64, favorite: bool) -> Res<()> {
    lock(&st.vault)?.set_favorite(id, favorite)
}

// Gist
#[tauri::command]
fn gist_has_token(st: State<AppState>) -> Res<bool> {
    Ok(lock(&st.vault)?.get_setting("gist_token")?.is_some())
}
#[tauri::command]
fn gist_set_token(st: State<AppState>, token: String) -> Res<()> {
    let v = lock(&st.vault)?;
    if token.trim().is_empty() {
        v.delete_setting("gist_token")
    } else {
        v.set_setting("gist_token", token.trim())
    }
}
fn token(st: &AppState) -> Res<String> {
    lock(&st.vault)?.get_setting("gist_token")?.ok_or_else(|| "Chưa cấu hình GitHub token".to_string())
}
#[tauri::command]
async fn gist_push(st: State<'_, AppState>, id: i64) -> Res<Snippet> {
    let t = token(&st)?;
    let snip = lock(&st.vault)?.get(id)?.ok_or("Snippet không tồn tại")?;
    let gid = vault::gist_push(&t, &snip).await?;
    let v = lock(&st.vault)?;
    v.set_gist_id(id, &gid)?;
    v.get(id)?.ok_or_else(|| "Snippet không tồn tại".into())
}
/// Nhập các gist 1 file chưa có trong vault. Trả về số snippet mới.
#[tauri::command]
async fn gist_pull(st: State<'_, AppState>) -> Res<usize> {
    let t = token(&st)?;
    let gists = vault::gist_list(&t).await?;
    let v = lock(&st.vault)?;
    let mut n = 0;
    for g in gists {
        if g.files.len() != 1 || v.find_by_gist(&g.id)?.is_some() {
            continue;
        }
        let f = g.files.values().next().unwrap();
        let (Some(name), Some(content)) = (f.filename.clone(), f.content.clone()) else { continue };
        if f.truncated.unwrap_or(false) {
            continue;
        }
        let title = name.rsplit_once('.').map(|x| x.0.to_string()).unwrap_or(name.clone());
        let saved = v.save(SnippetInput {
            id: None,
            title,
            language: vault::lang_for(&name, f.language.as_deref()),
            code: content,
            description: g.description.clone().unwrap_or_default(),
            tags: vec!["gist".into()],
            folder: String::new(),
            favorite: false,
        })?;
        v.set_gist_id(saved.id, &g.id)?;
        n += 1;
    }
    Ok(n)
}


// ---------- DiskCleaner ----------
const CLEAN_KEY: &str = "cleaner_settings";

fn load_clean(v: &Vault) -> CleanSettings {
    v.get_setting(CLEAN_KEY).ok().flatten().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}
fn save_clean(v: &Vault, s: &CleanSettings) -> Res<()> {
    v.set_setting(CLEAN_KEY, &serde_json::to_string(s).map_err(|e| e.to_string())?)
}

#[tauri::command]
fn cleaner_settings_get(st: State<AppState>) -> Res<CleanSettings> {
    Ok(load_clean(&*lock(&st.vault)?))
}
#[tauri::command]
fn cleaner_settings_set(st: State<AppState>, settings: CleanSettings) -> Res<CleanSettings> {
    let v = lock(&st.vault)?;
    let old = load_clean(&v);
    // thống kê do backend quản lý, giao diện không được sửa
    let s = CleanSettings { total_freed: old.total_freed, clean_count: old.clean_count, ..settings };
    save_clean(&v, &s)?;
    Ok(s)
}
#[tauri::command]
fn cleaner_ignore(st: State<AppState>, id: String, title: String, ignore: bool) -> Res<CleanSettings> {
    let v = lock(&st.vault)?;
    let mut s = load_clean(&v);
    s.ignored.retain(|x| x != &id);
    s.ignored_titles.remove(&id);
    if ignore {
        s.ignored.push(id.clone());
        s.ignored_titles.insert(id, title);
    }
    save_clean(&v, &s)?;
    Ok(s)
}
#[tauri::command]
fn cleaner_disk() -> cleaner::DiskUsage {
    cleaner::disk_usage()
}
/// Quét máy (chỉ đọc). Trả về None nếu bị huỷ.
#[tauri::command]
async fn cleaner_scan(app: AppHandle, st: State<'_, AppState>) -> Res<Option<ScanResult>> {
    let opts = load_clean(&*lock(&st.vault)?).options();
    let ctl = Ctl::default();
    {
        let mut g = lock(&st.cleaner.ctl)?;
        if g.is_some() {
            return Err("Đang có một tác vụ quét/dọn chạy".into());
        }
        *g = Some(ctl.clone());
    }
    let res = tauri::async_runtime::spawn_blocking(move || cleaner::scan(&opts, &ctl, &|m| { let _ = app.emit("cleaner://progress", m.to_string()); })).await;
    *lock(&st.cleaner.ctl)? = None;
    let res = res.map_err(|e| e.to_string())?;
    if let Some(r) = &res {
        *lock(&st.cleaner.items)? = r.items.clone();
    }
    Ok(res)
}
#[tauri::command]
fn cleaner_cancel(st: State<AppState>) -> Res<()> {
    if let Some(c) = lock(&st.cleaner.ctl)?.as_ref() {
        c.cancel();
    }
    Ok(())
}
/// Dọn các mục theo id (phải nằm trong kết quả quét gần nhất).
#[tauri::command]
async fn cleaner_clean(app: AppHandle, st: State<'_, AppState>, ids: Vec<String>) -> Res<CleanReport> {
    let (items, trash_large) = {
        let all = lock(&st.cleaner.items)?;
        let items: Vec<cleaner::Item> = ids.iter().filter_map(|id| all.iter().find(|i| &i.id == id).cloned()).collect();
        if items.len() != ids.len() {
            return Err("Có mục không còn trong kết quả quét — hãy quét lại rồi thử lại".into());
        }
        (items, load_clean(&*lock(&st.vault)?).trash_large)
    };
    {
        let mut g = lock(&st.cleaner.ctl)?;
        if g.is_some() {
            return Err("Đang có một tác vụ quét/dọn chạy".into());
        }
        *g = Some(Ctl::default());
    }
    let res = tauri::async_runtime::spawn_blocking(move || {
        let mut rep = CleanReport::default();
        for it in items {
            let _ = app.emit("cleaner://progress", format!("Đang dọn: {}", it.title));
            let trash: Option<&dyn Fn(&str) -> Result<(), String>> = if trash_large { Some(&cleaner::gio_trash) } else { None };
            match cleaner::clean_item(&it, trash) {
                Ok(()) => {
                    if trash_large && cleaner::TRASHABLE.contains(&it.group.as_str()) { rep.trashed += it.size } else { rep.freed += it.size }
                    rep.done.push(it.id);
                }
                Err(e) => rep.errors.push(format!("{}: {e}", it.title)),
            }
        }
        rep
    })
    .await;
    *lock(&st.cleaner.ctl)? = None;
    let rep = res.map_err(|e| e.to_string())?;
    lock(&st.cleaner.items)?.retain(|i| !rep.done.contains(&i.id));
    let v = lock(&st.vault)?;
    let mut s = load_clean(&v);
    s.total_freed += rep.freed;
    s.clean_count += rep.done.len() as u64;
    save_clean(&v, &s)?;
    Ok(rep)
}
#[tauri::command]
async fn cleaner_tree(rel: String) -> Res<Tree> {
    tauri::async_runtime::spawn_blocking(move || cleaner::tree_dir(&rel)).await.map_err(|e| e.to_string())?
}
#[tauri::command]
fn cleaner_open(path: String) -> Res<()> {
    let path = match path.strip_prefix("~/") {
        Some(r) => format!("{}/{r}", cleaner::home()),
        None => path,
    };
    let p = cleaner::assert_inside_home(&path)?;
    let dir = if p.is_dir() { p } else { p.parent().map(|x| x.to_path_buf()).ok_or("Không có thư mục cha")? };
    std::process::Command::new("xdg-open").arg(dir).spawn().map_err(|e| format!("Không mở được: {e}"))?;
    Ok(())
}
#[tauri::command]
fn cleaner_export_csv(st: State<AppState>, path: String, ids: Vec<String>) -> Res<()> {
    if !path.to_lowercase().ends_with(".csv") {
        return Err("Chỉ được ghi file .csv".into());
    }
    let all = lock(&st.cleaner.items)?;
    let rows: Vec<&cleaner::Item> = ids.iter().filter_map(|id| all.iter().find(|i| &i.id == id)).collect();
    cleaner::export_csv(&rows, std::path::Path::new(&path))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            let vault = Vault::open(&dir.join("vault.db"))?;
            app.manage(AppState { mon: Mutex::new(Monitor::new()), vault: Mutex::new(vault), pty: PtyManager::default(), cleaner: Arc::new(CleanerState::default()) });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sys_snapshot, sys_processes, sys_kill, sys_ports,
            docker_status, docker_containers, docker_action, docker_logs, docker_images,
            docker_remove_image, docker_pull, docker_stats,
            compose_list, compose_add, compose_remove, compose_run,
            pty_open, pty_write, pty_resize, pty_close,
            vault_list, vault_facets, vault_save, vault_delete, vault_favorite,
            gist_has_token, gist_set_token, gist_push, gist_pull,
            cleaner_settings_get, cleaner_settings_set, cleaner_ignore, cleaner_disk, cleaner_scan, cleaner_cancel,
            cleaner_clean, cleaner_tree, cleaner_open, cleaner_export_csv,
        ])
        .run(tauri::generate_context!())
        .expect("lỗi khi chạy DevDock");
}
