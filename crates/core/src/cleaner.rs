//! Quét và dọn rác trên đĩa (port từ Disk Cleaner Python, không phụ thuộc GUI).
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const MB: u64 = 1024 * 1024;
const DAY: f64 = 86400.0;
const ACTIVE_DAYS: i64 = 14; // project có hoạt động trong ngần này ngày => "cần xem lại"
const MAX_DEPTH: usize = 7;
const SKIP_DIRS: [&str; 3] = ["snap", "node_modules", ".git"];
const LARGE_SKIP: [&str; 8] = ["/snap/", "/node_modules/", "/.local/share/Trash/", "/.cache/", "/.git/", "/.config/", "/.var/", "/.local/share/docker/"];
const DUP_SKIP: [&str; 7] = ["/dist/", "/build/", "/site-packages/", "/_internal/", "/linux-unpacked/", "/target/", "/out/"];
/// Chỉ nhóm này được chuyển vào thùng rác (cache/build cần giải phóng dung lượng thật).
pub const TRASHABLE: [&str; 2] = ["large", "dupe"];

pub fn home() -> String {
    std::env::var("HOME").unwrap_or_default()
}
fn h(p: &str) -> String {
    format!("{}/{}", home(), p)
}

// ---------------- Kiểu dữ liệu ----------------
#[derive(Serialize, Clone, Debug, Default)]
pub struct Item {
    pub id: String,
    pub group: String,
    pub kind: String, // dir | file | contents | docker | root
    pub paths: Vec<String>,
    pub title: String,
    pub size: u64,
    pub level: String, // safe | review
    pub note: String,
    pub rebuild: Option<String>,
    pub docker_type: Option<String>,
    pub sys_type: Option<String>,
    pub cmd: Option<String>,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
#[serde(default)]
pub struct Settings {
    pub trash_large: bool,
    pub min_mb: u64,
    pub large_mb: u64,
    pub scan_large: bool,
    pub dup_scan: bool,
    pub dup_mb: u64,
    pub ignored: Vec<String>,
    pub ignored_titles: BTreeMap<String, String>,
    pub total_freed: u64,
    pub clean_count: u64,
}

impl Default for Settings {
    fn default() -> Self {
        Self { trash_large: true, min_mb: 5, large_mb: 200, scan_large: true, dup_scan: true, dup_mb: 10, ignored: vec![], ignored_titles: BTreeMap::new(), total_freed: 0, clean_count: 0 }
    }
}

impl Settings {
    pub fn options(&self) -> Options {
        Options { min_size: self.min_mb.max(1) * MB, large_size: self.large_mb.max(1) * MB, large: self.scan_large, dup: self.dup_scan, dup_size: self.dup_mb.max(1) * MB }
    }
}

#[derive(Clone, Debug)]
pub struct Options {
    pub min_size: u64,
    pub large_size: u64,
    pub large: bool,
    pub dup: bool,
    pub dup_size: u64,
}

impl Default for Options {
    fn default() -> Self {
        Settings::default().options()
    }
}

#[derive(Serialize, Clone, Debug)]
pub struct DiskUsage {
    pub total: u64,
    pub free: u64,
}

#[derive(Serialize, Clone, Debug)]
pub struct ScanResult {
    pub items: Vec<Item>,
    pub disk: DiskUsage,
    pub scanned_at: f64,
}

#[derive(Serialize, Debug)]
pub struct TreeEntry {
    pub name: String,
    pub size: u64,
    pub dir: bool,
}

#[derive(Serialize, Debug)]
pub struct Tree {
    pub path: String,
    pub total: u64,
    pub entries: Vec<TreeEntry>,
}

#[derive(Serialize, Debug, Default)]
pub struct CleanReport {
    pub done: Vec<String>,
    pub errors: Vec<String>,
    pub freed: u64,
    pub trashed: u64,
}

/// Cờ huỷ dùng chung giữa luồng giao diện và luồng quét.
#[derive(Clone, Default)]
pub struct Ctl(Arc<AtomicBool>);
impl Ctl {
    pub fn cancelled(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Relaxed)
    }
}

type Progress<'a> = &'a (dyn Fn(&str) + Sync);

// ---------------- Tiện ích ----------------
/// Chạy lệnh, trả stdout (None nếu lỗi/không có lệnh/rỗng). `du` vẫn có kết quả khi gặp thư mục không có quyền.
fn run(cmd: &str, args: &[&str], timeout: Duration) -> Option<String> {
    let mut child = Command::new(cmd).args(args).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = out.read_to_end(&mut b);
        b
    });
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() > deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(15)),
            Err(_) => return None,
        }
    }
    let s = String::from_utf8_lossy(&reader.join().ok()?).into_owned();
    if s.is_empty() { None } else { Some(s) }
}

pub fn size_of(path: &str) -> u64 {
    run("du", &["-sk", "--", path], Duration::from_secs(180))
        .and_then(|o| o.split_whitespace().next().and_then(|n| n.parse::<u64>().ok()))
        .map(|k| k * 1024)
        .unwrap_or(0)
}

fn id_of(parts: &[&str]) -> String {
    let mut hs = Sha256::new();
    hs.update(parts.join("\0").as_bytes());
    hs.finalize().iter().take(6).map(|b| format!("{b:02x}")).collect()
}

