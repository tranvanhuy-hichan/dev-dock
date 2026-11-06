use bollard::query_parameters::*;
use bollard::Docker;
use futures_util::{future::join_all, StreamExt};
use serde::Serialize;
use serde_json::Value;

#[derive(Serialize, Debug, Clone)]
pub struct ContainerInfo {
    pub id: String,
    pub name: String,
    pub image: String,
    pub state: String,
    pub status: String,
    pub ports: Vec<String>,
    pub created: i64,
    pub project: Option<String>,
}

#[derive(Serialize, Debug, Clone)]
pub struct ImageInfo {
    pub id: String,
    pub tags: Vec<String>,
    pub size: i64,
    pub created: i64,
    pub containers: i64,
}

#[derive(Serialize, Debug, Clone)]
pub struct ContainerStats {
    pub id: String,
    pub name: String,
    pub cpu: f64,
    pub mem_used: u64,
    pub mem_limit: u64,
    pub net_rx: u64,
    pub net_tx: u64,
}

#[derive(Serialize, Debug)]
pub struct DockerInfo {
    pub version: String,
    pub os: String,
    pub cpus: i64,
    pub mem_total: i64,
    pub containers: i64,
    pub running: i64,
    pub images: i64,
}

#[derive(Serialize, Debug)]
pub struct DockerStatus {
    pub available: bool,
    pub error: Option<String>,
    pub info: Option<DockerInfo>,
}

fn connect() -> Result<Docker, String> {
    Docker::connect_with_local_defaults().map_err(|e| e.to_string())
}

fn s(v: &Value, k: &str) -> String {
    v[k].as_str().unwrap_or_default().to_string()
}

pub async fn status() -> DockerStatus {
    match info().await {
        Ok(i) => DockerStatus { available: true, error: None, info: Some(i) },
        Err(e) => DockerStatus { available: false, error: Some(e), info: None },
    }
}

pub async fn info() -> Result<DockerInfo, String> {
    let d = connect()?;
    let i = d.info().await.map_err(|e| e.to_string())?;
    let v = serde_json::to_value(&i).map_err(|e| e.to_string())?;
    Ok(DockerInfo {
        version: s(&v, "ServerVersion"),
        os: s(&v, "OperatingSystem"),
        cpus: v["NCPU"].as_i64().unwrap_or(0),
        mem_total: v["MemTotal"].as_i64().unwrap_or(0),
        containers: v["Containers"].as_i64().unwrap_or(0),
        running: v["ContainersRunning"].as_i64().unwrap_or(0),
        images: v["Images"].as_i64().unwrap_or(0),
    })
}

pub async fn list_containers(all: bool) -> Result<Vec<ContainerInfo>, String> {
    let d = connect()?;
    let list = d
        .list_containers(Some(ListContainersOptionsBuilder::default().all(all).build()))
        .await
        .map_err(|e| e.to_string())?;
    let mut out: Vec<ContainerInfo> = list
        .iter()
        .filter_map(|c| serde_json::to_value(c).ok())
        .map(|v| {
            let ports = v["Ports"]
                .as_array()
                .map(|a| {
                    let mut p: Vec<String> = a
                        .iter()
                        .map(|p| {
                            let t = p["Type"].as_str().unwrap_or("tcp");
                            match p["PublicPort"].as_u64() {
                                Some(pub_) => format!("{}:{}->{}/{}", p["IP"].as_str().unwrap_or(""), pub_, p["PrivatePort"], t),
                                None => format!("{}/{}", p["PrivatePort"], t),
                            }
                        })
                        .collect();
                    p.dedup();
                    p
                })
                .unwrap_or_default();
            ContainerInfo {
                id: s(&v, "Id"),
                name: v["Names"][0].as_str().unwrap_or("").trim_start_matches('/').to_string(),
                image: s(&v, "Image"),
                state: s(&v, "State"),
                status: s(&v, "Status"),
                ports,
                created: v["Created"].as_i64().unwrap_or(0),
                project: v["Labels"]["com.docker.compose.project"].as_str().map(String::from),
            }
        })
        .collect();
    out.sort_by(|a, b| (a.state != "running").cmp(&(b.state != "running")).then(a.name.cmp(&b.name)));
    Ok(out)
}

