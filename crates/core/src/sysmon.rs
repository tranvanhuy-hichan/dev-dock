use serde::Serialize;
use std::process::Command;
use std::time::Instant;
use sysinfo::{Disks, Networks, Pid, ProcessesToUpdate, System};

#[derive(Serialize, Clone, Debug)]
pub struct DiskInfo {
    pub name: String,
    pub mount: String,
    pub fs: String,
    pub total: u64,
    pub available: u64,
}

#[derive(Serialize, Clone, Debug)]
pub struct GpuInfo {
    pub name: String,
    pub util: f32,
    pub mem_used: u64,  // bytes
    pub mem_total: u64, // bytes
    pub temp: f32,
}

#[derive(Serialize, Clone, Debug)]
pub struct NetIface {
    pub name: String,
    pub rx_total: u64,
    pub tx_total: u64,
    pub rx_rate: f64, // bytes/s
    pub tx_rate: f64,
}

#[derive(Serialize, Clone, Debug)]
pub struct Snapshot {
    pub host: String,
    pub os: String,
    pub uptime: u64,
    pub cpu_brand: String,
    pub cpu_total: f32,
    pub cpu_cores: Vec<f32>,
    pub mem_used: u64,
    pub mem_total: u64,
    pub swap_used: u64,
    pub swap_total: u64,
    pub load: [f64; 3],
    pub net: Vec<NetIface>,
    pub net_rx_rate: f64,
    pub net_tx_rate: f64,
    pub disks: Vec<DiskInfo>,
    pub gpus: Vec<GpuInfo>,
}

#[derive(Serialize, Clone, Debug)]
pub struct ProcInfo {
    pub pid: u32,
    pub name: String,
    pub cpu: f32,
    pub mem: u64,
    pub status: String,
    pub user: Option<String>,
    pub cmd: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct PortInfo {
    pub port: u16,
    pub protocol: String,
    pub address: String,
    pub pid: u32,
    pub process: String,
}

pub struct Monitor {
    sys: System,
    nets: Networks,
    last: Instant,
    gpu_cache: (Instant, Vec<GpuInfo>),
}

impl Default for Monitor {
    fn default() -> Self {
        Self::new()
    }
}

impl Monitor {
    pub fn new() -> Self {
        let mut sys = System::new_all().unwrap_or_else(|_| System::new().expect("sysinfo không khả dụng"));
        sys.refresh_all();
        Self {
            sys,
            nets: Networks::new_with_refreshed_list().or_else(|_| Networks::new()).expect("sysinfo networks"),
            last: Instant::now(),
            gpu_cache: (Instant::now() - std::time::Duration::from_secs(60), vec![]),
        }
    }

    pub fn snapshot(&mut self) -> Snapshot {
        self.sys.refresh_cpu_all();
        self.sys.refresh_memory();
        self.nets.refresh(true);
        let secs = self.last.elapsed().as_secs_f64().max(0.001);
        self.last = Instant::now();

        let mut net: Vec<NetIface> = self
            .nets
            .iter()
            .map(|(name, d)| NetIface {
                name: name.to_string_lossy().into(),
                rx_total: d.total_received(),
                tx_total: d.total_transmitted(),
                rx_rate: d.received() as f64 / secs,
                tx_rate: d.transmitted() as f64 / secs,
            })
            .collect();
        net.sort_by(|a, b| a.name.cmp(&b.name));
        let (rx, tx) = net
            .iter()
            .filter(|n| n.name != "lo")
            .fold((0.0, 0.0), |a, n| (a.0 + n.rx_rate, a.1 + n.tx_rate));

        let disks: Vec<DiskInfo> = Disks::new_with_refreshed_list()
            .map(|d| d.iter().map(|d| (d.name().to_string_lossy().to_string(), d.mount_point().to_string_lossy().to_string(), d.file_system().to_string_lossy().to_string(), d.total_space(), d.available_space())).collect::<Vec<_>>())
            .unwrap_or_default()
            .into_iter()
            .filter(|d| d.3 > 0)
            .map(|d| DiskInfo { name: d.0, mount: d.1, fs: d.2, total: d.3, available: d.4 })
            .collect();

        if self.gpu_cache.0.elapsed().as_secs() >= 2 {
            self.gpu_cache = (Instant::now(), read_gpus());
        }

        let la = System::load_average().unwrap_or_default();
        Snapshot {
            host: System::host_name().unwrap_or_default(),
            os: System::long_os_version().unwrap_or_default(),
            uptime: System::uptime().unwrap_or(0),
            cpu_brand: self.sys.cpus().first().map(|c| c.brand().to_string()).unwrap_or_default(),
            cpu_total: self.sys.global_cpu_usage(),
            cpu_cores: self.sys.cpus().iter().map(|c| c.usage()).collect(),
            mem_used: self.sys.used_memory(),
            mem_total: self.sys.total_memory(),
            swap_used: self.sys.used_swap(),
            swap_total: self.sys.total_swap(),
            load: [la.one, la.five, la.fifteen],
            net,
            net_rx_rate: rx,
            net_tx_rate: tx,
            disks,
            gpus: self.gpu_cache.1.clone(),
        }
    }