fn rel(path: &str) -> String {
    let hm = home();
    match path.strip_prefix(&format!("{hm}/")) {
        Some(r) => format!("~/{r}"),
        None => path.to_string(),
    }
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn mtime(p: &Path) -> f64 {
    std::fs::metadata(p).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Chạy `f` cho từng phần tử với tối đa `limit` luồng, giữ nguyên thứ tự kết quả.
fn pool_map<T: Sync, R: Send>(items: &[T], limit: usize, f: &(dyn Fn(&T) -> R + Sync)) -> Vec<R> {
    let next = AtomicUsize::new(0);
    let out: Mutex<Vec<Option<R>>> = Mutex::new((0..items.len()).map(|_| None).collect());
    std::thread::scope(|s| {
        for _ in 0..limit.min(items.len()).max(1) {
            s.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= items.len() {
                    break;
                }
                let r = f(&items[i]);
                out.lock().unwrap()[i] = Some(r);
            });
        }
    });
    out.into_inner().unwrap().into_iter().flatten().collect()
}

pub fn parse_docker_size(s: &str) -> u64 {
    let s = s.trim();
    let end = s.find(|c: char| !(c.is_ascii_digit() || c == '.')).unwrap_or(s.len());
    let Ok(n) = s[..end].parse::<f64>() else { return 0 };
    let unit: f64 = match s[end..].trim_start().split(|c: char| c.is_whitespace() || c == '(').next().unwrap_or("") {
        "B" => 1.0,
        "kB" | "KB" => 1e3,
        "MB" => 1e6,
        "GB" => 1e9,
        "TB" => 1e12,
        _ => return 0,
    };
    (n * unit).round() as u64
}

// ---------------- Thư mục build/cài đặt trong project ----------------
struct Rule {
    name: &'static str,
    need: &'static [&'static str],
    need_ext: Option<&'static str>,
    inside: Option<&'static str>,
    rebuild: &'static str,
}

const ARTIFACTS: &[Rule] = &[
    Rule { name: "node_modules", need: &[], need_ext: None, inside: None, rebuild: "npm install" },
    Rule { name: ".next", need: &["package.json"], need_ext: None, inside: None, rebuild: "npm run build" },
    Rule { name: "target", need: &["pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml"], need_ext: None, inside: None, rebuild: "mvn package / cargo build" },
    Rule { name: "bin", need: &[], need_ext: Some(".csproj"), inside: None, rebuild: "dotnet build" },
    Rule { name: "obj", need: &[], need_ext: Some(".csproj"), inside: None, rebuild: "dotnet build" },
    Rule { name: ".venv", need: &[], need_ext: None, inside: Some("pyvenv.cfg"), rebuild: "python -m venv .venv && pip install -r requirements.txt" },
    Rule { name: "venv", need: &[], need_ext: None, inside: Some("pyvenv.cfg"), rebuild: "python -m venv venv && pip install -r requirements.txt" },
    Rule { name: ".gradle", need: &["build.gradle", "build.gradle.kts", "settings.gradle"], need_ext: None, inside: None, rebuild: "gradle build" },
];

fn rule_matches(r: &Rule, full: &Path, siblings: &[String]) -> bool {
    if !r.need.is_empty() && !r.need.iter().any(|f| siblings.iter().any(|s| s == f)) {
        return false;
    }
    if let Some(ext) = r.need_ext {
        if !siblings.iter().any(|s| s.ends_with(ext)) {
            return false;
        }
    }
    if let Some(inside) = r.inside {
        if !full.join(inside).exists() {
            return false;
        }
    }
    true
}

struct Found {
    full: String,
    parent: String,
    rebuild: &'static str,
}

fn walk(d: &Path, depth: usize, found: &Mutex<Vec<Found>>, counter: &AtomicUsize, progress: Progress, ctl: &Ctl) {
    if ctl.cancelled() {
        return;
    }
    let Ok(rd) = std::fs::read_dir(d) else { return };
    let entries: Vec<_> = rd.flatten().collect();
    if counter.fetch_add(1, Ordering::Relaxed) % 200 == 199 {
        progress(&format!("Đang tìm trong {}", rel(&d.to_string_lossy())));
    }
    let names: Vec<String> = entries.iter().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    for e in &entries {
        // symlink không phải thư mục thật => bỏ qua
        if !e.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let name = e.file_name().to_string_lossy().into_owned();
        let path = e.path();
        if let Some(rule) = ARTIFACTS.iter().find(|r| r.name == name) {
            if rule_matches(rule, &path, &names) {
                found.lock().unwrap().push(Found { full: path.to_string_lossy().into_owned(), parent: d.to_string_lossy().into_owned(), rebuild: rule.rebuild });
                continue;
            }
        }
        if depth >= MAX_DEPTH || SKIP_DIRS.contains(&name.as_str()) || name.starts_with('.') {
            continue;
        }
        walk(&path, depth + 1, found, counter, progress, ctl);
    }
}

struct Activity {
    git: Mutex<HashMap<String, f64>>,
}

impl Activity {
    fn git_activity(&self, d: &str) -> f64 {
        let hm = home();
        let mut best: f64 = 0.0;
        let mut cur = PathBuf::from(d);
        while cur.to_string_lossy().starts_with(&hm) && cur.to_string_lossy() != hm {
            let key = cur.to_string_lossy().into_owned();
            let cached = self.git.lock().unwrap().get(&key).copied();
            let v = cached.unwrap_or_else(|| {
                let v = mtime(&cur.join(".git/logs/HEAD")).max(mtime(&cur.join(".git/index")));
                self.git.lock().unwrap().insert(key, v);
                v
            });
            best = best.max(v);
            if !cur.pop() {
                break;
            }
        }
        best
    }