pub async fn container_action(id: &str, action: &str) -> Result<(), String> {
    let d = connect()?;
    let e = |e: bollard::errors::Error| e.to_string();
    match action {
        "start" => d.start_container(id, None::<StartContainerOptions>).await.map_err(e),
        "stop" => d.stop_container(id, None::<StopContainerOptions>).await.map_err(e),
        "restart" => d.restart_container(id, None::<RestartContainerOptions>).await.map_err(e),
        "pause" => d.pause_container(id).await.map_err(e),
        "unpause" => d.unpause_container(id).await.map_err(e),
        "kill" => d.kill_container(id, None::<KillContainerOptions>).await.map_err(e),
        "remove" => d
            .remove_container(id, Some(RemoveContainerOptionsBuilder::default().force(true).build()))
            .await
            .map_err(e),
        other => Err(format!("Action không hỗ trợ: {other}")),
    }
}

/// Lấy `tail` dòng log cuối (stdout+stderr gộp, có timestamp).
pub async fn container_logs(id: &str, tail: u32) -> Result<String, String> {
    let d = connect()?;
    let opts = LogsOptionsBuilder::default()
        .stdout(true)
        .stderr(true)
        .timestamps(true)
        .tail(&tail.to_string())
        .build();
    let mut stream = d.logs(id, Some(opts));
    let mut out = String::new();
    while let Some(chunk) = stream.next().await {
        match chunk {
            Ok(c) => out.push_str(&String::from_utf8_lossy(&c.into_bytes())),
            Err(e) => return Err(e.to_string()),
        }
    }
    Ok(out)
}

pub async fn list_images() -> Result<Vec<ImageInfo>, String> {
    let d = connect()?;
    let list = d.list_images(Some(ListImagesOptionsBuilder::default().build())).await.map_err(|e| e.to_string())?;
    let mut out: Vec<ImageInfo> = list
        .iter()
        .filter_map(|i| serde_json::to_value(i).ok())
        .map(|v| ImageInfo {
            id: s(&v, "Id"),
            tags: v["RepoTags"]
                .as_array()
                .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
                .unwrap_or_default(),
            size: v["Size"].as_i64().unwrap_or(0),
            created: v["Created"].as_i64().unwrap_or(0),
            containers: v["Containers"].as_i64().unwrap_or(0),
        })
        .collect();
    out.sort_by(|a, b| b.created.cmp(&a.created));
    Ok(out)
}

