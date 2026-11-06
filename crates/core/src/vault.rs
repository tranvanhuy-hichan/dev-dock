use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Snippet {
    pub id: i64,
    pub title: String,
    pub language: String,
    pub code: String,
    pub description: String,
    pub tags: Vec<String>,
    pub folder: String,
    pub favorite: bool,
    pub gist_id: Option<String>,
    pub created: i64,
    pub updated: i64,
}

#[derive(Deserialize, Debug)]
pub struct SnippetInput {
    pub id: Option<i64>,
    pub title: String,
    pub language: String,
    pub code: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub folder: String,
    #[serde(default)]
    pub favorite: bool,
}

#[derive(Deserialize, Default, Debug)]
pub struct Filter {
    pub query: Option<String>,
    pub folder: Option<String>,
    pub tag: Option<String>,
    pub language: Option<String>,
    #[serde(default)]
    pub favorites_only: bool,
}

#[derive(Serialize, Debug)]
pub struct Facets {
    pub folders: Vec<(String, i64)>,
    pub tags: Vec<(String, i64)>,
    pub languages: Vec<(String, i64)>,
    pub total: i64,
    pub favorites: i64,
}

pub struct Vault {
    conn: Connection,
}

const COLS: &str = "id,title,language,code,description,tags,folder,favorite,gist_id,created,updated";

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn split_tags(s: &str) -> Vec<String> {
    s.split(',').map(|t| t.trim().to_string()).filter(|t| !t.is_empty()).collect()
}

fn row_to_snippet(r: &Row) -> rusqlite::Result<Snippet> {
    Ok(Snippet {
        id: r.get(0)?,
        title: r.get(1)?,
        language: r.get(2)?,
        code: r.get(3)?,
        description: r.get(4)?,
        tags: split_tags(&r.get::<_, String>(5)?),
        folder: r.get(6)?,
        favorite: r.get::<_, i64>(7)? != 0,
        gist_id: r.get(8)?,
        created: r.get(9)?,
        updated: r.get(10)?,
    })
}