    fn last_active(&self, d: &str) -> f64 {
        let mut best = self.git_activity(d);
        for f in ["package.json", "package-lock.json", "pom.xml", "requirements.txt"] {
            best = best.max(mtime(&Path::new(d).join(f)));
        }
        if best == 0.0 { mtime(Path::new(d)) } else { best }
    }
}

fn scan_projects(opts: &Options, progress: Progress, ctl: &Ctl) -> Vec<Item> {
    let hm = home();
    let roots: Vec<PathBuf> = std::fs::read_dir(&hm)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
                .filter(|e| {
                    let n = e.file_name().to_string_lossy().into_owned();
                    !n.starts_with('.') && !SKIP_DIRS.contains(&n.as_str())
                })
                .map(|e| e.path())
                .collect()
        })
        .unwrap_or_default();
    let found = Mutex::new(Vec::new());
    let counter = AtomicUsize::new(0);
    pool_map(&roots, 4, &|r| walk(r, 1, &found, &counter, progress, ctl));
    if ctl.cancelled() {
        return vec![];
    }
    let found = found.into_inner().unwrap();
    progress(&format!("Đo dung lượng {} thư mục...", found.len()));
    let act = Activity { git: Mutex::new(HashMap::new()) };
    pool_map(&found, 4, &|f| {
        if ctl.cancelled() {
            return None;
        }
        let size = size_of(&f.full);
        if size < opts.min_size {
            return None;
        }
        let days = ((now() - act.last_active(&f.parent)) / DAY) as i64;
        let active = days < ACTIVE_DAYS;
        Some(Item {
            id: id_of(&["dir", &f.full]),
            group: "project".into(),
            kind: "dir".into(),
            paths: vec![f.full.clone()],
            title: rel(&f.full),
            size,
            level: if active { "review" } else { "safe" }.into(),
            note: if active { format!("Project đang làm (hoạt động {days} ngày trước)") } else { format!("Project lâu không đụng ({days} ngày)") },
            rebuild: Some(f.rebuild.into()),
            ..Default::default()
        })
    })
    .into_iter()
    .flatten()
    .collect()
}

// ---------------- Cache ----------------
struct CacheDef {
    paths: Vec<String>,
    title: &'static str,
    note: &'static str,
    review: bool,
    contents: bool,
}

fn caches() -> Vec<CacheDef> {
    let c = |paths: Vec<String>, title, note, review| CacheDef { paths, title, note, review, contents: false };
    let code = ["Cache", "CachedData", "CachedExtensionVSIXs", "Code Cache", "GPUCache"].iter().map(|d| h(&format!(".config/Code/{d}"))).collect();
    vec![
        c(vec![h(".npm/_cacache")], "npm cache", "npm tự tải lại khi cần", false),
        c(vec![h(".npm/_npx")], "npx cache", "npx tự tải lại khi chạy", false),
        c(vec![h(".cache/pip")], "pip cache", "pip tự tải lại khi cần", false),
        c(vec![h(".cache/ms-playwright")], "Trình duyệt của Playwright", "Tải lại bằng npx playwright install", false),
        c(vec![h(".cache/typescript")], "TypeScript cache", "Tự tạo lại", false),
        c(vec![h(".cache/prisma")], "Prisma engines", "Tải lại khi chạy Prisma", false),
        c(vec![h(".cache/electron")], "Electron binary cache", "Tải lại khi npm install", false),
        c(vec![h(".cache/yarn")], "Yarn cache", "Yarn tự tải lại khi cần", false),
        c(vec![h(".local/share/pnpm/store"), h(".cache/pnpm")], "pnpm store", "pnpm tải lại khi cài package", false),
        c(vec![h(".gradle/caches")], "Gradle cache", "Gradle tải lại khi build", true),
        c(vec![h(".m2/repository")], "Maven repository", "Maven tải lại khi build", true),
        c(vec![h(".cargo/registry")], "Cargo registry", "Cargo tải lại khi build", true),
        c(vec![h(".cache/thumbnails")], "Ảnh thu nhỏ (thumbnails)", "Tự tạo lại khi mở thư mục ảnh", false),
        c(vec![h(".cache/google-chrome"), h(".cache/chromium")], "Cache trình duyệt Chrome/Chromium", "Trang web sẽ tải chậm hơn lần đầu", false),
        c(vec![h(".cache/mozilla/firefox")], "Cache Firefox", "Trang web sẽ tải chậm hơn lần đầu", false),
        c(vec![h(".cache/huggingface")], "Model Hugging Face", "Phải tải lại nếu dùng", true),
        c(vec![h(".cache/torch")], "Model PyTorch", "Phải tải lại nếu dùng", true),
        c(vec![h(".cache/tracker3")], "Chỉ mục tìm kiếm GNOME", "Tự tạo lại", false),
        c(vec![h(".cache/kagglehub")], "Dữ liệu/model từ Kaggle", "Phải tải lại nếu dùng", true),
        c(vec![h(".cache/rtmlib")], "Model rtmlib", "Phải tải lại nếu dùng", true),
        c(code, "Cache của VS Code", "VS Code tự tạo lại", false),
        CacheDef { paths: vec![h(".local/share/Trash/files"), h(".local/share/Trash/info")], title: "Thùng rác", note: "Xoá vĩnh viễn", review: false, contents: true },
    ]
}