pub async fn remove_image(id: &str, force: bool) -> Result<(), String> {
    let d = connect()?;
    d.remove_image(id, Some(RemoveImageOptionsBuilder::default().force(force).build()), None)
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub async fn pull_image(image: &str) -> Result<String, String> {
    let d = connect()?;
    let (name, tag) = match image.rsplit_once(':') {
        Some((n, t)) if !t.contains('/') => (n, t),
        _ => (image, "latest"),
    };
    let opts = CreateImageOptionsBuilder::default().from_image(name).tag(tag).build();
    let mut stream = d.create_image(Some(opts), None, None);
    let mut last = String::new();
    while let Some(r) = stream.next().await {
        let info = r.map_err(|e| e.to_string())?;
        if let Some(st) = info.status {
            last = st;
        }
    }
    Ok(last)
}

pub async fn stats_all() -> Result<Vec<ContainerStats>, String> {
    let d = connect()?;
    let running = list_containers(false).await?;
    let futs = running.into_iter().map(|c| {
        let d = d.clone();
        async move {
            let opts = StatsOptionsBuilder::default().stream(false).one_shot(false).build();
            let mut st = d.stats(&c.id, Some(opts));
            let first = st.next().await?.ok()?;
            let v = serde_json::to_value(first).ok()?;
            Some(compute_stats(&c.id, &c.name, &v))
        }
    });
    let mut out: Vec<ContainerStats> = join_all(futs).await.into_iter().flatten().collect();
    out.sort_by(|a, b| b.cpu.partial_cmp(&a.cpu).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}

pub fn compute_stats(id: &str, name: &str, v: &Value) -> ContainerStats {
    let cpu_total = v["cpu_stats"]["cpu_usage"]["total_usage"].as_f64().unwrap_or(0.0);
    let pre_total = v["precpu_stats"]["cpu_usage"]["total_usage"].as_f64().unwrap_or(0.0);
    let sys = v["cpu_stats"]["system_cpu_usage"].as_f64().unwrap_or(0.0);
    let pre_sys = v["precpu_stats"]["system_cpu_usage"].as_f64().unwrap_or(0.0);
    let cpus = v["cpu_stats"]["online_cpus"].as_f64().filter(|c| *c > 0.0).unwrap_or(1.0);
    let (dc, ds) = (cpu_total - pre_total, sys - pre_sys);
    let cpu = if ds > 0.0 && dc >= 0.0 { dc / ds * cpus * 100.0 } else { 0.0 };
    let usage = v["memory_stats"]["usage"].as_u64().unwrap_or(0);
    let cache = v["memory_stats"]["stats"]["inactive_file"]
        .as_u64()
        .or_else(|| v["memory_stats"]["stats"]["cache"].as_u64())
        .unwrap_or(0);
    let (mut rx, mut tx) = (0, 0);
    if let Some(nets) = v["networks"].as_object() {
        for n in nets.values() {
            rx += n["rx_bytes"].as_u64().unwrap_or(0);
            tx += n["tx_bytes"].as_u64().unwrap_or(0);
        }
    }
    ContainerStats {
        id: id.into(),
        name: name.into(),
        cpu,
        mem_used: usage.saturating_sub(cache),
        mem_limit: v["memory_stats"]["limit"].as_u64().unwrap_or(0),
        net_rx: rx,
        net_tx: tx,
    }
}

/// Chạy `docker compose` trên file compose. Trả về output gộp.
pub async fn compose_run(file: &str, action: &str) -> Result<String, String> {
    let path = std::path::Path::new(file);
    if !path.is_file() {
        return Err(format!("Không tìm thấy file: {file}"));
    }
    let args: Vec<&str> = match action {
        "up" => vec!["up", "-d"],
        "down" => vec!["down"],
        "stop" => vec!["stop"],
        "restart" => vec!["restart"],
        "pull" => vec!["pull"],
        "ps" => vec!["ps", "-a"],
        "logs" => vec!["logs", "--no-color", "--tail", "200"],
        other => return Err(format!("Action không hỗ trợ: {other}")),
    };
    let dir = path.parent().unwrap_or(std::path::Path::new("."));
    let out = tokio::process::Command::new("docker")
        .arg("compose")
        .arg("-f")
        .arg(path)
        .args(&args)
        .current_dir(dir)
        .output()
        .await
        .map_err(|e| format!("Không chạy được docker: {e}"))?;
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    if out.status.success() { Ok(text) } else { Err(text) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stats_math() {
        let v = serde_json::json!({
            "cpu_stats": {"cpu_usage":{"total_usage": 200.0}, "system_cpu_usage": 2000.0, "online_cpus": 4},
            "precpu_stats": {"cpu_usage":{"total_usage": 100.0}, "system_cpu_usage": 1000.0},
            "memory_stats": {"usage": 1000, "limit": 5000, "stats": {"inactive_file": 200}},
            "networks": {"eth0": {"rx_bytes": 5, "tx_bytes": 7}}
        });
        let s = compute_stats("i", "n", &v);
        assert!((s.cpu - 40.0).abs() < 1e-9);
        assert_eq!((s.mem_used, s.net_rx, s.net_tx), (800, 5, 7));
    }

    #[tokio::test]
    async fn status_never_panics() {
        let _ = status().await;
    }
}