impl Vault {
    pub fn open(path: &Path) -> Result<Self, String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        Self::from_conn(Connection::open(path).map_err(|e| e.to_string())?)
    }

    pub fn in_memory() -> Result<Self, String> {
        Self::from_conn(Connection::open_in_memory().map_err(|e| e.to_string())?)
    }

    fn from_conn(conn: Connection) -> Result<Self, String> {
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS snippets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'text',
                code TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '', folder TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0, gist_id TEXT,
                created INTEGER NOT NULL, updated INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);",
        )
        .map_err(|e| e.to_string())?;
        Ok(Self { conn })
    }

    pub fn list(&self, f: &Filter) -> Result<Vec<Snippet>, String> {
        let mut sql = format!("SELECT {COLS} FROM snippets WHERE 1=1");
        let mut args: Vec<String> = vec![];
        if let Some(q) = f.query.as_deref().filter(|q| !q.trim().is_empty()) {
            let like = format!("%{}%", q.trim().replace('%', "\\%").replace('_', "\\_"));
            sql += " AND (title LIKE ?1 ESCAPE '\\' OR code LIKE ?1 ESCAPE '\\' OR description LIKE ?1 ESCAPE '\\' OR tags LIKE ?1 ESCAPE '\\')";
            args.push(like);
        }
        // Tham số vị trí cố định để tránh lệch số thứ tự
        let mut n = args.len();
        let mut push = |s: &mut String, cond: &str, v: String, args: &mut Vec<String>| {
            n += 1;
            *s += &format!(" AND {}", cond.replace('?', &format!("?{n}")));
            args.push(v);
        };
        if let Some(v) = f.folder.clone().filter(|s| !s.is_empty()) {
            push(&mut sql, "folder = ?", v, &mut args);
        }
        if let Some(v) = f.language.clone().filter(|s| !s.is_empty()) {
            push(&mut sql, "language = ?", v, &mut args);
        }
        if let Some(v) = f.tag.clone().filter(|s| !s.is_empty()) {
            push(&mut sql, "(',' || tags || ',') LIKE ?", format!("%,{v},%"), &mut args);
        }
        if f.favorites_only {
            sql += " AND favorite = 1";
        }
        sql += " ORDER BY favorite DESC, updated DESC";
        let mut st = self.conn.prepare(&sql).map_err(|e| e.to_string())?;
        let rows = st
            .query_map(rusqlite::params_from_iter(args.iter()), row_to_snippet)
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<_, _>>().map_err(|e| e.to_string())
    }

    pub fn get(&self, id: i64) -> Result<Option<Snippet>, String> {
        self.conn
            .query_row(&format!("SELECT {COLS} FROM snippets WHERE id=?1"), [id], row_to_snippet)
            .optional()
            .map_err(|e| e.to_string())
    }

    pub fn save(&self, i: SnippetInput) -> Result<Snippet, String> {
        let t = now();
        let tags = i.tags.iter().map(|t| t.trim()).filter(|t| !t.is_empty()).collect::<Vec<_>>().join(",");
        let id = match i.id {
            Some(id) => {
                let n = self
                    .conn
                    .execute(
                        "UPDATE snippets SET title=?1,language=?2,code=?3,description=?4,tags=?5,folder=?6,favorite=?7,updated=?8 WHERE id=?9",
                        params![i.title, i.language, i.code, i.description, tags, i.folder.trim(), i.favorite as i64, t, id],
                    )
                    .map_err(|e| e.to_string())?;
                if n == 0 {
                    return Err("Snippet không tồn tại".into());
                }
                id
            }
            None => {
                self.conn
                    .execute(
                        "INSERT INTO snippets(title,language,code,description,tags,folder,favorite,created,updated) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?8)",
                        params![i.title, i.language, i.code, i.description, tags, i.folder.trim(), i.favorite as i64, t],
                    )
                    .map_err(|e| e.to_string())?;
                self.conn.last_insert_rowid()
            }
        };
        self.get(id)?.ok_or_else(|| "Lỗi đọc lại snippet".into())
    }

    pub fn delete(&self, id: i64) -> Result<(), String> {
        self.conn.execute("DELETE FROM snippets WHERE id=?1", [id]).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn set_favorite(&self, id: i64, fav: bool) -> Result<(), String> {
        self.conn
            .execute("UPDATE snippets SET favorite=?1 WHERE id=?2", params![fav as i64, id])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn set_gist_id(&self, id: i64, gist: &str) -> Result<(), String> {
        self.conn
            .execute("UPDATE snippets SET gist_id=?1 WHERE id=?2", params![gist, id])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn find_by_gist(&self, gist: &str) -> Result<Option<Snippet>, String> {
        self.conn
            .query_row(&format!("SELECT {COLS} FROM snippets WHERE gist_id=?1"), [gist], row_to_snippet)
            .optional()
            .map_err(|e| e.to_string())
    }

    pub fn facets(&self) -> Result<Facets, String> {
        let all = self.list(&Filter::default())?;
        let mut folders = std::collections::BTreeMap::<String, i64>::new();
        let mut tags = std::collections::BTreeMap::<String, i64>::new();
        let mut langs = std::collections::BTreeMap::<String, i64>::new();
        for s in &all {
            if !s.folder.is_empty() {
                *folders.entry(s.folder.clone()).or_default() += 1;
            }
            for t in &s.tags {
                *tags.entry(t.clone()).or_default() += 1;
            }
            *langs.entry(s.language.clone()).or_default() += 1;
        }
        Ok(Facets {
            folders: folders.into_iter().collect(),
            tags: tags.into_iter().collect(),
            languages: langs.into_iter().collect(),
            total: all.len() as i64,
            favorites: all.iter().filter(|s| s.favorite).count() as i64,
        })
    }

    pub fn get_setting(&self, key: &str) -> Result<Option<String>, String> {
        self.conn
            .query_row("SELECT value FROM settings WHERE key=?1", [key], |r| r.get(0))
            .optional()
            .map_err(|e| e.to_string())
    }

    pub fn set_setting(&self, key: &str, value: &str) -> Result<(), String> {
        self.conn
            .execute(
                "INSERT INTO settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![key, value],
            )
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn delete_setting(&self, key: &str) -> Result<(), String> {
        self.conn.execute("DELETE FROM settings WHERE key=?1", [key]).map_err(|e| e.to_string())?;
        Ok(())
    }
}

// ---------------- GitHub Gist ----------------

#[derive(Deserialize, Debug)]
pub struct RemoteGist {
    pub id: String,
    pub description: Option<String>,
    pub files: std::collections::HashMap<String, RemoteFile>,
}

#[derive(Deserialize, Debug)]
pub struct RemoteFile {
    pub filename: Option<String>,
    pub language: Option<String>,
    pub content: Option<String>,
    pub truncated: Option<bool>,
}

fn client(token: &str) -> Result<reqwest::Client, String> {
    use reqwest::header::*;
    let mut h = HeaderMap::new();
    h.insert(AUTHORIZATION, format!("Bearer {token}").parse().map_err(|_| "Token không hợp lệ")?);
    h.insert(ACCEPT, "application/vnd.github+json".parse().unwrap());
    h.insert("X-GitHub-Api-Version", "2022-11-28".parse().unwrap());
    reqwest::Client::builder()
        .user_agent("DevDock")
        .default_headers(h)
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())
}

async fn check(r: reqwest::Response) -> Result<reqwest::Response, String> {
    if r.status().is_success() {
        return Ok(r);
    }
    let code = r.status();
    let body = r.text().await.unwrap_or_default();
    let msg = serde_json::from_str::<serde_json::Value>(&body)
        .ok()
        .and_then(|v| v["message"].as_str().map(String::from))
        .unwrap_or(body);
    Err(format!("GitHub {code}: {msg}"))
}

pub fn gist_filename(s: &Snippet) -> String {
    let safe: String = s.title.chars().map(|c| if c.is_alphanumeric() || "-_. ".contains(c) { c } else { '_' }).collect();
    format!("{}.{}", safe.trim(), ext_for(&s.language))
}

pub fn ext_for(lang: &str) -> &'static str {
    match lang {
        "javascript" => "js", "typescript" => "ts", "python" => "py", "rust" => "rs", "go" => "go",
        "java" => "java", "cpp" => "cpp", "c" => "c", "html" => "html", "css" => "css", "json" => "json",
        "sql" => "sql", "markdown" => "md", "xml" => "xml", "php" => "php", "yaml" => "yml",
        "shell" => "sh", _ => "txt",
    }
}

pub fn lang_for(filename: &str, gh_lang: Option<&str>) -> String {
    let ext = filename.rsplit('.').next().unwrap_or("").to_lowercase();
    let by_ext = match ext.as_str() {
        "js" | "jsx" | "mjs" => "javascript", "ts" | "tsx" => "typescript", "py" => "python", "rs" => "rust",
        "go" => "go", "java" => "java", "cpp" | "cc" | "hpp" | "h" => "cpp", "c" => "c", "html" | "htm" => "html",
        "css" => "css", "json" => "json", "sql" => "sql", "md" => "markdown", "xml" => "xml", "php" => "php",
        "yml" | "yaml" => "yaml", "sh" | "bash" => "shell", _ => "",
    };
    if !by_ext.is_empty() {
        return by_ext.into();
    }
    gh_lang.map(|l| l.to_lowercase()).unwrap_or_else(|| "text".into())
}

/// Tạo/cập nhật gist (secret) cho snippet. Trả về gist id.
pub async fn gist_push(token: &str, s: &Snippet) -> Result<String, String> {
    let c = client(token)?;
    let name = gist_filename(s);
    let desc = if s.description.is_empty() { s.title.clone() } else { format!("{} — {}", s.title, s.description) };
    let content = if s.code.is_empty() { " ".to_string() } else { s.code.clone() };
    let body = serde_json::json!({ "description": desc, "public": false, "files": { name: { "content": content } } });
    let r = match &s.gist_id {
        Some(id) => c.patch(format!("https://api.github.com/gists/{id}")).json(&body).send().await,
        None => c.post("https://api.github.com/gists").json(&body).send().await,
    }
    .map_err(|e| e.to_string())?;
    let v: serde_json::Value = check(r).await?.json().await.map_err(|e| e.to_string())?;
    v["id"].as_str().map(String::from).ok_or_else(|| "Phản hồi thiếu id".into())
}

/// Lấy danh sách gist của user (tối đa 100, nội dung file đầy đủ nếu không bị truncate).
pub async fn gist_list(token: &str) -> Result<Vec<RemoteGist>, String> {
    let c = client(token)?;
    let r = c.get("https://api.github.com/gists?per_page=100").send().await.map_err(|e| e.to_string())?;
    let list: Vec<RemoteGist> = check(r).await?.json().await.map_err(|e| e.to_string())?;
    Ok(list)
}

pub async fn gist_delete(token: &str, id: &str) -> Result<(), String> {
    let c = client(token)?;
    let r = c.delete(format!("https://api.github.com/gists/{id}")).send().await.map_err(|e| e.to_string())?;
    if r.status().as_u16() == 404 {
        return Ok(());
    }
    check(r).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(title: &str, tags: &[&str], folder: &str) -> SnippetInput {
        SnippetInput {
            id: None, title: title.into(), language: "rust".into(), code: "fn main(){}".into(),
            description: "d".into(), tags: tags.iter().map(|s| s.to_string()).collect(),
            folder: folder.into(), favorite: false,
        }
    }

    #[test]
    fn crud_search_facets() {
        let v = Vault::in_memory().unwrap();
        let a = v.save(input("Debounce", &["js", "util"], "Work")).unwrap();
        let _b = v.save(input("Read file", &["io"], "")).unwrap();
        assert_eq!(v.list(&Filter::default()).unwrap().len(), 2);
        let f = Filter { query: Some("deb".into()), ..Default::default() };
        assert_eq!(v.list(&f).unwrap().len(), 1);
        let f = Filter { tag: Some("util".into()), folder: Some("Work".into()), ..Default::default() };
        assert_eq!(v.list(&f).unwrap().len(), 1);
        let f = Filter { tag: Some("ut".into()), ..Default::default() };
        assert_eq!(v.list(&f).unwrap().len(), 0, "tag phải khớp chính xác");
        v.set_favorite(a.id, true).unwrap();
        let fc = v.facets().unwrap();
        assert_eq!((fc.total, fc.favorites), (2, 1));
        let mut up = input("Debounce2", &[], "Work");
        up.id = Some(a.id);
        assert_eq!(v.save(up).unwrap().title, "Debounce2");
        v.delete(a.id).unwrap();
        assert!(v.get(a.id).unwrap().is_none());
    }

    #[test]
    fn search_escapes_wildcards() {
        let v = Vault::in_memory().unwrap();
        v.save(input("abc", &[], "")).unwrap();
        let f = Filter { query: Some("%".into()), ..Default::default() };
        assert_eq!(v.list(&f).unwrap().len(), 0);
    }

    #[test]
    fn settings_roundtrip() {
        let v = Vault::in_memory().unwrap();
        assert_eq!(v.get_setting("k").unwrap(), None);
        v.set_setting("k", "1").unwrap();
        v.set_setting("k", "2").unwrap();
        assert_eq!(v.get_setting("k").unwrap().as_deref(), Some("2"));
    }

    #[test]
    fn lang_mapping() {
        assert_eq!(lang_for("a.py", None), "python");
        assert_eq!(lang_for("README", Some("Markdown")), "markdown");
    }
}