fn scan_caches(opts: &Options, ctl: &Ctl) -> Vec<Item> {
    let defs = caches();
    let mut items: Vec<Item> = pool_map(&defs, 4, &|c| {
        if ctl.cancelled() {
            return None;
        }
        let present: Vec<String> = c.paths.iter().filter(|p| Path::new(p).exists()).cloned().collect();
        if present.is_empty() {
            return None;
        }
        let size: u64 = present.iter().map(|p| size_of(p)).sum();
        if size < opts.min_size {
            return None;
        }
        let refs: Vec<&str> = present.iter().map(String::as_str).collect();
        let mut parts = vec!["cache"];
        parts.extend(refs);
        Some(Item {
            id: id_of(&parts),
            group: "cache".into(),
            kind: if c.contents { "contents" } else { "dir" }.into(),
            paths: present,
            title: c.title.into(),
            size,
            level: if c.review { "review" } else { "safe" }.into(),
            note: c.note.into(),
            ..Default::default()
        })
    })
    .into_iter()
    .flatten()
    .collect();

    // Các thư mục lớn khác trong ~/.cache chưa nằm trong danh sách trên
    let known: HashSet<String> = defs.iter().flat_map(|c| c.paths.clone()).collect();
    let subs: Vec<String> = std::fs::read_dir(h(".cache"))
        .map(|rd| rd.flatten().filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false)).map(|e| e.path().to_string_lossy().into_owned()).collect())
        .unwrap_or_default();
    items.extend(
        pool_map(&subs, 4, &|p| {
            if known.contains(p) || ctl.cancelled() {
                return None;
            }
            let size = size_of(p);
            if size < 200 * MB {
                return None;
            }
            Some(Item {
                id: id_of(&["cache", p]),
                group: "cache".into(),
                kind: "dir".into(),
                paths: vec![p.clone()],
                title: rel(p),
                size,
                level: "review".into(),
                note: "Cache của ứng dụng khác, kiểm tra trước khi xoá".into(),
                ..Default::default()
            })
        })
        .into_iter()
        .flatten(),
    );
    items
}

// ---------------- Docker ----------------
struct DockerRule {
    name: &'static str,
    args: &'static [&'static str],
    safe: bool,
    note: &'static str,
}
const DOCKER: &[DockerRule] = &[
    DockerRule { name: "Build Cache", args: &["builder", "prune", "-af"], safe: true, note: "Lần docker build sau sẽ chậm hơn" },
    DockerRule { name: "Images", args: &["image", "prune", "-af"], safe: false, note: "Xoá mọi image không có container, kể cả image của app đang làm" },
    DockerRule { name: "Containers", args: &["container", "prune", "-f"], safe: true, note: "Chỉ xoá container đã dừng" },
    DockerRule { name: "Local Volumes", args: &["volume", "prune", "-f"], safe: false, note: "Có thể chứa dữ liệu database" },
];

fn scan_docker(opts: &Options) -> Vec<Item> {
    let Some(out) = run("docker", &["system", "df", "--format", "{{json .}}"], Duration::from_secs(20)) else { return vec![] };
    out.lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter_map(|row| {
            let ty = row["Type"].as_str()?.to_string();
            let rule = DOCKER.iter().find(|r| r.name == ty)?;
            let size = parse_docker_size(row["Reclaimable"].as_str().unwrap_or(""));
            if size < opts.min_size {
                return None;
            }
            Some(Item {
                id: id_of(&["docker", &ty]),
                group: "docker".into(),
                kind: "docker".into(),
                title: format!("Docker: {ty}"),
                size,
                level: if rule.safe { "safe" } else { "review" }.into(),
                note: rule.note.into(),
                cmd: Some(format!("docker {}", rule.args.join(" "))),
                docker_type: Some(ty),
                ..Default::default()
            })
        })
        .collect()
}

// ---------------- Cần quyền root ----------------
/// Chạy với quyền root qua pkexec (polkit). Chỉ các lệnh cố định này.
fn root_cmd(t: &str) -> Option<&'static [&'static str]> {
    match t {
        "apt" => Some(&["apt-get", "clean"]),
        "journal" => Some(&["journalctl", "--vacuum-size=100M"]),
        _ => None,
    }
}

pub fn parse_journal_usage(s: &str) -> Option<u64> {
    let rest = s.split("take up ").nth(1)?;
    let end = rest.find(|c: char| !(c.is_ascii_digit() || c == '.'))?;
    let n: f64 = rest[..end].parse().ok()?;
    let mult: f64 = match rest[end..].chars().next()? {
        'K' => 1024.0,
        'M' => 1024.0 * 1024.0,
        'G' => 1024.0f64.powi(3),
        'T' => 1024.0f64.powi(4),
        _ => return None,
    };
    Some((n * mult) as u64)
}

fn scan_system(opts: &Options) -> Vec<Item> {
    let mut items = vec![];
    let apt = size_of("/var/cache/apt/archives");
    if apt >= opts.min_size {
        items.push(Item {
            id: id_of(&["sys", "apt"]), group: "system".into(), kind: "root".into(), sys_type: Some("apt".into()), title: "Cache gói apt".into(),
            size: apt, level: "safe".into(), note: "Cần quyền root: sẽ hiện hộp thoại nhập mật khẩu (polkit)".into(), cmd: Some("apt-get clean".into()), ..Default::default()
        });
    }
    if let Some(total) = run("journalctl", &["--disk-usage"], Duration::from_secs(20)).and_then(|j| parse_journal_usage(&j)) {
        let keep = 100 * MB;
        if total.saturating_sub(keep) >= opts.min_size {
            items.push(Item {
                id: id_of(&["sys", "journal"]), group: "system".into(), kind: "root".into(), sys_type: Some("journal".into()), title: "Log hệ thống (journal)".into(),
                size: total - keep, level: "safe".into(), note: "Cần quyền root (polkit), giữ lại 100 MB gần nhất".into(), cmd: Some("journalctl --vacuum-size=100M".into()), ..Default::default()
            });
        }
    }
    items
}