    pub fn processes(&mut self) -> Vec<ProcInfo> {
        self.sys.refresh_processes(ProcessesToUpdate::All, true);
        let users = sysinfo::Users::new_with_refreshed_list().or_else(|_| sysinfo::Users::new()).ok();
        let mut v: Vec<ProcInfo> = self
            .sys
            .processes()
            .iter()
            .map(|(pid, p)| ProcInfo {
                pid: pid.as_u32(),
                name: p.name().to_string_lossy().into(),
                cpu: p.cpu_usage(),
                mem: p.memory(),
                status: format!("{:?}", p.status()),
                user: p.user_id().and_then(|u| users.as_ref()?.get_user_by_id(u)).map(|u| u.name().to_string()),
                cmd: p.cmd().iter().map(|s| s.to_string_lossy()).collect::<Vec<_>>().join(" "),
            })
            .collect();
        v.sort_by(|a, b| b.cpu.partial_cmp(&a.cpu).unwrap_or(std::cmp::Ordering::Equal));
        v
    }

    pub fn kill(&mut self, pid: u32, force: bool) -> Result<(), String> {
        self.sys.refresh_processes(ProcessesToUpdate::Some(&[Pid::from_u32(pid)]), true);
        let p = self.sys.process(Pid::from_u32(pid)).ok_or("Process không tồn tại")?;
        let ok = if force {
            p.kill()
        } else {
            p.kill_with(sysinfo::Signal::Term).unwrap_or_else(|| p.kill())
        };
        if ok { Ok(()) } else { Err("Không có quyền hoặc không thể dừng process".into()) }
    }
}

pub fn list_ports() -> Result<Vec<PortInfo>, String> {
    let all = listeners::get_all().map_err(|e| e.to_string())?;
    let mut v: Vec<PortInfo> = all
        .into_iter()
        .map(|l| PortInfo {
            port: l.socket.port(),
            protocol: format!("{:?}", l.protocol).to_uppercase(),
            address: l.socket.ip().to_string(),
            pid: l.process.pid,
            process: l.process.name,
        })
        .collect();
    v.sort_by_key(|p| (p.port, p.pid, p.protocol.clone(), p.address.clone()));
    v.dedup_by(|a, b| a.port == b.port && a.pid == b.pid && a.protocol == b.protocol && a.address == b.address);
    Ok(v)
}

/// Đọc GPU NVIDIA qua nvidia-smi (không có thì trả về rỗng).
fn read_gpus() -> Vec<GpuInfo> {
    let out = Command::new("nvidia-smi")
        .args(["--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"])
        .output();
    let Ok(out) = out else { return vec![] };
    if !out.status.success() {
        return vec![];
    }
    parse_gpus(&String::from_utf8_lossy(&out.stdout))
}

pub fn parse_gpus(s: &str) -> Vec<GpuInfo> {
    s.lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split(',').map(str::trim).collect();
            if p.len() < 5 {
                return None;
            }
            Some(GpuInfo {
                name: p[0].into(),
                util: p[1].parse().unwrap_or(0.0),
                mem_used: p[2].parse::<u64>().unwrap_or(0) * 1024 * 1024,
                mem_total: p[3].parse::<u64>().unwrap_or(0) * 1024 * 1024,
                temp: p[4].parse().unwrap_or(0.0),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_parse() {
        let g = parse_gpus("NVIDIA RTX 4090, 35, 2048, 24564, 51\n");
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].mem_total, 24564 * 1024 * 1024);
    }

    #[test]
    fn snapshot_works() {
        let mut m = Monitor::new();
        let s = m.snapshot();
        assert!(s.mem_total > 0);
        assert!(!m.processes().is_empty());
    }

    #[test]
    fn ports_ok() {
        assert!(list_ports().is_ok());
    }
}