// ---------------- Tệp lớn + tệp trùng ----------------
#[derive(Clone, Debug)]
pub struct Row {
    pub p: String,
    pub size: u64,
    pub mtime: f64,
}

fn list_files(min_bytes: u64) -> Vec<Row> {
    let hm = home();
    let mb = format!("+{}M", min_bytes / MB);
    let out = run("find", &[&hm, "-xdev", "-type", "f", "-size", &mb, "-printf", "%s\t%T@\t%p\n"], Duration::from_secs(120)).unwrap_or_default();
    out.lines()
        .filter_map(|line| {
            let mut it = line.splitn(3, '\t');
            let (size, mt, p) = (it.next()?, it.next()?, it.next()?);
            if LARGE_SKIP.iter().any(|k| p.contains(k)) {
                return None;
            }
            // thư mục/tệp ẩn: thường là dữ liệu chương trình
            let relp = p.strip_prefix(&format!("{hm}/")).unwrap_or(p);
            if relp.split('/').any(|c| c.starts_with('.')) {
                return None;
            }
            Some(Row { p: p.to_string(), size: size.parse().ok()?, mtime: mt.parse().ok()? })
        })
        .collect()
}

fn hash_file(path: &str, limit: Option<u64>) -> Option<String> {
    let mut f = std::fs::File::open(path).ok()?;
    let mut hs = Sha256::new();
    let mut left = limit;
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let want = match left {
            Some(0) => break,
            Some(l) => l.min(buf.len() as u64) as usize,
            None => buf.len(),
        };
        let n = f.read(&mut buf[..want]).ok()?;
        if n == 0 {
            break;
        }
        hs.update(&buf[..n]);
        if let Some(l) = left.as_mut() {
            *l -= n as u64;
        }
    }
    Some(hs.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Nhóm theo kích thước -> hash 64 KB đầu -> hash toàn bộ. Chỉ tệp qua cả ba bước mới là trùng.
pub fn find_duplicates(rows: &[Row], ctl: &Ctl) -> Vec<Vec<Row>> {
    let mut by_size: BTreeMap<u64, Vec<&Row>> = BTreeMap::new();
    for r in rows {
        by_size.entry(r.size).or_default().push(r);
    }
    let mut sets = vec![];
    for group in by_size.values().filter(|g| g.len() >= 2) {
        let keys = pool_map(group, 3, &|r| if ctl.cancelled() { None } else { hash_file(&r.p, Some(65536)) });
        let mut partial: BTreeMap<String, Vec<&Row>> = BTreeMap::new();
        for (r, k) in group.iter().zip(keys) {
            if let Some(k) = k {
                partial.entry(k).or_default().push(r);
            }
        }
        for cand in partial.values().filter(|c| c.len() >= 2) {
            if ctl.cancelled() {
                continue;
            }
            let keys = pool_map(cand, 2, &|r| hash_file(&r.p, None));
            let mut full: BTreeMap<String, Vec<Row>> = BTreeMap::new();
            for (r, k) in cand.iter().zip(keys) {
                if let Some(k) = k {
                    full.entry(k).or_default().push((*r).clone());
                }
            }
            sets.extend(full.into_values().filter(|s| s.len() > 1));
        }
    }
    sets
}

fn scan_files(opts: &Options, skip_paths: &[String], progress: Progress, ctl: &Ctl) -> Vec<Item> {
    if !opts.large && !opts.dup {
        return vec![];
    }
    let min_bytes = [opts.large.then_some(opts.large_size), opts.dup.then_some(opts.dup_size)].into_iter().flatten().min().unwrap_or(0);
    let rows: Vec<Row> = list_files(min_bytes).into_iter().filter(|r| !skip_paths.iter().any(|d| r.p.starts_with(&format!("{d}/")))).collect();
    if ctl.cancelled() {
        return vec![];
    }
    let mut items = vec![];
    let mut in_dup: HashSet<String> = HashSet::new();

    if opts.dup {
        progress("Đang so sánh tệp trùng lặp...");
        let cand: Vec<Row> = rows.iter().filter(|r| r.size >= opts.dup_size && !DUP_SKIP.iter().any(|k| r.p.contains(k))).cloned().collect();
        let mut sets = find_duplicates(&cand, ctl);
        sets.sort_by_key(|s| std::cmp::Reverse(s[0].size * (s.len() as u64 - 1)));
        for mut same in sets.into_iter().take(30) {
            same.sort_by(|a, b| a.mtime.partial_cmp(&b.mtime).unwrap_or(std::cmp::Ordering::Equal).then(a.p.len().cmp(&b.p.len()))); // giữ bản cũ nhất
            let keep = same[0].p.clone();
            in_dup.extend(same.iter().map(|r| r.p.clone()));
            for r in &same[1..] {
                items.push(Item {
                    id: id_of(&["dup", &r.p]), group: "dupe".into(), kind: "file".into(), paths: vec![r.p.clone()], title: rel(&r.p), size: r.size, level: "review".into(),
                    note: format!("Trùng nội dung với {} (bản này mới hơn, sẽ giữ lại bản kia)", rel(&keep)), ..Default::default()
                });
            }
        }
    }

    if opts.large {
        let mut big: Vec<&Row> = rows.iter().filter(|r| r.size >= opts.large_size && !in_dup.contains(&r.p)).collect();
        big.sort_by_key(|r| std::cmp::Reverse(r.size));
        let dl = format!("{}/", h("Downloads"));
        for r in big.into_iter().take(40) {
            let days = ((now() - r.mtime) / DAY) as i64;
            items.push(Item {
                id: id_of(&["file", &r.p]), group: "large".into(), kind: "file".into(), paths: vec![r.p.clone()], title: rel(&r.p), size: r.size, level: "review".into(),
                note: format!("{}Sửa lần cuối {days} ngày trước · không tự tạo lại được", if r.p.starts_with(&dl) { "Trong Downloads · " } else { "" }), ..Default::default()
            });
        }
    }
    items
}

// ---------------- Treemap ----------------
/// Đường dẫn tương đối so với HOME → dung lượng từng mục con. Từ chối thoát ra ngoài HOME.
pub fn tree_dir(rel_path: &str) -> Result<Tree, String> {
    let hm = std::fs::canonicalize(home()).map_err(|e| e.to_string())?;
    let d = std::fs::canonicalize(Path::new(&home()).join(if rel_path.is_empty() { "." } else { rel_path })).map_err(|e| e.to_string())?;
    if d != hm && !d.starts_with(&hm) {
        return Err("Ngoài thư mục home".into());
    }
    let dirs: HashSet<String> = std::fs::read_dir(&d)
        .map_err(|e| e.to_string())?
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    let ds = d.to_string_lossy().into_owned();
    let out = run("du", &["-ak", "-x", "--max-depth=1", "--", &ds], Duration::from_secs(240)).unwrap_or_default();
    let (mut entries, mut total) = (vec![], 0u64);
    for line in out.lines() {
        let Some((n, p)) = line.split_once('\t') else { continue };
        let Ok(n) = n.parse::<u64>() else { continue };
        if p == ds {
            total = n * 1024;
            continue;
        }
        let name = Path::new(p).file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        entries.push(TreeEntry { dir: dirs.contains(&name), name, size: n * 1024 });
    }
    entries.sort_by_key(|e| std::cmp::Reverse(e.size));
    entries.truncate(60);
    let r = d.strip_prefix(&hm).map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
    Ok(Tree { path: r, total, entries })
}

// ---------------- Quét ----------------
pub fn disk_usage() -> DiskUsage {
    let disks = sysinfo::Disks::new_with_refreshed_list().ok();
    let pick = disks.as_ref().and_then(|d| d.iter().find(|x| x.mount_point() == Path::new("/")).or_else(|| d.iter().max_by_key(|x| x.total_space())).map(|x| (x.total_space(), x.available_space())));
    let (total, free) = pick.unwrap_or((0, 0));
    DiskUsage { total, free }
}

/// None nếu bị huỷ.
pub fn scan(opts: &Options, ctl: &Ctl, progress: Progress) -> Option<ScanResult> {
    progress("Đang quét project...");
    let projects = scan_projects(opts, progress, ctl);
    if ctl.cancelled() {
        return None;
    }
    progress("Đang quét cache...");
    let cache = scan_caches(opts, ctl);
    if ctl.cancelled() {
        return None;
    }
    progress("Đang kiểm tra Docker...");
    let docker = scan_docker(opts);
    let system = scan_system(opts);
    if ctl.cancelled() {
        return None;
    }
    progress("Đang tìm tệp lớn...");
    let skip: Vec<String> = projects.iter().map(|p| p.paths[0].clone()).collect();
    let files = scan_files(opts, &skip, progress, ctl);
    if ctl.cancelled() {
        return None;
    }
    let mut items: Vec<Item> = [projects, cache, docker, system, files].concat();
    items.sort_by_key(|i| std::cmp::Reverse(i.size));
    Some(ScanResult { items, disk: disk_usage(), scanned_at: now() })
}

// ---------------- Dọn ----------------
/// Chỉ cho xoá đường dẫn nằm trong HOME (và không phải chính HOME), không có `..`, cha không thoát khỏi HOME qua symlink.
pub fn assert_inside_home(p: &str) -> Result<PathBuf, String> {
    let hm = home();
    let path = PathBuf::from(p);
    let bad = || format!("Từ chối xoá ngoài thư mục home: {p}");
    if hm.is_empty() || !path.starts_with(&hm) || path == Path::new(&hm) || path.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
        return Err(bad());
    }
    let real_home = std::fs::canonicalize(&hm).map_err(|e| e.to_string())?;
    if let Some(parent) = path.parent() {
        if let Ok(rp) = std::fs::canonicalize(parent) {
            if !rp.starts_with(&real_home) {
                return Err(bad());
            }
        }
    }
    Ok(path)
}

fn remove(p: &Path, trash: Option<&dyn Fn(&str) -> Result<(), String>>) -> Result<(), String> {
    if let Some(t) = trash {
        return t(&p.to_string_lossy());
    }
    let meta = std::fs::symlink_metadata(p).map_err(|e| e.to_string())?;
    if meta.is_dir() { std::fs::remove_dir_all(p) } else { std::fs::remove_file(p) }.map_err(|e| e.to_string())
}

/// `trash`: hàm chuyển vào thùng rác (chỉ dùng cho nhóm large/dupe); None thì xoá vĩnh viễn.
pub fn clean_item(item: &Item, trash: Option<&dyn Fn(&str) -> Result<(), String>>) -> Result<(), String> {
    match item.kind.as_str() {
        "docker" => {
            let rule = DOCKER.iter().find(|r| Some(r.name) == item.docker_type.as_deref()).ok_or("Lệnh Docker không hợp lệ")?;
            let o = Command::new("docker").args(rule.args).output().map_err(|e| e.to_string())?;
            if !o.status.success() {
                let e = String::from_utf8_lossy(&o.stderr).trim().to_string();
                return Err(if e.is_empty() { "docker lỗi".into() } else { e });
            }
            Ok(())
        }
        "root" => {
            let args = item.sys_type.as_deref().and_then(root_cmd).ok_or("Lệnh hệ thống không hợp lệ")?;
            let o = Command::new("pkexec").args(args).output().map_err(|e| e.to_string())?;
            match o.status.code() {
                Some(126) | Some(127) => Err("Đã huỷ xác thực quyền root".into()),
                _ if !o.status.success() => {
                    let e = String::from_utf8_lossy(&o.stderr).trim().to_string();
                    Err(if e.is_empty() { "lệnh lỗi".into() } else { e })
                }
                _ => Ok(()),
            }
        }
        _ => {
            let trash = if TRASHABLE.contains(&item.group.as_str()) { trash } else { None };
            for p in &item.paths {
                let r = assert_inside_home(p)?;
                let res = if item.kind == "contents" {
                    match std::fs::read_dir(&r) {
                        Ok(rd) => rd.flatten().try_for_each(|e| remove(&e.path(), None)),
                        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                        Err(e) => Err(e.to_string()),
                    }
                } else {
                    remove(&r, trash)
                };
                match res {
                    Err(e) if !e.contains("No such file") && !e.contains("not found") => return Err(e),
                    _ => {}
                }
            }
            Ok(())
        }
    }
}

/// Chuyển vào thùng rác bằng `gio trash`.
pub fn gio_trash(p: &str) -> Result<(), String> {
    let o = Command::new("gio").args(["trash", "--", p]).output().map_err(|e| format!("Không chạy được gio: {e}"))?;
    if o.status.success() { Ok(()) } else { Err(String::from_utf8_lossy(&o.stderr).trim().to_string()) }
}

// ---------------- Xuất CSV ----------------
fn csv_cell(v: &str) -> String {
    // Ô bắt đầu bằng = + - @ bị Excel/Sheets hiểu là công thức, nên thêm dấu nháy phía trước.
    let t = if matches!(v.chars().next(), Some('=' | '+' | '-' | '@' | '\t' | '\r')) { format!("'{v}") } else { v.to_string() };
    format!("\"{}\"", t.replace('"', "\"\""))
}

pub fn export_csv(items: &[&Item], path: &Path) -> Result<(), String> {
    let mut f = std::fs::File::create(path).map_err(|e| e.to_string())?;
    let mut s = String::from("\u{feff}");
    let row = |cols: Vec<String>| cols.iter().map(|c| csv_cell(c)).collect::<Vec<_>>().join(",") + "\r\n";
    s += &row(["Nhóm", "Tên", "Đường dẫn", "Dung lượng (byte)", "Dung lượng (MB)", "Mức độ", "Ghi chú"].iter().map(|x| x.to_string()).collect());
    for i in items {
        let paths = if i.paths.is_empty() { i.cmd.clone().unwrap_or_default() } else { i.paths.join(" | ") };
        s += &row(vec![i.group.clone(), i.title.clone(), paths, i.size.to_string(), format!("{:.1}", i.size as f64 / MB as f64), if i.level == "safe" { "An toàn" } else { "Xem lại" }.into(), i.note.clone()]);
    }
    f.write_all(s.as_bytes()).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(group: &str, kind: &str, paths: Vec<String>) -> Item {
        Item { group: group.into(), kind: kind.into(), paths, ..Default::default() }
    }

    #[test]
    fn duplicates_need_same_content() {
        let d = tempfile::tempdir().unwrap();
        let mk = |name: &str, data: Vec<u8>| {
            let p = d.path().join(name);
            std::fs::write(&p, &data).unwrap();
            Row { p: p.to_string_lossy().into(), size: data.len() as u64, mtime: 0.0 }
        };
        let head = vec![b'x'; 70000]; // lớn hơn 64 KB để kiểm tra bước hash toàn bộ
        let with = |t: u8| [head.clone(), vec![t]].concat();
        let rows = vec![mk("a", with(b'1')), mk("b", with(b'1')), mk("c", with(b'2')), mk("d", b"zz".to_vec())];
        let sets = find_duplicates(&rows, &Ctl::default());
        assert_eq!(sets.len(), 1);
        let names: HashSet<_> = sets[0].iter().map(|r| Path::new(&r.p).file_name().unwrap().to_string_lossy().into_owned()).collect();
        assert_eq!(names, HashSet::from(["a".to_string(), "b".to_string()]));
    }

    #[test]
    fn refuses_outside_home() {
        let hm = home();
        for bad in ["/etc/passwd".to_string(), hm.clone(), format!("{hm}/../other"), "/".to_string(), format!("{hm}/a/../../x")] {
            assert!(assert_inside_home(&bad).is_err(), "{bad}");
        }
        assert!(assert_inside_home(&format!("{hm}/a/b")).is_ok());
    }

    #[test]
    fn refuses_symlink_escape() {
        let hm = home();
        let outside = tempfile::tempdir().unwrap();
        std::fs::write(outside.path().join("victim"), "x").unwrap();
        let d = tempfile::tempdir_in(&hm).unwrap();
        let link = d.path().join("link");
        std::os::unix::fs::symlink(outside.path(), &link).unwrap();
        let target = link.join("victim");
        assert!(assert_inside_home(&target.to_string_lossy()).is_err());
        assert!(outside.path().join("victim").exists());
    }

    #[test]
    fn rejects_unknown_commands() {
        let mut it = item("system", "root", vec![]);
        it.sys_type = Some("rm -rf /".into());
        assert!(clean_item(&it, None).is_err());
        let mut it = item("docker", "docker", vec![]);
        it.docker_type = Some("x".into());
        assert!(clean_item(&it, None).is_err());
    }

    #[test]
    fn tree_rejects_escape() {
        for bad in ["..", "/etc", "../.."] {
            assert!(tree_dir(bad).is_err(), "{bad}");
        }
        assert!(tree_dir("").is_ok());
    }

    #[test]
    fn trash_only_for_large_and_dupe() {
        let d = tempfile::tempdir_in(home()).unwrap();
        for (group, expect_trash) in [("cache", false), ("large", true), ("dupe", true)] {
            let p = d.path().join(group);
            std::fs::write(&p, "").unwrap();
            let calls = Mutex::new(0);
            let t = |_: &str| -> Result<(), String> { *calls.lock().unwrap() += 1; Ok(()) };
            clean_item(&item(group, "file", vec![p.to_string_lossy().into()]), Some(&t)).unwrap();
            assert_eq!(*calls.lock().unwrap() > 0, expect_trash, "{group}");
            assert_eq!(p.exists(), expect_trash, "{group}: tệp không đi qua trash thì phải bị xoá thật");
        }
    }

    #[test]
    fn contents_kind_keeps_dir() {
        let d = tempfile::tempdir_in(home()).unwrap();
        std::fs::write(d.path().join("a"), "1").unwrap();
        std::fs::create_dir(d.path().join("sub")).unwrap();
        std::fs::write(d.path().join("sub/b"), "2").unwrap();
        clean_item(&item("cache", "contents", vec![d.path().to_string_lossy().into()]), None).unwrap();
        assert!(d.path().exists());
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 0);
    }

    #[test]
    fn csv_escapes() {
        let it = Item { group: "large".into(), title: "=HYPERLINK(\"x\")".into(), paths: vec!["/a/b,\"c\"".into()], size: 1048576, level: "review".into(), note: "+cmd".into(), ..Default::default() };
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("o.csv");
        export_csv(&[&it], &p).unwrap();
        let raw = std::fs::read_to_string(&p).unwrap();
        assert!(raw.contains("\"'=HYPERLINK(\"\"x\"\")\""));
        assert!(raw.contains("\"'+cmd\""));
        assert!(raw.contains("\"1.0\""));
    }

    #[test]
    fn parsers() {
        assert_eq!(parse_docker_size("1.5GB (50%)"), 1_500_000_000);
        assert_eq!(parse_docker_size("12.3kB"), 12_300);
        assert_eq!(parse_docker_size("0B"), 0);
        assert_eq!(parse_docker_size("???"), 0);
        assert_eq!(parse_journal_usage("Archived and active journals take up 1.5G in the file system."), Some((1.5 * 1024.0f64.powi(3)) as u64));
        assert_eq!(id_of(&["a", "b"]).len(), 12);
    }

    #[test]
    fn artifact_rules_avoid_false_positives() {
        let d = tempfile::tempdir().unwrap();
        let bin = ARTIFACTS.iter().find(|r| r.name == "bin").unwrap();
        assert!(!rule_matches(bin, &d.path().join("bin"), &["main.sh".into()]));
        assert!(rule_matches(bin, &d.path().join("bin"), &["App.csproj".into()]));
        let target = ARTIFACTS.iter().find(|r| r.name == "target").unwrap();
        assert!(!rule_matches(target, d.path(), &["README.md".into()]));
        assert!(rule_matches(target, d.path(), &["Cargo.toml".into()]));
    }

    #[test]
    fn scan_finds_node_modules_in_temp_project() {
        // Tạo project giả trong HOME, quét và kiểm tra nhận diện + mức độ "lâu không đụng/đang làm".
        let proj = tempfile::Builder::new().prefix("dd-scan-test-").tempdir_in(home()).unwrap();
        std::fs::write(proj.path().join("package.json"), "{}").unwrap();
        let nm = proj.path().join("node_modules");
        std::fs::create_dir(&nm).unwrap();
        std::fs::write(nm.join("big.bin"), vec![0u8; 3 * 1024 * 1024]).unwrap();
        let opts = Options { min_size: 1024 * 1024, large: false, dup: false, ..Options::default() };
        let items = scan_projects(&opts, &|_| {}, &Ctl::default());
        let hit = items.iter().find(|i| i.paths[0] == nm.to_string_lossy()).expect("phải thấy node_modules");
        assert_eq!(hit.level, "review", "project vừa tạo => đang làm");
        assert!(hit.size >= 3 * 1024 * 1024);
        assert_eq!(hit.rebuild.as_deref(), Some("npm install"));
    }

    #[test]
    fn cancel_stops_scan() {
        let ctl = Ctl::default();
        ctl.cancel();
        assert!(scan(&Options::default(), &ctl, &|_| {}).is_none());
    }
}
